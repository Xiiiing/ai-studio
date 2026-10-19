"""Single durable project consumer; GPU work is independent of HTTP requests."""
from __future__ import annotations

import fcntl
import json
import os
import signal
import socket
import threading
import time
import traceback

from .environment import load_environment
load_environment()

from studio.core import write_json
from . import store
from .config import DATA
from .workflows import ProjectPaused, ReviewBlocked, advance
from .schemas import CreativeReviewError
from .remote_tasks import ProductionDeferred, defer_job, owns_job

_stop=threading.Event()
_current=None


def heartbeat():
    while not _stop.is_set():
        write_json(DATA/"worker.json",{"pid":os.getpid(),"heartbeat":time.time(),
                   "current_project":_current,"status":"running","queue":"sqlite_durable",
                   "execution_host":socket.gethostname()})
        _stop.wait(10)


def handle(job):
    if not owns_job(job):
        return  # A completed/recovered claim is not permission to execute again.
    pid=job["project_id"]
    try:
        episode_job = job.get('kind') == 'workflow' and store.get(pid,False).get('record_kind') == 'episode'
        if job.get('kind') in {'drama_plan', 'drama_episode'} or episode_job:
            from .drama_runtime import run_plan, execute_episode
            (run_plan if job['kind'] == 'drama_plan' else execute_episode)(pid)
            store.finish_job(job['id'], lease=job['lease'])
            return
        if job.get("kind") == "team_instruction":
            from .team_control import process_pending
            process_pending(pid)
            store.finish_job(job["id"], lease=job["lease"])
            return
        if job.get("kind") == "review_advice":
            from .review_advice import run
            try:
                run(pid, job)
            except Exception as advice_error:
                store.event(pid, "AI 辅助分析暂不可用，原审核问题和人工入口已保留。", "review_advice",
                            {"error_type": type(advice_error).__name__[:100]})
            store.finish_job(job["id"], lease=job["lease"])
            return
        advance(pid)
        store.finish_job(job["id"], lease=job["lease"])
        from .team_control import process_pending
        process_pending(pid)
    except ProductionDeferred as deferred:
        defer_job(job, deferred)
        from .team_control import process_pending
        process_pending(pid)
    except ProjectPaused as exc:
        store.update(pid,{"status":"cancelled" if store.get(pid,False)['status']=='cancelled' else "paused","error":None,"message":str(exc)})
        store.finish_job(job["id"], lease=job["lease"])
    except Exception as exc:
        from .team_control import TeamInterventionPending, process_pending
        if isinstance(exc, TeamInterventionPending):
            store.update(pid, {"status": "paused", "team_wait": "boundary", "error": None, "message": str(exc)})
            store.finish_job(job["id"], lease=job["lease"])
            process_pending(pid)
            return
        name=type(exc).__name__
        details=getattr(exc,"details",None) or getattr(exc,"reason",None)
        if isinstance(exc, CreativeReviewError):
            details={"issues":exc.issues}
        if name=="ProductionPaused":
            store.update(pid,{"status":"cancelled" if store.get(pid,False)['status']=='cancelled' else "paused","error":None,"message":str(exc)})
            store.finish_job(job["id"], lease=job["lease"])
            return
        is_budget=name=="ProductionBudgetExceeded"
        needs={"type":"budget" if is_budget else "production" if name=="ProductionNeedsDecision" else "review",
               "message":str(exc),"details":details,"code":getattr(exc,"code",getattr(exc,"reason",None))}
        if is_budget:
            state=details if isinstance(details,dict) else {}
            needs["exhausted_limits"]=[label for label,used,cap in [("wall","elapsed_seconds","max_wall_seconds"),("gpu","gpu_seconds","max_gpu_seconds")] if state.get(used,0)>=state.get(cap,float("inf"))] or ["gpu"]
        if isinstance(exc,ReviewBlocked):
            needs={**needs,**exc.details}
        if isinstance(exc, CreativeReviewError):
            project=store.get(pid,False)
            needs.update(kind="script_review" if project.get("selected_story") else "creative_review",
                         issues=exc.issues,
                         type="edit_feedback" if project.get("revision_request") else "review")
        if not isinstance(exc,(ReviewBlocked,CreativeReviewError)) and name not in {"ProductionNeedsDecision","ProductionBudgetExceeded"}:
            needs["type"]="runtime"
        store.update(pid,{"status":"blocked","error":str(exc)[:2500],"needs_decision":needs,
                          "message":"制片已保留进度并暂停："+str(exc)[:1500]})
        expected = isinstance(exc, (ReviewBlocked, CreativeReviewError)) or name in {"ProductionNeedsDecision", "ProductionBudgetExceeded"}
        store.event(pid,"请在待办决策中查看问题并选择处理方式。" if expected else "异常详情已写入本地运行日志。",
                    "review_blocked" if expected else "error",{"exception_type":name,"stage":store.get(pid,False).get("stage")})
        if isinstance(exc, (ReviewBlocked, CreativeReviewError)):
            from .review_advice import eligible, run
            if (not isinstance(exc, CreativeReviewError) or bool(exc.issues)) and eligible(store.get(pid, False)):
                try:
                    run(pid, job)
                except Exception as advice_error:
                    store.event(pid, "AI 辅助分析暂不可用，原审核问题和人工入口已保留。", "review_advice",
                                {"error_type": type(advice_error).__name__[:100]})
        store.finish_job(job["id"],str(exc)[:2500],lease=job["lease"])
        if expected:
            print(json.dumps({"event":"project_needs_attention","project":pid,"reason_type":needs.get("kind") or needs.get("type"),"exception_type":name},ensure_ascii=False),flush=True)
        else:
            traceback.print_exc()
    finally:
        if job.get('kind') in {'drama_episode','workflow'} and store.get(pid,False).get('record_kind') == 'episode':
            from .dramas import after_episode
            after_episode(pid)
        if job.get('kind') == 'workflow' and store.get(pid,False).get('record_kind') == 'scene_unit':
            unit = store.get(pid,False)
            with store.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                parent=store._read(db,unit['episode_id'])
                if parent['status'] in {'awaiting_final','approved'}:
                    parent.update(status='queued',pause_requested=False,production_revision=parent['production_revision']+1)
                    store._write(db,parent)
                    store._enqueue(db,parent['id'],'drama_episode')


def main():
    global _current
    DATA.mkdir(parents=True,exist_ok=True)
    guard=(DATA/"worker.lock").open("a+")
    try:
        fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError("已有制片执行器正在运行")
    store.initialize()
    recovered=store.recover_jobs()
    from . import material_tasks
    material_tasks.recover()
    from .prompt_workbench import recover_trials, run_next_trial
    recover_trials()
    print(json.dumps({"event":"worker_ready","recovered_jobs":recovered,"pid":os.getpid()}),flush=True)
    signal.signal(signal.SIGTERM,lambda *_:_stop.set())
    signal.signal(signal.SIGINT,lambda *_:_stop.set())
    threading.Thread(target=heartbeat,daemon=True).start()
    try:
        material_turn = True
        while not _stop.is_set():
            material_job = material_tasks.claim() if material_turn else None
            if material_job:
                _current = material_job["id"]
                material_tasks.run(material_job)
                _current = None
                material_turn = False
                continue
            job=store.claim_job()
            if not job:
                material_turn = True
                if run_next_trial():
                    continue
                _stop.wait(1)
                continue
            _current=job["project_id"]
            print(json.dumps({"event":"job_started","job_id":job["id"],"project":_current}),flush=True)
            handle(job)
            _current=None
            material_turn = True
    finally:
        write_json(DATA/"worker.json",{"pid":os.getpid(),"heartbeat":time.time(),"status":"stopped"})
        fcntl.flock(guard,fcntl.LOCK_UN)
        guard.close()


if __name__=="__main__":
    main()
