"""Bounded live observations; installed, reachable, and verified are distinct."""
from __future__ import annotations

import copy
import json
import subprocess
import threading
import time
from pathlib import Path

from .providers import check_model, model_matches
from .video_providers import resolve_video, probe_video_profile
from studio.core import ROOT, COMFY
from .config import DATA, load_config, resolve_llm

_lock=threading.Lock()
_cached=None
_last=0.0
_refreshing=False


def _model_files(paths):
    return all((ROOT/p).is_file() for p in paths)


def _observe(config):
    llm=resolve_llm(config,"screenwriter")
    visual=resolve_llm(config,"visual_critic")
    info={"llm":{"available":False,"model":llm["model"],"message":"模型接口待检查","provider":llm.get("provider"),"network_mode":llm.get("network_mode")},
          "research":{"available":True,"model":"Google News RSS","message":"实时公开报道检索；逐项目保留来源，不将报道等同于热榜"},
          "image":{"available":_model_files(["models/diffusion_models/flux-2-klein-4b.safetensors",
                    "models/text_encoders/qwen_3_4b.safetensors","models/vae/flux2-vae.safetensors"]),
                   "model":"FLUX.2 klein 4B","message":"本地权重与ComfyUI工作器；按空闲显存调度"},
          "video":{"available":_model_files(["models/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors"]),
                   "model":"Wan2.2 TI2V 5B","message":"真人/二维动画使用图生视频；动态漫使用关键帧与程序运镜"},
          "audio":{"available":(ROOT/"envs/tts/bin/python").is_file() and (ROOT/"models/tts/Qwen3-TTS-12Hz-1.7B-CustomVoice").is_dir(),
                   "model":"Qwen3-TTS 1.7B","message":"本地中文角色配音，合成后检查音轨和时长"},
          "editor":{"available":True,"model":"FFmpeg / PyAV","message":"剪辑、混音、字幕、版本化MP4与完整解码检查"},
          "quality":{"available":False,"model":visual.get("model","VLM")+" + 确定性媒体检查","message":"语义判定与技术检查分别记录，VBench未启用"},
          "optimizer":{"available":True,"model":"有界反馈返修","message":"按实测错误修改提示词并重生成；DSPy离线优化尚未启用"},
          "gpus":[],"observed_at":time.time(),"orchestrator":"LangGraph + SQLite WAL",
          "worker":{"available":False},"lipsync":{"available":False,"model":"LatentSync 1.6","message":"保留旧管线；需要可独占且显存充足的GPU5"}}
    try:
        models=check_model(llm,timeout=3)
        if models.get("discovery")=="skipped":
            info["llm"].update(available=None,status="unverified",message="模型已配置；未探测目录，实际调用能力以执行回执为准")
        else:
            info["llm"].update(available=True,message="已连通配置的模型接口；实际内容质量按逐次调用和评审回执记录")
    except Exception as exc:
        info["llm"]["message"]=str(exc)[:220]
    try:
        visual_models=models if visual==llm and info["llm"].get("available") is not False else check_model(visual,timeout=3)
        if visual_models.get("discovery")=="skipped":
            info["quality"].update(available=None,status="unverified",message="视觉模型已配置；语义质量由实际评审回执确认")
        else:
            info["quality"]["available"]=any(model_matches(visual,m.get("id")) for m in visual_models.get("data",[]))
    except Exception:
        info["quality"]["available"]=False
    video=resolve_video(config)
    if video["provider"]=="minimax":
        preflight=probe_video_profile(video["profile"],config)
        info["video"].update(available=None,status=preflight["status"],configured=preflight["status"]=="configured",
                             model=video["model"],provider=video["provider"],profile=video["profile"],
                             message="已选择云视频连接；任务提交、生成和下载由实际回执确认。动态漫仍使用程序运镜。")
    try:
        p=subprocess.run(["nvidia-smi","--query-gpu=index,name,memory.total,memory.free,utilization.gpu","--format=csv,noheader,nounits"],
                         capture_output=True,text=True,timeout=45,check=True)
        for line in p.stdout.strip().splitlines():
            idx,name,total,free,util=[x.strip() for x in line.split(",")]
            info["gpus"].append({"index":int(idx),"name":name,"total_mb":int(total),
                                 "free_mb":int(free),"utilization":int(util)})
        gpu5=next((g for g in info["gpus"] if g["index"]==5),None)
        if gpu5 and gpu5["free_mb"]>=21500 and gpu5["utilization"]<10:
            info["lipsync"]["available"]=True
    except Exception as exc:
        info["gpu_message"]="GPU快照暂时不可用："+str(exc)[:150]
    state=DATA/"worker.json"
    if state.exists():
        try:
            data=json.loads(state.read_text())
            info["worker"]={**data,"available":data.get("status")=="running" and time.time()-data.get("heartbeat",0)<45}
        except (OSError,ValueError):
            pass
    return info


def _refresh():
    global _cached,_last,_refreshing
    try:
        value=_observe(load_config())
    except Exception as exc:
        value={"gpus":[],"observed_at":time.time(),"observation_error":str(exc)[:220]}
    with _lock:
        _cached=value
        _last=time.monotonic()
        _refreshing=False


def snapshot(force=False):
    """Return promptly; health probes never block project loading or decisions."""
    global _refreshing
    with _lock:
        if not _refreshing and (force or _cached is None or time.monotonic()-_last>60):
            _refreshing=True
            threading.Thread(target=_refresh,name="factory-capability-probe",daemon=True).start()
        value=copy.deepcopy(_cached or {
            "gpus":[],"observed_at":None,"orchestrator":"LangGraph + SQLite WAL",
            "observation_message":"资源探测正在后台进行；项目与决策可正常使用"})
        value["refreshing"]=_refreshing
        return value
