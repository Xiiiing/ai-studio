"""Contract inspected on Wkp's existing ComfyUI dual-GPU H3 installation."""
from __future__ import annotations

import math
import os
from pathlib import Path

MODEL = "MiniMax-H3"
REVISION = "wkp-dualgpu-turbo4-v1"
UNET = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VAE = "minimax_h3_video_vae_fp16.safetensors"
LORA = "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"


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


def dependencies():
    from .core import COMFY, ROOT
    return {
        "comfy_python": COMFY / ".venv/bin/python",
        "comfy_main": COMFY / "main.py",
        "model": COMFY / "models/diffusion_models" / UNET,
        "text_encoder": COMFY / "models/text_encoders" / CLIP,
        "vae": COMFY / "models/vae" / VAE,
        "lora": lora_directory() / LORA,
        "device_nodes": ROOT / "nodes/pilot_device_loaders/__init__.py",
        "h3_nodes": COMFY / "comfy_extras/nodes_minimax_h3.py",
    }


def installation():
    missing = [key for key, path in dependencies().items() if not path.is_file()]
    return {"model": MODEL, "revision": REVISION, "installed": not missing,
            "missing": missing, "gpu_count": 2, "fps": 24,
            "frame_grid": "17k+5", "steps": 4,
            "validation": "installed_files_only_not_inference"}


def require_installation():
    info = installation()
    if not info["installed"]:
        raise ValueError("MiniMax H3 dependencies missing: " + ", ".join(info["missing"]))
