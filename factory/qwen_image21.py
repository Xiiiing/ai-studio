"""Bounded Wkp-local Qwen-Image-2.1 inference adapter.

This adapter talks only to the resident loopback ComfyUI 8352 process. It
accepts explicit image requests, never caller-controlled graph nodes, URLs,
model paths, or arbitrary file destinations. A submission journal is written
before HTTP dispatch; uncertain effects are never automatically resubmitted.
"""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import math
from io import BytesIO
from pathlib import Path
import re
import shutil
import socket
import time
import urllib.error
import urllib.request

from studio.core import ROOT, write_json

MODEL_ROOT = ROOT / "runtime/qwen-image-2.1"
INPUT_DIR = MODEL_ROOT / "input"
OUTPUT_DIR = MODEL_ROOT / "output"
SERVICE = "http://127.0.0.1:8352"
MODEL = "Qwen-Image-2.1"
REVISION = "Comfy-Org/Qwen-Image-2.1@a8342359c61979b06a8dafb1a47991fe1f30e994"
IMAGE_MODEL = "qwen_image_2.1_int8_convrot.safetensors"
ENCODER = "qwen3vl_8b_int8_convrot.safetensors"
VAE = "qwen_image_2.1_vae_bf16.safetensors"
SHA = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\Z")
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
MAX_INPUT_BYTES = 20 * 1024 * 1024


def installed():
    files = (
        MODEL_ROOT / "models/diffusion_models" / IMAGE_MODEL,
        MODEL_ROOT / "models/text_encoders" / ENCODER,
        MODEL_ROOT / "models/vae" / VAE,
    )
    return all(path.is_file() and path.stat().st_size > 1_000_000 for path in files)


def validate_request(payload):
    if not isinstance(payload, dict):
        raise ValueError("Qwen Image request must be an object")
    supported = {"prompt", "negative_prompt", "seed", "steps", "width", "height", "inputs"}
    if set(payload) - supported:
        raise ValueError("Unexpected Qwen Image fields")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 5000:
        raise ValueError("Qwen Image prompt must contain 1..5000 characters")
    negative = payload.get("negative_prompt", "")
    if not isinstance(negative, str) or len(negative) > 2000:
        raise ValueError("Qwen Image negative prompt too long")
    seed = payload.get("seed", 20261017)
    steps = payload.get("steps", 8)
    width = payload.get("width", 512)
    height = payload.get("height", 768)
    if type(seed) is not int or not 0 <= seed < 2**64:
        raise ValueError("Qwen Image seed must be an unsigned integer")
    if type(steps) is not int or not 1 <= steps <= 40:
        raise ValueError("Qwen Image steps outside bounds")
    for size in (width, height):
        if type(size) is not int or not 256 <= size <= 1536 or size % 32:
            raise ValueError("Qwen Image dimensions must be 256..1536 multiples of 32")
    if width * height > 1024 * 1536:
        raise ValueError("Qwen Image canvas exceeds tested bounds")
    inputs = payload.get("inputs", [])
    if not isinstance(inputs, list) or len(inputs) > 4:
        raise ValueError("Qwen Image permits 0..4 reference images")
    names, total = set(), 0
    for item in inputs:
        if not isinstance(item, dict) or set(item) != {"name", "sha256", "data"}:
            raise ValueError("Invalid reference input")
        name = item["name"]
        digest = item["sha256"]
        encoded = item["data"]
        if (not isinstance(name, str) or not NAME.fullmatch(name)
                or Path(name).suffix.lower() not in IMAGE_EXT
                or name in names or not isinstance(digest, str)
                or not SHA.fullmatch(digest) or not isinstance(encoded, str)
                or len(encoded) > (MAX_INPUT_BYTES * 4 // 3) + 8):
            raise ValueError("Unsafe or repeated reference input")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Malformed reference Base64") from None
        total += len(raw)
        if total > MAX_INPUT_BYTES or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Reference is oversized or has invalid digest")
        # Comfy must not consume malformed pixels or disguised file extensions.
        from PIL import Image
        with Image.open(BytesIO(raw)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"} or image.width * image.height > 16_777_216:
                raise ValueError("Unsupported reference image")
            image.verify()
        names.add(name)
    return {
        "prompt": prompt.strip(), "negative_prompt": negative, "seed": seed,
        "steps": steps, "width": width, "height": height,
        "inputs": copy.deepcopy(inputs),
    }


def _node(kind, **inputs):
    return {"class_type": kind, "inputs": inputs}


def build_graph(payload, reference_names, prefix):
    """Construct exactly the approved native ComfyUI node graph."""
    req = validate_request(payload)
    if not re.fullmatch(r"wkp-image21/[a-f0-9]{20}", prefix):
        raise ValueError("Unsafe generated output prefix")
    if len(reference_names) != len(req["inputs"]):
        raise ValueError("Reference count mismatch")
    g = {
        "1": _node("UNETLoader", unet_name=IMAGE_MODEL, weight_dtype="default"),
        "2": _node("CLIPLoader", clip_name=ENCODER, type="qwen_image", device="cpu"),
        "3": _node("VAELoader", vae_name=VAE),
        "4": _node("TextEncodeQwenImage21", clip=["2", 0],
                   prompt=req["prompt"], negative_prompt=req["negative_prompt"],
                   resolution=0 if reference_names else 512, images={}),
        "5": _node("LatentUpscale", samples=["4", 2], upscale_method="nearest-exact",
                   width=req["width"], height=req["height"], crop="disabled"),
        "6": _node("KSampler", model=["1", 0], seed=req["seed"], steps=req["steps"],
                   cfg=1.0, sampler_name="euler", scheduler="simple",
                   positive=["4", 0], negative=["4", 1],
                   latent_image=["5", 0], denoise=1.0),
        "7": _node("VAEDecode", samples=["6", 0], vae=["3", 0]),
        "8": _node("SaveImage", images=["7", 0], filename_prefix=prefix),
    }
    if reference_names:
        g["4"]["inputs"].pop("images")
        g["4"]["inputs"]["vae"] = ["3", 0]
        for i, name in enumerate(reference_names):
            node_id = str(20 + i)
            g[node_id] = _node("LoadImage", image=name)
            g["4"]["inputs"][f"images.image_{i+1}"] = [node_id, 0]
    return g


def _json_request(route, payload=None, timeout=12):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(
        SERVICE + route, data=data, headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as response:
        data = response.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("ComfyUI response too large")
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError("Unexpected ComfyUI response")
    return result


def _install_inputs(req):
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    names = []
    for item in req["inputs"]:
        raw = base64.b64decode(item["data"], validate=True)
        name = item["sha256"][:40] + Path(item["name"]).suffix.lower()
        target = INPUT_DIR / name
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("Existing reference filename collision")
        else:
            tmp = target.with_suffix(target.suffix + ".tmp")
            try:
                with tmp.open("xb") as f:
                    f.write(raw)
                tmp.replace(target)
            finally:
                tmp.unlink(missing_ok=True)
        names.append(name)
    return names


def normalize_image(source, output, width, height):
    """Preserve native pixels; deliver exactly the requested bounded canvas."""
    from PIL import Image
    output = Path(output)
    original = output.parent / "image_native.png"
    shutil.copy2(source, original)
    with Image.open(original) as img:
        img.load()
        native = img.size
        if native == (width, height):
            shutil.copy2(original, output)
        elif (native[0] < width or native[1] < height
              or native[0] * height != native[1] * width):
            raise RuntimeError("Qwen Image delivered unexpected image geometry")
        else:
            img.resize((width, height), Image.Resampling.LANCZOS).save(output, "PNG")
    with Image.open(output) as img:
        img.verify()
    return list(native)


def render(payload, folder, *, timeout_seconds=1800, max_gpu_seconds=1800):
    """Produce an image and provenance receipt, or leave an unresolved journal."""
    request = validate_request(payload)
    if not installed():
        raise RuntimeError("Official Qwen Image 2.1 weights are missing")
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    inputs = _install_inputs(request)
    ident = hashlib.sha256(
        json.dumps({"request": request, "folder": str(folder)}, sort_keys=True,
                   ensure_ascii=False).encode()).hexdigest()
    graph = build_graph(request, inputs, "wkp-image21/" + ident[:20])
    signature = hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()
    journal = folder / "image21_submission.json"
    result_path = folder / "image.png"
    started = time.monotonic()
    if journal.exists():
        saved = json.loads(journal.read_text())
        if saved.get("graph_sha256") != signature:
            raise RuntimeError("Existing image task has incompatible inputs")
        if saved.get("state") == "dispatching":
            raise RuntimeError("Uncertain image submission requires reconciliation; not resubmitting")
        task_id = saved["prompt_id"]
    else:
        # Durable intent before the first external effect. Never auto-post twice.
        write_json(journal, {"state": "dispatching", "graph_sha256": signature})
        task = _json_request("/prompt", {"prompt": graph, "client_id": "wkp-drama-image21"})
        task_id = task.get("prompt_id")
        if not isinstance(task_id, str) or not re.fullmatch(r"[a-f0-9-]{36}", task_id):
            raise ValueError("ComfyUI did not provide a valid task ID")
        write_json(journal, {
            "state": "accepted", "prompt_id": task_id, "graph_sha256": signature,
        })
    deadline = started + min(float(timeout_seconds), float(max_gpu_seconds))
    while time.monotonic() < deadline:
        history = _json_request("/history/" + task_id, timeout=15).get(task_id)
        if history is None:
            time.sleep(3)
            continue
        status = history.get("status", {}).get("status_str")
        if status not in ("error", "success"):
            time.sleep(3)
            continue
        write_json(folder / "image21_history.json", history)
        if status != "success":
            raise RuntimeError("Qwen Image execution failed; inspect image21_history.json")
        matches = [
            image for node in history.get("outputs", {}).values()
            for image in node.get("images", [])
            if image.get("type", "output") == "output"
        ]
        if len(matches) != 1:
            raise RuntimeError("Expected one image from Qwen Image ComfyUI")
        media = matches[0]
        name = media.get("filename")
        subdir = media.get("subfolder", "")
        if (not isinstance(name, str) or not NAME.fullmatch(name)
                or Path(name).suffix.lower() not in IMAGE_EXT
                or not isinstance(subdir, str) or subdir.startswith("/")
                or any(part in ("", ".", "..") for part in subdir.split("/"))):
            raise RuntimeError("Unsafe Qwen Image output path")
        source = (OUTPUT_DIR / subdir / name).resolve()
        if not source.is_relative_to(OUTPUT_DIR.resolve()) or not source.is_file():
            raise RuntimeError("Image output missing or outside owned directory")
        native_size = normalize_image(source, result_path, request["width"], request["height"])
        seconds = round(time.monotonic() - started, 3)
        receipt = {
            "provider": "comfyui_qwen_image21", "model": MODEL,
            "model_revision": REVISION, "prompt_id": task_id,
            "gpu_seconds": seconds, "elapsed_seconds": seconds,
            "gpu_count": 1, "accounting": "bounded_single_gpu_request_wall_time",
            "execution_host": socket.gethostname(),
            "sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
            "path": str(result_path.relative_to(ROOT)),
            "native_size": native_size, "output_size": [request["width"], request["height"]],
        }
        write_json(folder / "image21_result.json", receipt)
        return result_path, receipt
    raise TimeoutError("Qwen Image task still pending; preserve submission journal")
