from __future__ import annotations
import fcntl, json, os, shutil, socket, subprocess, time, urllib.error, urllib.request, uuid
from pathlib import Path
from .core import ROOT, COMFY, GPU_LOCK_DIR, write_json, fingerprint, media_receipt, within

def api(base,route,payload=None,timeout=40):
    data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode()
    request=urllib.request.Request(base+route,data=data,headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(request,timeout=timeout) as r:return json.load(r)

def gpu_snapshot(cards=None):
    selector=["-i",",".join(map(str,cards))] if cards is not None else []
    p=subprocess.run(["nvidia-smi",*selector,"--query-gpu=index,memory.free,utilization.gpu","--format=csv,noheader,nounits"],capture_output=True,text=True,check=True,timeout=120)
    return {int(a):{"free_mib":int(b),"utilization":int(c)} for a,b,c in (line.split(",") for line in p.stdout.strip().splitlines())}

class GpuLease:
    def __init__(self,cards,min_free=21500):self.cards=list(cards);self.min_free=min_free;self.files=[]
    def __enter__(self):
        # Same advisory lock namespace as the pre-existing local H3 workers.
        locks=GPU_LOCK_DIR
        locks.mkdir(parents=True,exist_ok=True)
        try:
            for card in sorted(self.cards):
                f=(locks/f"gpu{card}.lock").open("a+")
                try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:f.close();raise RuntimeError(f"GPU {card} is leased by another production")
                self.files.append(f)
            snap=gpu_snapshot(self.cards)
            for card in self.cards:
                if snap[card]["free_mib"]<self.min_free or snap[card]["utilization"]>=10:raise RuntimeError(f"GPU {card} is occupied; existing job preserved")
            return self
        except BaseException:self.__exit__(None,None,None);raise
    def __exit__(self,*args):
        for f in self.files:fcntl.flock(f,fcntl.LOCK_UN);f.close()
        self.files=[]

class GpuStartupLock:
    """Serialize CUDA/NVML cold starts; generation itself remains parallel."""
    def __enter__(self):
        path=ROOT/"runtime/gpu_startup.lock";path.parent.mkdir(exist_ok=True)
        self.file=path.open("a+");deadline=time.monotonic()+600
        while True:
            try:fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB);return self
            except BlockingIOError:
                if time.monotonic()>=deadline:self.file.close();raise TimeoutError("Another GPU service is still starting")
                time.sleep(.5)
    def __exit__(self,*args):
        fcntl.flock(self.file,fcntl.LOCK_UN);self.file.close()

class ComfyWorker:
    def __init__(self,cards,port,tag,precision="fp16"):
        self.cards=list(cards);self.port=port;self.tag=tag;self.precision=precision
        self.folder=ROOT/"runtime"/"workers"/tag
        self.base=f"http://127.0.0.1:{port}";self.child=None;self.log=None;self.lease=None
    def __enter__(self):
        with GpuStartupLock():return self._start()
    def _start(self):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1",self.port))==0:raise RuntimeError(f"Port {self.port} is in use")
        self.lease=GpuLease(self.cards);self.lease.__enter__()
        try:
            for name in ["input","output","temp","user"]: (self.folder/name).mkdir(parents=True,exist_ok=True)
            extra=self.folder/"models.yaml"
            extra.write_text("drama_studio:\n  base_path: "+str(ROOT/"models")+"\n  diffusion_models: diffusion_models\n  text_encoders: text_encoders\n  vae: vae\n  loras: loras\n  upscale_models: upscale_models\n  custom_nodes: ../nodes\n")
            env=os.environ.copy();env.update(CUDA_VISIBLE_DEVICES=",".join(map(str,self.cards)),OMP_NUM_THREADS="6",MKL_NUM_THREADS="6",PYTHONUNBUFFERED="1")
            argv=[str(COMFY/".venv/bin/python"),str(COMFY/"main.py"),"--listen","127.0.0.1","--port",str(self.port),"--disable-auto-launch","--disable-dynamic-vram","--reserve-vram","1.5","--use-pytorch-cross-attention","--disable-api-nodes","--preview-method","none","--extra-model-paths-config",str(extra),"--output-directory",str(self.folder/"output"),"--input-directory",str(self.folder/"input"),"--temp-directory",str(self.folder/"temp"),"--user-directory",str(self.folder/"user"),"--database-url","sqlite:///"+str(self.folder/"comfyui.db")]
            if self.precision=="fp16":argv+=["--fp16-unet","--fp16-text-enc","--fp32-vae"]
            self.log=(self.folder/"comfy.log").open("a")
            self.child=subprocess.Popen(argv,cwd=COMFY,env=env,stdout=self.log,stderr=subprocess.STDOUT)
            write_json(self.folder/"launch.json",{"argv":argv,"physical_gpus":self.cards,"pid":self.child.pid,"started":time.time()})
            deadline=time.monotonic()+240
            while time.monotonic()<deadline:
                if self.child.poll() is not None:raise RuntimeError("Comfy startup failed: "+str(self.folder/"comfy.log"))
                try:api(self.base,"/system_stats");self.info=api(self.base,"/object_info");break
                except (OSError,urllib.error.URLError,ValueError):time.sleep(2)
            else:raise TimeoutError("Comfy startup deadline")
            print("WORKER_READY",self.tag,self.cards,flush=True);return self
        except BaseException:self.__exit__(None,None,None);raise
    def __exit__(self,*args):
        if self.child is not None and self.child.poll() is None:
            self.child.terminate()
            try:self.child.wait(timeout=35)
            except subprocess.TimeoutExpired:self.child.kill();self.child.wait(timeout=30)
        if self.log:self.log.close()
        if self.lease:self.lease.__exit__(*args)
    def copy_input(self,path):
        path=Path(path);name=fingerprint({"path":str(path),"size":path.stat().st_size,"mtime":path.stat().st_mtime_ns})[:20]+path.suffix
        shutil.copy2(path,self.folder/"input"/name);return name
    def execute(self,graph,destination,kind,timeout=7200):
        dest=Path(destination);dest.mkdir(parents=True,exist_ok=True)
        signature=fingerprint(graph);receipt=dest/"submission.json";result=dest/"result.json"
        if result.exists():
            saved=json.loads(result.read_text())
            if saved["graph_sha256"]!=signature:raise RuntimeError("Changed graph: select a new take")
            candidate=ROOT/saved["path"]
            from .core import sha256
            if not candidate.exists() or sha256(candidate)!=saved["sha256"]:raise RuntimeError("Cached media does not match its receipt")
            print("TAKE_REUSED",candidate,flush=True);return candidate
        if receipt.exists():raise RuntimeError("Existing unresolved submission; inspect it before selecting a new take: "+str(receipt))
        missing={n["class_type"] for n in graph.values()}-set(self.info)
        if missing:raise RuntimeError("Missing Comfy nodes: "+str(sorted(missing)))
        write_json(dest/"workflow_api.json",graph)
        write_json(receipt,{"state":"dispatching","graph_sha256":signature,"worker":self.tag,"submitted_at":time.time()})
        try:
            answer=api(self.base,"/prompt",{"prompt":graph,"client_id":"drama-studio-"+uuid.uuid4().hex})
        except urllib.error.HTTPError as e:
            body=e.read().decode(errors="replace")
            write_json(dest/"submission_error.json",{"status":e.code,"body":body});raise
        write_json(receipt,{"state":"accepted","graph_sha256":signature,**answer,"worker":self.tag,"submitted_at":time.time()})
        prompt_id=answer["prompt_id"];start=time.monotonic();deadline=start+timeout
        print("SUBMITTED",dest.name,prompt_id,flush=True)
        last_report=0
        while time.monotonic()<deadline:
            if self.child.poll() is not None:raise RuntimeError("Owned Comfy process exited; receipt retained")
            history=api(self.base,"/history/"+prompt_id).get(prompt_id)
            if history:
                state=history.get("status",{}).get("status_str")
                if state in ["success","error"]:
                    write_json(dest/"history.json",history)
                    if state=="error":raise RuntimeError("Generation failed; inspect "+str(dest/"history.json"))
                    break
            if time.monotonic()-last_report>=45:
                print("RENDERING",dest.name,"elapsed",round(time.monotonic()-start),"s",flush=True);last_report=time.monotonic()
            time.sleep(4)
        else:raise TimeoutError("Generation timeout; submission receipt retained")
        matches=[]
        extensions=[".png",".jpg",".webp"] if kind=="image" else [".mp4",".webm",".mov"]
        for output in history.get("outputs",{}).values():
            for key in ["images","gifs","videos"]:
                for file in output.get(key,[]):
                    if Path(file["filename"]).suffix.lower() in extensions and file.get("type","output")=="output":
                        matches.append(within(self.folder/"output",Path(file.get("subfolder",""))/file["filename"]))
        if len(matches)!=1:raise RuntimeError(f"Expected one {kind}, found {len(matches)}")
        source=matches[0];target=dest/("image.png" if kind=="image" else "video.mp4")
        shutil.copy2(source,target)
        saved=media_receipt(target,graph_sha256=signature,prompt_id=prompt_id,elapsed_seconds=round(time.monotonic()-start,2),physical_gpus=self.cards,origin="new_local_inference",source_output=str(source))
        write_json(result,saved);print("TAKE_READY",str(target),saved["elapsed_seconds"],flush=True);return target
