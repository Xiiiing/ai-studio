from __future__ import annotations
import json,mimetypes,re,subprocess,threading,time,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote,urlsplit
from .core import ROOT,episode_path,load_episode,read_json,write_json,within,sha256

JOBS={};LOCK=threading.Lock()
ALLOWED={"images","video","voice","mix","lipsync","assemble","verify"}
def episode_state(slug):
    folder,data=load_episode(slug);shots=[]
    for shot in data["shots"]:
        row={"id":shot["id"],"seconds":shot["seconds"],"dialogue":shot.get("dialogue",[]),"prompt":shot["prompt"],"lip_sync":shot.get("lip_sync",False)}
        for kind,base in [("image","assets/shots"),("video","renders")]:
            ref=folder/base/shot["id"]/"selected.json"
            if ref.exists():
                chosen=read_json(ref);path=within(ROOT,chosen["path"])
                row[kind]={"path":str(path.relative_to(ROOT)),"sha256":chosen["sha256"],"origin":chosen.get("origin","local_inference")} if path.exists() else None
            else:row[kind]=None
        shots.append(row)
    release=folder/"release"
    state_path=folder/"quality/release_state.json";manifest_path=release/"manifest.json"
    ready=False
    if state_path.exists() and manifest_path.exists():
        state=read_json(state_path)
        ready=state.get("status")=="verified" and state.get("manifest_sha256")==sha256(manifest_path)
    files=[{"name":x.name,"path":str(x.relative_to(ROOT)),"bytes":x.stat().st_size} for x in sorted(release.glob("*")) if x.is_file() and x.suffix in {".mp4",".jpg",".srt",".ass",".json",".zip"} and (ready or x.suffix not in {".mp4",".jpg"})]
    for name in ["technical_report.json","visual_review.json"]:
        path=folder/"quality"/name
        if path.exists():files.append({"name":name,"path":str(path.relative_to(ROOT)),"bytes":path.stat().st_size})
    return {"slug":slug,"title":data["title"],"logline":data.get("logline",""),"duration":sum(x["seconds"] for x in data["shots"]),"shots":shots,"files":files,"definition":str((folder/"episode.json").relative_to(ROOT))}
def jobs_state():
    out=[]
    for path in sorted((ROOT/"runtime/ui_jobs").glob("*/state.json"),reverse=True)[:12]:
        state=read_json(path);log=path.parent/"output.log"
        if log.exists():
            with log.open("rb") as f:f.seek(max(0,log.stat().st_size-4000));state["tail"]=f.read().decode(errors="replace")
        out.append(state)
    return out
def launch(payload):
    stage=payload.get("stage");slug=payload.get("episode")
    if stage not in ALLOWED:raise ValueError("Unknown production stage")
    folder,data=load_episode(slug);take=payload.get("take") or time.strftime("take_%Y%m%d_%H%M%S",time.gmtime())
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,60}",take):raise ValueError("Invalid take name")
    argv=[str(ROOT/".venv/bin/python"),"-u",str(ROOT/"drama.py"),stage,slug]
    if stage in {"images","video"}:
        cards=str(payload.get("gpus","1,2" if stage=="images" else "1,2,3,4"))
        if not re.fullmatch(r"[0-7](,[0-7])*",cards) or len(set(cards.split(",")))!=len(cards.split(",")):raise ValueError("GPU list must contain distinct indices 0 to 7")
        argv+=["--gpus",cards,"--take",take]
    if stage=="video":argv+=["--profile",payload.get("profile","quality")]
    if stage=="lipsync":argv+=["--take",take]
    if stage in {"images","video","lipsync"} and payload.get("shots"):
        ids=payload["shots"]
        if not isinstance(ids,list) or not set(ids)<={x["id"] for x in data["shots"]}:raise ValueError("Unknown shot selection")
        argv+=["--shots",*ids]
    with LOCK:
        if any(j.poll() is None for j in JOBS.values()):raise RuntimeError("A dashboard production job is already running")
        jid=time.strftime("%Y%m%dT%H%M%S",time.gmtime())+"_"+uuid.uuid4().hex[:8]
        dest=ROOT/"runtime/ui_jobs"/jid;dest.mkdir(parents=True)
        handle=(dest/"output.log").open("w")
        child=subprocess.Popen(argv,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=handle,stderr=subprocess.STDOUT)
        JOBS[jid]=child
        state={"id":jid,"episode":slug,"stage":stage,"take":take,"state":"running","started":time.time(),"pid":child.pid,"argv":argv}
        write_json(dest/"state.json",state)
        def finish():
            code=child.wait();handle.close();state.update(state="completed" if code==0 else "failed",exit_code=code,finished=time.time());write_json(dest/"state.json",state)
        threading.Thread(target=finish,daemon=True).start()
    return state

class Handler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args):print(time.strftime("%Y-%m-%d %H:%M:%S"),fmt%args,flush=True)
    def json(self,obj,status=200):
        payload=json.dumps(obj,ensure_ascii=False).encode();self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8");self.send_header("Content-Length",str(len(payload)));self.send_header("Cache-Control","no-store");self.end_headers();self.wfile.write(payload)
    def do_GET(self):
        try:
            route=unquote(urlsplit(self.path).path)
            if route=="/api/health":return self.json({"service":"drama-studio","root":str(ROOT),"status":"ready"})
            if route=="/api/episodes":
                rows=[]
                for p in sorted((ROOT/"production").glob("*/episode.json")):
                    try:
                        d=read_json(p);rows.append({"slug":p.parent.name,"title":d["title"]})
                    except (ValueError,KeyError):continue
                return self.json(rows)
            if route.startswith("/api/episode/"):return self.json(episode_state(route.rsplit("/",1)[1]))
            if route=="/api/jobs":return self.json(jobs_state())
            if route=="/":
                episodes=[]
                for path in sorted((ROOT/"production").glob("*/episode.json")):
                    info=read_json(path);episodes.append({"slug":path.parent.name,"title":info["title"]})
                slug="the-last-stop" if any(x["slug"]=="the-last-stop" for x in episodes) else episodes[0]["slug"] if episodes else None
                initial={"episodes":episodes,"episode":episode_state(slug) if slug else None}
                payload=json.dumps(initial,ensure_ascii=False).replace("<","\\u003c")
                markup=(ROOT/"ui/index.html").read_text().replace("<script>",'<script type="application/json" id="boot-data">'+payload+"</script><script>",1).encode()
                self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.send_header("Content-Length",str(len(markup)));self.send_header("Cache-Control","no-store");self.end_headers();self.wfile.write(markup);return
            if route.startswith("/media/"):
                path=within(ROOT,route[len("/media/"):])
                if not path.is_relative_to(ROOT/"production") or path.suffix.lower() not in {".png",".jpg",".jpeg",".mp4",".wav",".srt",".ass",".json",".md",".csv",".zip"}:raise ValueError("File is outside the production media set")
                return self.send_file(path)
            self.json({"error":"Not found"},404)
        except (ValueError,KeyError,FileNotFoundError) as e:self.json({"error":str(e)},400)
        except (BrokenPipeError,ConnectionResetError):pass
    def do_POST(self):
        try:
            origin=self.headers.get("Origin")
            if origin and origin not in {"http://127.0.0.1:8787","http://localhost:8787"}:return self.json({"error":"Origin rejected"},403)
            if self.headers.get("Content-Type","").split(";")[0]!="application/json":return self.json({"error":"JSON required"},415)
            size=int(self.headers.get("Content-Length","0"))
            if not 0<size<=8192:raise ValueError("Invalid request length")
            payload=json.loads(self.rfile.read(size))
            if urlsplit(self.path).path!="/api/start":return self.json({"error":"Not found"},404)
            self.json(launch(payload),202)
        except (ValueError,KeyError,FileNotFoundError,RuntimeError) as e:self.json({"error":str(e)},409)
    def send_file(self,path):
        if not path.is_file():return self.json({"error":"File not ready"},404)
        size=path.stat().st_size;start=0;end=size-1;partial=False
        header=self.headers.get("Range")
        if header:
            match=re.fullmatch(r"bytes=(\d*)-(\d*)",header)
            if not match:return self.json({"error":"Unsupported range"},416)
            a,b=match.groups()
            if a:start=int(a);end=min(end,int(b)) if b else end
            elif b:start=max(0,size-int(b))
            else:return self.json({"error":"Empty range"},416)
            if start>end or start>=size:return self.json({"error":"Range outside file"},416)
            partial=True
        self.send_response(206 if partial else 200);self.send_header("Content-Type",mimetypes.guess_type(str(path))[0] or "application/octet-stream");self.send_header("Content-Length",str(end-start+1));self.send_header("Accept-Ranges","bytes")
        self.send_header("X-Content-Type-Options","nosniff")
        if partial:self.send_header("Content-Range",f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as source:
            source.seek(start);remaining=end-start+1
            while remaining>0:
                block=source.read(min(256*1024,remaining))
                if not block:break
                self.wfile.write(block);remaining-=len(block)

if __name__=="__main__":
    server=ThreadingHTTPServer(("127.0.0.1",8787),Handler)
    print("DRAMA_STUDIO_READY http://127.0.0.1:8787",flush=True)
    server.serve_forever()
