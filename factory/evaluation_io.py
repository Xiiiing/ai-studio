"""Read-only inputs and append-only evaluation exports; no runtime service writes."""
from __future__ import annotations

import csv
import json
import math
import sqlite3
import shutil
from pathlib import Path

from studio.core import fingerprint, sha256, within, write_json


def read_json(path, maximum=16 * 1024 * 1024):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError("Evaluation JSON is missing or exceeds its size bound")
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def database_project(database, pid):
    """Do not initialize, migrate, checkpoint, enqueue or approve a live project."""
    path = Path(database).resolve(strict=True)
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT snapshot,revision FROM projects WHERE id=?", (pid,)).fetchone()
        if row is None:
            raise KeyError("Project does not exist")
        project = json.loads(row[0]); project["revision"] = row[1]
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cloud_tasks'").fetchone():
            db.row_factory = sqlite3.Row
            project["evaluation_cloud_tasks"] = [dict(row) for row in db.execute("SELECT * FROM cloud_tasks WHERE project_id=?", (pid,))]
    return project


def media_path(root, receipt):
    value = receipt.get("path") if isinstance(receipt, dict) else receipt
    if not isinstance(value, str) or not value or Path(value).is_absolute() or not value.startswith("production/"):
        raise ValueError("Evaluation media must use a recorded production-relative path")
    path = within(Path(root).resolve(), value)
    if not path.is_file() or path.stat().st_size > 2 * 1024**3:
        raise ValueError("Recorded media is missing or exceeds the evaluation size bound")
    return path


def check_receipt(root, receipt):
    path = media_path(root, receipt)
    digest = sha256(path)
    expected = receipt.get("sha256")
    if not expected or digest != expected or receipt.get("bytes", path.stat().st_size) != path.stat().st_size:
        raise ValueError("Media does not match the immutable checksum/size receipt")
    return path, digest


def finite(value):
    return value if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) else None


def row(category, check, status, *, shot_id=None, evidence=None, metrics=None, source="deterministic"):
    if status not in {"pass", "fail", "not_run", "unavailable", "review_needed"}:
        raise ValueError("Unknown evaluation status")
    return {"category": category, "check": check, "status": status, "shot_id": shot_id,
            "evidence": evidence or [], "metrics": metrics or {}, "source": source}


def export_report(report, output, observations_dir=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "report.json", report)
    if observations_dir is not None and Path(observations_dir).is_dir() and any(Path(observations_dir).iterdir()):
        shutil.copytree(observations_dir, output / "observations")
    fields = ["project_id", "production_revision", "run_id", "category", "check", "shot_id", "status", "source", "metrics", "evidence"]
    with (output / "metrics.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for value in report["checks"]:
            entry = {**{key: report.get(key) for key in fields[:3]}, **value}
            for key in ("metrics", "evidence"):
                entry[key] = json.dumps(entry.get(key), ensure_ascii=False, allow_nan=False)
            # Spreadsheet applications should never execute untrusted metadata.
            for key, value in entry.items():
                if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                    entry[key] = "'" + value
            writer.writerow({key: entry.get(key) for key in fields})
    write_json(output / "human-review-template.json", {
        "schema": "ai-studio.human-review.v1", "project_id": report["project_id"],
        "production_revision": report["production_revision"], "media_signature": report["media_signature"],
        "reviewer": "", "reviewed_at": "", "decision": "pending",
        "dimensions": {name: {"status": "pending", "evidence": [], "notes": ""} for name in report["dimensions"]}})
    write_json(output / "export-manifest.json", {"schema": "ai-studio.evaluation-export.v1", "run_id": report["run_id"],
        "files": [{"path": path.relative_to(output).as_posix(), "sha256": sha256(path), "bytes": path.stat().st_size}
                  for path in sorted(output.rglob("*")) if path.is_file()]})
    return output
