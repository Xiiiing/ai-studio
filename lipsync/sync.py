#!/usr/bin/env python3
"""Synchronize a local project video to speech; only physical GPU 5 is used."""
import os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PYTHON = ROOT / "envs/lipsync/bin/python"
if Path(sys.prefix).resolve() != (ROOT / "envs/lipsync").resolve():
    if not ENV_PYTHON.is_file():
        raise SystemExit("Missing isolated environment: " + str(ENV_PYTHON))
    os.execv(str(ENV_PYTHON), [str(ENV_PYTHON), str(Path(__file__).resolve())] + sys.argv[1:])

import argparse, json, math, time, uuid
from runtime import LIPSYNC, VENDOR, configure_environment, media_info, project_path, run, sha256, verify_models

def main():
    p = argparse.ArgumentParser(description="LatentSync 1.6 on physical GPU 5; preserve project frame rate and duration.")
    p.add_argument("--video", required=True, help="Input video inside drama_studio")
    p.add_argument("--audio", required=True, help="Input speech audio inside drama_studio")
    p.add_argument("--output", required=True, help="New output MP4 path inside drama_studio")
    p.add_argument("--audio-offset", type=float, default=0.0, help="Seconds before unpadded speech; keep 0 for the already aligned production WAV files")
    p.add_argument("--fps", type=float, default=24.0, help="Final output FPS, default 24")
    p.add_argument("--duration", type=float, default=None, help="Optional output duration; use 6 for production shots")
    p.add_argument("--steps", type=int, default=20, help="Denoising steps, 20 to 50")
    p.add_argument("--guidance", type=float, default=1.5, help="Guidance scale, 1.0 to 3.0")
    p.add_argument("--seed", type=int, default=1247)
    p.add_argument("--disable-deepcache", action="store_true")
    args = p.parse_args()
    if not 20 <= args.steps <= 50 or not 1.0 <= args.guidance <= 3.0:
        p.error("Use 20..50 steps and guidance 1.0..3.0")
    if not math.isfinite(args.audio_offset) or args.audio_offset < 0 or not 1 <= args.fps <= 60:
        p.error("Invalid audio offset or frame rate")
    video = project_path(args.video)
    audio = project_path(args.audio)
    output = project_path(args.output, must_exist=False)
    if output.suffix.lower() != ".mp4":
        p.error("--output must have .mp4 extension")
    if output.exists():
        raise FileExistsError("Output exists; select a new take: " + str(output))
    ffmpeg = configure_environment()
    verification = verify_models()
    info = media_info(video)
    import soundfile as sf
    sound = sf.info(str(audio))
    speech_duration = sound.frames / sound.samplerate
    requested_duration = info["duration"] if args.duration is None else args.duration
    if not math.isfinite(requested_duration) or requested_duration <= 0 or requested_duration > info["duration"] + 0.04:
        raise ValueError("Requested duration exceeds available video or is invalid")
    target_frames = round(requested_duration * args.fps)
    duration = target_frames / args.fps
    if speech_duration + args.audio_offset > duration + 0.04:
        raise ValueError("Speech plus offset exceeds video duration; retime the speech explicitly first")
    run_id = time.strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:10]
    work = LIPSYNC / "runs" / run_id
    work.mkdir(parents=True, exist_ok=False)
    input_25 = work / "input_25fps.mp4"
    padded_audio = work / "speech_16k.wav"
    synced_25 = work / "synced_25fps.mp4"
    final_video = work / "final_24fps.mp4"
    receipt = {"state": "preparing", "run_id": run_id, "video": str(video), "audio": str(audio),
               "output": str(output), "input_video": info, "speech_duration": speech_duration,
               "audio_offset": args.audio_offset, "fps": args.fps, "target_frames": target_frames,
               "duration": duration, "physical_gpu": 5, "steps": args.steps, "guidance": args.guidance,
               "seed": args.seed, "deepcache": not args.disable_deepcache, **verification}
    receipt_path = work / "receipt.json"
    def save():
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    save()
    started = time.monotonic()
    try:
        # Literal argv preprocessing shields official shell-based helpers from user path text.
        run([ffmpeg, "-y", "-loglevel", "error", "-nostdin", "-i", video, "-an", "-vf", "fps=25,format=yuv420p", "-t", str(duration), "-c:v", "libx264", "-crf", "16", "-preset", "fast", input_25], timeout=180)
        delay_ms = int(round(args.audio_offset * 1000))
        af = "adelay=%d:all=1,apad,atrim=duration=%.9f" % (delay_ms, duration)
        run([ffmpeg, "-y", "-loglevel", "error", "-nostdin", "-i", audio, "-af", af, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", padded_audio], timeout=90)
        from studio.comfy import GpuLease, GpuStartupLock
        from contextlib import ExitStack
        argv = [ENV_PYTHON, LIPSYNC / "infer.py", "--video", input_25, "--audio", padded_audio,
                "--output", synced_25, "--temp-dir", work / "render_tmp", "--steps", str(args.steps),
                "--guidance", str(args.guidance), "--seed", str(args.seed)]
        if args.disable_deepcache:
            argv.append("--disable-deepcache")
        with ExitStack() as leases:
            with GpuStartupLock():
                leases.enter_context(GpuLease([5]))
            receipt["state"] = "inference"
            save()
            with (work / "inference.log").open("w", encoding="utf-8") as log:
                run(argv, cwd=str(VENDOR), env=os.environ.copy(), stdout=log, stderr=-2, timeout=7200)
        if not synced_25.is_file():
            raise RuntimeError("Inference produced no video; inspect " + str(work / "inference.log"))
        run([ffmpeg, "-y", "-loglevel", "error", "-nostdin", "-i", synced_25, "-i", padded_audio,
             "-map", "0:v:0", "-map", "1:a:0", "-vf", "fps=" + str(args.fps),
             "-frames:v", str(target_frames), "-t", str(duration), "-c:v", "libx264",
             "-crf", "16", "-preset", "slow", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", final_video], timeout=180)
        result = media_info(final_video)
        if result["frames"] != target_frames or abs(result["fps"] - args.fps) > 0.001:
            raise RuntimeError("Output timebase/frame count differs from requested timeline")
        if (result["width"], result["height"]) != (info["width"], info["height"]):
            raise RuntimeError("Output resolution changed unexpectedly")
        # Export without overwriting an existing take.
        output.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        with output.open("xb") as out, final_video.open("rb") as source:
            shutil.copyfileobj(source, out, 8 * 1024 * 1024)
        receipt.update({"state": "completed", "elapsed_seconds": round(time.monotonic() - started, 3),
                        "result": result, "output_bytes": output.stat().st_size, "output_sha256": sha256(output),
                        "input_video_sha256": sha256(video), "input_audio_sha256": sha256(audio)})
        save()
        print(json.dumps(receipt, ensure_ascii=False), flush=True)
    except BaseException as exc:
        receipt.update({"state": "failed", "error": type(exc).__name__ + ": " + str(exc), "elapsed_seconds": round(time.monotonic() - started, 3)})
        save()
        raise

if __name__ == "__main__":
    main()
