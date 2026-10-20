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
from .scheduler import ProductionNeedsDecision

_stop=threading.Event()
_current=None


def _defer_review_transport(job, exc):
    """Retry saved-media review only for typed, proven transient transport errors."""
    details = getattr(exc, 'details', None)
    if (not isinstance(exc, ProductionNeedsDecision) or exc.reason != 'semantic_unavailable'
            or not isinstance(details, dict) or details.get('resume_action') != 'review_existing_assets_only'):
        return False
    pending = details.get('pending')
    if (not isinstance(pending, list) or not pending or any(
            not isinstance(item, dict) or item.get('retryable_transport') is not True
            or not item.get('path') or not item.get('take') for item in pending)):
        return False
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        owner = db.execute('SELECT status,lease FROM jobs WHERE id=?', (job['id'],)).fetchone()
        if not owner or owner['status'] != 'running' or owner['lease'] != job.get('lease'):
            return False
        project = store._read(db, job['project_id'])
        from .production_execution import enabled
        configuration = project.get('execution_config') or {}
        if not isinstance(configuration, dict) or not enabled(configuration.get('production') or {}):
            return False
        from .config import resolve_llm
        try:
            reviewer = resolve_llm(configuration, 'visual_critic', require_vision=True)
        except (TypeError, ValueError):
            return False
        # No retry submits to a possibly paid provider whose charge is unknown.
        if reviewer.get('provider') not in {'llama_cpp', 'ollama'} or reviewer.get('enabled') is False:
            return False
        ancestors = [project]
        if project.get('episode_id'):
            ancestors.append(store._read(db, project['episode_id']))
        if project.get('drama_id'):
            ancestors.append(store._read(db, project['drama_id']))
        if any(p.get('pause_requested') or p['status'] in {'paused','cancelled'}
               or p.get('automation', {}).get('suspended') for p in ancestors):
            return False
        production = project.setdefault('production', {})
        prior = production.get('review_transport_recovery') or {}
        count = prior.get('count', 0)
        if type(count) is not int or count >= 2:
            return False
        count += 1
        due = time.time() + 30 * count
        production['review_transport_recovery'] = {'status': 'waiting', 'count': count, 'max_deferrals': 2,
            'job_id': job['id'], 'pending': pending, 'next_poll_at': due,
            'message': '审核服务暂时连接失败，系统稍后仅复核已保存素材，不重新生成。'}
        store._write(db, project)
        if (project.get('record_kind') == 'episode' and project.get('active_unit_id') in project.get('unit_ids', [])):
            unit = store._read(db, project['active_unit_id'])
            import copy
            unit.setdefault('production', {})['review_transport_recovery'] = copy.deepcopy(production['review_transport_recovery'])
            store._write(db, unit)
    return defer_job(job, ProductionDeferred(due, 'quality_review', {
        'reason': 'review_transport_retry', 'attempt': count, 'max_attempts': 2,
        'resume_action': 'review_existing_assets_only'}))


def heartbeat():
    while not _stop.is_set():
        write_json(DATA/"worker.json",{"pid":os.getpid(),"heartbeat":time.time(),
                   "current_project":_current,"status":"running","queue":"sqlite_durable",
                   "execution_host":socket.gethostname()})
        _stop.wait(10)


def handle(job):
    from .execution_context import current_job
    token = current_job.set(dict(job))
    try:
        # A scene unit can be queued independently by team instructions or a
        # recovery action. Its roles and media must use the same frozen batch
        # configuration as when it runs inside its parent episode.
        project = store.get(job['project_id'], False) if owns_job(job) else {}
        configuration = project.get('execution_config')
        if project.get('record_kind') == 'scene_unit' and isinstance(configuration, dict):
            from .config import config_snapshot
            with config_snapshot(configuration):
                return _handle(job)
        return _handle(job)
    finally:
        current_job.reset(token)


def _handle(job):
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
        if not owns_job(job):return
        store.update(pid,{"status":"cancelled" if store.get(pid,False)['status']=='cancelled' else "paused","error":None,"message":str(exc)})
        store.finish_job(job["id"], lease=job["lease"])
    except Exception as exc:
        if not owns_job(job):return  # Late workers retain private receipts only.
        if _defer_review_transport(job, exc):
            return
        current = store.get(pid, False)
        if (isinstance(exc, ProductionNeedsDecision) and exc.reason == 'semantic_unavailable'
                and (current.get('pause_requested') or current['status'] in {'paused','cancelled'})):
            store.update(pid, {'status': 'cancelled' if current['status'] == 'cancelled' else 'paused', 'error': None})
            store.finish_job(job['id'], lease=job['lease'])
            return
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
        needs={"type":"budget" if is_budget else "production" if isinstance(exc,ProductionNeedsDecision) else "review",
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
        if not isinstance(exc,(ReviewBlocked,CreativeReviewError,ProductionNeedsDecision)) and name!="ProductionBudgetExceeded":
            needs["type"]="runtime"
        store.update(pid,{"status":"blocked","error":str(exc)[:2500],"needs_decision":needs,
                          "message":"制片已保留进度并暂停："+str(exc)[:1500]})
        expected = isinstance(exc, (ReviewBlocked, CreativeReviewError, ProductionNeedsDecision)) or name=="ProductionBudgetExceeded"
        blocked_project = store.get(pid, False)
        planning = blocked_project.get('record_kind') == 'drama' and not blocked_project.get('plan_version')
        next_message = ('规划已暂停。请在全剧规划中查看问题，补充要求后继续；已有草稿保留。' if planning else
            '请在待办决策中查看问题并选择处理方式。' if expected else '异常详情已写入本地运行日志。')
        if isinstance(exc, ProductionNeedsDecision) and (needs.get('code') == 'retry_limit' or str(needs.get('code', '')).startswith('production_recovery_')):
            next_message = str(exc)
        store.event(pid, next_message, "review_blocked" if expected else "error",
                    {"exception_type":name,"stage":blocked_project.get("stage"), **({'next_tab': 'plan'} if planning else {})})
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
        with store.connection() as db:
            owner=db.execute('SELECT lease,status FROM jobs WHERE id=?',(job['id'],)).fetchone()
        if not owner or owner[0]!=job.get('lease'):return
        if job.get('kind') == 'drama_plan' and owner['status'] == 'completed':
            from .drama_automation import reconcile_project
            reconcile_project(pid)
        if job.get('kind') in {'drama_episode','workflow'} and store.get(pid,False).get('record_kind') in {'episode','scene_unit'}:
            from .dramas import settle_execution
            settle_execution(pid, job)


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
    from .drama_automation import recover as recover_automation
    recover_automation()
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
