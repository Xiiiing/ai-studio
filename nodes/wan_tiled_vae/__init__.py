"""Project-local Wan2.2 tiled VAE accounting; original ComfyUI remains unchanged."""

import copy
import json
import logging
import time

import nodes
import torch


class WanTiledVAEDecode(nodes.VAEDecodeTiled):
    CATEGORY = "drama_studio/video"
    DESCRIPTION = (
        "Decode Wan2.2 tiles using their actual working dimensions for memory planning. "
        "Preserves the VAE precision and the native tiled decoder."
    )

    def decode(self, vae, samples, tile_size, overlap=64, temporal_size=64, temporal_overlap=8):
        latent = samples["samples"]
        spatial = vae.spacial_compression_decode()
        temporal = vae.temporal_compression_decode()
        if latent.ndim != 5 or vae.latent_channels != 48 or spatial != 16 or temporal != 4:
            raise ValueError(
                "WanTiledVAEDecode requires a Wan2.2 VAE with 48 latent channels, "
                "16x spatial compression, 4x temporal compression and a 5D video latent."
            )
        if type(vae.first_stage_model).__module__ != "comfy.ldm.wan.vae2_2":
            raise ValueError("This memory correction is scoped to the native Wan2.2 VAE.")

        spatial_tile = max(1, tile_size // spatial)
        temporal_tile = max(2, temporal_size // temporal)
        original_estimate = vae.memory_used_decode

        def effective_shape(shape):
            working = list(shape)
            working[-3] = min(working[-3], temporal_tile)
            working[-2] = min(working[-2], spatial_tile)
            working[-1] = min(working[-1], spatial_tile)
            return working

        # A shallow copy retains the same model, patcher, precision and device.
        # Only the copied object's memory estimator differs; no live shared VAE
        # property or global ComfyUI function is changed.
        scoped_vae = copy.copy(vae)
        scoped_vae.memory_used_decode = lambda shape, dtype: original_estimate(effective_shape(shape), dtype)
        estimate = {
            "input_shape": list(latent.shape),
            "working_shape": effective_shape(latent.shape),
            "dtype": str(vae.vae_dtype),
            "device": str(vae.device),
            "original_estimate_bytes": int(original_estimate(latent.shape, vae.vae_dtype)),
            "tiled_estimate_bytes": int(scoped_vae.memory_used_decode(latent.shape, vae.vae_dtype)),
            "tile_size": tile_size, "overlap": overlap,
            "temporal_size": temporal_size, "temporal_overlap": temporal_overlap,
            "spatial_compression": spatial, "temporal_compression": temporal,
        }
        logging.info("WAN_TILED_VAE_MEMORY " + json.dumps(estimate))
        device = torch.device(vae.device)
        cuda = device.type == "cuda"
        before = {}
        if cuda:
            torch.cuda.synchronize(device)
            before = {
                "allocated_before_bytes": torch.cuda.memory_allocated(device),
                "reserved_before_bytes": torch.cuda.memory_reserved(device),
                "prior_process_peak_allocated_bytes": torch.cuda.max_memory_allocated(device),
            }
            torch.cuda.reset_peak_memory_stats(device)
        started = time.monotonic()
        result = None
        try:
            result = super().decode(
                scoped_vae, samples, tile_size=tile_size, overlap=overlap,
                temporal_size=temporal_size, temporal_overlap=temporal_overlap,
            )
            return result
        finally:
            telemetry = {
                **before, "seconds_including_vae_load": round(time.monotonic()-started, 3),
                "decode_completed": result is not None,
                "memory_measurement": "PyTorch CUDA allocator in this isolated worker; not a driver-wide VRAM measurement.",
            }
            if cuda:
                telemetry.update(
                    peak_allocated_bytes=torch.cuda.max_memory_allocated(device),
                    peak_reserved_bytes=torch.cuda.max_memory_reserved(device),
                    allocated_after_bytes=torch.cuda.memory_allocated(device),
                )
            if result is not None:
                telemetry["image_shape"] = list(result[0].shape)
            logging.info("WAN_TILED_VAE_DONE " + json.dumps(telemetry))


NODE_CLASS_MAPPINGS = {"WanTiledVAEDecode": WanTiledVAEDecode}
NODE_DISPLAY_NAME_MAPPINGS = {"WanTiledVAEDecode": "Wan2.2 Tiled VAE Decode"}
