"""Contract inspected on Wkp's existing ComfyUI dual-GPU H3 installation."""
from __future__ import annotations

import math
import os
from pathlib import Path

MODEL = "MiniMax-H3"
REVISION = "wkp-dualgpu-turbo4-v1"
BASE_REVISION = "wkp-dualgpu-base-v1"
UNET = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VAE = "minimax_h3_video_vae_fp16.safetensors"
LORA = "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"


def recipe(profile=None):
    """Bounded recipes; absence of new fields retains the original turbo graph."""
    profile = profile or {}
    quality = profile.get("quality", "production" if profile.get("revision") == BASE_REVISION else "preview")
    if quality not in ("preview", "production"):
        raise ValueError("H3 quality must be preview or production")
    revision = BASE_REVISION if quality == "production" else REVISION
    if profile.get("revision", revision) != revision:
        raise ValueError("H3 quality and workflow revision disagree")
    result = {"quality": quality, "revision": revision, "steps": profile.get("steps", 20 if quality == "production" else 4),
              "shift_video": profile.get("shift_video", 12.0), "shift_audio": profile.get("shift_audio", 3.0),
              "sampler": profile.get("sampler", "euler")}
    if type(result["steps"]) is not int or not (20 <= result["steps"] <= 50 if quality == "production" else result["steps"] == 4):
        raise ValueError("H3 steps must be 20..50 for production or 4 for preview")
    for field, upper in (("shift_video", 20), ("shift_audio", 10)):
        value = result[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 1 <= value <= upper:
            raise ValueError("Invalid H3 " + field)
    if result["sampler"] not in ("euler", "heun"):
        raise ValueError("Unsupported H3 sampler")
    if quality == "preview" and (result["shift_video"] != 12 or result["shift_audio"] != 3 or result["sampler"] != "euler"):
        raise ValueError("The turbo4 preview recipe requires its trained sampling parameters")
    return result


def graph_recipe(graph):
    """Read parameters only; callers still compare the complete canonical graph."""
    return recipe({"quality": "preview" if "17" in graph else "production",
                   "steps": graph["10"]["inputs"]["steps"],
                   "shift_video": graph["2"]["inputs"]["shift_video"],
                   "shift_audio": graph["2"]["inputs"]["shift_audio"],
                   "sampler": graph["9"]["inputs"]["sampler_name"]})


def backend_ready(backend, cards, profile=None):
    selected = recipe(profile)
    observed = backend if selected["revision"] == REVISION else backend.get("recipes", {}).get(selected["quality"], {})
    return (backend.get("model") == MODEL and backend.get("gpu_count") == 2 and len(set(cards)) >= 2 and
            observed.get("revision") == selected["revision"] and observed.get("installed") is True)


def frame_count(seconds):
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 1 <= seconds <= 15:
        raise ValueError("MiniMax H3 shot duration must be 1..15 seconds")
    frames = math.ceil(seconds * 24)
    return frames + (5 - frames) % 17


def lora_directory():
    from .core import ROOT
    configured = os.environ.get("DRAMA_H3_LORA_DIR")
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            raise ValueError("DRAMA_H3_LORA_DIR must be absolute")
        return path
    local = ROOT / "models/loras"
    original = ROOT.parent / "ai_short_drama/pilot_01/03_design/models/loras"
    return local if (local / LORA).is_file() else original


def dependencies(profile=None):
    from .core import COMFY, ROOT
    paths = {
        "comfy_python": COMFY / ".venv/bin/python",
        "comfy_main": COMFY / "main.py",
        "model": COMFY / "models/diffusion_models" / UNET,
        "text_encoder": COMFY / "models/text_encoders" / CLIP,
        "vae": COMFY / "models/vae" / VAE,
        "device_nodes": ROOT / "nodes/pilot_device_loaders/__init__.py",
        "int8_memory_adapter": ROOT / "nodes/pilot_device_loaders/int8_memory.py",
        "h3_nodes": COMFY / "comfy_extras/nodes_minimax_h3.py",
    }
    if recipe(profile)["quality"] == "preview":
        paths["lora"] = lora_directory() / LORA
    return paths


def installation(profile=None):
    selected = recipe(profile)
    missing = [key for key, path in dependencies(selected).items() if not path.is_file()]
    info = {"model": MODEL, "revision": selected["revision"], "installed": not missing,
            "missing": missing, "gpu_count": 2, "fps": 24,
            "frame_grid": "17k+5", "steps": selected["steps"], "quality": selected["quality"],
            "validation": "installed_files_only_not_inference"}
    if profile is None:
        info["recipes"] = {quality: installation({"quality": quality}) for quality in ("preview", "production")}
    return info


def require_installation(profile=None):
    # Worker startup needs shared base components; the graph preflight checks LoRA
    # only when the submitted recipe actually uses it.
    info = installation(profile if profile is not None else {"quality": "production"})
    if not info["installed"]:
        raise ValueError("MiniMax H3 dependencies missing: " + ", ".join(info["missing"]))
