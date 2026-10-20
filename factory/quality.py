"""Measured media checks and explicit separation of technical and semantic QA."""
from __future__ import annotations

import math
from pathlib import Path

import av
import numpy as np
from PIL import Image


def issue(code, detail, severity="error"):
    return {"code": code, "detail": str(detail), "severity": severity}


def _frame_metrics(image):
    gray = np.asarray(image.convert("L").resize((180, 320)), dtype=np.float32)
    lap = (-4 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1] +
           gray[1:-1, :-2] + gray[1:-1, 2:])
    return {"mean_luma": float(gray.mean()), "luma_std": float(gray.std()),
            "laplacian_variance": float(lap.var()), "finite": bool(np.isfinite(gray).all())}


def inspect_image(path, expected_size=None):
    issues = []
    try:
        with Image.open(path) as image:
            image.load()
            size = image.size
            values = _frame_metrics(image)
        if min(size) < 64:
            issues.append(issue("resolution", "Image is too small for the production profile"))
        if expected_size and tuple(expected_size) != tuple(size):
            issues.append(issue("resolution", f"Expected {expected_size}, got {size}"))
        if not values["finite"]:
            issues.append(issue("nonfinite", "Image contains invalid samples"))
        if values["mean_luma"] < 2 and values["luma_std"] < 1:
            issues.append(issue("black_frame", "Image is effectively black"))
        elif values["luma_std"] < .65:
            issues.append(issue("blank_frame", "Image is effectively uniform"))
        elif values["laplacian_variance"] < 8:
            issues.append(issue("possible_blur", "Low spatial detail; requires visual review", "warning"))
        return {"status": "fail" if any(i["severity"] == "error" for i in issues) else "pass",
                "width": size[0], "height": size[1], "metrics": values, "issues": issues,
                "scope": "decode_dimensions_luminance_spatial_detail",
                "identity_checked": False, "dramatic_quality_checked": False}
    except Exception as exc:
        return {"status": "fail", "issues": [issue("decode", exc)], "scope": "technical_image"}


def inspect_video(path, expected_seconds=None, expected_size=None, fps=24, require_audio=False):
    issues, frames, sampled, changes = [], 0, [], []
    audio_samples, audio_rate, audio_channels, audio_peak = 0, 0, 0, 0.0
    audio_finite, previous, vstream = True, None, None
    try:
        with av.open(str(path)) as container:
            videos = container.streams.video
            if not videos:
                return {"status": "fail", "issues": [issue("video_missing", "No video stream")]}
            vstream = videos[0]
            width, height = vstream.width, vstream.height
            actual_fps = float(vstream.average_rate or 0)
            duration = float(container.duration / av.time_base) if container.duration else 0
            for frame in container.decode(vstream):
                frames += 1
                if frames == 1 or frames % max(1, round(actual_fps)) == 0:
                    image = frame.to_image()
                    values = _frame_metrics(image)
                    sampled.append(values)
                    gray = np.asarray(image.convert("L").resize((90, 160)), dtype=np.float32)
                    if previous is not None:
                        changes.append(float(np.mean(np.abs(gray - previous))))
                    previous = gray
        with av.open(str(path)) as container:
            if container.streams.audio:
                audio_stream = container.streams.audio[0]
                audio_rate = audio_stream.codec_context.sample_rate
                audio_channels = audio_stream.codec_context.channels
                for frame in container.decode(audio_stream):
                    audio_samples += frame.samples
                    values = frame.to_ndarray()
                    audio_finite = audio_finite and bool(np.isfinite(values).all())
                    scale = np.iinfo(values.dtype).max if np.issubdtype(values.dtype, np.integer) else 1.0
                    if values.size:
                        audio_peak = max(audio_peak, float(np.abs(values.astype(np.float64)).max()) / scale)
        if frames < 2:
            issues.append(issue("decode", "Video contains fewer than two decodable frames"))
        if expected_size and tuple(expected_size) != (width, height):
            issues.append(issue("resolution", f"Expected {expected_size}, got {(width, height)}"))
        if abs(actual_fps - fps) > .02:
            issues.append(issue("fps", f"Expected {fps} fps, got {actual_fps}"))
        if expected_seconds is not None:
            expected_frames = round(float(expected_seconds) * fps)
            if abs(frames - expected_frames) > 1:
                issues.append(issue("duration", f"Expected {expected_frames} frames, decoded {frames}"))
            if abs(duration - float(expected_seconds)) > .15:
                issues.append(issue("duration", f"Expected {expected_seconds}s, got {duration:.3f}s"))
        if any(not s["finite"] for s in sampled):
            issues.append(issue("nonfinite", "Nonfinite decoded video samples"))
        black_fraction = sum(s["mean_luma"] < 2 and s["luma_std"] < 1 for s in sampled) / max(1, len(sampled))
        if black_fraction > .5:
            issues.append(issue("black_frame", "More than half the sampled frames are effectively black"))
        if require_audio:
            if not audio_samples or audio_rate < 16000:
                issues.append(issue("audio_missing", "No usable audio track"))
            if not audio_finite:
                issues.append(issue("audio_nonfinite", "Invalid decoded audio samples"))
            if audio_peak < .0001:
                issues.append(issue("audio_silent", "Delivered soundtrack is effectively silent"))
            if expected_seconds and abs(audio_samples / max(1, audio_rate) - expected_seconds) > .15:
                issues.append(issue("audio_duration", "Soundtrack and timeline duration differ"))
        return {"status": "fail" if any(i["severity"] == "error" for i in issues) else "pass",
                "width": width, "height": height, "fps": actual_fps,
                "duration_seconds": duration, "decoded_frames": frames,
                "audio_samples": audio_samples, "audio_sample_rate": audio_rate,
                "audio_channels": audio_channels, "audio_peak": audio_peak,
                "audio_finite": audio_finite, "black_sample_fraction": black_fraction,
                "sampled_frame_change": float(np.mean(changes)) if changes else 0,
                "issues": issues, "scope": "full_decode_timebase_dimensions_audio_and_sampled_luminance",
                "identity_checked": False, "dramatic_quality_checked": False}
    except Exception as exc:
        return {"status": "fail", "issues": [issue("decode", exc)], "scope": "technical_video"}


def is_transient_transport(exc):
    """Program-owned classification; model response fields cannot grant retries."""
    import urllib.error
    from .providers import ProviderError
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {429, 502, 503, 504}
    if isinstance(exc, urllib.error.URLError):
        return isinstance(exc.reason, (TimeoutError, ConnectionError))
    if isinstance(exc, ProviderError):
        return exc.code in {'transport_failed', 'http_429', 'http_502', 'http_503', 'http_504'}
    return isinstance(exc, (TimeoutError, ConnectionError))


def semantic_review(evaluator, project, shot, path, references):
    if evaluator is None:
        return {"status": "unavailable", "score": None, "issues": [],
                "detail": "A semantic evaluator was not configured; technical checks do not certify identity or drama."}
    try:
        raw = evaluator(project, shot, str(path), [str(p) for p in references])
        if not isinstance(raw, dict):
            raise ValueError("Semantic evaluator did not return a structured assessment")
        raw = dict(raw)
        raw.pop('retryable_transport', None)  # Only caught transport exceptions grant retry eligibility.
        status = raw.get("status")
        if status not in ("pass", "fail", "unavailable"):
            raw["status"] = "unavailable"
            raw["detail"] = "Evaluator omitted an explicit supported verdict"
        normalized = []
        for value in raw.get("issues", []):
            normalized.append(value if isinstance(value, dict) else issue("semantic", value))
        raw["issues"] = normalized
        score = raw.get("score")
        raw["score"] = float(score) if isinstance(score, (int, float)) and math.isfinite(score) else None
        raw["scope"] = "vlm_sampled_review_not_vbench_or_human_certification"
        return raw
    except Exception as exc:
        transient = is_transient_transport(exc)
        return {"status": "unavailable", "score": None, "issues": [],
                "retryable_transport": transient,
                "detail": f"Semantic evaluator unavailable: {type(exc).__name__}: {str(exc)[:240]}"}


def combine(technical, semantic=None, semantic_required=False):
    semantic = semantic or {"status": "not_run", "score": None, "issues": []}
    failure = technical.get("status") == "fail" or semantic.get("status") == "fail"
    return {"status": "fail" if failure else ("pass" if semantic.get("status") == "pass"
                                             else "technical_pass_semantic_pending"),
            "technical": technical, "semantic": semantic,
            "issues": list(technical.get("issues", [])) + list(semantic.get("issues", [])),
            "technical_passed": technical.get("status") == "pass",
            "semantic_passed": semantic.get("status") == "pass",
            "semantic_required": bool(semantic_required),
            "eligible_for_selection": technical.get("status") == "pass" and not failure and
                (not semantic_required or semantic.get("status") == "pass")}


def repair_plan(assessment, stage, attempt):
    """Choose a specific bounded action from measured/evaluated failure reasons."""
    text = " ".join(str(x.get("code", "")) + " " + str(x.get("detail", ""))
                    for x in assessment.get("issues", []) if isinstance(x, dict)).lower()
    if any(k in text for k in ("identity", "face", "character", "身份", "人脸", "角色", "服装", "clothing")):
        return {"action": "strengthen_identity_and_regenerate_keyframe", "seed_delta": 1009 * attempt,
                "prompt_suffix": "Preserve the exact face, hairstyle, age and clothing of each supplied character reference. Keep one clear subject and simple composition.",
                "regenerate_keyframe": stage == "video"}
    if any(k in text for k in ("action", "motion", "动作", "行为", "timing", "提前")):
        return {"action": "simplify_action_and_camera", "seed_delta": 1013 * attempt,
                "prompt_suffix": "One simple readable action only, gentle camera motion, stable background and coherent anatomy. Do not introduce new people or events.",
                "simplify_motion": True}
    if any(k in text for k in ("black", "blank", "暗", "全黑", "blur", "模糊", "composition", "构图")):
        return {"action": "regenerate_clearer_keyframe", "seed_delta": 1019 * attempt,
                "prompt_suffix": "Readable soft lighting on the subject, clear facial features, simple unobstructed composition and sharp subject detail.",
                "regenerate_keyframe": stage == "video"}
    if any(k in text for k in ("duration", "fps", "audio", "decode", "resolution", "时长", "音频")):
        return {"action": "repair_media_timing_or_encoding", "seed_delta": 0,
                "prompt_suffix": "", "reencode": True}
    return {"action": "new_seed_with_simpler_staging", "seed_delta": 1021 * attempt,
            "prompt_suffix": "Clear single-subject staging, restrained expression, simple composition; avoid complex simultaneous actions."}
