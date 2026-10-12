#!/usr/bin/env python3
"""Pinned LatentSync 1.6 inference adapter with explicit Turing FP16 support."""
import argparse, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from runtime import VENDOR, MODELS, configure_environment

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--video", required=True)
    p.add_argument("--audio", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--temp-dir", required=True)
    p.add_argument("--steps", type=int, default=20)
    p.add_argument("--guidance", type=float, default=1.5)
    p.add_argument("--seed", type=int, default=1247)
    p.add_argument("--disable-deepcache", action="store_true")
    args = p.parse_args()
    # This process is launched by sync.py while its parent holds GpuLease([5]).
    configure_environment()
    sys.path.insert(0, str(VENDOR))
    os.chdir(VENDOR)
    from studio.comfy import GpuStartupLock
    with GpuStartupLock():
        import torch
        from omegaconf import OmegaConf
        from diffusers import AutoencoderKL, DDIMScheduler
        from accelerate.utils import set_seed
        from latentsync.models.unet import UNet3DConditionModel
        from latentsync.pipelines.lipsync_pipeline import LipsyncPipeline
        from latentsync.whisper.audio2feature import Audio2Feature

        if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
            raise RuntimeError("Exactly one isolated CUDA device is required")
        capability = torch.cuda.get_device_capability(0)
        if capability < (7, 0):
            raise RuntimeError("This adapter requires a tested FP16-capable CUDA GPU")
        dtype = torch.float16
        print("DEVICE", torch.cuda.get_device_name(0), capability, "dtype", dtype, flush=True)
        config = OmegaConf.load(VENDOR / "configs/unet/stage2_512.yaml")
        scheduler = DDIMScheduler.from_pretrained(str(VENDOR / "configs"), local_files_only=True)
        encoder = Audio2Feature(model_path=str(MODELS / "checkpoints/whisper/tiny.pt"), device="cuda", num_frames=config.data.num_frames, audio_feat_length=config.data.audio_feat_length)
        vae = AutoencoderKL.from_pretrained(str(MODELS / "vae"), torch_dtype=dtype, local_files_only=True, use_safetensors=True)
        vae.config.scaling_factor = 0.18215
        vae.config.shift_factor = 0
        unet, _ = UNet3DConditionModel.from_pretrained(OmegaConf.to_container(config.model), str(MODELS / "checkpoints/latentsync_unet.pt"), device="cpu")
        unet = unet.to(dtype=dtype)
        pipeline = LipsyncPipeline(vae=vae, audio_encoder=encoder, unet=unet, scheduler=scheduler).to("cuda")
        if not args.disable_deepcache:
            from DeepCache import DeepCacheSDHelper
            helper = DeepCacheSDHelper(pipe=pipeline)
            helper.set_params(cache_interval=3, cache_branch_id=0)
            helper.enable()
        set_seed(args.seed)
    pipeline(video_path=args.video, audio_path=args.audio, video_out_path=args.output,
             num_frames=config.data.num_frames, num_inference_steps=args.steps, guidance_scale=args.guidance,
             weight_dtype=dtype, width=512, height=512, video_fps=25,
             mask_image_path=str(VENDOR / "latentsync/utils/mask.png"), temp_dir=args.temp_dir)
    if not Path(args.output).is_file() or Path(args.output).stat().st_size == 0:
        raise RuntimeError("LatentSync did not produce a video")

if __name__ == "__main__":
    main()
