from __future__ import annotations
import json,subprocess
from pathlib import Path
from .core import ROOT,load_episode,read_json,write_json,sha256,media_receipt,fingerprint
from .pipeline import selected_media,select

def synchronize(slug,ids=None,take="lipsync_01",adopt=False):
    folder,data=load_episode(slug)
    shots=[s for s in data["shots"] if s.get("lip_sync",False)]
    if ids:
        wanted=set(ids);shots=[s for s in shots if s["id"] in wanted]
        if len(shots)!=len(wanted):raise ValueError("Requested shot is missing or not marked lip_sync")
    outputs=[]
    for shot in shots:
        sid=shot["id"];chosen=read_json(folder/"renders"/sid/"selected.json")
        video=selected_media(folder,"renders",sid)
        if chosen.get("origin")=="local_lipsync":
            video=ROOT/chosen["input_video"]["path"]
            if sha256(video)!=chosen["input_video"]["sha256"]:raise RuntimeError("Original video checksum mismatch")
        audio=folder/"audio/lipsync"/(sid+".wav")
        if not audio.exists():raise RuntimeError("Run mix before lipsync: "+str(audio))
        signature=fingerprint({"video":sha256(video),"audio":sha256(audio),"seconds":shot["seconds"],"fps":24,"steps":20,"guidance":1.5,"seed":1247})
        dest=folder/"renders"/sid/take;dest.mkdir(parents=True,exist_ok=True)
        result=dest/"result.json";target=dest/"video.mp4"
        if result.exists():
            record=read_json(result)
            if record.get("graph_sha256")!=signature or sha256(target)!=record["sha256"]:raise RuntimeError("Changed inputs; use a new lip-sync take")
        else:
            if target.exists():raise RuntimeError("Unresolved output exists; inspect its lip-sync receipt")
            argv=[str(ROOT/"envs/lipsync/bin/python"),str(ROOT/"lipsync/sync.py"),"--video",str(video),"--audio",str(audio),"--output",str(target),"--duration",str(shot["seconds"]),"--fps","24","--steps","20","--guidance","1.5","--seed","1247"]
            print("LIPSYNC_STARTED",sid,flush=True)
            run=subprocess.run(argv,cwd=ROOT,capture_output=True,text=True,timeout=7200)
            (dest/"wrapper.stdout.log").write_text(run.stdout)
            (dest/"wrapper.stderr.log").write_text(run.stderr)
            if run.returncode:raise RuntimeError("Lip-sync failed; inspect "+str(dest/"wrapper.stderr.log"))
            receipt=json.loads(run.stdout.strip().splitlines()[-1])
            if receipt.get("state")!="completed":raise RuntimeError("Lip-sync completion not proven")
            if sha256(target)!=receipt["output_sha256"]:raise RuntimeError("Lip-sync result checksum mismatch")
            write_json(dest/"lipsync_receipt.json",receipt)
            record=media_receipt(target,origin="local_lipsync",graph_sha256=signature,input_video=media_receipt(video),input_audio=media_receipt(audio),elapsed_seconds=receipt["elapsed_seconds"],physical_gpus=[5],perceptual_review="required")
            write_json(result,record)
        if adopt:select(folder,"renders",sid,target)
        outputs.append({"shot":sid,"take":take,"selected":adopt,**record})
        print("LIPSYNC_READY",sid,str(target),flush=True)
    return outputs
