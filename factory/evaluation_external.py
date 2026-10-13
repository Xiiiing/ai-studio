"""Explicit offline ASR, imported VBench metrics and checksum-bound human review."""
from __future__ import annotations

import importlib.metadata
from pathlib import Path
import time

from .evaluation_io import check_receipt, finite, read_json, row


def asr_checks(project, root, model_dir=None, transcriber=None):
    from audio.check_dialogue import check_lines
    if model_dir is None and transcriber is None:
        return [row("dialogue", "blind_asr_cer", "not_run", source="asr")]
    provenance = {"source":"injected_test_transcriber"}
    if transcriber is None:
        model_dir = Path(model_dir).resolve(strict=True)
        if not model_dir.is_dir() or not (model_dir / "model.bin").is_file() or not (model_dir / "download_receipt.json").is_file():
            raise ValueError("ASR needs an already installed local model and download_receipt.json; no automatic download is allowed")
        from faster_whisper import WhisperModel
        transcriber = WhisperModel(str(model_dir), device="cpu", compute_type="int8", cpu_threads=4,
                                   num_workers=1, local_files_only=True)
        provenance = {"model_receipt":read_json(model_dir / "download_receipt.json"),
                      "engine_version":importlib.metadata.version("faster-whisper"), "device":"cpu", "compute_type":"int8"}
    lines, owners, checks = [], {}, []
    recorded = {line.get("id"):line for line in project.get("assets", {}).get("audio", {}).get("lines", [])}
    for shot in project.get("shots", []):
        for line in shot.get("dialogue", []):
            try:
                receipt = line.get("audio") or recorded.get(line["id"]) or {}
                if receipt.get("text", line["text"]) != line["text"] or receipt.get("role", line["role"]) != line["role"]:
                    raise ValueError("Recorded audio text or voice identity differs from the current dialogue")
                path, digest = check_receipt(root, receipt)
                ident = line["id"]
                if ident in owners:
                    raise ValueError("Duplicate dialogue ID")
                owners[ident] = shot["id"]
                lines.append({"id":ident,"text":line["text"],"wav":str(path),"quality":{"wav_sha256":digest}})
            except (ValueError, KeyError, OSError) as exc:
                checks.append(row("dialogue", "blind_asr_cer", "unavailable", shot_id=shot["id"], evidence=[str(exc)], source="asr"))
    start = time.monotonic()
    for result in check_lines(lines, transcriber, hotwords=None):
        # Homophones remain review-needed: they do not prove the written word.
        checks.append(row("dialogue", "blind_asr_cer", "pass" if result["status"] == "exact_match" else "review_needed",
                          shot_id=owners[result["id"]], metrics={**result,"model":provenance,
                          "elapsed_batch_seconds":round(time.monotonic()-start,3),"recognition_mode":"blind",
                          "limitations":"ASR errors, homophones, names and music can mislead; naturalness and acting require listening"}, source="asr"))
    return checks or [row("dialogue", "blind_asr_cer", "not_run", evidence=["No recorded dialogue clips"], source="asr")]


def vbench_checks(document, media):
    if document is None:
        return [row("action", "vbench_offline", "not_run", source="vbench")]
    if not isinstance(document, dict) or document.get("tool") != "VBench" or not document.get("tool_revision") or not document.get("protocol"):
        raise ValueError("VBench import requires tool, tool_revision and protocol provenance")
    entries = document.get("results")
    if not isinstance(entries, list) or not entries or len(entries) > 1000:
        raise ValueError("VBench import results must be bounded and nonempty")
    checks = []
    for value in entries:
        if not isinstance(value, dict) or value.get("shot_id") not in media or value.get("media_sha256") != media[value["shot_id"]]["sha256"]:
            raise ValueError("VBench metrics do not match the current media checksum")
        scores = value.get("scores")
        if not isinstance(scores, dict) or not scores or any(not isinstance(key,str) or len(key)>80 or finite(score) is None for key,score in scores.items()):
            raise ValueError("VBench scores must be named finite numbers, not a fabricated pass verdict")
        checks.append(row("action", "vbench_offline", "review_needed", shot_id=value["shot_id"], source="vbench",
            metrics={"raw_scores":scores,"tool_revision":document["tool_revision"],"protocol":document["protocol"],
                     "weight_provenance":document.get("weight_provenance"),"not_an_artistic_guarantee":True},
            evidence=[{"media_sha256":value["media_sha256"]}]))
    return checks


def human_checks(document, project, media_signature, dimensions):
    if document is None:
        return [row(key, "producer_review", "not_run", source="human") for key in dimensions]
    if document.get("schema") != "ai-studio.human-review.v1" or document.get("project_id") != project["id"] or document.get("production_revision") != project.get("production_revision",1) or document.get("media_signature") != media_signature:
        raise ValueError("Human review belongs to another project, production version or media checksum")
    if document.get("decision") not in {"approve", "revise"} or not isinstance(document.get("reviewer"),str) or not document["reviewer"].strip() or not document.get("reviewed_at"):
        raise ValueError("Human conclusion requires reviewer, timestamp and explicit decision")
    if set(document.get("dimensions", {})) != set(dimensions):
        raise ValueError("Human review must cover every required dimension")
    result = []
    for name, value in document["dimensions"].items():
        if value.get("status") not in {"pass","fail","uncertain"} or not isinstance(value.get("evidence"),list) or not value["evidence"]:
            raise ValueError("Every human dimension needs an explicit status and evidence")
        result.append(row(name, "producer_review", "review_needed" if value["status"] == "uncertain" else value["status"],
                          metrics={"reviewer":document["reviewer"],"reviewed_at":document["reviewed_at"],"decision":document["decision"],"notes":value.get("notes")},
                          evidence=value["evidence"], source="human"))
    return result
