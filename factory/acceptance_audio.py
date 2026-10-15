"""Reconcile measured dialogue, edit receipts and subtitle cues; never run a model."""
from __future__ import annotations

from pathlib import Path

from .acceptance_inputs import source_path
from .acceptance_checks import receipt_evidence
from .evaluation_io import check_receipt, finite, read_json, row
from .evaluation_media import parse_srt, subtitle_text


def decoded_audio(path):
    import av
    import numpy as np
    samples, rate, peak = 0, None, 0.0
    with av.open(str(path)) as container:
        if len(container.streams.audio) != 1:
            raise ValueError("Exactly one recorded audio stream is required")
        for frame in container.decode(audio=0):
            if rate is not None and rate != frame.sample_rate:
                raise ValueError("Audio sample rate changes inside a clip")
            rate = frame.sample_rate
            values = frame.to_ndarray()
            if not np.isfinite(values).all():
                raise ValueError("Non-finite audio samples")
            divisor = max(abs(np.iinfo(values.dtype).min), np.iinfo(values.dtype).max) if values.dtype.kind in "iu" else 1
            peak = max(peak, float(np.max(np.abs(values.astype("float64")), initial=0)) / divisor)
            samples += frame.samples
            if not 8000 <= rate <= 192000 or samples / rate > 180:
                raise ValueError("Audio exceeds the bounded acceptance duration/rate")
    if not rate or not samples or not .0001 <= peak <= 1.00001:
        raise ValueError("Recorded audio is empty, silent or invalid")
    return {"seconds": samples / rate, "sample_rate": rate, "peak": peak}


def contracts(project, root, slug):
    checks = []
    delivery = project.get("delivery") or {}
    artifacts = {Path(v.get("path", "")).name: v for v in delivery.get("artifacts", [])}
    def load(name):
        receipt = artifacts.get(name, {})
        path, _ = check_receipt(root, receipt)
        return read_json(path)
    try:
        timeline, edit = load("timeline.json"), load("audio_edit.json")
        if not isinstance(timeline, dict) or not isinstance(edit, dict):
            raise ValueError("Timeline and audio edit must be objects")
        total = sum(s["seconds"] for s in project.get("shots", []))
        # Existing AudioEditorMixin emits per-line durations, not a total.
        # Validate a total when explicitly recorded; never invent a missing one.
        duration_documents = [timeline] + ([edit] if "duration_seconds" in edit else [])
        duration_matches = timeline.get("fps") == 24 and all(
            finite(value.get("duration_seconds")) is not None and abs(value["duration_seconds"] - total) <= 1 / 48
            for value in duration_documents)
        checks.append(row("delivery", "timeline_audio_duration_metadata", "pass" if duration_matches else "fail",
                          evidence=[{"planned_seconds": total, "timeline_seconds": timeline.get("duration_seconds"), "audio_edit_seconds": edit.get("duration_seconds")}]))
        sources_match = timeline.get("shots") == delivery.get("sources") and len(timeline.get("shots", [])) == len(project.get("shots", []))
        checks.append(row("delivery", "manifest_timeline_sources", "pass" if sources_match else "fail",
                          evidence=[receipt_evidence(artifacts.get("timeline.json", {}))]))
        sources = timeline.get("shots", [])
        if not isinstance(sources, list) or len(sources) > 80:
            raise ValueError("Invalid timeline source sequence")
        for source, shot in zip(sources, project.get("shots", [])):
            valid = source.get("shot") == shot["id"] and all(
                (source.get(kind) or {}).get("sha256") == (shot.get(kind) or {}).get("sha256")
                for kind in ("video", "image"))
            segment = source.get("segment", {})
            source_path(root, segment.get("path"), slug=slug)
            check_receipt(root, segment)
            checks.append(row("delivery", "timeline_source_binding", "pass" if valid else "fail",
                              shot_id=shot["id"], evidence=[receipt_evidence(segment), receipt_evidence(shot.get("video") or {})]))
        soundtrack = timeline.get("audio", {})
        source_path(root, soundtrack.get("path"), slug=slug)
        path, _ = check_receipt(root, soundtrack)
        audio = decoded_audio(path)
        total = sum(s["seconds"] for s in project.get("shots", []))
        checks.append(row("delivery", "soundtrack_duration", "pass" if abs(audio["seconds"] - total) <= .02 else "fail",
                          evidence=[receipt_evidence(soundtrack)], metrics=audio))
        edits = edit.get("dialogue")
        cues = timeline.get("subtitles")
        if not isinstance(edits, list) or len(edits) > 3200 or not isinstance(cues, list) or len(cues) > 10000:
            raise ValueError("Missing bounded audio edits or subtitle timing receipts")
        expected = [(shot, line) for shot in project.get("shots", []) for line in shot.get("dialogue", [])]
        if [v.get("id") for v in edits] != [line.get("id") for _, line in expected]:
            raise ValueError("Audio edits do not identify every dialogue line in timeline order")
        audio_lines = (project.get("assets") or {}).get("audio", {}).get("lines", [])
        audio_map = {v.get("id"): v for v in audio_lines}
        if len(audio_map) != len(audio_lines):
            raise ValueError("Duplicate voice receipt IDs")
        offsets, clock = {}, 0.0
        for shot in project.get("shots", []):
            offsets[shot["id"]] = clock
            clock += shot["seconds"]
        for entry, (shot, line) in zip(edits, expected):
            evidence = [receipt_evidence(entry.get("source", {})), receipt_evidence(entry.get("processed", {}))]
            try:
                original = audio_map.get(line.get("id"), {})
                source, processed = entry["source"], entry["processed"]
                for receipt in (original, source, processed):
                    source_path(root, receipt.get("path"), slug=slug)
                    check_receipt(root, receipt)
                if original.get("sha256") != source["sha256"] or original.get("text") != line.get("text") or original.get("role") != line.get("role"):
                    raise ValueError("Voice receipt identity/text/hash differs from screenplay")
                measured = decoded_audio(Path(root) / source["path"])
                rendered = decoded_audio(Path(root) / processed["path"])
                timing = shot.get("audio_timing") or shot.get("dialogue_measurement") or {}
                proof = next((v for v in timing.get("lines", []) if v.get("id") == line["id"]), {})
                if (timing.get("fits") is not True or proof.get("sha256") != source["sha256"] or
                    finite(proof.get("duration_seconds")) is None or abs(proof["duration_seconds"] - measured["seconds"]) > .03):
                    raise ValueError("Measured dialogue evidence is missing or stale")
                start, seconds = finite(entry.get("start")), finite(entry.get("seconds"))
                left, right = offsets[shot["id"]], offsets[shot["id"]] + shot["seconds"]
                if (start is None or seconds is None or entry.get("shot") != shot["id"] or seconds <= 0 or
                    not left <= start < start + seconds <= right + .001 or abs(seconds - rendered["seconds"]) > .03):
                    raise ValueError("Processed dialogue is truncated or outside its shot")
                pieces = [c for c in cues if c.get("line") == line["id"]]
                if (not pieces or any(c.get("shot") != shot["id"] or finite(c.get("start")) is None or finite(c.get("end")) is None or
                                     not start - .002 <= c["start"] < c["end"] <= start + seconds + .002 for c in pieces) or
                    subtitle_text("".join(c.get("text", "") for c in pieces)) != subtitle_text(line["text"])):
                    raise ValueError("Subtitle segments do not cover the measured spoken line")
                checks.append(row("dialogue", "voice_edit_evidence", "pass", shot_id=shot["id"], evidence=evidence,
                                  metrics={"line_id": line["id"], "source_seconds": measured["seconds"], "processed_seconds": rendered["seconds"]}))
            except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
                checks.append(row("dialogue", "voice_edit_evidence", "fail", shot_id=shot["id"], evidence=evidence + [{"line_id": line.get("id"), "error": str(exc)}]))
        srt_path, _ = check_receipt(root, artifacts.get("subtitles.zh.srt", {}))
        entries = parse_srt(srt_path.read_text(encoding="utf-8-sig"), total)
        valid = len(entries) == len(cues) and all(
            subtitle_text(a["text"]) == subtitle_text(b.get("text", "")) and
            finite(b.get("start")) is not None and finite(b.get("end")) is not None and
            abs(a["start"] - b["start"]) <= .002 and abs(a["end"] - b["end"]) <= .002
            for a, b in zip(entries, cues))
        checks.append(row("subtitles", "measured_cues_match_srt", "pass" if valid else "fail",
                          evidence=[receipt_evidence(artifacts.get("subtitles.zh.srt", {}))]))
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        checks.append(row("dialogue", "audio_timeline_evidence", "fail", evidence=[str(exc)]))
    return checks
