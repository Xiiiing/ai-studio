"""Shared local paths and verification for the isolated LatentSync adapter."""
import hashlib, json, os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIPSYNC = ROOT / "lipsync"
VENDOR = LIPSYNC / "vendor/LatentSync"
MODELS = ROOT / "models/lipsync"
ENV_PYTHON = ROOT / "envs/lipsync/bin/python"
SOURCE_COMMIT = "a229c3948406bc2cf6eaf4873e662e70c6a04746"

def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def project_path(raw, must_exist=True):
    path = Path(raw).expanduser().resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError("Input/output must stay in the drama_studio project: " + str(path))
    if must_exist and not path.is_file():
        raise FileNotFoundError(str(path))
    return path

def configure_environment():
    os.environ["CUDA_VISIBLE_DEVICES"] = "5"
    os.environ["OMP_NUM_THREADS"] = "4"
    os.environ["MKL_NUM_THREADS"] = "4"
    os.environ["OPENBLAS_NUM_THREADS"] = "4"
    os.environ["PYTHONUNBUFFERED"] = "1"
    os.environ["HF_HOME"] = str(MODELS / "hf_cache")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    ffmpeg = LIPSYNC / "bin/ffmpeg"
    if not ffmpeg.is_file():
        raise FileNotFoundError("Missing project-local ffmpeg link: " + str(ffmpeg))
    os.environ["PATH"] = str(ffmpeg.parent) + os.pathsep + os.environ.get("PATH", "")
    os.environ["IMAGEIO_FFMPEG_EXE"] = str(ffmpeg.resolve())
    sys.path.insert(0, str(ROOT))
    return ffmpeg

def verify_models():
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(VENDOR), text=True, timeout=15).strip()
    if source != SOURCE_COMMIT:
        raise RuntimeError("LatentSync source commit changed")
    manifest = json.loads((MODELS / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "verified":
        raise RuntimeError("Model download and verification are not complete")
    required = {
        "checkpoints/latentsync_unet.pt",
        "checkpoints/whisper/tiny.pt",
        "vae/config.json",
        "vae/diffusion_pytorch_model.safetensors",
        "checkpoints/auxiliary/models/buffalo_l/det_10g.onnx",
        "checkpoints/auxiliary/models/buffalo_l/2d106det.onnx",
    }
    entries = {item["path"]: item for item in manifest["files"]}
    for name in sorted(required):
        if name not in entries:
            raise RuntimeError("Missing model manifest entry: " + name)
        path = MODELS / name
        item = entries[name]
        if not path.is_file() or path.stat().st_size != item["bytes"]:
            raise RuntimeError("Model size/path mismatch: " + name)
        if sha256(path) != item["sha256"]:
            raise RuntimeError("Model SHA256 mismatch: " + name)
    terms_path = LIPSYNC / "model_terms.json"
    terms = json.loads(terms_path.read_text(encoding="utf-8"))
    return {"source_commit": source, "verified_model_files": len(required),
            "model_manifest_sha256": sha256(MODELS / "manifest.json"),
            "model_terms_path": str(terms_path.relative_to(ROOT)), "model_terms_sha256": sha256(terms_path),
            "contains_noncommercial_pretrained_weights": terms["contains_noncommercial_pretrained_weights"],
            "commercial_release_cleared": terms["commercial_release_cleared"]}

def run(argv, **kwargs):
    print("RUN", " ".join(str(arg) for arg in argv), flush=True)
    return subprocess.run([str(arg) for arg in argv], check=True, **kwargs)

def media_info(path):
    import cv2
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise RuntimeError("Cannot decode video: " + str(path))
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        ok, frame = cap.read()
        if not ok or fps <= 0 or frames <= 0:
            raise RuntimeError("Invalid video metadata/frame: " + str(path))
        return {"fps": fps, "frames": frames, "width": width, "height": height, "duration": frames / fps}
    finally:
        cap.release()
