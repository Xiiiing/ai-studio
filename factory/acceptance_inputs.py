"""Bounded, read-only inputs for production acceptance, independent of runtime config."""
from __future__ import annotations

import re
from pathlib import Path

from .evaluation_io import finite


ID = re.compile(r"[a-f0-9]{12}")
SHA = re.compile(r"[a-f0-9]{64}")


def project_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError("An exact 12-character lowercase project ID is required")
    return value


def source_path(root, value, *, slug=None):
    """Reject links and traversal before reading; missing files become evidence later."""
    if not isinstance(value, str) or not value.startswith("production/"):
        raise ValueError("Expected a recorded production-relative media path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts) or "\\" in value:
        raise ValueError("Non-canonical production media path")
    if slug is not None and (len(parts) < 3 or parts[1] != slug):
        raise ValueError("Media belongs to another project")
    path = Path(root)
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("Symbolic links are not accepted as production evidence")
    return path


def guard(project, root):
    """Validate shape/ownership, not quality. Never repair or upgrade the input."""
    if not isinstance(project, dict):
        raise ValueError("Project snapshot must be an object")
    pid = project_id(project.get("id"))
    revision = project.get("production_revision")
    if revision is not None and (type(revision) is not int or not 1 <= revision <= 2147483647):
        raise ValueError("Recorded production_revision must be a bounded positive integer")
    for name in ("edit_plan", "series_references"):
        if project.get(name) is not None and not isinstance(project[name], dict):
            raise ValueError("Invalid structured " + name)
    slug = project.get("slug") or "factory-" + pid
    if not isinstance(slug, str) or not re.fullmatch(r"factory-[A-Za-z0-9_-]{1,60}", slug):
        raise ValueError("Invalid factory project slug")
    source_path(root, "production/" + slug + "/factory_owner.json", slug=slug)
    shots = project.get("shots", [])
    if not isinstance(shots, list) or len(shots) > 80:
        raise ValueError("Acceptance supports at most 80 recorded shots")
    for shot in shots:
        if not isinstance(shot, dict) or not isinstance(shot.get("id"), str):
            raise ValueError("Each shot needs its recorded ID")
        seconds = finite(shot.get("seconds"))
        if seconds is None or not 0 < seconds <= 15:
            raise ValueError("Invalid recorded shot duration")
        lines = shot.get("dialogue", [])
        if not isinstance(lines, list) or len(lines) > 40 or any(not isinstance(v, dict) for v in lines):
            raise ValueError("Invalid recorded dialogue")
        for kind in ("video", "image"):
            receipt = shot.get(kind)
            if receipt is not None:
                if not isinstance(receipt, dict):
                    raise ValueError("Media receipts must be objects")
                if receipt.get("path"):
                    source_path(root, receipt["path"], slug=slug if kind == "video" else None)
    for name in ("episode", "script"):
        value = project.get(name)
        if value is not None:
            if not isinstance(value, dict) or not isinstance(value.get("shots", []), list):
                raise ValueError("Invalid structured screenplay")
            if len(value.get("shots", [])) > 80 or any(not isinstance(v, dict) or not isinstance(v.get("id"), str) for v in value.get("shots", [])):
                raise ValueError("Invalid screenplay shot sequence")
    delivery = project.get("delivery") or {}
    if not isinstance(delivery, dict) or not isinstance(delivery.get("artifacts", []), list) or len(delivery.get("artifacts", [])) > 200:
        raise ValueError("Invalid bounded delivery manifest")
    for receipt in delivery.get("artifacts", []):
        if not isinstance(receipt, dict):
            raise ValueError("Delivery artifacts must be receipt objects")
        if receipt.get("path"):
            source_path(root, receipt["path"], slug=slug)
    if delivery.get("path"):
        source_path(root, delivery["path"], slug=slug)
    assets = project.get("assets") or {}
    if not isinstance(assets, dict) or not isinstance(assets.get("audio", {}), dict):
        raise ValueError("Invalid recorded asset collection")
    lines = assets.get("audio", {}).get("lines", [])
    if not isinstance(lines, list) or len(lines) > 3200:
        raise ValueError("Invalid recorded voice receipt collection")
    for receipt in lines:
        if not isinstance(receipt, dict):
            raise ValueError("Voice receipts must be objects")
        if receipt.get("path"):
            source_path(root, receipt["path"], slug=slug)
    return slug


def output_parent(root, output):
    path = Path(output).absolute()
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("Report output may not follow symbolic links")
    path = path.resolve()
    if path.is_relative_to(Path(root).resolve() / "production"):
        raise ValueError("Reports must be outside production media")
    return path
