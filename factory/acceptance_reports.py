"""Versioned acceptance provenance and append-only exports, using film-evaluation v1."""
from __future__ import annotations

from pathlib import Path
import subprocess

from studio.core import ROOT, fingerprint, sha256, write_json
from . import evaluation
from .evaluation_io import export_report
from .acceptance_baseline import load_report


PROTOCOL = "ai-studio.production-acceptance.v2"
CODE = ("acceptance.py", "acceptance_inputs.py", "acceptance_checks.py", "acceptance_audio.py", "acceptance_reports.py", "acceptance_baseline.py",
        "evaluation.py", "evaluation_io.py", "evaluation_media.py", "evaluation_external.py", "production_profiles.py", "quality.py")


def provenance(project, report, profile):
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5, check=True)
        commit = result.stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                                    text=True, timeout=5, check=True).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        commit, dirty = "unknown", "unknown"
    records = report.get("observations", {}).get("records", [])
    configs = [{"role": r.get("role"), "revision": r["config_revision"]}
               for r in records if r.get("kind") == "model_call" and r.get("config_revision") is not None]
    models = [{key: r.get(key) for key in ("role", "provider", "model", "configured_model", "model_identity")}
              for r in records if r.get("model") or r.get("configured_model")]
    code = {"factory/" + name: sha256(ROOT / "factory" / name) for name in CODE}
    return {"schema": PROTOCOL, "protocol_version": 2, "git_commit": commit, "git_dirty": dirty,
            "source_code": code, "source_signature": fingerprint(code),
            "input_snapshot_sha256": fingerprint(project), "resolved_profile": profile,
            "resolved_profile_signature": fingerprint(profile),
            "project_version": {"revision": project.get("revision"), "production_revision": project.get("production_revision")},
            "configuration_versions": configs or ["unknown"], "model_versions": models or ["unknown"],
            "model_execution": "not_run", "generation_submission": "not_run", "database_writes": False,
            "consistency_boundary": "Read-only SQLite transaction; receipt hashes checked again after evaluation. Not an atomic cross-filesystem snapshot."}


def compare(current, baseline):
    if not isinstance(baseline, dict) or baseline.get("schema") != "ai-studio.film-evaluation.v1":
        return {"comparable": False, "reason": "Historical sample reports lack the versioned film protocol and media binding"}
    return evaluation.compare_reports(current, baseline)


def export(report, parent):
    revision = report.get("production_revision")
    version = str(revision) if type(revision) is int and 1 <= revision <= 2147483647 else "unknown"
    directory = Path(parent) / (report["project_id"] + "-v" + version + "-" + report["run_id"])
    report["report_dir"] = str(directory)
    export_report(report, directory, finalize=False)
    failures = []
    for check in report["checks"]:
        if check["status"] != "fail":
            continue
        evidence = list(check.get("evidence", []))
        media = report.get("media", {}).get(check.get("shot_id"))
        if media:
            evidence.append(media)
        failures.append({**check, "project_id": report["project_id"], "production_revision": report["production_revision"],
                         "media_signature": report["media_signature"], "evidence": evidence})
    write_json(directory / "failures.json", failures)
    lines = ["# Production acceptance", "", f"Project: {report['project_id']}",
             f"Production revision: {report.get('production_revision')}", f"Verdict: {report['verdict']}",
             f"Technical passed: {report['technical_passed']}", f"Human approved: {report['human_approved']}",
             "Model execution: not_run; imported evidence is not a new model execution.",
             f"Failed checks: {len(failures)}", "", "## Evidence", ""]
    lines.extend(f"- {v['category']}/{v['check']} ({v.get('shot_id') or 'project'}): see failures.json" for v in failures)
    (directory / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(directory / "export-manifest.json", {"schema": "ai-studio.evaluation-export.v1", "run_id": report["run_id"],
        "files": [{"path": p.relative_to(directory).as_posix(), "sha256": sha256(p), "bytes": p.stat().st_size}
                  for p in sorted(directory.rglob("*")) if p.is_file() and p.name != "export-manifest.json"]})
    return report
