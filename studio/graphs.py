from __future__ import annotations

def node(kind,**inputs):return {"class_type":kind,"inputs":inputs}

def flux_image(prompt,seed,width=768,height=1344,references=(),prefix="images/frame"):
    g={
        "1":node("UNETLoader",unet_name="flux-2-klein-4b.safetensors",weight_dtype="default"),
        "2":node("CLIPLoader",clip_name="qwen_3_4b.safetensors",type="flux2",device="default"),
        "3":node("VAELoader",vae_name="flux2-vae.safetensors"),
        "4":node("CLIPTextEncode",clip=["2",0],text=prompt),
        "5":node("EmptyFlux2LatentImage",width=width,height=height,batch_size=1),
        "6":node("BasicGuider",model=["1",0],conditioning=["4",0]),
        "7":node("RandomNoise",noise_seed=seed),
        "8":node("KSamplerSelect",sampler_name="euler"),
        "9":node("Flux2Scheduler",steps=4,width=width,height=height),
        "10":node("SamplerCustomAdvanced",noise=["7",0],guider=["6",0],sampler=["8",0],sigmas=["9",0],latent_image=["5",0]),
        "11":node("VAEDecode",samples=["10",0],vae=["3",0]),
        "12":node("SaveImage",images=["11",0],filename_prefix=prefix)}
    cond=["4",0]
    for i,image in enumerate(references):
        load,enc,ref=map(str,[20+i*3,21+i*3,22+i*3])
        g[load]=node("LoadImage",image=image)
        g[enc]=node("VAEEncode",pixels=[load,0],vae=["3",0])
        g[ref]=node("ReferenceLatent",conditioning=cond,latent=[enc,0])
        cond=[ref,0]
    g["6"]["inputs"]["conditioning"]=cond
    return g

def wan_video(prompt,seed,image,width=720,height=1280,seconds=6,prefix="video/shot",steps=20,profile=None):
    frames=int(round(seconds*24))
    while frames%4!=1:frames+=1
    g={
        "1":node("UNETLoader",unet_name="wan2.2_ti2v_5B_fp16.safetensors",weight_dtype="default"),
        "2":node("CLIPLoader",clip_name="umt5_xxl_fp16.safetensors",type="wan",device="default"),
        "3":node("VAELoader",vae_name="wan2.2_vae.safetensors"),
        "4":node("CLIPTextEncode",clip=["2",0],text=prompt),
        "5":node("CLIPTextEncode",clip=["2",0],text="blurry, distorted face, deformed hands, extra fingers, identity change, plastic skin, oversaturated, text, watermark, subtitles, low quality, frozen image"),
        "6":node("LoadImage",image=image),
        "7":node("Wan22ImageToVideoLatent",vae=["3",0],width=width,height=height,length=frames,batch_size=1,start_image=["6",0]),
        "8":node("ModelSamplingSD3",model=["1",0],shift=8.0),
        "9":node("KSampler",model=["8",0],positive=["4",0],negative=["5",0],latent_image=["7",0],seed=seed,steps=steps,cfg=5.0,sampler_name="uni_pc",scheduler="simple",denoise=1.0),
        "10":node("WanTiledVAEDecode",samples=["9",0],vae=["3",0],tile_size=512,overlap=64,temporal_size=64,temporal_overlap=8),
        "11":node("CreateVideo",images=["10",0],fps=24.0),
        "12":node("SaveVideo",video=["11",0],filename_prefix=prefix,format="mp4",codec="h264")}
    if profile is not None:
        g["20"]=node("LoraLoaderModelOnly",model=["1",0],lora_name=profile["lora"],strength_model=profile.get("strength",1.0))
        g["8"]["inputs"].update(model=["20",0],shift=profile.get("shift",1.0))
        g["21"]=node("ManualSigmas",sigmas=", ".join(map(str,profile["sigmas"])))
        g["22"]=node("BasicGuider",model=["8",0],conditioning=["4",0])
        g["23"]=node("RandomNoise",noise_seed=seed)
        g["24"]=node("KSamplerSelect",sampler_name=profile.get("sampler","euler"))
        g["9"]=node("SamplerCustomAdvanced",noise=["23",0],guider=["22",0],sampler=["24",0],sigmas=["21",0],latent_image=["7",0])
    return g

def h3_portrait(prompt,seed,prefix="benchmark/h3_portrait"):
    return {
        "1":node("UNETLoader",unet_name="minimax_h3_fl2va_pruned_int8_convrot.safetensors",weight_dtype="default"),
        "2":node("MiniMaxH3SigmaShift",model=["1",0],shift_video=12.0,shift_audio=3.0),
        "3":node("PilotCLIPOnSecondaryGPU",clip_name="qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"),
        "4":node("PilotVAEOnDevice",vae_name="minimax_h3_video_vae_fp16.safetensors",placement="secondary_gpu"),
        "6":node("MiniMaxH3ImageToVideo",clip=["3",0],vae=["4",0],prompt=prompt,width=768,height=1344,length=5),
        "7":node("BasicGuider",model=["2",0],conditioning=["6",0]),
        "8":node("RandomNoise",noise_seed=seed),
        "9":node("KSamplerSelect",sampler_name="euler"),
        "10":node("BasicScheduler",model=["2",0],scheduler="simple",steps=20,denoise=1.0),
        "11":node("SamplerCustomAdvanced",noise=["8",0],guider=["7",0],sampler=["9",0],sigmas=["10",0],latent_image=["6",1]),
        "12":node("LTXVSeparateAVLatent",av_latent=["11",1]),
        "13":node("VAEDecodeTiled",samples=["12",0],vae=["4",0],tile_size=256,overlap=64,temporal_size=32,temporal_overlap=8),
        "14":node("ImageFromBatch",image=["13",0],batch_index=0,length=1),
        "15":node("SaveImage",images=["14",0],filename_prefix=prefix)}


def h3_video(prompt, seed, image, width=352, height=640, seconds=6, prefix="video/shot"):
    """Wkp's original turbo4 graph, with a supplied first frame and MP4 output."""
    from .h3 import UNET, CLIP, VAE, LORA, frame_count
    if any(type(v) is not int or not 128 <= v <= 1536 or v % 32 for v in (width, height)) or width * height > 768 * 1344:
        raise ValueError("MiniMax H3 canvas must align to 32 pixels within 768*1344")
    if not isinstance(image, str) or not image:
        raise ValueError("MiniMax H3 video requires an approved first frame")
    return {
        "1": node("UNETLoader", unet_name=UNET, weight_dtype="default"),
        "17": node("LoraLoaderModelOnly", model=["1", 0], lora_name=LORA, strength_model=1.0),
        "2": node("MiniMaxH3SigmaShift", model=["17", 0], shift_video=12.0, shift_audio=3.0),
        "3": node("PilotCLIPOnSecondaryGPU", clip_name=CLIP),
        "4": node("PilotVAEOnDevice", vae_name=VAE, placement="secondary_gpu"),
        "18": node("LoadImage", image=image),
        "6": node("MiniMaxH3ImageToVideo", clip=["3", 0], vae=["4", 0], prompt=prompt,
                  width=width, height=height, length=frame_count(seconds), first_frame=["18", 0]),
        "7": node("BasicGuider", model=["2", 0], conditioning=["6", 0]),
        "8": node("RandomNoise", noise_seed=seed),
        "9": node("KSamplerSelect", sampler_name="euler"),
        "10": node("BasicScheduler", model=["2", 0], scheduler="simple", steps=4, denoise=1.0),
        "11": node("SamplerCustomAdvanced", noise=["8", 0], guider=["7", 0], sampler=["9", 0],
                   sigmas=["10", 0], latent_image=["6", 1]),
        "12": node("LTXVSeparateAVLatent", av_latent=["11", 1]),
        "13": node("VAEDecodeTiled", samples=["12", 0], vae=["4", 0], tile_size=256,
                   overlap=64, temporal_size=32, temporal_overlap=8),
        "15": node("CreateVideo", images=["13", 0], fps=24.0, bit_depth=8),
        "16": node("SaveVideo", video=["15", 0], filename_prefix=prefix, format="mp4", codec="auto"),
    }
