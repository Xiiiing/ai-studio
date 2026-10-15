"""Read-only, version-bound production acceptance. Never starts services or models."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from studio.core import ROOT, fingerprint, sha256
from . import evaluation
from .acceptance_inputs import SHA, guard, output_parent, project_id, source_path
from .acceptance_checks import contracts, receipt_evidence
from .acceptance_audio import contracts as audio_contracts
from .acceptance_reports import compare, export, provenance, load_report
from .evaluation_io import database_project, read_json, row
from .evaluation_external import human_checks, vbench_checks


def validate(pid, *, database=None, project_json=None, root=ROOT, output=None,
             production_revision=None, media_sha=None, human=None, vbench=None, baseline=None):
    """Evaluate an immutable observed revision, exporting only to a new report folder."""
    project_id(pid)
    root = Path(root).resolve(strict=True)
    parent = output_parent(root, output if output is not None else root / "runtime/factory/acceptance")
    if production_revision is not None and (type(production_revision) is not int or production_revision < 1):
        raise ValueError("production_revision must be a positive integer")
    if media_sha is not None and (not isinstance(media_sha, str) or not SHA.fullmatch(media_sha)):
        raise ValueError("media_sha must be an exact lowercase SHA-256")
    if database is not None and project_json is not None:
        raise ValueError("Choose a read-only database or a snapshot JSON, not both")
    if project_json is None and database is None:
        from .config import DATABASE
        database = DATABASE
    load = (lambda: read_json(project_json)) if project_json is not None else (lambda: database_project(database, pid))
    project = load()
    if not isinstance(project, dict) or project.get("id") != pid:
        raise ValueError("Snapshot project ID does not match the requested project")
    slug = guard(project, root)
    signature = fingerprint(project)
    checks, profile = contracts(project, root, slug, production_revision=production_revision, media_sha=media_sha)
    checks.extend(audio_contracts(project, root, slug))
    context = {"delivery_artifacts": [receipt_evidence(v) for v in (project.get("delivery") or {}).get("artifacts", [])],
               "voice_artifacts": [{"id": v.get("id"), **receipt_evidence(v)} for v in (project.get("assets") or {}).get("audio", {}).get("lines", [])]}
    report = evaluation.evaluate(project, root, additional_checks=checks, media_context=context)
    for document, kind in ((human, "human"), (vbench, "vbench")):
        if document is None:
            continue
        try:
            imported = (human_checks(document, project, report["media_signature"], report["dimensions"])
                        if kind == "human" else vbench_checks(document, report["media"]))
            report["checks"] = [v for v in report["checks"] if not (
                v["source"] == kind and (kind == "vbench" or v["check"] == "producer_review"))]
            report["checks"].extend(imported)
            if kind == "human":
                report["human_approved"] = document.get("decision") == "approve" and bool(imported) and all(v["status"] == "pass" for v in imported)
        except (ValueError, TypeError, AttributeError) as exc:
            report["checks"].append(row("delivery", kind + "_review_binding", "fail", source=kind,
                                        evidence=[str(exc)]))
    # Observe-only: if another process advances a project or changes receipt-backed
    # input while decoding, this run cannot certify the originally selected state.
    try:
        stable = fingerprint(load()) == signature
    except (ValueError, OSError, KeyError):
        stable = False
    report["checks"].append(row("delivery", "input_snapshot_unchanged", "pass" if stable else "fail",
                                evidence=[{"input_snapshot_sha256": signature}]))
    evidence = {}
    for check in report["checks"]:
        if check.get("source") != "deterministic":
            continue  # User-authored review evidence is never a filesystem read instruction.
        for item in check.get("evidence", []):
            if isinstance(item, dict) and item.get("path") and item.get("sha256"):
                evidence[(item["path"], item["sha256"])] = check.get("shot_id")
    for (path, digest), sid in evidence.items():
        try:
            stable = sha256(source_path(root, path)) == digest
        except (ValueError, OSError):
            stable = False
        report["checks"].append(row("delivery", "evidence_unchanged", "pass" if stable else "fail",
                                    shot_id=sid, evidence=[{"path": path, "sha256": digest}]))
    report.update(evaluation.conclusion(report["checks"], report["human_approved"], report["dimensions"]))
    report["acceptance"] = provenance(project, report, profile)
    report["acceptance"]["requested"] = {"project_id": pid, "production_revision": production_revision, "media_sha": media_sha}
    report["acceptance"]["evidence_receipts_signature"] = fingerprint(context)
    report["acceptance"]["imported_document_signatures"] = {key: fingerprint(value) if value is not None else None for key, value in (("human", human), ("vbench", vbench), ("baseline", baseline))}
    report["evaluation_code"].update(report["acceptance"]["source_code"])
    if baseline is not None:
        report["comparison"] = compare(report, baseline)
    return export(report, parent)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", nargs="?", help="Legacy positional project ID remains accepted")
    parser.add_argument("--project-id")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--database", type=Path, help="Existing SQLite file, opened mode=ro in one read transaction")
    source.add_argument("--project-json", type=Path, help="Frozen project snapshot; exact project ID still required")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, help="Report parent outside production; each run gets a new child directory")
    parser.add_argument("--production-revision", type=int)
    parser.add_argument("--media-sha")
    parser.add_argument("--human-review", type=Path)
    parser.add_argument("--vbench-report", type=Path, help="Import existing hash-bound evidence; do not execute VBench")
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    if bool(args.project) == bool(args.project_id):
        parser.error("Provide one positional project ID or --project-id")
    try:
        report = validate(args.project or args.project_id, database=args.database, project_json=args.project_json,
                          root=args.root, output=args.output, production_revision=args.production_revision,
                          media_sha=args.media_sha, human=read_json(args.human_review) if args.human_review else None,
                          vbench=read_json(args.vbench_report) if args.vbench_report else None,
                          baseline=load_report(args.baseline) if args.baseline else None)
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({key: report[key] for key in ("project_id", "production_revision", "verdict", "technical_passed", "human_approved", "report_dir")}, ensure_ascii=False))
    return 0 if report["verdict"] == "human_approved" else 2 if report["verdict"] == "needs_repair" else 3


if __name__ == "__main__":
    raise SystemExit(main())
