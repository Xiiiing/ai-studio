#!/usr/bin/env python3
"""Compatibility preflight only: animate an existing S01 still with the final speech."""
import argparse, json, subprocess, sys, time
from pathlib import Path
from runtime import ROOT, LIPSYNC, ENV_PYTHON, configure_environment, media_info, run, sha256


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=["prepare", "verify"])
    args = p.parse_args()
    ffmpeg = configure_environment()
    bench = LIPSYNC / "benchmarks/static_s01"
    bench.mkdir(parents=True, exist_ok=True)
    source = ROOT / "production/the-last-stop/assets/shots/S01/take_01/image.png"
    audio = ROOT / "production/the-last-stop/audio/lipsync/S01.wav"
    input_video = bench / "input.mp4"
    output_video = bench / "output.mp4"
    receipt_path = bench / "benchmark.json"
    if args.stage == "prepare":
        if input_video.exists():
            raise FileExistsError("Benchmark input already exists; inspect its receipt before reuse")
        run([ffmpeg, "-n", "-loglevel", "error", "-nostdin", "-loop", "1", "-framerate", "24", "-i", source,
             "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,format=yuv420p", "-t", "6", "-frames:v", "144",
             "-an", "-c:v", "libx264", "-crf", "16", "-preset", "fast", "-movflags", "+faststart", input_video], timeout=120)
        info = media_info(input_video)
        if (info["width"], info["height"], info["frames"], info["fps"]) != (720,1280,144,24.0):
            raise RuntimeError("Unexpected static preflight input properties: " + str(info))
        receipt = {"kind": "static_image_lipsync_compatibility_preflight", "production_eligible": False,
                   "source_is_generated_video": False, "formal_wan_video_tested": False,
                   "source_image": str(source), "source_image_sha256": sha256(source),
                   "input": str(input_video), "input_sha256": sha256(input_video), "input_media": info,
                   "audio": str(audio), "audio_sha256": sha256(audio), "physical_gpu": 5,
                   "state": "prepared", "prepared_at": time.time(),
                   "note": "Fixed S01 storyboard image repeated for six seconds. Compatibility and face-animation test only; never select as a formal Wan production take."}
    else:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if sha256(input_video) != receipt["input_sha256"] or sha256(audio) != receipt["audio_sha256"]:
            raise RuntimeError("Preflight inputs changed since preparation")
        info = media_info(output_video)
        if (info["width"], info["height"], info["frames"], info["fps"]) != (720,1280,144,24.0):
            raise RuntimeError("Unexpected preflight output properties: " + str(info))
        # Decode every video and audio packet; error-out on decoding failures.
        decoded = subprocess.run([str(ffmpeg), "-v", "error", "-xerror", "-nostdin", "-i", str(output_video), "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], capture_output=True, text=True, timeout=180)
        (bench / "decode.log").write_text(decoded.stdout + decoded.stderr, encoding="utf-8")
        if decoded.returncode:
            raise RuntimeError("Full decode failed: " + decoded.stderr[-2000:])
        import numpy as np
        from scipy.signal import correlate, correlation_lags
        def pcm(path):
            raw = subprocess.check_output([str(ffmpeg), "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], timeout=60)
            return np.frombuffer(raw, dtype="<f4").astype(np.float64)
        reference_audio, rendered_audio = pcm(audio), pcm(output_video)
        n = min(len(reference_audio), len(rendered_audio))
        reference_audio, rendered_audio_trimmed = reference_audio[:n], rendered_audio[:n]
        values = correlate(rendered_audio_trimmed, reference_audio, mode="full", method="fft")
        lags = correlation_lags(n, n, mode="full")
        nearby = np.flatnonzero(np.abs(lags) <= 1600)
        peak = nearby[np.argmax(values[nearby])]
        lag_seconds = float(lags[peak] / 16000)
        audio_correlation = float(values[peak] / np.sqrt(np.sum(reference_audio**2) * np.sum(rendered_audio_trimmed**2)))
        audio_check = {"decoded_output_seconds": len(rendered_audio)/16000, "lag_seconds": lag_seconds,
                       "correlation": audio_correlation, "within_30ms": abs(lag_seconds) <= 0.03}
        if not audio_check["within_30ms"] or audio_correlation < 0.95 or abs(len(rendered_audio)/16000-6) > 0.04:
            raise RuntimeError("Rendered speech alignment check failed: " + str(audio_check))
        snapshots = []
        for second in range(6):
            first = bench / ("input_%ds.png" % second)
            final = bench / ("output_%ds.png" % second)
            pair = bench / ("compare_%ds.png" % second)
            for video, frame in [(input_video, first), (output_video, final)]:
                run([ffmpeg, "-n", "-v", "error", "-nostdin", "-ss", str(second), "-i", video, "-frames:v", "1", frame], timeout=30)
            run([ffmpeg, "-n", "-v", "error", "-nostdin", "-i", first, "-i", final, "-filter_complex", "hstack=inputs=2", "-frames:v", "1", pair], timeout=30)
            snapshots.append({"seconds": second, "input": str(first), "output": str(final), "comparison_left_input_right_synced": str(pair), "comparison_sha256": sha256(pair)})
        from PIL import Image, ImageDraw
        # Compact review contact sheet, made from decoded evidence frames, not generated art.
        sheet = Image.new("RGB", (1080, 1530), "#141920")
        draw = ImageDraw.Draw(sheet)
        for i, snapshot in enumerate(snapshots):
            pair = Image.open(snapshot["comparison_left_input_right_synced"]).convert("RGB")
            pair.thumbnail((540,480))
            x, y = (i % 2) * 540, (i // 2) * 510
            draw.text((x+10,y+8), "%ds | static input / synced output" % i, fill="white")
            sheet.paste(pair, (x,y+30))
        sheet_path = bench / "contact_sheet.jpg"
        sheet.save(sheet_path, quality=94)
        receipt.update({"state": "inference_completed_decode_passed", "output": str(output_video),
                        "output_sha256": sha256(output_video), "output_media": info, "full_decode": "passed", "audio_alignment": audio_check,
                        "inference_tested": True, "lip_sync_quality_verified": False,
                        "snapshots": snapshots, "contact_sheet": str(sheet_path), "verified_at": time.time()})
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
