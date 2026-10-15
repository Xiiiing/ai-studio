"""Build a handoff manifest from fresh local acceptance, without generating or uploading."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import uuid

from studio.core import ROOT, fingerprint, sha256
from . import acceptance
from .acceptance_inputs import output_parent, project_id
from .creative_evidence_io import new_directory, write_new
from .delivery_readiness import bind_acceptance
from .evaluation_io import database_project, read_json


def prepare(pid, *, database=None, project_json=None, root=ROOT, output=None, production_revision=None):
    project_id(pid)
    if (database is None) == (project_json is None):
        raise ValueError("Choose exactly one existing read-only database or snapshot JSON")
    root = Path(root).resolve(strict=True)
    parent = output_parent(root, output if output is not None else root / "runtime/factory/submissions")
    load = (lambda: database_project(database, pid)) if database is not None else (lambda: read_json(project_json))
    observed = load()
    if not isinstance(observed, dict) or observed.get("id") != pid:
        raise ValueError("Project ID does not match the selected source")
    version = observed.get("production_revision") if production_revision is None else production_revision
    report = acceptance.validate(pid, database=database, project_json=project_json, root=root,
                                 output=parent, production_revision=version)
    manifest = bind_acceptance(observed, report)
    if fingerprint(load()) != fingerprint(observed):
        manifest.update(status="blocked", engineering_ready=False, evidence_basis="source_changed_during_handoff")
        manifest["checks"].append({"id": "handoff_snapshot_unchanged", "label": "交付期间项目版本保持不变", "status": "blocked"})
    report_file = Path(report["report_dir"]) / "report.json"
    manifest["acceptance_report"] = {"path": str(report_file), "sha256": sha256(report_file)}
    manifest["operations"] = {"model_requests": 0, "generation_submissions": 0, "platform_uploads": 0, "database_writes": False}
    directory = new_directory(parent / (pid + "-submission-" + uuid.uuid4().hex))
    write_new(directory / "submission-manifest.json", manifest)
    return {**manifest, "manifest_path": str(directory / "submission-manifest.json")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--database", type=Path)
    source.add_argument("--project-json", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--production-revision", type=int)
    args = parser.parse_args()
    try:
        result = prepare(args.project_id, database=args.database, project_json=args.project_json,
                         root=args.root, output=args.output, production_revision=args.production_revision)
    except (ValueError, KeyError, OSError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({key: result[key] for key in ("project_id", "status", "engineering_ready", "platform_ready", "manifest_path")}, ensure_ascii=False))
    # Exit zero certifies only engineering handoff. Platform review is always separate.
    return 0 if result["engineering_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
