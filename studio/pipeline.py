from __future__ import annotations
import concurrent.futures, csv, json, os, subprocess, time
from pathlib import Path
from .core import ROOT, load_episode, write_json, read_json, sha256, within
from .comfy import ComfyWorker
from .graphs import flux_image, wan_video

STYLE="Photorealistic cinematic production still, grounded contemporary Chinese science-fiction drama. Natural textured skin, believable anatomy, restrained film color, cool cyan ambient light and soft amber light on skin, cinematic shallow depth of field, elegant vertical composition."
MASTER={
"female":"Medium close-up of Lin Xia, a 28-year-old Chinese woman with a clean jaw-length straight black bob, natural dark brown eyes and an oval face, wearing a simple deep navy trench coat over an ivory knit sweater. She sits in a nearly empty modern metro carriage at night, looking gently to the right. Both shoulders visible, relaxed closed lips, softly tired eyes. Plain brushed metal panels and seats behind her.",
"male":"Medium close-up of Lin Zhou, a 58-year-old Chinese man, short salt-and-pepper hair, clean-shaven face, warm dark brown eyes and realistic age lines, wearing a charcoal wool cardigan over a light gray shirt. He sits in a nearly empty modern metro carriage at night, looking gently to the left. Both shoulders visible, relaxed closed lips and a kind steady expression. Plain brushed metal panels and seats behind him."
}

def selected_media(folder,kind,sid):
    record=folder/kind/sid/"selected.json"
    if not record.exists():raise FileNotFoundError("No selected take: "+str(record))
    item=read_json(record);p=within(ROOT,item["path"])
    if not p.is_file() or sha256(p)!=item["sha256"]:raise RuntimeError("Selected media hash mismatch: "+str(record))
    return p

def select(folder,kind,sid,target):
    result=read_json(Path(target).parent/"result.json")
    write_json(folder/kind/sid/"selected.json",result)

def make_images(slug,cards=(1,2),take="take_01",ids=None):
    import threading
    folder,data=load_episode(slug)
    shots=data["shots"]
    if ids:
        wanted=set(ids);shots=[shot for shot in shots if shot["id"] in wanted]
        if len(shots)!=len(wanted):raise ValueError("Unknown shot id")
    # References may point forward in the edit, so schedule their source first.
    requested={s["id"] for s in shots};pending=list(shots);ordered=[];done=set()
    while pending:
        ready=next((s for s in pending if set(s.get("reference_shots",[])) & requested <= done),None)
        if ready is None:raise ValueError("Cyclic image reference dependency")
        for ref in set(ready.get("reference_shots",[]))-requested:selected_media(folder,"assets/shots",ref)
        pending.remove(ready);ordered.append(ready);done.add(ready["id"])
    shots=ordered
    width,height=data.get("generation",{}).get("width",736),data.get("generation",{}).get("height",1312)
    style=data.get("visual_style","Photorealistic cinematic production still. Natural textured skin, believable anatomy, restrained film color, cinematic depth of field, elegant vertical composition.")
    cast=data.get("cast",{})
    if not cast:raise ValueError("episode.cast must define character reference descriptions")
    missing=[]
    for idx,(role,description) in enumerate(cast.items()):
        if (folder/"assets/characters"/role/"selected.json").exists():selected_media(folder,"assets/characters",role)
        else:missing.append((idx,role,description))
    if missing:
        with ComfyWorker([cards[0]],8281,"images_master") as worker:
            for idx,role,description in missing:
                prompt="Medium close-up photographic character reference, both shoulders visible, relaxed closed lips, uncluttered background. "+description
                graph=flux_image(style+" "+prompt,2026100901+idx,width,height,prefix="characters/"+role)
                image=worker.execute(graph,folder/"assets/characters"/role/take,"image",timeout=1800)
                select(folder,"assets/characters",role,image)
    events={s["id"]:threading.Event() for s in shots};aborted=threading.Event()
    def batch(card,rows):
        try:
            with ComfyWorker([card],8282+card,"images_"+str(card)) as worker:
                for shot in rows:
                    for ref in set(shot.get("reference_shots",[])) & requested:
                        while not events[ref].wait(1):
                            if aborted.is_set():return
                    if aborted.is_set():return
                    group=shot["reference_group"]
                    roles=["male"] if group=="male" else ["female","male"] if group=="two" else ["female"]
                    if shot.get("reference_mode")=="scene":roles=[]
                    names=[worker.copy_input(selected_media(folder,"assets/characters",role)) for role in roles]
                    identity="Use the exact identity, haircut and clothing of the person in reference image 1. "
                    if len(roles)==2:identity="Use the woman from reference image 1 and the man from reference image 2, preserving both identities, haircuts and clothing. "
                    for ref in shot.get("reference_shots",[]):names.append(worker.copy_input(selected_media(folder,"assets/shots",ref)))
                    if shot.get("reference_mode")=="scene":identity="Edit the exact scene shown in reference image 1, preserving the existing scene, subjects, props, proportions and lighting. Apply only the composition and action changes requested below. "
                    elif shot.get("reference_shots"):identity+="Additional reference images establish exact prop and set continuity; preserve their designs and proportions in the requested new composition. "
                    prompt=style+" "+identity+"Create this new camera composition as a single film still: "+shot["prompt"]
                    graph=flux_image(prompt,shot.get("seed",2026101000+int(shot["id"][1:])),width,height,names,prefix="shots/"+shot["id"])
                    image=worker.execute(graph,folder/"assets/shots"/shot["id"]/take,"image",timeout=1800)
                    select(folder,"assets/shots",shot["id"],image);events[shot["id"]].set()
        except BaseException:
            aborted.set()
            for event in events.values():event.set()
            raise
    used=list(cards)[:min(len(cards),len(shots))]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(used)) as pool:
        futures=[pool.submit(batch,card,shots[i::len(used)]) for i,card in enumerate(used)]
        for f in concurrent.futures.as_completed(futures):f.result()

def make_video(slug,ids=None,cards=(1,2,3,4),take="take_01",profile="quality"):
    folder,data=load_episode(slug);shots=data["shots"]
    if ids:
        wanted=set(ids);shots=[s for s in shots if s["id"] in wanted]
        if len(shots)!=len(wanted):raise ValueError("Unknown shot id")
    fast=read_json(ROOT/"workflows"/"fastwan_profile.json") if profile=="fastwan" else None
    if profile not in ["quality","fastwan"]:raise ValueError("Unknown video profile")
    def batch(card,rows):
        with ComfyWorker([card],8300+card,"video_"+str(card)) as worker:
            for s in rows:
                image=selected_media(folder,"assets/shots",s["id"])
                name=worker.copy_input(image)
                prompt=s.get("motion_prompt",s["prompt"])+" Subtle natural breathing and coherent small movements. The shot retains the reference face, clothing, lighting and framing throughout."
                if not s.get("lip_sync",False):prompt+=" Any visible face listens silently with relaxed, gently closed lips; the scene has no visible speaking articulation."
                graph=wan_video(prompt,s.get("seed",2026102000+int(s["id"][1:])),name,width=data["generation"]["width"],height=data["generation"]["height"],seconds=s["seconds"],prefix=s["id"]+"/"+take,steps=20,profile=fast)
                target=worker.execute(graph,folder/"renders"/s["id"]/take,"video",timeout=7200)
                select(folder,"renders",s["id"],target)
    used=list(cards)[:min(len(cards),len(shots))]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(used)) as pool:
        futures=[pool.submit(batch,card,shots[i::len(used)]) for i,card in enumerate(used)]
        for f in concurrent.futures.as_completed(futures):f.result()

def voice(slug):
    folder,data=load_episode(slug)
    lines=[line for s in data["shots"] for line in s.get("dialogue",[])]
    write_json(folder/"dialogue.json",lines)
    argv=[str(ROOT/"envs/tts/bin/python"),str(ROOT/"audio/tts.py"),"--input",str(folder/"dialogue.json"),"--output-dir",str(folder/"audio/dialogue"),"--device","cuda:0"]
    if (folder/"voices.json").exists():argv+=["--voices",str(folder/"voices.json")]
    subprocess.run(argv,cwd=ROOT,check=True)

def status(slug):
    folder,data=load_episode(slug);out={"title":data["title"],"duration_plan":sum(s["seconds"] for s in data["shots"]),"shots":[]}
    for s in data["shots"]:
        out["shots"].append({"id":s["id"],"image":(folder/"assets/shots"/s["id"]/"selected.json").exists(),"video":(folder/"renders"/s["id"]/"selected.json").exists(),"voice":all((folder/"audio/dialogue"/(d["id"]+".wav")).exists() for d in s.get("dialogue",[]))})
    return out
