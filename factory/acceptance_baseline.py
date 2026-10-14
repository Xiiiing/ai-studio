"""Verify completed acceptance exports before using their historical evidence."""
from __future__ import annotations

from pathlib import Path, PurePosixPath

from studio.core import sha256
from .evaluation_io import read_json


def load_report(path):
    path = Path(path).absolute()
    if any(value.is_symlink() for value in (path, *path.parents)):
        raise ValueError("Baseline report paths may not follow symbolic links")
    report = read_json(path)
    if not isinstance(report, dict):
        raise ValueError("Baseline report must be an object")
    if "acceptance" not in report:
        return report  # Historical files remain readable, not implicitly comparable.
    protocol = report["acceptance"]
    if (not isinstance(protocol, dict) or protocol.get("schema") != "ai-studio.production-acceptance.v2" or
            type(protocol.get("protocol_version")) is not int or protocol["protocol_version"] != 2):
        raise ValueError("Unsupported acceptance protocol in baseline")
    manifest_path = path.parent / "export-manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("Baseline completion manifest may not be a symbolic link")
    manifest = read_json(manifest_path)
    if (not isinstance(manifest, dict) or manifest.get("schema") != "ai-studio.evaluation-export.v1" or
            manifest.get("run_id") != report.get("run_id")):
        raise ValueError("Baseline completion manifest does not identify this run")
    items = manifest.get("files")
    if not isinstance(items, list) or not 1 <= len(items) <= 512:
        raise ValueError("Baseline completion manifest must be bounded and non-empty")
    seen, total = set(), 0
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValueError("Invalid baseline evidence receipt")
        value = item["path"]
        relative = PurePosixPath(value)
        if (relative.is_absolute() or "\\" in value or
                any(part in {"", ".", ".."} for part in value.split("/")) or value in seen):
            raise ValueError("Non-canonical or repeated baseline evidence path")
        candidate = path.parent
        for part in relative.parts:
            candidate = candidate / part
            if candidate.is_symlink():
                raise ValueError("Baseline evidence may not follow symbolic links")
        if not candidate.is_file() or type(item.get("bytes")) is not int or item["bytes"] < 0:
            raise ValueError("Missing or invalid baseline evidence")
        size = candidate.stat().st_size
        total += size
        if total > 256 * 1024 * 1024 or size != item["bytes"] or sha256(candidate) != item.get("sha256"):
            raise ValueError("Baseline evidence differs from its completion manifest")
        seen.add(value)
    if path.name != "report.json" or "report.json" not in seen:
        raise ValueError("Baseline manifest must bind the selected report.json")
    required = {"report.json", "metrics.csv", "human-review-template.json", "summary.md", "failures.json"}
    if not required <= seen:
        raise ValueError("Baseline manifest omits required acceptance evidence")
    return report
