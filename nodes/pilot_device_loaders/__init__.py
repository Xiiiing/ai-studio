"""Project-local device placement; only the isolated pilot worker loads these nodes."""
import torch
import folder_paths
import comfy.sd
import comfy.utils
import os

if os.environ.get("DRAMA_H3_CHUNKED_INT8") == "1":
    from comfy_kitchen.registry import registry
    from .int8_memory import install
    install(registry, scratch_mib=int(os.environ.get("DRAMA_H3_INT8_SCRATCH_MIB", "64")))

class PilotCLIPOnSecondaryGPU:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'clip_name': (folder_paths.get_filename_list('text_encoders'),)}}
    RETURN_TYPES = ('CLIP',)
    FUNCTION = 'load'
    CATEGORY = 'pilot/device-placement'

    def load(self, clip_name):
        if torch.cuda.device_count() != 2:
            raise RuntimeError('Pilot worker requires exactly two explicitly reserved visible GPUs')
        path = folder_paths.get_full_path_or_raise('text_encoders', clip_name)
        clip = comfy.sd.load_clip(
            ckpt_paths=[path],
            embedding_directory=folder_paths.get_folder_paths('embeddings'),
            clip_type=comfy.sd.CLIPType.MINIMAX,
            model_options={'load_device': torch.device('cuda:1'), 'offload_device': torch.device('cpu')},
        )
        return (clip,)

class PilotVAEOnDevice:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {
            'vae_name': (folder_paths.get_filename_list('vae'),),
            'placement': (['secondary_gpu', 'cpu'],),
        }}
    RETURN_TYPES = ('VAE',)
    FUNCTION = 'load'
    CATEGORY = 'pilot/device-placement'

    def load(self, vae_name, placement):
        if placement == 'secondary_gpu' and torch.cuda.device_count() != 2:
            raise RuntimeError('No explicitly reserved secondary GPU')
        path = folder_paths.get_full_path_or_raise('vae', vae_name)
        state, metadata = comfy.utils.load_torch_file(path, return_metadata=True)
        device = torch.device('cuda:1' if placement == 'secondary_gpu' else 'cpu')
        dtype = torch.float16 if placement == 'secondary_gpu' else torch.float32
        vae = comfy.sd.VAE(sd=state, device=device, dtype=dtype, metadata=metadata)
        return (vae,)

NODE_CLASS_MAPPINGS = {
    'PilotCLIPOnSecondaryGPU': PilotCLIPOnSecondaryGPU,
    'PilotVAEOnDevice': PilotVAEOnDevice,
}
