#!/usr/bin/env python3
"""Official SyncNet measurement with shared source face crops and offset controls.

This is a measurement, not a universal lip-quality acceptance test. It never
changes production selections and only uses physical GPU 5 under the studio lock.
"""
import argparse, contextlib, json, math, os, re, shutil, subprocess, sys, time, traceback
from pathlib import Path
from runtime import ROOT, LIPSYNC, MODELS, VENDOR, SOURCE_COMMIT, configure_environment, sha256, media_info

def write_json(path, data):
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)

def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--episode", default="the-last-stop")
    p.add_argument("--shot", required=True)
    p.add_argument("--take", required=True)
    p.add_argument("--source-take", required=True)
    p.add_argument("--shift-frames", type=int, default=6)
    p.add_argument("--tag", default="")
    a = p.parse_args()
    for value in [a.episode, a.shot, a.take, a.source_take]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
            p.error("Names must use safe project identifiers")
    if a.tag and not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", a.tag):
        p.error("Unsafe tag")
    if not 1 <= a.shift_frames <= 10:
        p.error("Control shift must be 1 to 10 frames at 25 fps")
    return a

def verify_evaluation_models():
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=VENDOR, text=True, timeout=15).strip()
    if source != SOURCE_COMMIT:
        raise RuntimeError("Pinned vendor commit mismatch")
    path = MODELS / "evaluation_manifest.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    if record["status"] != "verified" or len(record["files"]) != 2:
        raise RuntimeError("Evaluation weights are not verified")
    for item in record["files"]:
        file = MODELS / item["path"]
        if file.stat().st_size != item["bytes"] or sha256(file) != item["sha256"] or item["sha256"] != item["upstream_sha256"]:
            raise RuntimeError("Evaluation model hash mismatch: " + str(file))
    return {"manifest_path": str(path.relative_to(ROOT)), "manifest_sha256": sha256(path),
            "vendor_commit": source, "manifest": record}

def main():
    a = parse_args()
    ffmpeg = configure_environment()
    root = ROOT / "production" / a.episode
    shot = root / "renders" / a.shot
    episode = json.loads((root / "episode.json").read_text(encoding="utf-8"))
    shot_config = next(s for s in episode["shots"] if s["id"] == a.shot)
    duration = float(shot_config["seconds"])
    source_record = json.loads((shot / a.source_take / "result.json").read_text())
    candidate_record = json.loads((shot / a.take / "result.json").read_text())
    source = ROOT / source_record["path"]
    candidate = ROOT / candidate_record["path"]
    audio = root / "audio/lipsync" / (a.shot + ".wav")
    hashes = {"source": sha256(source), "candidate": sha256(candidate), "audio": sha256(audio)}
    if source_record["origin"] != "new_local_inference" or candidate_record["origin"] != "local_lipsync":
        raise RuntimeError("Expected formal locally generated source and lip-sync candidate")
    if hashes["source"] != source_record["sha256"] or hashes["candidate"] != candidate_record["sha256"]:
        raise RuntimeError("Production receipt hash mismatch")
    if candidate_record["input_video"]["sha256"] != hashes["source"] or candidate_record["input_audio"]["sha256"] != hashes["audio"]:
        raise RuntimeError("Candidate/source/audio chain mismatch")
    models = verify_evaluation_models()
    identity = sha256(audio)[:8] + "_" + hashes["candidate"][:8]
    name = a.shot + "_" + a.take + "_" + identity + ("_" + a.tag if a.tag else "")
    work = LIPSYNC / "evaluations" / name
    report_path = work / "evaluation.json"
    if work.exists():
        previous = json.loads(report_path.read_text()) if report_path.exists() else {}
        if previous.get("state") == "completed" and previous.get("input_sha256") == hashes:
            print(json.dumps({"reused": True, "report": str(report_path), "results": previous["results"]}))
            return
        raise RuntimeError("Existing unfinished evaluation retained; inspect it and choose a new --tag")
    # The upstream helper invokes shell commands, so only generated safe paths go to it.
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", str(work)):
        raise RuntimeError("Evaluation work path cannot be passed safely to official helper")
    work.mkdir(parents=True)
    started = time.monotonic()
    record = {
        "kind": "official_syncnet_shared_crop_control_comparison",
        "state": "preparing", "episode": a.episode, "shot": a.shot,
        "source": str(source), "candidate": str(candidate), "audio": str(audio),
        "input_sha256": hashes, "models": models, "physical_gpus": [5],
        "fps": 25, "audio_sample_rate": 16000, "audio_channels": 1,
        "timeline_seconds": duration, "search_frames_each_side": 15,
        "source_video_geometry": media_info(source), "candidate_video_geometry": media_info(candidate),
        "review_crop": {"width": int(episode["width"]), "height": int(episode["height"]), "position": "center"},
        "same_source_track_and_crop_for_all_variants": True,
        "control_audio_shifts_frames": [0, a.shift_frames, -a.shift_frames],
        "metric_definition": "confidence = median(mean embedding distances over tested offsets) - minimum distance; not a probability",
        "interpretation": "Use offset, minimum distance, confidence, original/candidate differences, and control response together. No universal quality cutoff is imposed.",
        "limitations": [
            "A six-second Chinese synthetic-voice shot does not establish language/domain calibration of SyncNet.",
            "Low motion, pauses, profile views, face crop errors, edge truncation in shifted controls, and brief duration can reduce score meaning.",
            "This embedding score does not certify phoneme-by-phoneme perceptual synchronization, facial identity, eye acting, or absence of flicker.",
            "Face crops are detected once on the source and reused; this controls crop variation but can miss a displaced candidate face.",
            "The main lip-sync inference still includes non-commercial research InsightFace weights. Evaluation model provenance does not clear commercial release."
        ],
        "selection_changed": False, "lip_sync_quality_verified": False,
        "perceptual_review_required": True,
    }
    write_json(report_path, record)
    log_path = work / "evaluation.log"
    def ff(args):
        with log_path.open("a") as log:
            subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *map(str, args)],
                           check=True, stdout=log, stderr=log, timeout=180)
    try:
        clean = work / "speech_16k.wav"
        ff(["-i", audio, "-vn", "-af", "apad,atrim=duration=" + str(duration),
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", clean])
        for label, path in [("source", source), ("candidate", candidate)]:
            folder = work / label
            for sub in ["frames", "video", "temp", "crop"]:
                (folder / sub).mkdir(parents=True)
            prepared = folder / "video/video.mp4"
            width, height = int(episode["width"]), int(episode["height"])
            ff(["-i", path, "-an", "-vf", f"fps=25,crop={width}:{height}",
                "-t", duration, "-frames:v", round(duration * 25), "-c:v", "libx264",
                "-crf", "16", "-preset", "fast", "-pix_fmt", "yuv420p", "-threads", "4", prepared])
            info = media_info(prepared)
            if info["frames"] != round(duration * 25) or abs(info["fps"] - 25) > 1e-6:
                raise RuntimeError("25 fps input normalization failed")
            ff(["-i", prepared, "-q:v", "2", "-threads", "4", folder / "frames/%06d.jpg"])
            shutil.copy2(clean, folder / "video/audio.wav")
        from studio.comfy import GpuLease, GpuStartupLock
        lease = None
        try:
            record["state"] = "loading_evaluation_models"
            write_json(report_path, record)
            with GpuStartupLock():
                lease = GpuLease([5])
                lease.__enter__()
                sys.path.insert(0, str(VENDOR))
                os.chdir(VENDOR)
                import torch
                from eval.syncnet import SyncNetEval
                from eval.syncnet_detect import SyncNetDetector
                with log_path.open("a") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                    syncnet = SyncNetEval(device="cuda")
                    syncnet.loadParameters(str(MODELS / "checkpoints/auxiliary/syncnet_v2.model"))
                    detector = SyncNetDetector(device="cuda", detect_results_dir=str(work / "unused_detector_default"))
            record["state"] = "evaluating"
            write_json(report_path, record)
            with log_path.open("a") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log), torch.inference_mode():
                faces = detector.detect_face(str(work / "source/frames"))
                tracks = detector.track_face(faces, min_track=50)
                if not tracks:
                    raise RuntimeError("No official S3FD face track long enough for evaluation")
                if len(tracks) != 1:
                    raise RuntimeError("Expected one actor/continuous face track; inspect ambiguous tracks")
                track = tracks[0]
                record["shared_face_track"] = {
                    "frame": track["frame"].tolist(), "bbox": track["bbox"].tolist(),
                    "frames": len(track["frame"]), "coverage_fraction": len(track["frame"]) / round(duration * 25)
                }
                write_json(work / "shared_face_track.json", record["shared_face_track"])
                for label in ["source", "candidate"]:
                    folder = work / label
                    detector.crop_video(track, str(folder / "crop/face"), str(folder / "frames"),
                                        25, str(folder / "temp"), str(folder / "video"))
                original_crop = work / "source/crop/face.mp4"
                candidate_crop = work / "candidate/crop/face.mp4"
                crop_audio = work / "candidate/temp/audio.wav"
                variants = {"source": original_crop, "candidate": candidate_crop}
                crop_seconds = len(track["frame"]) / 25
                shift_seconds = a.shift_frames / 25
                for label, shift in [("candidate_audio_delayed", shift_seconds), ("candidate_audio_advanced", -shift_seconds)]:
                    path = work / (label + ".mp4")
                    if shift > 0:
                        af = "adelay=" + str(round(shift * 1000)) + ":all=1,apad,atrim=duration=" + str(crop_seconds)
                    else:
                        af = "atrim=start=" + str(-shift) + ",asetpts=PTS-STARTPTS,apad,atrim=duration=" + str(crop_seconds)
                    ff(["-i", candidate_crop, "-i", crop_audio, "-map", "0:v:0", "-map", "1:a:0",
                        "-af", af, "-t", crop_seconds, "-c:v", "copy", "-c:a", "aac", "-ac", "1", "-ar", "16000", path])
                    variants[label] = path
                results = {}
                for label, path in variants.items():
                    info = media_info(path)
                    if abs(info["fps"] - 25) > 1e-6:
                        raise RuntimeError("Official metric input must be 25 fps")
                    offset, distance, confidence = syncnet.evaluate(
                        video_path=str(path), temp_dir=str(work / ("temporary_metric_" + label)), batch_size=20, vshift=15)
                    if not all(math.isfinite(float(v)) for v in [offset, distance, confidence]):
                        raise RuntimeError("Non-finite metric result")
                    results[label] = {"offset_frames": int(offset), "offset_seconds": float(offset) / 25,
                                      "minimum_distance": float(distance), "confidence": float(confidence),
                                      "input": str(path.relative_to(ROOT)), "input_sha256": sha256(path),
                                      "media": info}
                    print("MEASURED", label, json.dumps(results[label]), flush=True)
                    record["results"] = results
                    write_json(report_path, record)
            record["control_response"] = {}
            for label, imposed in [("candidate_audio_delayed", a.shift_frames), ("candidate_audio_advanced", -a.shift_frames)]:
                delta = results[label]["offset_frames"] - results["candidate"]["offset_frames"]
                record["control_response"][label] = {
                    "imposed_audio_shift_frames": imposed,
                    "expected_offset_delta_frames_from_index_convention": -imposed,
                    "observed_offset_delta_frames": delta,
                    "residual_frames": delta + imposed,
                }
            record["comparison"] = {
                "candidate_minus_source_confidence": results["candidate"]["confidence"] - results["source"]["confidence"],
                "candidate_minus_source_minimum_distance": results["candidate"]["minimum_distance"] - results["source"]["minimum_distance"],
                "source_offset_frames": results["source"]["offset_frames"],
                "candidate_offset_frames": results["candidate"]["offset_frames"],
            }
        finally:
            if lease is not None:
                lease.__exit__(None, None, None)
        record.update(state="completed", measurement_execution_passed=True, elapsed_seconds=round(time.monotonic()-started, 3))
        write_json(report_path, record)
        lines = ["# SyncNet 对照测量", "", "此报告不设置通用合格线，也不代替实际观感审看。", "",
                 "| 输入 | 偏移（25 fps 帧） | 最小距离 | confidence |", "| --- | ---: | ---: | ---: |"]
        for label, result in results.items():
            lines.append(f"| {label} | {result['offset_frames']} | {result['minimum_distance']:.4f} | {result['confidence']:.4f} |")
        lines += ["", "所有输入使用同一 720 × 1280 中心裁剪、25 fps、16 kHz 单声道对白，以及源视频检测得到的同一面部轨迹；标准面部裁剪为 224 × 224。",
                  "", "confidence 是偏移距离曲线的相对突出程度，不是概率。单个六秒中文镜头、合成语音和静音边界会限制解释；人为错位对照用于检查测量对延迟的响应。",
                  "", "原始视频、候选、音频、模型和测量输入的 SHA256 及完整数值见 evaluation.json。此脚本未改变选片指针。"]
        (work / "evaluation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(json.dumps({"report": str(report_path), "state": record["state"], "results": results, "control_response": record["control_response"]}, ensure_ascii=False), flush=True)
    except BaseException as exc:
        record.update(state="failed", error=str(exc), error_type=type(exc).__name__, elapsed_seconds=round(time.monotonic()-started,3))
        write_json(report_path, record)
        with log_path.open("a") as log:
            traceback.print_exc(file=log)
        raise

if __name__ == "__main__":
    main()
