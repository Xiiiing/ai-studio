from __future__ import annotations
import argparse,json,os,signal,subprocess,sys,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from studio.core import read_json,write_json
STATE=ROOT/"runtime/web_service.json"
def health():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8787/api/health",timeout=2) as r:data=json.load(r)
        if data.get("service")=="drama-studio" and data.get("root")==str(ROOT):return data
    except (OSError,ValueError):pass
    return None
def owned(pid):
    path=Path("/proc")/str(pid)
    try:return b"studio.web" in (path/"cmdline").read_bytes() and (path/"cwd").resolve()==ROOT
    except OSError:return False
def main():
    p=argparse.ArgumentParser();p.add_argument("action",choices=["start","status","stop"]);a=p.parse_args()
    if a.action=="status":print(json.dumps({"health":health(),"process":read_json(STATE) if STATE.exists() else None},ensure_ascii=False));return
    if a.action=="stop":
        if not STATE.exists():print("Not running");return
        state=read_json(STATE)
        if owned(state["pid"]):
            for f in (ROOT/"runtime/ui_jobs").glob("*/state.json"):
                job=read_json(f)
                if job.get("state")=="running" and (Path("/proc")/str(job["pid"])).exists():raise RuntimeError("A dashboard job is active; let it finish before stopping the dashboard")
            os.kill(state["pid"],signal.SIGTERM)
            for _ in range(50):
                if not owned(state["pid"]):break
                time.sleep(.1)
            if owned(state["pid"]):raise RuntimeError("Owned service has not exited yet")
        state["stopped"]=time.time();write_json(STATE,state);print("Owned dashboard stopped");return
    if health():print("READY http://127.0.0.1:8787");return
    if STATE.exists() and owned(read_json(STATE)["pid"]):raise RuntimeError("Owned service is starting or unhealthy; inspect runtime/web_service.log")
    (ROOT/"runtime").mkdir(exist_ok=True)
    with (ROOT/"runtime/web_service.log").open("a") as log:
        child=subprocess.Popen([str(ROOT/".venv/bin/python"),"-u","-m","studio.web"],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    write_json(STATE,{"pid":child.pid,"root":str(ROOT),"started":time.time(),"url":"http://127.0.0.1:8787"})
    for _ in range(40):
        if child.poll() is not None:raise RuntimeError("Dashboard startup failed; inspect runtime/web_service.log")
        if health():print("READY http://127.0.0.1:8787");return
        time.sleep(.25)
    child.terminate();raise RuntimeError("Dashboard readiness timed out")
if __name__=="__main__":main()
