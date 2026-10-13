#!/usr/bin/env python3
"""Blind local ASR check for generated dialogue (not a substitute for listening)."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time
import unicodedata
from functools import lru_cache

@lru_cache(maxsize=1)
def _converter():
    from opencc import OpenCC
    return OpenCC('t2s')
ROOT = Path(__file__).resolve().parents[1]

def normalized(s):
    return "".join(c for c in _converter().convert(unicodedata.normalize("NFKC", s)).lower()
                   if not c.isspace() and not unicodedata.category(c).startswith("P"))

def distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        row = [i]
        for j, cb in enumerate(b, 1):
            row.append(min(prev[j] + 1, row[j-1] + 1, prev[j-1] + (ca != cb)))
        prev = row
    return prev[-1]

def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def compare_transcript(expected, transcript):
    """Same normalization/CER contract for CLI, evaluation and offline tests."""
    from pypinyin import lazy_pinyin, Style
    if not isinstance(expected, str) or not isinstance(transcript, str) or max(len(expected), len(transcript)) > 4000:
        raise ValueError("Dialogue comparison requires bounded text")
    expected_n, observed_n = normalized(expected), normalized(transcript)
    if not expected_n:
        raise ValueError("Expected dialogue must contain non-punctuation characters")
    errors = distance(expected_n, observed_n)
    expected_p = lazy_pinyin(expected_n, style=Style.TONE3, neutral_tone_with_five=True)
    observed_p = lazy_pinyin(observed_n, style=Style.TONE3, neutral_tone_with_five=True)
    phonetic_errors = distance(expected_p, observed_p)
    return {"expected_text": expected, "transcript": transcript,
            "normalized_expected": expected_n, "normalized_transcript": observed_n,
            "character_error_rate": round(errors / len(expected_n), 4), "edit_distance": errors,
            "expected_pinyin": expected_p, "transcript_pinyin": observed_p,
            "phonetic_edit_distance": phonetic_errors,
            "phonetic_error_rate": round(phonetic_errors / max(1, len(expected_p)), 4),
            "status": "exact_match" if errors == 0 else "homophone_match" if phonetic_errors == 0 else "review_needed"}


def check_lines(lines, transcriber, *, hotwords=None):
    """Run blind ASR on checksum-bound inputs; transcriber is supplied explicitly."""
    if not isinstance(lines, list) or len(lines) > 240:
        raise ValueError("ASR input must contain at most 240 lines")
    rows = []
    for line in lines:
        if file_sha(line["wav"]) != line["quality"]["wav_sha256"]:
            raise ValueError("WAV no longer matches TTS receipt: " + str(line["id"]))
        segments, _ = transcriber.transcribe(line["wav"], language="zh", beam_size=5, temperature=0,
                                             condition_on_previous_text=False, vad_filter=False, hotwords=hotwords)
        pieces = [{"start": s.start, "end": s.end, "text": s.text,
                   "avg_logprob": s.avg_logprob, "no_speech_prob": s.no_speech_prob} for s in segments]
        transcript = "".join(piece["text"] for piece in pieces).strip()
        rows.append({"id": line["id"], **compare_transcript(line["text"], transcript), "segments": pieces,
                     "wav_sha256": line["quality"]["wav_sha256"]})
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--model", type=Path, default=ROOT / "models/tts/faster-whisper-small")
    p.add_argument("--output", type=Path)
    p.add_argument("--threads", type=int, default=8)
    p.add_argument("--hotwords", default=None, help="Optional domain terms; recorded separately from blind ASR.")
    a = p.parse_args()
    from faster_whisper import WhisperModel
    manifest = json.loads(a.manifest.read_text(encoding="utf-8"))
    model = WhisperModel(str(a.model.resolve()), device="cpu", compute_type="int8",
                         cpu_threads=max(1, a.threads), num_workers=1, local_files_only=True)
    t0 = time.perf_counter()
    rows = check_lines(manifest["lines"], model, hotwords=a.hotwords)
    for row in rows:
        print(json.dumps(row, ensure_ascii=False), flush=True)
    provenance = json.loads((a.model / "download_receipt.json").read_text(encoding="utf-8"))
    report = {"schema": "drama-studio.dialogue-asr-check.v1",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "manifest_sha256": file_sha(a.manifest), "asr_model": provenance,
              "engine_version": importlib.metadata.version("faster-whisper"),
              "device": "cpu", "compute_type": "int8", "hotwords": a.hotwords,
              "recognition_mode": "domain_terms" if a.hotwords else "blind",
              "seconds": round(time.perf_counter()-t0, 3),
              "all_exact_match": all(r["status"] == "exact_match" for r in rows),
              "all_phonetic_match": all(r["phonetic_edit_distance"] == 0 for r in rows),
              "normalization": "Unicode NFKC; traditional-to-simplified OpenCC; remove whitespace/punctuation. Homophones are reported separately using tone-marked pinyin.",
              "limitations": "ASR can itself make errors; textual matches do not prove acting quality or naturalness.",
              "lines": rows}
    target = a.output or (a.manifest.parent / "asr_report.json")
    tmp = target.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)
    print(json.dumps({"status": "complete", "output": str(target), "all_exact_match": report["all_exact_match"]}), flush=True)

if __name__ == "__main__":
    main()
