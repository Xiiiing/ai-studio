"""Export the current episode configuration as an atomic, UTF-8 storyboard CSV.

One row is written for each dialogue line; a silent shot retains one empty-dialogue row.
configured_seed is only shot.seed from the current configuration. An empty value means
generator defaults apply; it is never substituted with a selected asset's actual seed.
references lists planned logical reference slots, not historical input asset receipts.
Actual generation provenance remains in each take's workflow and result receipts.
"""
from __future__ import annotations

import csv
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import tempfile
from .core import fingerprint

FIELDS = [
    "id", "timeline_start", "timeline_end", "seconds", "frames", "trim_frames",
    "native_width", "native_height", "width", "height", "fps", "reference_group",
    "dialogue_id", "role", "dialogue", "emotion", "dialogue_start", "chinese_chars",
    "prompt", "motion_prompt", "continuity", "sfx", "lip_sync", "reference_shots",
    "configured_seed", "reference_mode", "references",
]


def number(value):
    """Avoid floating-point accumulation artifacts in the sequential edit timeline."""
    return format(Decimal(str(value)).normalize(), "f")


def storyboard_rows(data):
    generation = data.get("generation", {})
    profile = data.get("production_profile")
    if profile:
        from factory.production_profiles import normalize
        profile = normalize(profile)
    character_map = {item["character_id"]: item for item in (profile or {}).get("characters", [])}
    elapsed = Decimal("0")
    for shot in data["shots"]:
        seconds = Decimal(str(shot["seconds"]))
        end = elapsed + seconds
        group = shot.get("reference_group", "")
        mode = shot.get("reference_mode", "")
        reference_shots = shot.get("reference_shots", []) or []
        # Mirror the current generator's logical reference order; do not inspect selected assets.
        roles = ["male"] if group == "male" else (
            ["female", "male"] if group == "two" else ["female"])
        if profile:
            from factory.series_bindings import shot_roles
            roles = shot_roles(shot, data.get("cast", {}))
        if mode == "scene":
            roles = []
        references = ["characters/" + role for role in roles]
        references.extend("shots/" + reference for reference in reference_shots)
        base = {
            "id": shot["id"], "timeline_start": number(elapsed),
            "timeline_end": number(end), "seconds": number(seconds),
            "frames": shot.get("frames", generation.get("frames_per_shot", "")),
            "trim_frames": shot.get("trim_frames", generation.get("trim_frames_per_shot", "")),
            "native_width": generation.get("width", ""),
            "native_height": generation.get("height", ""),
            "width": data.get("width", ""), "height": data.get("height", ""),
            "fps": data.get("fps", ""), "reference_group": group,
            "prompt": shot.get("prompt", ""), "motion_prompt": shot.get("motion_prompt", ""),
            "continuity": shot.get("continuity", ""), "sfx": shot.get("sfx", ""),
            "lip_sync": str(bool(shot.get("lip_sync", False))).lower(),
            "reference_shots": ";".join(reference_shots),
            "configured_seed": shot.get("seed", ""), "reference_mode": mode,
            "references": json.dumps(references, ensure_ascii=False, separators=(",", ":")),
        }
        if profile:
            base.update(production_profile=json.dumps(profile, ensure_ascii=False, sort_keys=True),
                        profile_signature=fingerprint(profile))
        for line in shot.get("dialogue", []) or [{}]:
            text = line.get("text", "")
            yield {
                **base, "dialogue_id": line.get("id", ""), "role": line.get("role", ""),
                "dialogue": text, "emotion": line.get("emotion", ""),
                "dialogue_start": line.get("start", ""),
                "chinese_chars": len(re.findall(r"[\u4e00-\u9fff]", text)),
                **({"character_id": line.get("role", ""), "voice_id": character_map.get(line.get("role"), {}).get("voice_id", "")}
                   if profile else {}),
            }
        elapsed = end


def export_storyboard(data, path):
    """Atomically replace only the CSV; the caller validates the episode beforehand."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    rows = 0
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            fields = FIELDS + (["production_profile", "profile_signature", "character_id", "voice_id"] if data.get("production_profile") else [])
            writer = csv.DictWriter(stream, fieldnames=fields, quoting=csv.QUOTE_ALL,
                                    lineterminator="\n")
            writer.writeheader()
            for row in storyboard_rows(data):
                writer.writerow(row)
                rows += 1
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return {"path": str(path), "rows": rows, "shots": len(data["shots"])}
