"""Persistent MiniMax submit/poll/download lifecycle, without implicit resubmission."""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import time
from pathlib import Path

from studio.core import fingerprint, read_json, sha256, write_json
from .providers import ProviderError, redact_secrets
from .video_providers import normalize_video_profile
from .video_transport import download_video, video_request
from .remote_tasks import ProductionDeferred, MAX_POLL_FAILURES, phase_for


class VideoPending(RuntimeError):
    """An accepted or uncertain task must be resumed/reconciled, never resubmitted."""
    def __init__(self, message, code="video_pending", details=None):
        super().__init__(message)
        self.code, self.details = code, details or {}


class VideoFailed(RuntimeError):
    """The provider has positively reported a terminal generation failure."""


def video_identity(profile):
    return {key: copy.deepcopy(profile.get(key)) for key in
            ("profile", "provider", "base_url", "model", "revision", "duration",
             "resolution", "prompt_optimizer")}


def validate_video_plan(profile, shots):
    if profile.get("provider") != "minimax":
        return
    clean = normalize_video_profile({key: value for key, value in profile.items() if key != "profile"})
    if not clean["enabled"]:
        raise ValueError("The selected video profile is disabled")
    secret = os.environ.get(clean["api_key_env"])
    if not secret or any(char in secret for char in ("\r", "\n")):
        raise ValueError("MiniMax API key environment variable is missing or invalid")
    too_long = [str(shot.get("id", "?")) for shot in shots
                if float(shot.get("seconds", 0)) > clean["duration"]]
    if too_long:
        raise ValueError("Shots exceed the configured MiniMax duration (" + str(clean["duration"]) +
                         "s): " + ", ".join(too_long[:10]) + ". Choose a supported longer duration or shorten these shots.")
    if clean["resolution"] == "1080P" and clean["duration"] == 10:
        raise ValueError("This MiniMax adapter requires 6s at 1080P; use 768P for a 10s profile")


def _identifier(value, label):
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not value or len(value) > 128 or not all(c.isalnum() or c in "_-" for c in value):
        raise ProviderError("invalid_response", "Video service omitted a valid " + label)
    return value


def _first_frame(path, expected_sha256=None):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("The first-frame image must be a local image of at most 5 MiB")
    with path.open("rb") as handle:
        content = handle.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise ValueError("The first-frame image exceeds 5 MiB")
    if expected_sha256 and hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError("The first-frame image changed before authorization")
    from PIL import Image
    with Image.open(io.BytesIO(content)) as image:
        width, height = image.size
        if min(width, height) <= 300 or not .4 <= width / height <= 2.5:
            raise ValueError("MiniMax first frame needs a short side over 300px and an aspect ratio within 2:5–5:2")
        image.verify()
        mime = Image.MIME.get(image.format)
    if mime not in ("image/jpeg", "image/png", "image/webp"):
        raise ValueError("MiniMax first frame must be JPEG, PNG or WebP")
    return "data:" + mime + ";base64," + base64.b64encode(content).decode("ascii")


def generate_video(profile, prompt, first_frame, folder, *, source_signature, check=None,
                   timeout=None, event=None, before_submit=None, step=False):
    """Serialize one take across processes; a competing caller never submits."""
    import fcntl
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "remote_task.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise VideoPending("This video task is already being handled", "video_task_busy") from None
        return _generate_video(profile, prompt, first_frame, folder, source_signature=source_signature,
                               check=check, timeout=timeout, event=event, before_submit=before_submit, step=step)


def _generate_video(profile, prompt, first_frame, folder, *, source_signature, check=None,
                    timeout=None, event=None, before_submit=None, step=False):
    """Return a downloaded local source and public receipt; journals survive all pauses."""
    clean = normalize_video_profile({key: value for key, value in profile.items() if key != "profile"})
    if clean["provider"] != "minimax":
        raise ValueError("This lifecycle handles MiniMax video tasks")
    if not clean["enabled"]:
        raise ValueError("Video profile is disabled")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 2000:
        raise ValueError("MiniMax video prompt must contain 1–2000 characters")
    check = check or (lambda: None)
    event = event or (lambda value: None)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    journal_path = folder / "remote_task.json"
    frame_hash = sha256(first_frame)
    request_id = fingerprint({"profile": video_identity(profile), "prompt": prompt,
                              "first_frame_sha256": frame_hash})
    receipt = read_json(journal_path) if journal_path.exists() else None
    if receipt:
        if receipt.get("request_fingerprint") != request_id or receipt.get("source_signature") != source_signature:
            raise VideoPending("An earlier video task uses different inputs; reconcile it before changing the request",
                               "video_request_changed", {"task_id": receipt.get("task_id")})
        if receipt.get("status") == "failed":
            raise VideoFailed("MiniMax reported that the video generation failed")
        if receipt.get("status") == "rejected":
            raise VideoPending("The recorded video submission was rejected; review its configuration",
                               "video_submission_rejected", {"code": receipt.get("error_code")})
        if not receipt.get("task_id"):
            raise VideoPending("The previous video submission has an unknown outcome; no duplicate was submitted",
                               "video_submission_unknown")
        # The generation's original connection and identity remain authoritative across resume.
        clean = normalize_video_profile(receipt["connection"])
    def save(**updates):
        receipt.update(updates, updated_at=time.time())
        receipt["phase"] = phase_for(receipt)
        write_json(journal_path, redact_secrets(receipt, clean))
        directories = [folder, *folder.parents] if receipt.get("status") == "submitting" else [folder]
        for directory in directories:
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        event({key: copy.deepcopy(receipt.get(key)) for key in
               ("provider", "profile", "model", "task_id", "file_id", "status", "provider_status", "updated_at",
                "phase", "next_poll_at", "poll_failures")})
    def pending(message, code):
        raise VideoPending(message, code, {"task_id": receipt.get("task_id"),
                                          "file_id": receipt.get("file_id")})
    def defer(phase, delay, **updates):
        due = time.time() + max(0, min(300, delay))
        save(next_poll_at=due, **updates)
        raise ProductionDeferred(due, phase, {"task_id": receipt.get("task_id"),
                                             "file_id": receipt.get("file_id")})
    def backoff(phase, error):
        failures = int(receipt.get("poll_failures") or 0) + 1
        save(poll_failures=failures, error_code=error.code)
        if step and failures < MAX_POLL_FAILURES and error.code not in {"task_mismatch", "file_mismatch", "unknown_status", "invalid_response"}:
            defer(phase, clean["poll_interval_seconds"] * 2 ** failures)
    deadline = time.monotonic() + min(clean["max_wait_seconds"], float(timeout or clean["max_wait_seconds"]))
    def remaining():
        check()
        value = deadline - time.monotonic()
        if value <= 0:
            pending("Video is still pending; resume will query the same task", "video_wait_timeout")
        return min(clean["request_timeout_seconds"], value)
    if receipt is None:
        check()
        payload = {"model": clean["model"], "prompt": prompt, "duration": clean["duration"],
                   "resolution": clean["resolution"], "prompt_optimizer": clean["prompt_optimizer"],
                   "first_frame_image": _first_frame(first_frame, frame_hash)}
        if not callable(before_submit):
            raise VideoPending("A new paid task requires explicit project authorization", "cloud_authorization_required")
        # Reserve in the project ledger before the journal and the sole POST.
        # A crash in either gap fails closed; it never creates retry permission.
        authorization = before_submit({"request_fingerprint": request_id,
                                       "source_signature": source_signature})
        receipt = {"schema_version": 1, "provider": "minimax", "profile": profile.get("profile"),
                   "model": clean["model"], "connection": clean,
                   "source_signature": source_signature, "request_fingerprint": request_id,
                   "first_frame_sha256": frame_hash, "status": "submitting", "started_at": time.time(),
                   "authorization": authorization}
        write_json(folder / "request_summary.json", redact_secrets(
            {key: value for key, value in payload.items() if key != "first_frame_image"}, clean))
        # The intent is durable before the sole effectful POST.
        save()
        try:
            answer = video_request(clean, "/video_generation", payload=payload, timeout=remaining())
            task_id = _identifier(answer.get("task_id"), "task_id")
        except ProviderError as exc:
            known_rejection = exc.code.startswith("api_") or exc.code == "missing_auth"
            save(status="rejected" if known_rejection else "submission_unknown", error_code=exc.code)
            pending("Video submission could not be confirmed; no automatic resubmission will occur",
                    "video_submission_rejected" if known_rejection else "video_submission_unknown")
        save(task_id=task_id, status="submitted")
        if step:
            defer("remote_poll", clean["poll_interval_seconds"], poll_failures=0)
    raw = folder / "source.mp4"
    if receipt.get("download_sha256") and raw.exists():
        if sha256(raw) != receipt["download_sha256"]:
            pending("Downloaded video no longer matches its checksum", "video_cache_integrity")
        return raw, copy.deepcopy(receipt)
    if step and float(receipt.get("next_poll_at") or 0) > time.time():
        raise ProductionDeferred(receipt["next_poll_at"], phase_for(receipt),
                                 {"task_id": receipt["task_id"], "file_id": receipt.get("file_id")})
    while not receipt.get("file_id"):
        try:
            answer = video_request(clean, "/query/video_generation",
                                   params={"task_id": receipt["task_id"]}, timeout=remaining())
            if _identifier(answer.get("task_id"), "task_id") != receipt["task_id"]:
                raise ProviderError("task_mismatch", "Video response referred to another task")
            state = answer.get("status")
            if state not in ("Preparing", "Queueing", "Processing", "Success", "Fail"):
                raise ProviderError("unknown_status", "Video service returned an unknown task status")
            if state == "Success":
                file_id = _identifier(answer.get("file_id"), "file_id")
                save(status="generated", provider_status=state, file_id=file_id,
                     video_width=answer.get("video_width"), video_height=answer.get("video_height"))
                if step:
                    defer("remote_download", 0, poll_failures=0)
                break
            save(status="failed" if state == "Fail" else "running" if state == "Processing" else "queued",
                 provider_status=state, poll_failures=0)
            if state == "Fail":
                raise VideoFailed("MiniMax reported that the video generation failed")
        except ProviderError as exc:
            backoff("remote_poll", exc)
            pending("Video status is temporarily unavailable; the original task is retained",
                    "video_status_unavailable")
        if step:
            defer("remote_poll", clean["poll_interval_seconds"])
        wait_until = time.monotonic() + min(clean["poll_interval_seconds"], remaining())
        while time.monotonic() < wait_until:
            check()
            delay = wait_until - time.monotonic()
            if delay > 0:
                time.sleep(min(.5, delay))
    try:
        files = video_request(clean, "/files/retrieve", params={"file_id": receipt["file_id"]},
                              timeout=remaining())
        file = files.get("file")
        if not isinstance(file, dict) or _identifier(file.get("file_id"), "file_id") != receipt["file_id"]:
            raise ProviderError("file_mismatch", "Video download response referred to another file")
        download_video(clean, file.get("download_url"), raw, timeout=remaining())
    except ProviderError as exc:
        save(status="download_pending", error_code=exc.code)
        backoff("remote_download", exc)
        pending("Video is generated; its download will resume without a new generation",
                "video_download_pending")
    save(status="downloaded", download_sha256=sha256(raw), bytes=raw.stat().st_size,
         finished_at=time.time(), poll_failures=0, next_poll_at=None)
    return raw, copy.deepcopy(receipt)
