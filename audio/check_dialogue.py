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
from opencc import OpenCC
from pypinyin import lazy_pinyin, Style

CONVERTER = OpenCC('t2s')
ROOT = Path(__file__).resolve().parents[1]

def normalized(s):
    return "".join(c for c in CONVERTER.convert(unicodedata.normalize("NFKC", s)).lower()
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
    rows = []
    t0 = time.perf_counter()
    for line in manifest["lines"]:
        if file_sha(line["wav"]) != line["quality"]["wav_sha256"]:
            raise ValueError(f"WAV no longer matches TTS receipt: {line['id']}")
        segments, info = model.transcribe(
            line["wav"], language="zh", beam_size=5, temperature=0,
            condition_on_previous_text=False, vad_filter=False, hotwords=a.hotwords,
        )
        seg = [{"start": s.start, "end": s.end, "text": s.text,
                "avg_logprob": s.avg_logprob, "no_speech_prob": s.no_speech_prob} for s in segments]
        transcript = "".join(s["text"] for s in seg).strip()
        expected_n, observed_n = normalized(line["text"]), normalized(transcript)
        errors = distance(expected_n, observed_n)
        cer = errors / max(1, len(expected_n))
        expected_p = lazy_pinyin(expected_n, style=Style.TONE3, neutral_tone_with_five=True)
        observed_p = lazy_pinyin(observed_n, style=Style.TONE3, neutral_tone_with_five=True)
        phonetic_errors = distance(expected_p, observed_p)
        row = {"id": line["id"], "expected_text": line["text"], "transcript": transcript,
               "normalized_expected": expected_n, "normalized_transcript": observed_n,
               "character_error_rate": round(cer, 4), "edit_distance": errors,
               "expected_pinyin": expected_p, "transcript_pinyin": observed_p,
               "phonetic_edit_distance": phonetic_errors,
               "phonetic_error_rate": round(phonetic_errors / max(1, len(expected_p)), 4),
               "status": "exact_match" if errors == 0 else ("homophone_match" if phonetic_errors == 0 else "review_needed"),
               "segments": seg, "wav_sha256": line["quality"]["wav_sha256"]}
        rows.append(row)
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
