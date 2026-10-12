#!/usr/bin/env python3
"""Generate resumable, fixed-character Chinese dialogue with local Qwen3-TTS."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = ROOT / "models/tts/Qwen3-TTS-12Hz-1.7B-CustomVoice"
EMOTIONS = {
    "neutral": "自然清楚地说话，电影对白的语气。",
    "fear": "压低声音，克制恐惧，吐字清楚。",
    "urgent": "急迫，紧张，吐字清楚，不尖叫。",
    "cold": "低沉，冷静，语速稍慢。",
    "sad": "悲伤但克制，声音自然。",
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def atomic_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def validate_wav(path):
    import numpy as np
    import soundfile as sf
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    if data.shape[1] != 1 or sr != 24000 or len(data) < sr * 0.1:
        raise ValueError(f"Invalid speech format: {path}: shape={data.shape}, sr={sr}")
    if not np.isfinite(data).all():
        raise ValueError(f"Non-finite speech samples: {path}")
    peak = float(np.max(np.abs(data)))
    rms = float(np.sqrt(np.mean(data.astype(np.float64) ** 2)))
    if peak < 0.001 or rms < 0.0001:
        raise ValueError(f"Empty or near-silent speech: {path}")
    clipping = float(np.mean(np.abs(data) >= 0.9999))
    if clipping > 0.001:
        raise ValueError(f"Clipped speech: {path}: fraction={clipping}")
    return {
        "sample_rate": sr, "channels": 1, "frames": len(data),
        "duration_seconds": round(len(data) / sr, 6),
        "peak": round(peak, 6), "rms_dbfs": round(20 * math.log10(rms), 3),
        "clipped_fraction": clipping, "wav_sha256": digest_file(path),
    }


def prepare_waveform(wave, sample_rate):
    import numpy as np
    w = np.asarray(wave, dtype=np.float32).reshape(-1)
    if sample_rate != 24000 or not len(w) or not np.isfinite(w).all():
        raise ValueError("The TTS model returned invalid audio.")
    # Trim only outer silence; keep short handles for the editor.
    frame = max(1, int(sample_rate * 0.01))
    pads = (-len(w)) % frame
    windows = np.pad(w, (0, pads)).reshape(-1, frame)
    rms = np.sqrt(np.mean(windows.astype(np.float64) ** 2, axis=1))
    active = np.flatnonzero(rms > max(0.0001, float(rms.max()) * 0.012))
    if not len(active):
        raise ValueError("The TTS model returned silence.")
    begin = max(0, int(active[0] * frame - 0.12 * sample_rate))
    end = min(len(w), int((active[-1] + 1) * frame + 0.20 * sample_rate))
    w = w[begin:end].copy()
    peak = float(np.max(np.abs(w)))
    gain = min(1.0, 0.95 / max(peak, 1e-9))
    w *= gain
    fade = min(int(sample_rate * 0.005), len(w) // 2)
    if fade:
        w[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
        w[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
    return w, {"outer_trim_start_seconds": begin / sample_rate,
               "outer_trim_end_seconds": (len(wave) - end) / sample_rate,
               "peak_limit_gain": gain, "fade_ms": 5}


def parse_input(path, voices):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(value, dict):
        value = value.get("lines", value.get("dialogue"))
    if not isinstance(value, list) or not value:
        raise ValueError("Input must be a nonempty JSON list, or an object with lines/dialogue.")
    result, ids = [], set()
    for i, line in enumerate(value):
        if not isinstance(line, dict):
            raise ValueError(f"Line {i} must be an object.")
        ident, role, text = line.get("id"), line.get("role"), line.get("text")
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", ident):
            raise ValueError(f"Line {i}: id must contain only letters, numbers, '-' or '_'.")
        if ident in ids:
            raise ValueError(f"Duplicate line id: {ident}")
        ids.add(ident)
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            raise ValueError(f"{ident}: text must contain 1–500 characters.")
        if role not in voices:
            raise ValueError(f"{ident}: role '{role}' has no fixed voice; add it to --voices.")
        voice = voices[role]
        if isinstance(voice, str):
            voice = {"speaker": voice}
        emotion = line.get("emotion", "") or ""
        if not isinstance(emotion, str):
            raise ValueError(f"{ident}: emotion must be a string.")
        instruction = " ".join(filter(None, [voice.get("instruct", ""), EMOTIONS.get(emotion, emotion)]))
        result.append({"id": ident, "role": role, "text": text.strip(),
                       "speaker": voice["speaker"], "language": voice.get("language", "Chinese"),
                       "emotion": emotion, "instruct": instruction})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", "--output_dir", required=True, type=Path)
    parser.add_argument("--voices", type=Path, default=ROOT / "audio/voices.json")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--dtype", choices=["float32", "float16"], default="float32")
    parser.add_argument("--seed", type=int, default=20261009)
    parser.add_argument("--max-new-tokens", type=int, default=768)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--threads", type=int, default=8)
    args = parser.parse_args()
    if args.max_new_tokens < 32 or args.max_new_tokens > 4096:
        raise ValueError("--max-new-tokens must be between 32 and 4096.")
    if not 0 < args.temperature <= 2:
        raise ValueError("--temperature must be in (0,2].")
    if args.device == "cpu" and args.dtype != "float32":
        raise ValueError("CPU inference requires --dtype float32.")
    voices = json.loads(args.voices.read_text(encoding="utf-8"))
    lines = parse_input(args.input, voices)
    model_dir = args.model.resolve()
    provenance_path = model_dir / "download_receipt.json"
    if not provenance_path.is_file():
        raise FileNotFoundError(f"Missing verified model download receipt: {provenance_path}")
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    for filename in ("config.json", "model.safetensors", "speech_tokenizer/model.safetensors"):
        if not (model_dir / filename).is_file():
            raise FileNotFoundError(model_dir / filename)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    settings = {"model_id": provenance["repo_id"], "model_revision": provenance["revision"],
                "model_files": provenance["files"], "dtype": args.dtype,
                "attention": "sdpa", "device": args.device, "do_sample": True,
                "temperature": args.temperature, "top_p": 0.9, "top_k": 50,
                "repetition_penalty": 1.05, "max_new_tokens": args.max_new_tokens,
                "implementation_sha256": digest_file(__file__)}
    receipts, model, load_seconds = [], None, 0.0
    run_started = time.perf_counter()
    for line in lines:
        seed = (args.seed + int(hashlib.sha256(line["id"].encode()).hexdigest()[:8], 16)) % (2**31)
        source = {"line": line, "settings": settings, "seed": seed}
        source_hash = hashlib.sha256(canonical(source).encode("utf-8")).hexdigest()
        wav_path = args.output_dir / (line["id"] + ".wav")
        meta_path = args.output_dir / (line["id"] + ".json")
        if not args.force and wav_path.is_file() and meta_path.is_file():
            old = json.loads(meta_path.read_text(encoding="utf-8"))
            if old.get("source_sha256") == source_hash:
                try:
                    qa = validate_wav(wav_path)
                    if old.get("quality", {}).get("wav_sha256") == qa["wav_sha256"]:
                        receipts.append(old)
                        print(json.dumps({"id": line["id"], "status": "cached", "wav": str(wav_path),
                                          "duration_seconds": qa["duration_seconds"]}), flush=True)
                        continue
                except (ValueError, RuntimeError):
                    pass
        if model is None:
            import torch
            from qwen_tts import Qwen3TTSModel
            torch.set_num_threads(max(1, args.threads))
            if args.device.startswith("cuda"):
                if not torch.cuda.is_available():
                    raise RuntimeError("CUDA is unavailable; use --device cpu if intended.")
                torch.cuda.set_device(args.device)
                torch.backends.cuda.matmul.allow_tf32 = False
                torch.backends.cudnn.allow_tf32 = False
                torch.cuda.reset_peak_memory_stats(args.device)
            t0 = time.perf_counter()
            model = Qwen3TTSModel.from_pretrained(
                str(model_dir), device_map=args.device,
                dtype=getattr(torch, args.dtype), attn_implementation="sdpa",
                local_files_only=True,
            )
            load_seconds = time.perf_counter() - t0
            print(json.dumps({"status": "model_loaded", "seconds": round(load_seconds, 3),
                              "dtype": args.dtype, "attention": "sdpa"}), flush=True)
        import numpy as np
        import soundfile as sf
        import torch
        torch.manual_seed(seed)
        np.random.seed(seed)
        if args.device.startswith("cuda"):
            torch.cuda.manual_seed_all(seed)
            torch.cuda.synchronize(args.device)
        started = time.perf_counter()
        with torch.inference_mode():
            wavs, sr = model.generate_custom_voice(
                text=line["text"], language=line["language"], speaker=line["speaker"],
                instruct=line["instruct"], non_streaming_mode=True,
                do_sample=True, temperature=args.temperature, top_p=0.9, top_k=50,
                repetition_penalty=1.05, max_new_tokens=args.max_new_tokens,
            )
        if args.device.startswith("cuda"):
            torch.cuda.synchronize(args.device)
        elapsed = time.perf_counter() - started
        w, processing = prepare_waveform(wavs[0], sr)
        if len(w) / sr > max(12, len(line["text"]) * 1.5):
            raise ValueError(f"{line['id']}: implausibly long speech; inspect text or generation.")
        tmp_wav = wav_path.with_suffix(".tmp.wav")
        sf.write(str(tmp_wav), w, sr, format="WAV", subtype="PCM_24")
        qa = validate_wav(tmp_wav)
        os.replace(tmp_wav, wav_path)
        receipt = {"id": line["id"], "role": line["role"], "text": line["text"],
                   "wav": str(wav_path.resolve()), "source_sha256": source_hash,
                   "source": source, "quality": qa, "postprocess": processing,
                   "generation_seconds": round(elapsed, 3),
                   "real_time_factor": round(elapsed / qa["duration_seconds"], 3),
                   "created_at": datetime.now(timezone.utc).isoformat(),
                   "peak_gpu_memory_gib": round(torch.cuda.max_memory_allocated(args.device) / 2**30, 3)
                         if args.device.startswith("cuda") else None}
        atomic_json(meta_path, receipt)
        receipts.append(receipt)
        print(json.dumps({"id": line["id"], "status": "generated", "wav": str(wav_path),
                          "duration_seconds": qa["duration_seconds"],
                          "generation_seconds": round(elapsed, 3)}, ensure_ascii=False), flush=True)
    manifest = {"schema": "drama-studio.tts-manifest.v1", "input": str(args.input.resolve()),
                "voices": str(args.voices.resolve()), "model": str(model_dir),
                "model_load_seconds": round(load_seconds, 3),
                "run_seconds": round(time.perf_counter() - run_started, 3),
                "total_duration_seconds": round(sum(x["quality"]["duration_seconds"] for x in receipts), 6),
                "lines": receipts}
    atomic_json(args.output_dir / "manifest.json", manifest)
    print(json.dumps({"status": "complete", "count": len(receipts),
                      "manifest": str((args.output_dir / "manifest.json").resolve()),
                      "total_duration_seconds": manifest["total_duration_seconds"]}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"TTS failed: {exc}", file=sys.stderr, flush=True)
        raise
