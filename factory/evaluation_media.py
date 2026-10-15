"""Deterministic media, subtitle and manifest checks without semantic claims."""
from __future__ import annotations

import re
import unicodedata

from .evaluation_io import check_receipt, finite, read_json, row
from .quality import inspect_video


def subtitle_text(text):
    return "".join(char for char in unicodedata.normalize("NFKC", text) if not char.isspace())


def parse_srt(text, duration):
    if not isinstance(text, str) or len(text) > 1000000:
        raise ValueError("SRT input exceeds evaluation bounds")
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()) if text.strip() else []
    entries, previous = [], 0.0
    def seconds(value):
        h, m, s, ms = (int(item) for item in re.split(r"[:,]", value))
        if m > 59 or s > 59:
            raise ValueError("Invalid subtitle timestamp")
        return h * 3600 + m * 60 + s + ms / 1000
    for index, block in enumerate(blocks, 1):
        parts = block.strip().splitlines()
        match = re.fullmatch(r"(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})", parts[1] if len(parts) > 1 else "")
        if len(parts) < 3 or parts[0] != str(index) or not match:
            raise ValueError("Malformed SRT entry")
        start, end = seconds(match[1]), seconds(match[2])
        if not 0 <= start < end <= duration + .001 or start < previous - .001:
            raise ValueError("Subtitle timing overlaps or falls outside the film")
        text = "\n".join(parts[2:]).strip()
        if not text:
            raise ValueError("Empty subtitle text")
        entries.append({"start": start, "end": end, "text": text})
        previous = end
    return entries


def subtitle_contract(entries, shots, cues=None):
    """Bind emitted cue splits to ordered dialogue; never infer missing text."""
    expected, clock = [], 0.0
    for shot in shots:
        expected.extend((line, shot["id"], clock, clock + shot["seconds"]) for line in shot.get("dialogue", []))
        clock += shot["seconds"]
    if cues is None:  # Historical evaluation files without emitted cue metadata.
        return len(entries) == len(expected) and all(
            subtitle_text(item["text"]) == subtitle_text(str(line.get("text", ""))) and left - .05 <= item["start"] < item["end"] <= right + .05
            for item, (line, _, left, right) in zip(entries, expected))
    if not isinstance(cues, list) or len(cues) > 10000 or len(cues) != len(entries):
        return False
    lookup = {line.get("id"): (line, sid, left, right) for line, sid, left, right in expected}
    if len(lookup) != len(expected):
        return False
    seen, text = [], {key: "" for key in lookup}
    for entry, cue in zip(entries, cues):
        if not isinstance(cue, dict) or not isinstance(cue.get("line"), str) or cue["line"] not in lookup or not isinstance(cue.get("text"), str):
            return False
        line, sid, left, right = lookup[cue["line"]]
        start, end = finite(cue.get("start")), finite(cue.get("end"))
        if (start is None or end is None or cue.get("shot") != sid or not left - .002 <= start < end <= right + .002 or
            abs(entry["start"] - start) > .002 or abs(entry["end"] - end) > .002 or
            subtitle_text(entry["text"]) != subtitle_text(cue["text"])):
            return False
        if not seen or seen[-1] != cue["line"]:
            seen.append(cue["line"])
        text[cue["line"]] += cue["text"]
    return seen == [line.get("id") for line, *_ in expected] and all(
        subtitle_text(text[key]) == subtitle_text(str(value[0].get("text", ""))) for key, value in lookup.items())


def deterministic(project, root):
    shots = project.get("shots", [])
    script = (project.get("episode") or project.get("script") or {}).get("shots", [])
    if not isinstance(shots, list) or not 1 <= len(shots) <= 80:
        return [row("delivery", "planned_shots", "fail", evidence=["No bounded shot sequence exists"])], {}
    checks, media = [], {}
    checks.append(row("delivery", "planned_shots", "pass" if script and sorted(s["id"] for s in shots) == sorted(s["id"] for s in script) and len({s['id'] for s in shots}) == len(shots) else "fail"))
    expected_order = (project.get("edit_plan") or {}).get("shot_order") or [shot["id"] for shot in script]
    checks.append(row("delivery", "approved_shot_order", "pass" if [shot["id"] for shot in shots] == expected_order else "fail",
                      evidence=[{"expected": expected_order, "actual": [shot["id"] for shot in shots]}]))
    total = sum(float(shot.get("seconds", 0)) for shot in shots)
    if not 0 < total <= 180:
        raise ValueError("Evaluation supports films up to 180 seconds")
    for shot in shots:
        sid = shot["id"]
        receipt = shot.get("video") or {}
        try:
            path, digest = check_receipt(root, receipt)
            quality = inspect_video(path, shot["seconds"], fps=24)
            media[sid] = {"path": receipt["path"], "sha256": digest, "seconds": shot["seconds"]}
            checks.append(row("delivery", "shot_media", quality["status"], shot_id=sid,
                              evidence=[{"path": receipt["path"], "sha256": digest}], metrics=quality))
        except (ValueError, OSError, KeyError) as exc:
            checks.append(row("delivery", "shot_media", "fail", shot_id=sid,
                              evidence=[{"path": receipt.get("path"), "sha256": receipt.get("sha256"), "error": str(exc)}]))
        measurement = shot.get("audio_timing") or shot.get("dialogue_measurement")
        if shot.get("dialogue"):
            checks.append(row("dialogue", "measured_voice_fit", "pass" if isinstance(measurement, dict) and measurement.get("fits") is True else "not_run" if measurement is None else "fail",
                              shot_id=sid, metrics=measurement or {}, source="recorded_audio_measurement"))
    delivery = project.get("delivery") or {}
    artifacts = {value.get("path", "").split("/")[-1]: value for value in delivery.get("artifacts", []) if isinstance(value, dict)}
    required = {"episode.mp4", "episode_clean.mp4", "subtitles.zh.srt", "timeline.json", "audio_edit.json"}
    checks.append(row("delivery", "required_exports", "pass" if required <= set(artifacts) else "fail", evidence=[{"missing": sorted(required - set(artifacts))}]))
    for name, receipt in artifacts.items():
        try:
            path, digest = check_receipt(root, receipt)
            if name in {"episode.mp4", "episode_clean.mp4"}:
                dimensions = delivery.get("resolution") or (delivery.get("width"), delivery.get("height"))
                dimensions = dimensions if all(isinstance(v, int) and v > 0 for v in dimensions) else None
                quality = inspect_video(path, total, dimensions, require_audio=True)
                media[name] = {"path": receipt["path"], "sha256": digest, "seconds": total}
                checks.append(row("delivery", name, quality["status"], evidence=[{"path": receipt["path"], "sha256": digest}], metrics=quality))
            else:
                checks.append(row("delivery", "artifact_checksum", "pass", evidence=[{"path": receipt["path"], "sha256": digest}]))
            if name == "timeline.json":
                timeline = read_json(path)
                entries = timeline.get("shots", [])
                matches = (len(entries) == len(shots) and
                    all(entry.get("shot") == shot["id"] and abs(float(entry.get("seconds", -1)) - shot["seconds"]) <= 1/24
                        for entry, shot in zip(entries, shots)) and
                    abs(float(timeline.get("duration_seconds", -1)) - total) <= 1/24)
                checks.append(row("delivery", "timeline_plan_matches_media", "pass" if matches else "fail",
                                  evidence=[{"path":receipt["path"], "sha256":digest}], metrics={"entries":len(entries)}))
            if name == "subtitles.zh.srt":
                entries = parse_srt(path.read_text(encoding="utf-8-sig"), total)
                expected = [str(line.get("text", "")) for shot in shots for line in shot.get("dialogue", [])]
                cues = None
                if "timeline.json" in artifacts:
                    timeline_path, _ = check_receipt(root, artifacts["timeline.json"])
                    cues = read_json(timeline_path).get("subtitles")
                matches = subtitle_contract(entries, shots, cues)
                checks.append(row("subtitles", "srt_content_and_bounds", "pass" if matches else "fail",
                                  metrics={"entries": len(entries), "expected": len(expected)}, evidence=entries))
        except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
            checks.append(row("subtitles" if name == "subtitles.zh.srt" else "delivery", "artifact_integrity", "fail",
                              evidence=[{"artifact": name, "path": receipt.get("path"), "sha256": receipt.get("sha256"), "error": str(exc)}]))
    if "subtitles.zh.srt" not in artifacts:
        checks.append(row("subtitles", "srt_content_and_bounds", "fail", evidence=["Subtitle export missing"]))
    checks.append(row("subtitles", "readability_and_occlusion", "not_run", source="human"))
    return checks, media
