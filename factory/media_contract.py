"""Describe actual media provenance independently from the requested format."""
from __future__ import annotations


def video_motion(record):
    record = record if isinstance(record, dict) else {}
    if (record.get("origin") == "procedural_camera_motion"
            or record.get("generation_kind") == "procedural_camera_motion"
            or record.get("motion_method") == "still_camera"
            or record.get("diffusion_video") is False):
        return "still_camera"
    if record.get("generation_kind") in {"local_diffusion_video", "cloud_diffusion_video"}:
        return "generated_video"
    return "unknown"


def motion_provenance(shots, requested=None):
    records = [shot.get("video", {}) for shot in shots]
    methods = [video_motion(record) for record in records]
    return {"requested": requested,
            "generated_video_shots": methods.count("generated_video"),
            "still_camera_shots": methods.count("still_camera"),
            "unknown_shots": methods.count("unknown"),
            "lip_sync": bool(records) and all(record.get("lip_sync") is True for record in records)}


def visual_direction(style):
    return {key: style[key] for key in ("id", "name", "description", "prompt", "palette") if key in style}


def preview_contract_valid(project, style):
    from .creative_contract import resolve
    from studio.core import fingerprint
    contract = resolve(project)
    if contract is None:
        return True
    record = style.get("preview") or {}
    qa = record.get("qa") or {}
    semantic = qa.get("semantic") or {}
    return bool(qa.get("eligible_for_selection") is True
                and qa.get("technical", {}).get("status") == "pass"
                and semantic.get("status") == "pass"
                and semantic.get("style_alignment", {}).get("status") == "match"
                and record.get("creative_contract_signature") == fingerprint(contract)
                and record.get("visual_direction_signature") == fingerprint(visual_direction(style)))
