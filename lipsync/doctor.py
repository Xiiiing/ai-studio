#!/usr/bin/env python3
"""Validate installation and GPU primitives, never claim rendered lip-sync quality."""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "envs/lipsync/bin/python"
if Path(sys.prefix).resolve() != (ROOT / "envs/lipsync").resolve():
    os.execv(str(PYTHON), [str(PYTHON), str(Path(__file__).resolve())] + sys.argv[1:])

import argparse, importlib.metadata, json, subprocess
from runtime import LIPSYNC, VENDOR, configure_environment, verify_models

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-models", action="store_true", help="Allow dependency/GPU checking while weights download")
    args = parser.parse_args()
    ffmpeg = configure_environment()
    report = {"inference_tested": False, "lip_sync_quality_verified": False, "physical_gpu": 5, "python": sys.version}
    report["packages"] = {p: importlib.metadata.version(p) for p in ["torch","torchvision","diffusers","transformers","decord","insightface","onnxruntime-gpu","numpy","opencv-python-headless"]}
    report["ffmpeg"] = subprocess.check_output([str(ffmpeg), "-version"], text=True, timeout=15).splitlines()[0]
    if not args.skip_models:
        report.update(verify_models())
    sys.path.insert(0, str(VENDOR))
    os.chdir(VENDOR)
    from studio.comfy import GpuLease, GpuStartupLock
    with GpuStartupLock(), GpuLease([5]):
        import torch
        import torchvision
        import diffusers
        import cv2
        import decord
        import onnxruntime as ort
        import insightface
        from latentsync.pipelines.lipsync_pipeline import LipsyncPipeline
        from latentsync.whisper.audio2feature import Audio2Feature
        from DeepCache import DeepCacheSDHelper
        if torch.cuda.device_count() != 1:
            raise RuntimeError("GPU visibility is not isolated")
        a = torch.randn((256,256), device="cuda", dtype=torch.float16)
        product = a @ a
        if not torch.isfinite(product).all().item():
            raise RuntimeError("FP16 GPU primitive produced non-finite output")
        torch.cuda.synchronize()
        report.update({"cuda": torch.version.cuda, "gpu_name": torch.cuda.get_device_name(0),
                       "compute_capability": list(torch.cuda.get_device_capability(0)), "fp16_matmul": "passed",
                       "onnx_providers_available": ort.get_available_providers(), "pipeline_import": "passed"})
        if not args.skip_models:
            from latentsync.utils.face_detector import FaceDetector
            detector = FaceDetector(device="cuda")
            providers = {name: model.session.get_providers() for name, model in detector.app.models.items()}
            if not providers or any("CUDAExecutionProvider" not in names for names in providers.values()):
                raise RuntimeError("Face detector did not load with CUDA: " + str(providers))
            report["face_model_providers"] = providers
    report["status"] = "dependencies_ready_models_pending" if args.skip_models else "ready_for_clip_test"
    path = LIPSYNC / ("dependency_check.json" if args.skip_models else "installation_check.json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
