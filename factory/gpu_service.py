"""Wkp-only durable GPU inference jobs; no Studio workflow or project database.

The loopback listener must be reached through the authenticated mTLS gateway.
Running jobs become unresolved after a crash and are never automatically replayed.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import copy
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import threading
import time
from urllib.parse import urlsplit

from studio.core import ROOT, fingerprint, read_json, sha256, within, write_json

MAX_BODY = 32 * 1024 * 1024
MAX_INPUT = 20 * 1024 * 1024
ID = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\Z")
# The two existing, bounded Studio graph builders are the complete node surface.
NODES = {
    "UNETLoader": {"unet_name", "weight_dtype"}, "CLIPLoader": {"clip_name", "type", "device"},
    "VAELoader": {"vae_name"}, "CLIPTextEncode": {"clip", "text"},
    "EmptyFlux2LatentImage": {"width", "height", "batch_size"}, "BasicGuider": {"model", "conditioning"},
    "RandomNoise": {"noise_seed"}, "KSamplerSelect": {"sampler_name"},
    "Flux2Scheduler": {"steps", "width", "height"},
    "SamplerCustomAdvanced": {"noise", "guider", "sampler", "sigmas", "latent_image"},
    "VAEDecode": {"samples", "vae"}, "SaveImage": {"images", "filename_prefix"},
    "LoadImage": {"image"}, "VAEEncode": {"pixels", "vae"}, "ReferenceLatent": {"conditioning", "latent"},
    "Wan22ImageToVideoLatent": {"vae", "width", "height", "length", "batch_size", "start_image"},
    "ModelSamplingSD3": {"model", "shift"},
    "KSampler": {"model", "positive", "negative", "latent_image", "seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"},
    "WanTiledVAEDecode": {"samples", "vae", "tile_size", "overlap", "temporal_size", "temporal_overlap"},
    "CreateVideo": {"images", "fps"}, "SaveVideo": {"video", "filename_prefix", "format", "codec"},
    "LoraLoaderModelOnly": {"model", "lora_name", "strength_model"}, "ManualSigmas": {"sigmas"},
}
MODELS = {
    "unet_name": {"flux-2-klein-4b.safetensors", "wan2.2_ti2v_5B_fp16.safetensors"},
    "clip_name": {"qwen_3_4b.safetensors", "umt5_xxl_fp16.safetensors"},
    "vae_name": {"flux2-vae.safetensors", "wan2.2_vae.safetensors"},
    "lora_name": {"Wan2_2_5B_FastWanFullAttn_lora_rank_128_bf16.safetensors"},
}
OPTIONS = {"tts_dtype", "tts_seed", "tts_temperature", "tts_max_new_tokens"}
CONNECTIONS = {"clip", "model", "conditioning", "noise", "guider", "sampler", "sigmas",
               "latent_image", "samples", "vae", "images", "pixels", "latent", "start_image",
               "positive", "negative", "video"}


class Conflict(ValueError):
    pass


def number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("Numeric input outside bounded inference limits")
    return value


def validate_graph(graph, names, output_kind):
    if not isinstance(graph, dict) or not 1 <= len(graph) <= 80:
        raise ValueError("Graph must contain 1..80 known nodes")
    from studio.h3 import UNET
    if any(isinstance(n, dict) and (str(n.get("class_type", "")).startswith(("MiniMaxH3", "Pilot")) or
           isinstance(n.get("inputs"), dict) and n["inputs"].get("unet_name") == UNET) for n in graph.values()):
        validate_h3_graph(graph, names, output_kind)
        return
    output = []
    for ident, node in graph.items():
        if not isinstance(ident, str) or not re.fullmatch(r"[0-9]{1,4}", ident):
            raise ValueError("Invalid graph node id")
        if not isinstance(node, dict) or set(node) != {"class_type", "inputs"}:
            raise ValueError("Only API graph nodes are supported")
        kind, inputs = node["class_type"], node["inputs"]
        if kind not in NODES or not isinstance(inputs, dict) or set(inputs) != NODES[kind]:
            raise ValueError("Unknown node or node inputs")
        if kind in ("SaveImage", "SaveVideo"):
            output.append(kind)
        for key, value in inputs.items():
            connection = key in CONNECTIONS and not (kind == "ManualSigmas" and key == "sigmas")
            if isinstance(value, list):
                if not connection or len(value) != 2 or not isinstance(value[0], str) or value[0] not in graph or type(value[1]) is not int or not 0 <= value[1] <= 4:
                    raise ValueError("Invalid graph connection")
                continue
            if connection:
                raise ValueError("Graph connection is required")
            if key in MODELS:
                if value not in MODELS[key]:
                    raise ValueError("Unapproved model name")
            elif key == "image":
                if value not in names:
                    raise ValueError("LoadImage must reference a supplied input")
            elif key == "filename_prefix":
                if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_/-]{1,160}", value) or any(p in ("", ".", "..") for p in value.split("/")):
                    raise ValueError("Unsafe output prefix")
            elif key in ("width", "height"):
                number(value, 64, 1536)
                if type(value) is not int or value % 8:
                    raise ValueError("Geometry must be aligned to eight pixels")
            elif key == "batch_size":
                if value != 1 or type(value) is not int:
                    raise ValueError("Inference batches are limited to one")
            elif key == "length":
                number(value, 1, 241)
                if type(value) is not int:
                    raise ValueError("Frame count must be integral")
            elif key == "steps":
                number(value, 1, 50)
            elif key in ("seed", "noise_seed"):
                number(value, 0, 2**64 - 1)
            elif key == "text":
                if not isinstance(value, str) or len(value) > 10000:
                    raise ValueError("Prompt too large")
            elif key == "sigmas":
                if not isinstance(value, str) or len(value) > 1000:
                    raise ValueError("Invalid sigma schedule")
                values = value.split(",")
                if not 2 <= len(values) <= 51:
                    raise ValueError("Invalid sigma schedule")
                for item in values:
                    number(float(item), 0, 1)
            elif isinstance(value, str):
                enums = {"weight_dtype": {"default"}, "type": {"flux2", "wan"}, "device": {"default"},
                         "sampler_name": {"euler", "lcm", "uni_pc"}, "scheduler": {"simple"},
                         "format": {"mp4"}, "codec": {"h264"}}
                if key not in enums or value not in enums[key]:
                    raise ValueError("Unapproved node setting")
            else:
                number(value, 0, 2048)
                if key == "fps" and value != 24:
                    raise ValueError("Only 24 fps video is supported")
    if output != ["SaveImage" if output_kind == "image" else "SaveVideo"]:
        raise ValueError("Graph must have exactly one matching output")


def validate_h3_graph(graph, names, output_kind):
    """Accept the inspected H3 recipe, never arbitrary H3 nodes or file operations."""
    from studio.graphs import h3_video
    try:
        conditioning = graph["6"]["inputs"]
        prompt, frames = conditioning["prompt"], conditioning["length"]
        image = graph["18"]["inputs"]["image"]
        seed = graph["8"]["inputs"]["noise_seed"]
        prefix = graph["16"]["inputs"]["filename_prefix"]
        if output_kind != "video" or image not in names:
            raise ValueError("H3 requires a supplied first frame and video output")
        if not isinstance(prompt, str) or not 1 <= len(prompt) <= 10000:
            raise ValueError("H3 prompt must contain 1..10000 characters")
        if type(frames) is not int or not 39 <= frames <= 362 or frames % 17 != 5:
            raise ValueError("H3 frame count must use the bounded 17k+5 grid")
        if type(seed) is not int or not 0 <= seed < 2**64:
            raise ValueError("H3 seed must be an unsigned 64-bit integer")
        if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9_/-]{1,160}", prefix) or any(p in ("", ".", "..") for p in prefix.split("/")):
            raise ValueError("Unsafe H3 output prefix")
        expected = h3_video(prompt, seed, image, conditioning["width"], conditioning["height"], 1, prefix)
        expected["6"]["inputs"]["length"] = frames
        if graph != expected:
            raise ValueError("H3 graph differs from the approved dual-GPU turbo4 recipe")
    except (KeyError, TypeError) as exc:
        raise ValueError("Invalid H3 graph structure") from exc


def validate_speech(payload):
    if not isinstance(payload, dict):
        raise ValueError("Speech request must be an object")
    if set(payload) - {"lines", "voices", "options", "project_profile"}:
        raise ValueError("Unexpected speech fields")
    lines, voices = payload.get("lines"), payload.get("voices", {})
    if not isinstance(lines, list) or not 1 <= len(lines) <= 100 or not isinstance(voices, dict) or len(voices) > 8:
        raise ValueError("Speech request must contain bounded lines and voices")
    seen = set()
    for line in lines:
        if not isinstance(line, dict) or set(line) - {"id", "role", "text", "emotion", "start"}:
            raise ValueError("Invalid speech line")
        ident = line.get("id")
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", ident) or ident in seen:
            raise ValueError("Invalid or duplicate line id")
        seen.add(ident)
        if not isinstance(line.get("role"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", line["role"]):
            raise ValueError("Invalid voice role")
        if not isinstance(line.get("text"), str) or not 1 <= len(line["text"].strip()) <= 500:
            raise ValueError("Invalid dialogue text")
        if not isinstance(line.get("emotion", ""), str) or len(line.get("emotion", "")) > 300:
            raise ValueError("Invalid emotion")
    for role, voice in voices.items():
        if not isinstance(role, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", role):
            raise ValueError("Invalid voice role")
        if isinstance(voice, str):
            voice = {"speaker": voice}
        if not isinstance(voice, dict) or set(voice) - {"speaker", "language", "instruct"}:
            raise ValueError("Unexpected voice options")
        if not isinstance(voice.get("speaker"), str) or not re.fullmatch(r"[A-Za-z_]{1,64}", voice["speaker"]):
            raise ValueError("Invalid speaker")
        for key in ("language", "instruct"):
            if not isinstance(voice.get(key, ""), str) or len(voice.get(key, "")) > 300:
                raise ValueError("Invalid voice instruction")
    options = payload.get("options", {})
    if not isinstance(options, dict) or set(options) - OPTIONS:
        raise ValueError("Unexpected speech settings")
    if options.get("tts_dtype", "float32") not in ("float32", "float16"):
        raise ValueError("Invalid TTS dtype")
    number(options.get("tts_seed", 20261012), 0, 2**31 - 1)
    number(options.get("tts_temperature", .7), .01, 2)
    number(options.get("tts_max_new_tokens", 768), 32, 4096)
    if payload.get("project_profile") is not None:
        from .production_profiles import normalize
        normalize(payload["project_profile"])
    return copy.deepcopy(payload)


def validate_request(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("request_id"), str) or not ID.fullmatch(payload["request_id"]):
        raise ValueError("A SHA256 request_id is required")
    kind = payload.get("kind")
    common = {"request_id", "kind", "timeout_seconds", "max_gpu_seconds"}
    number(payload.get("timeout_seconds", 7200), 1, 21600)
    number(payload.get("max_gpu_seconds", 7200), 1, 21600)
    if kind == "comfy":
        if set(payload) - common - {"graph", "inputs", "output_kind"}:
            raise ValueError("Unexpected inference fields")
        inputs = payload.get("inputs", [])
        if not isinstance(inputs, list) or len(inputs) > 16:
            raise ValueError("Too many input images")
        names, size = set(), 0
        for value in inputs:
            if not isinstance(value, dict) or set(value) != {"name", "data", "sha256"}:
                raise ValueError("Invalid inference input")
            name = value["name"]
            if not isinstance(name, str) or not NAME.fullmatch(name) or Path(name).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"} or name in names:
                raise ValueError("Unsafe or duplicate input filename")
            names.add(name)
            if not isinstance(value["data"], str) or len(value["data"]) > MAX_INPUT * 4 // 3 + 8:
                raise ValueError("Image input is too large")
            try:
                data = base64.b64decode(value["data"], validate=True)
            except (ValueError, binascii.Error):
                raise ValueError("Invalid base64 input") from None
            size += len(data)
            if size > MAX_INPUT or hashlib.sha256(data).hexdigest() != value["sha256"]:
                raise ValueError("Input exceeds bounds or has mismatched digest")
        if payload.get("output_kind") not in {"image", "video"}:
            raise ValueError("Expected image or video output")
        validate_graph(payload.get("graph"), names, payload["output_kind"])
    elif kind == "tts":
        if set(payload) - common - {"lines", "voices", "options", "project_profile"}:
            raise ValueError("Unexpected TTS job fields")
        validate_speech({key: value for key, value in payload.items() if key not in common})
    else:
        raise ValueError("Only comfy and tts inference are available")
    return copy.deepcopy(payload)


class InferenceEngine:
    def __init__(self, production, model_profiles=None):
        self.model_profiles = copy.deepcopy(model_profiles or {})
        self.production = copy.deepcopy(production)
        self.production.pop("remote_gpu", None)
        self.production.pop("gpu_service", None)
        self.production["gpu_backend"] = "local"

    def health(self):
        from studio.h3 import installation
        h3 = installation()
        return {"ok": True, "service": "gpu_inference", "workflow_execution": False,
                "execution_host": socket.gethostname(),
                "gpu_cards": self.production.get("gpu_cards", list(range(8))),
                "models": {
                    "image": (ROOT / "models/diffusion_models/flux-2-klein-4b.safetensors").is_file(),
                    "video": (ROOT / "models/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors").is_file(),
                    "audio": (ROOT / "audio/tts.py").is_file() and
                             (ROOT / "models/tts/Qwen3-TTS-12Hz-1.7B-CustomVoice").is_dir()},
                "video_backends": {"comfyui_h3": h3,
                    "comfyui_wan": {"model": "Wan2.2-TI2V-5B", "installed": (ROOT / "models/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors").is_file()}},
                "model_check": "installed_files_only_not_inference"}

    def model(self, name, route, payload, method):
        from .providers import request_json, model_matches
        profiles = {"primary": "qwen38_primary", "critic": "qwen38_secondary", "vision": "qwen_vision_existing"}
        routes = {("GET", "/v1/models"), ("GET", "/props"),
                  ("POST", "/v1/chat/completions"), ("POST", "/apply-template"), ("POST", "/tokenize")}
        if name not in profiles or (method, route) not in routes:
            raise FileNotFoundError("Model inference route not found")
        profile = self.model_profiles.get(profiles[name])
        if not isinstance(profile, dict) or not profile.get("base_url", "").endswith("/v1"):
            raise ValueError("Server model profile is unavailable")
        if method == "POST" and not isinstance(payload, dict):
            raise ValueError("Model request must be an object")
        if payload is not None:
            if payload.get("stream", False) is not False:
                raise ValueError("Only bounded JSON completions are supported")
            if route == "/v1/chat/completions":
                if not isinstance(payload.get("model"), str) or not model_matches(profile, payload["model"]):
                    raise ValueError("Requested model does not match the fixed server profile")
                for field in ("max_tokens", "max_completion_tokens"):
                    if field in payload:
                        number(payload[field], 1, 32768)
                if payload.get("n", 1) != 1:
                    raise ValueError("Only one completion is supported")
            if route in ("/v1/chat/completions", "/apply-template"):
                messages = payload.get("messages")
                if not isinstance(messages, list) or not 1 <= len(messages) <= 1000:
                    raise ValueError("Bounded model messages are required")
                for message in messages:
                    if not isinstance(message, dict):
                        raise ValueError("Invalid model message")
                    content = message.get("content")
                    if isinstance(content, list):
                        for part in content:
                            if not isinstance(part, dict) or part.get("type") not in ("text", "image_url"):
                                raise ValueError("Only text and inline image content is supported")
                            if part["type"] == "image_url":
                                image = part.get("image_url")
                                url = image.get("url", "") if isinstance(image, dict) else ""
                                if not isinstance(url, str) or not url.startswith(("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")):
                                    raise ValueError("Vision inputs must be inline images")
                    elif not isinstance(content, str):
                        raise ValueError("Invalid model message content")
        base = profile["base_url"]
        local_route = route[3:] if route.startswith("/v1/") else route
        if not route.startswith("/v1/"):
            base = base[:-3]
        return request_json(base, local_route, payload, timeout=profile.get("request_timeout_seconds", 480), profile=profile)

    def speech(self, payload, folder):
        from .speech import SpeechMixin
        from .scheduler import Budget, Scheduler
        class Session(SpeechMixin):
            def boundary(self, gpu=False):
                self.budget.check(require_gpu=gpu)
            def emit(self, *args, **kwargs):
                return None
            def receipt_path(self, record):
                return within(ROOT, record["path"])
        session = Session()
        session.config = {**self.production, **payload.get("options", {})}
        session.project = ({"production_profile": payload["project_profile"]} if payload.get("project_profile") else {})
        session.folder, session.production, session.control = folder, {}, None
        session.budget = Budget({"max_wall_seconds": payload.get("timeout_seconds", 7200),
                                 "max_gpu_seconds": payload.get("max_gpu_seconds", 7200)})
        session.scheduler = Scheduler(session.config, session.budget)
        voices = read_json(ROOT / "audio/voices.json")
        voices.update(copy.deepcopy(payload.get("voices", {})))
        return session, voices

    def sources(self, payload, folder):
        session, voices = self.speech(payload, folder)
        return {"expected": session._speech_sources(payload["lines"], voices)}

    def execute(self, payload, folder):
        self.active_budget = None
        try:
            result = self._execute(payload, folder)
            result["receipt"]["execution_host"] = socket.gethostname()
            return result
        except Exception as exc:
            exc.gpu_receipt = {"execution_host": socket.gethostname(),
                               **(self.active_budget.snapshot() if self.active_budget else {"gpu_seconds": 0})}
            raise

    def _execute(self, payload, folder):
        from .scheduler import Budget, Scheduler
        files = []
        if payload["kind"] == "comfy":
            budget = Budget({"max_wall_seconds": payload.get("timeout_seconds", 7200),
                             "max_gpu_seconds": payload.get("max_gpu_seconds", 7200)})
            self.active_budget = budget
            scheduler = Scheduler(self.production, budget)
            h3 = any(node.get("class_type") == "MiniMaxH3ImageToVideo" for node in payload["graph"].values())
            if h3:
                from studio.h3 import require_installation
                require_installation()
            with scheduler.worker("inference-" + payload["request_id"][:24], **({"h3": True} if h3 else {})) as worker:
                for value in payload.get("inputs", []):
                    path = within(worker.folder / "input", value["name"])
                    path.write_bytes(base64.b64decode(value["data"], validate=True))
                path = worker.execute(payload["graph"], folder / "output", payload["output_kind"],
                                      timeout=budget.timeout(payload.get("timeout_seconds", 7200), 2 if h3 else 1))
                files.append(path)
            result = {"receipt": {**read_json(path.parent / "result.json"), **budget.snapshot()}}
            if h3:
                from studio.h3 import MODEL, REVISION
                result["receipt"].update(provider="comfyui_h3", model=MODEL, model_revision=REVISION)
        else:
            session, voices = self.speech(payload, folder)
            self.active_budget = session.budget
            expected = session._speech_sources(payload["lines"], voices)
            records = session._speech_batch(payload["lines"], voices, expected)
            result = {"receipt": session.budget.snapshot(), "expected": expected, "records": records}
            for ident, record in records.items():
                record["wave_artifact_index"] = len(files)
                files.append(within(ROOT, record["path"]))
                record["metadata_artifact_index"] = len(files)
                files.append(within(ROOT, record["metadata_path"]))
        artifacts = []
        target = folder / "artifacts"
        target.mkdir(exist_ok=True)
        for index, source in enumerate(files):
            filename = f"{index:03d}-" + source.name
            copied = target / filename
            shutil.copy2(source, copied)
            artifacts.append({"index": index, "name": filename, "sha256": sha256(copied), "bytes": copied.stat().st_size})
        result["artifacts"] = artifacts
        return result


class InferenceStore:
    def __init__(self, folder, engine, *, start_worker=True):
        self.folder, self.engine = Path(folder), engine
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.folder.chmod(0o700)
        self.lease = (self.folder / "service.lock").open("a+")
        try:
            fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lease.close()
            raise RuntimeError("Another inference service owns this task directory") from None
        self.lock, self.queue, self.stopped = threading.RLock(), queue.Queue(), threading.Event()
        for path in sorted(self.folder.glob("*/job.json")):
            state = read_json(path)
            if state["status"] == "running":
                state.update(status="unresolved", error="Service restarted during inference; inspect saved submission before a new request", finished_at=time.time())
                write_json(path, state)
            elif state["status"] == "queued":
                self.queue.put(state["id"])
        self.thread = None
        if start_worker:
            self.thread = threading.Thread(target=self._consume, name="gpu-inference", daemon=True)
            self.thread.start()

    def get(self, ident):
        if not ID.fullmatch(ident):
            raise ValueError("Invalid inference id")
        path = self.folder / ident / "job.json"
        if not path.exists():
            raise FileNotFoundError("Inference job not found")
        with self.lock:
            state = read_json(path)
            return {key: value for key, value in state.items() if key not in {"request"}}

    def submit(self, payload):
        payload = validate_request(payload)
        ident = payload["request_id"]
        signature = fingerprint({key: value for key, value in payload.items() if key != "request_id"})
        with self.lock:
            path = self.folder / ident / "job.json"
            if path.exists():
                if read_json(path)["request_hash"] != signature:
                    raise Conflict("request_id already identifies different inference inputs")
                return self.get(ident)
            if self.queue.qsize() >= 64:
                raise Conflict("Inference queue is full")
            state = {"id": ident, "request_hash": signature, "status": "queued", "request": payload, "created_at": time.time()}
            write_json(path, state)  # Persist intent before queue publication or GPU effects.
            self.queue.put(ident)
            return self.get(ident)

    def execute_one(self, ident):
        with self.lock:
            path = self.folder / ident / "job.json"
            state = read_json(path)
            if state["status"] != "queued":
                return
            state.update(status="running", started_at=time.time())
            write_json(path, state)  # A crash after this write must never replay the GPU request.
        try:
            result = self.engine.execute(state["request"], path.parent)
            state.update(status="completed", result=result, finished_at=time.time())
        except Exception as exc:
            state.update(status="failed", result={"receipt": getattr(exc, "gpu_receipt", {"execution_host": socket.gethostname(), "gpu_seconds": 0})}, error=type(exc).__name__ + ": " + str(exc)[:1500],
                         reason=getattr(exc, "reason", "inference_failed"), details=getattr(exc, "details", {}), finished_at=time.time())
        with self.lock:
            write_json(path, state)

    def artifact(self, ident, index):
        state = self.get(ident)
        if state["status"] != "completed":
            raise Conflict("Inference artifact is not ready")
        artifacts = state["result"]["artifacts"]
        if not 0 <= index < len(artifacts):
            raise FileNotFoundError("Artifact not found")
        record = artifacts[index]
        path = within(self.folder / ident / "artifacts", record["name"])
        if not path.is_file() or path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
            raise Conflict("Inference artifact no longer matches its receipt")
        return path

    def _consume(self):
        while not self.stopped.is_set():
            try:
                ident = self.queue.get(timeout=.25)
            except queue.Empty:
                continue
            self.execute_one(ident)

    def close(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=1)
            if self.thread.is_alive():
                # Do not release ownership while inference still runs.
                raise RuntimeError("Inference is still running; stop at its terminal receipt")
        fcntl.flock(self.lease, fcntl.LOCK_UN)
        self.lease.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def json(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_request(self):
        try:
            from studio.http_security import check_origin
            check_origin(self)
            self.connection.settimeout(15)
            if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Encoding"):
                raise ValueError("Unsupported body framing")
            parsed = urlsplit(self.path)
            if len(self.path) > 512 or parsed.query or parsed.fragment or "%" in parsed.path:
                raise ValueError("Invalid inference path")
            payload = None
            if self.command == "POST":
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) != 1 or not lengths[0].isdigit():
                    raise ValueError("One Content-Length is required")
                length = int(lengths[0])
                if not 0 < length <= MAX_BODY:
                    self.json({"error": "Inference request too large"}, 413)
                    return
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("JSON content is required")
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("Incomplete request")
                payload = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON")))
            store = self.server.store
            model = re.fullmatch(r"/models/(primary|critic|vision)(/v1/models|/v1/chat/completions|/props|/apply-template|/tokenize)", parsed.path)
            if model:
                return self.json(store.engine.model(model[1], model[2], payload, self.command))
            if self.command == "GET" and parsed.path == "/health":
                return self.json(store.engine.health())
            if self.command == "POST" and parsed.path == "/tts/sources":
                payload = validate_speech(payload)
                folder = store.folder / "sources" / fingerprint(payload)
                folder.mkdir(parents=True, exist_ok=True)
                return self.json(store.engine.sources(payload, folder))
            if self.command == "POST" and parsed.path == "/jobs":
                return self.json(store.submit(payload), 202)
            match = re.fullmatch(r"/jobs/([0-9a-f]{64})(?:/artifacts/([0-9]{1,3}))?", parsed.path)
            if self.command == "GET" and match:
                if match[2] is None:
                    return self.json(store.get(match[1]))
                path = store.artifact(match[1], int(match[2]))
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(path.stat().st_size))
                self.end_headers()
                with path.open("rb") as source:
                    shutil.copyfileobj(source, self.wfile)
                return
            self.json({"error": "Inference endpoint not found"}, 404)
        except Conflict as exc:
            self.json({"error": str(exc)}, 409)
        except FileNotFoundError as exc:
            self.json({"error": str(exc)}, 404)
        except (ValueError, TypeError, KeyError) as exc:
            from studio.http_security import HTTPError
            self.json({"error": str(exc)}, exc.status if isinstance(exc, HTTPError) else 400)
        except Exception:
            self.json({"error": "GPU inference service failed; inspect server-local records"}, 500)
        finally:
            self.close_connection = True

    do_GET = handle_request
    do_POST = handle_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args()
    if args.host != "127.0.0.1" or not 1024 <= args.port <= 65535:
        raise ValueError("GPU service must bind IPv4 loopback on an unprivileged port")
    from .config import load_config
    config = load_config()
    engine = InferenceEngine(config.get("production", {}), config.get("llm_profiles", {}))
    store = InferenceStore(ROOT / "runtime/inference", engine)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.store = store
    try:
        server.serve_forever()
    finally:
        server.server_close()
        store.close()


if __name__ == "__main__":
    main()
