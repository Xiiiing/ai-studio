"""Producer interventions, immutable output locks and cooperative task admission.

Commands close admission immediately; only the existing worker may apply them
after its current execution unit has drained. Provider receipts and accounting
are never cancelled or discarded by a command.
"""
from __future__ import annotations

import copy
import json
import re
import uuid
from contextvars import ContextVar

from . import store, team_ledger as ledger
from .scheduler import ProductionPaused
_APPLYING = ContextVar("team_applying_command", default=None)


class TeamInterventionPending(ProductionPaused):
    def __init__(self, message="新的团队安排等待当前执行单元收尾", task_id=None):
        super().__init__(message, "team_boundary", {"task_id": task_id})


def initialize(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS team_instructions(
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
      request_id TEXT NOT NULL, request_hash TEXT NOT NULL, status TEXT NOT NULL,
      created_at TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(project_id,request_id));
    CREATE INDEX IF NOT EXISTS team_instruction_queue ON team_instructions(project_id,status,created_at);
    CREATE TABLE IF NOT EXISTS team_locks(
      project_id TEXT NOT NULL REFERENCES projects(id), task_id TEXT NOT NULL,
      generation INTEGER NOT NULL, signature TEXT NOT NULL, data TEXT NOT NULL,
      created_at TEXT NOT NULL, PRIMARY KEY(project_id,task_id));
    CREATE TABLE IF NOT EXISTS team_control_requests(
      project_id TEXT NOT NULL REFERENCES projects(id), request_id TEXT NOT NULL,
      request_hash TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(project_id,request_id));
    """)


def _require(project):
    if not ledger.enabled(project):
        raise ValueError("请先在安全边界启用团队控制；旧 v1 项目仅支持观察")
    if project["status"] == "cancelled":
        raise ValueError("项目已取消")


def _request(payload):
    request = payload.get("request_id")
    if not isinstance(request, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", request):
        raise ValueError("请提供有效的指令幂等编号")
    return request, ledger.fingerprint({k: v for k, v in payload.items() if k != "expected_control_revision"})


def _cas(db, pid, payload):
    expected = payload.get("expected_control_revision")
    if type(expected) is not int or expected != ledger._revision(db, pid):
        raise ValueError("团队安排已变化，请核对后重新提交；修改要求已保留")


def _bump(db, pid):
    db.execute("UPDATE team_control SET revision=revision+1 WHERE project_id=?", (pid,))
    return ledger._revision(db, pid)


def _instruction(row):
    return {**json.loads(row["data"]), "id": row["id"], "status": row["status"], "created_at": row["created_at"]}


def _tasks(db, pid):
    return {r["id"]: ledger._data(dict(r)) for r in db.execute("SELECT * FROM team_work_items WHERE project_id=? ORDER BY ordinal", (pid,))}


def _selection(db, project, payload):
    tasks = _tasks(db, project["id"])
    ids = payload.get("task_ids")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(not isinstance(v, str) for v in ids) or len(set(ids)) != len(ids) or not set(ids) <= tasks.keys():
        raise ValueError("请明确选择本项目内 1–100 个不重复任务")
    target_type, target = payload.get("target_type"), payload.get("target_id")
    if target_type == "task":
        if ids != [target]:
            raise ValueError("成员任务指令必须对应选定的单个任务")
    elif target_type == "member":
        if target not in ledger.ROLE_DEPARTMENT or any(tasks[t]["owner"] != target for t in ids):
            raise ValueError("所选任务超出该成员负责范围")
    elif target_type in {"department", "leader"}:
        group = next((g for g in ledger.DEPARTMENTS if (g["id"] == target if target_type == "department" else g["lead"] == target)), None)
        if not group:
            raise ValueError("未知负责人或部门")
        if group["id"] != "direction" and any(tasks[t]["department"] != group["id"] for t in ids):
            raise ValueError("部门负责人不能修改其他部门的任务")
    else:
        raise ValueError("请选择任务、成员、部门或负责人")
    generations = payload.get("task_generations")
    if not isinstance(generations, dict) or set(generations) != set(ids):
        raise ValueError("请提供所选任务的原始版本")
    if any(type(generations[t]) is not int or generations[t] != tasks[t]["generation"] for t in ids):
        raise ValueError("任务版本已变化，请核对原要求后重新提交")
    preserve = payload.get("preserve", [])
    if not isinstance(preserve, list) or any(not isinstance(v, str) for v in preserve) or len(set(preserve)) != len(preserve) or not set(preserve) <= tasks.keys():
        raise ValueError("保留成果必须属于本项目的明确任务")
    operation = payload.get("operation", "revise")
    if operation not in {"retry", "revise", "replan"}:
        raise ValueError("未知介入操作")
    instruction = payload.get("instruction", "")
    if not isinstance(instruction, str) or not 1 <= len(instruction.strip()) <= 4000:
        raise ValueError("请填写 1–4000 字的任务要求")
    if payload.get("after", "auto") not in {"auto", "wait"}:
        raise ValueError("请选择自动继续或完成后等待")
    return tasks, ids, preserve


def _impact(tasks, roots, operation):
    affected = set(roots)
    # Queue-only plans don't invalidate media until their concrete operations
    # have been validated. The planner may not invent new execution nodes.
    if operation == "replan":
        return sorted(affected)
    changed = True
    while changed:
        changed = False
        for ident, task in tasks.items():
            if ident not in affected and set(task["dependencies"]) & affected:
                affected.add(ident)
                changed = True
    return sorted(affected)


def _conflicts(db, pid, affected, preserve):
    locked = {r[0] for r in db.execute("SELECT task_id FROM team_locks WHERE project_id=?", (pid,))}
    return [{"task_id": t, "reason": "成果已锁定，请解锁或调整范围"} for t in affected if t in locked] + [
        {"task_id": t, "reason": "返工依赖与明确保留的成果冲突"} for t in affected if t in preserve]


def preview(pid, payload):
    with store.connection() as db:
        db.execute("BEGIN")
        project = store._read(db, pid)
        _require(project)
        tasks, roots, preserve = _selection(db, project, payload)
        affected = _impact(tasks, roots, payload.get("operation", "revise"))
        return {"affected_tasks": affected, "preserved_tasks": preserve,
                "conflicts": _conflicts(db, pid, affected, preserve), "control_revision": ledger._revision(db, pid)}


def _invalidate(db, pid, ids):
    for ident in ids:
        row = ledger._stored(db, pid, ident)
        task = ledger._data(row)
        task.update(generation=task["generation"] + 1, status="pending", attempt=0,
                    active_run=None, artifacts=[], started_at=None, finished_at=None,
                    review_status="pending" if task["kind"] in {"image", "video", "image_review", "video_review"} else "not_required")
        ledger._save(db, row, task)
        db.execute("UPDATE team_unit_attempts SET status='superseded' WHERE project_id=? AND task_id=? AND status='running'", (pid, ident))


def submit(pid, payload):
    allowed = {"request_id", "expected_control_revision", "target_type", "target_id", "task_ids", "task_generations", "instruction", "operation", "preserve", "after"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("未知介入字段")
    request_id, signature = _request(payload)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        project = store._read(db, pid)
        _require(project)
        previous = db.execute("SELECT * FROM team_instructions WHERE project_id=? AND request_id=?", (pid, request_id)).fetchone()
        if previous:
            if previous["request_hash"] != signature:
                raise ValueError("幂等编号已用于不同指令")
            return {"instruction": _instruction(previous), "control_revision": ledger._revision(db, pid)}
        _cas(db, pid, payload)
        tasks, roots, preserve = _selection(db, project, payload)
        affected = _impact(tasks, roots, payload.get("operation", "revise"))
        conflicts = _conflicts(db, pid, affected, preserve)
        data = {**copy.deepcopy(payload), "affected_tasks": affected, "preserved_tasks": preserve,
                "accepted_generations": {}, "conflicts": conflicts, "reason": "；".join(c["reason"] + " · " + c["task_id"] for c in conflicts),
                "plan": None}
        data["pause_event_at_submit"] = db.execute("SELECT COALESCE(MAX(id),0) FROM events WHERE project_id=? AND kind='pause'", (pid,)).fetchone()[0]
        if not conflicts and payload.get("operation") != "replan":
            _invalidate(db, pid, affected)
            data["accepted_generations"] = {t: ledger._stored(db, pid, t)["generation"] for t in affected}
        ident, created = uuid.uuid4().hex, store.now()
        state = "needs_decision" if conflicts else "queued"
        db.execute("INSERT INTO team_instructions VALUES(?,?,?,?,?,?,?)", (ident, pid, request_id, signature, state, created, store.encoded(data)))
        revision = _bump(db, pid)
        project["team_last_instruction"] = ident
        store._write(db, project)
        store._event(db, pid, "新要求已登记，当前调用收尾后切换安排。", "team_instruction", {"instruction_id": ident, "task_id": roots[0]})
        if not db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='running'", (pid,)).fetchone():
            store._enqueue(db, pid, kind="team_instruction")
        return {"instruction": {**data, "id": ident, "status": state, "created_at": created}, "control_revision": revision}


def instructions(pid, offset=0, limit=30):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("指令分页无效")
    with store.connection() as db:
        db.execute("BEGIN")
        store._read(db, pid)
        total = db.execute("SELECT COUNT(*) FROM team_instructions WHERE project_id=?", (pid,)).fetchone()[0]
        values = [_instruction(r) for r in db.execute("SELECT * FROM team_instructions WHERE project_id=? ORDER BY rowid DESC LIMIT ? OFFSET ?", (pid, limit, offset))]
        return {"instructions": values, "total": total, "offset": offset, "limit": limit, "control_revision": ledger._revision(db, pid)}


def _pending(db, pid):
    return db.execute("SELECT * FROM team_instructions WHERE project_id=? AND status IN ('queued','planning','needs_decision') ORDER BY rowid LIMIT 1", (pid,)).fetchone()


def _draining_remote(db, pid, kind="stage", scope="production"):
    if kind == "stage":
        if scope not in {"production", "visual_development"}:
            return False
        return bool(db.execute("SELECT 1 FROM cloud_tasks WHERE project_id=? AND status NOT IN ('downloaded','failed','rejected')", (pid,)).fetchone())
    candidates = [kind + ":" + str(scope)]
    if kind == "video":
        candidates.append(str(scope))
    if kind == "character":
        candidates.append("image:character_" + str(scope))
    if kind == "speech":
        candidates.append("audio:" + str(scope))
    return bool(db.execute("SELECT 1 FROM cloud_tasks WHERE project_id=? AND shot_id IN (" + ",".join("?" for _ in candidates) + ") AND status NOT IN ('downloaded','failed','rejected')", (pid, *candidates)).fetchone())


def admit(pid, kind, scope, *, check_locks=True):
    if not ledger._existing(pid):
        return
    with store.connection() as db:
        try:
            project = store._read(db, pid)
        except KeyError:
            return
        if not ledger.enabled(project):
            return
        if _pending(db, pid) and not _draining_remote(db, pid, kind, scope):
            raise TeamInterventionPending(task_id=kind + ":" + str(scope))
        if check_locks and kind in {"character", "image", "video", "speech"} and db.execute("SELECT 1 FROM team_locks WHERE project_id=? AND task_id=?", (pid, kind + ":" + str(scope))).fetchone():
            raise TeamInterventionPending("具体成果已锁定，请解锁后再生成新版本", kind + ":" + str(scope))
        rows = list(db.execute("SELECT * FROM team_work_items WHERE project_id=? AND (status='paused' OR hold_after=1)", (pid,)))
        ident = kind + ":" + str(scope)
        for row in rows:
            data = ledger._data(dict(row))
            if data.get("awaiting_ack"):
                raise TeamInterventionPending("成果已完成，等待出品人确认继续", data["id"])
            if data["id"] == ident and data.get("paused"):
                raise TeamInterventionPending("此任务已暂停，恢复后继续", ident)
        row = db.execute("SELECT data FROM team_work_items WHERE project_id=? AND id=?", (pid, ident)).fetchone()
        if row:
            for dep in json.loads(row[0]).get("dependencies", []):
                dependency = db.execute("SELECT data FROM team_work_items WHERE project_id=? AND id=?", (pid, dep)).fetchone()
                if dependency and json.loads(dependency[0]).get("paused"):
                    raise TeamInterventionPending("前置任务已暂停，等待恢复", dep)


def assert_publishable(pid, kind, scope, input_version=None):
    if not ledger._existing(pid):
        return
    with store.connection() as db:
        try:
            project = store._read(db, pid)
        except KeyError:
            return
        if not ledger.enabled(project):
            return
        ident = kind + ":" + str(scope)
        for row in db.execute("SELECT data FROM team_instructions WHERE project_id=? AND status IN ('queued','planning','needs_decision')", (pid,)):
            if ident in json.loads(row[0]).get("affected_tasks", []):
                raise TeamInterventionPending("旧执行已收尾，成果保留为历史候选，新要求等待应用", ident)
        if input_version is not None:
            row = db.execute("SELECT data FROM team_work_items WHERE project_id=? AND id=?", (pid, ident)).fetchone()
            if row:
                current = json.loads(row[0]).get("input_version")
                if current not in (None, input_version):
                    raise TeamInterventionPending("输入版本已变化，旧成果仅保留为历史候选", ident)


def after_unit(pid, kind, scope):
    # Finishing/rechecking an existing locked result is not a new generation.
    admit(pid, kind, scope, check_locks=False)


def ordered(pid, kind, values, key=lambda value: value):
    values = list(values)
    if not ledger._existing(pid):
        return values
    with store.connection() as db:
        try:
            project = store._read(db, pid)
        except KeyError:
            return values
        if not ledger.enabled(project):
            return values
        tasks = _tasks(db, pid)
    def rank(value):
        task = tasks.get(kind + ":" + str(key(value)), {})
        return (bool(task.get("paused") or task.get("status") == "paused"), -task.get("priority", 0))
    return sorted(values, key=rank)


def is_locked(pid, kind, scope):
    if not ledger._existing(pid):
        return False
    with store.connection() as db:
        return bool(db.execute("SELECT 1 FROM team_locks WHERE project_id=? AND task_id=?", (pid, kind + ":" + str(scope))).fetchone())


def _resource(project, task):
    kind, scope = task["kind"], task["scope"]
    episode = project.get("episode") or project.get("script") or {}
    if not isinstance(episode, dict):
        episode = {}
    shot = next((s for s in episode.get("shots", []) if s.get("id") == scope), None)
    runtime = next((s for s in project.get("shots", []) if s.get("id") == scope), {})
    if kind in {"image", "video"}:
        result = runtime.get(kind)
        if not result and task.get("artifacts"):
            result = task["artifacts"][0].get("receipt")
        pin = project.get("shot_selections", {}).get(scope, {}).get(kind)
        return {"result": _media_identity(result), "selection": {k: pin.get(k) for k in ("take", "plan_signature")} if pin else None,
                "inputs": {k: shot.get(k) for k in (("prompt", "characters", "cast", "reference_shots", "character_states", "render_style_prompt") if kind == "image" else ("motion_prompt", "camera_motion", "seconds", "lip_sync"))} if shot else None,
                "render_revision": (shot or {}).get("render_revisions", {}).get(kind, 0),
                "visual": {"selected_style": project.get("selected_style"), "cast": episode.get("cast"),
                           "fixed": project.get("material_package", {}).get("references", []),
                           "series": project.get("series_references", {}).get(scope, [])}}
    if kind == "character":
        return {"cast": episode.get("cast", {}).get(scope), "reference": project.get("assets", {}).get("characters", {}).get(scope),
                "fixed": [r for r in project.get("material_package", {}).get("references", []) if r.get("role") == scope]}
    if kind == "scene":
        return [r for r in project.get("material_package", {}).get("references", []) if r.get("asset_id") == scope]
    if kind in {"speech", "dialogue_text"}:
        line = next((line for s in episode.get("shots", []) for i, line in enumerate(s.get("dialogue", [])) if (line.get("id") or f"{s['id']}_D{i + 1:02d}") == scope), None)
        if kind == "dialogue_text" and line:
            line = {key: line.get(key) for key in ("id", "role", "text", "start")}
        return {"line": line, **({"audio": next((v for v in project.get("assets", {}).get("audio", {}).get("lines", []) if v.get("id") == scope), None)} if kind == "speech" else {})}
    if kind == "script":
        return {k: v for k, v in (shot or {}).items() if k not in {"shot_revision", "render_revisions"}}
    if kind == "stage":
        keys = {"research": ["brief", "research"], "proposals": ["selected_story", "proposals"],
                "story_resolution": ["resolved_story_brief"], "screenplay": ["screenplay_draft", "script"],
                "storyboard": ["screenplay_draft", "storyboard_receipt"], "script_review": ["script_review"],
                "visual_design": ["styles"], "visual_development": ["selected_style", "styles"],
                "production": ["delivery"], "revision": ["revision_request"]}.get(scope, [])
        return {k: copy.deepcopy(project.get(k)) for k in keys}
    if kind in {"edit", "technical_review"}:
        return project.get("delivery")
    return None


def _media_identity(receipt):
    if not isinstance(receipt, dict):
        return None
    return {k: receipt.get(k) for k in ("path", "sha256", "source_signature", "plan_signature")}


def settle_instructions(db, pid):
    tasks = _tasks(db, pid)
    for row in db.execute("SELECT * FROM team_instructions WHERE project_id=? AND status='applied'", (pid,)).fetchall():
        command = _instruction(row)
        ids = list(command["task_ids"])
        for ident in list(ids):
            task = tasks.get(ident, {})
            if task.get("kind") in {"image", "character", "video"}:
                ids.append(("video_review:" if task["kind"] == "video" else "image_review:") + task["scope"])
        if all(ident in tasks and tasks[ident]["status"] in {"completed", "reused"} and tasks[ident].get("review_status") not in {"failed", "pending"} for ident in ids):
            command["completed_at"] = store.now()
            db.execute("UPDATE team_instructions SET status='completed',data=? WHERE id=?", (store.encoded(command), command["id"]))


def guard_project_write(db, project):
    """Shared mutation fence for old UI, CLI, worker and new team commands."""
    for lock in db.execute("SELECT * FROM team_locks WHERE project_id=?", (project["id"],)):
        task = ledger._data(ledger._stored(db, project["id"], lock["task_id"]))
        if ledger.fingerprint(_resource(project, task)) != lock["signature"]:
            raise ValueError("成果已锁定，修改涉及 " + task["label"] + "；请先解锁或调整范围")
    if _APPLYING.get() != project["id"]:
        previous = store._read(db, project["id"])
        tasks = _tasks(db, project["id"])
        for row in db.execute("SELECT data FROM team_instructions WHERE project_id=? AND status IN ('queued','planning','needs_decision')", (project["id"],)):
            for ident in json.loads(row[0]).get("affected_tasks", []):
                if ident in tasks and _resource(previous, tasks[ident]) != _resource(project, tasks[ident]):
                    raise TeamInterventionPending("新要求等待应用，旧成果仅保留在历史文件中", ident)


def task_action(pid, ident, payload):
    if not isinstance(payload, dict) or set(payload) - {"action", "request_id", "expected_control_revision", "expected_generation"}:
        raise ValueError("未知团队任务控制字段")
    request_id, signature = _request(payload)
    action = payload.get("action")
    if action not in {"pause", "resume", "lock", "unlock", "hold_after", "release"}:
        raise ValueError("未知任务动作")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        project = store._read(db, pid)
        _require(project)
        old = db.execute("SELECT * FROM team_control_requests WHERE project_id=? AND request_id=?", (pid, request_id)).fetchone()
        if old:
            if old["request_hash"] != signature + ":" + ident:
                raise ValueError("幂等编号已用于其他操作")
            return json.loads(old["result"])
        _cas(db, pid, payload)
        row = ledger._stored(db, pid, ident)
        task = ledger._data(row)
        if type(payload.get("expected_generation")) is not int or payload["expected_generation"] != task["generation"]:
            raise ValueError("任务版本已变化，请重新核对")
        if action == "lock":
            resource = _resource(project, task)
            if not resource or task["status"] not in {"completed", "reused"} or (task["kind"] in {"image", "video"} and not resource.get("result")):
                raise ValueError("只有已交付的具体成果才能锁定")
            db.execute("INSERT OR REPLACE INTO team_locks VALUES(?,?,?,?,?,?)", (pid, ident, task["generation"], ledger.fingerprint(resource), store.encoded(resource), store.now()))
        elif action == "unlock":
            db.execute("DELETE FROM team_locks WHERE project_id=? AND task_id=?", (pid, ident))
        elif action == "pause":
            task.update(paused=True, status="paused")
        elif action == "resume":
            task.update(paused=False, status="pending" if task["status"] == "paused" else task["status"])
        elif action == "hold_after":
            task["hold_after"] = True
            task["awaiting_ack"] = task["status"] in {"completed", "reused"}
        elif action == "release":
            task.update(hold_after=False, awaiting_ack=False)
        ledger._save(db, row, task)
        revision = _bump(db, pid)
        store._event(db, pid, task["label"] + " · " + action, "team_control", {"task_id": ident})
        if action in {"resume", "release", "unlock"} and project.get("team_wait") and not project.get("pause_requested"):
            project.update(status="queued", team_wait=None, error=None, needs_decision=None)
            store._write(db, project)
            store._enqueue(db, pid, kind="team_instruction" if _pending(db, pid) else "workflow")
        result = {"task": ledger._public_task(db, ledger._stored(db, pid, ident)), "control_revision": revision}
        db.execute("INSERT INTO team_control_requests VALUES(?,?,?,?)", (pid, request_id, signature + ":" + ident, store.encoded(result)))
        return result


def enable(pid, payload):
    if not isinstance(payload, dict) or set(payload) != {"action", "request_id", "expected_control_revision"} or payload["action"] != "enable":
        raise ValueError("无效的团队启用请求")
    _request(payload)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        project = store._read(db, pid)
        if ledger.enabled(project):
            return {"enabled": True, "control_revision": ledger._revision(db, pid)}
        if project.get("workflow_version", 1) != 2:
            raise ValueError("旧 v1 项目保留原检查点，仅支持团队观察")
        _cas(db, pid, payload)
        if project["status"] not in {"paused", "blocked", "awaiting_final"} or db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='running'", (pid,)).fetchone():
            raise ValueError("请在当前执行单元结束后的安全边界启用")
        project["team_control_version"] = 1
        store._write(db, project)
        ledger.ensure_project(db, project)
        _bump(db, pid)
        # Import only demonstrable outcomes, without attempts or guessed times.
        for row in db.execute("SELECT * FROM team_work_items WHERE project_id=?", (pid,)).fetchall():
            task = ledger._data(dict(row))
            value = _resource(project, task)
            if task["kind"] in {"image", "video", "speech", "character"}:
                known = value and (value.get("result") or value.get("audio") or value.get("reference"))
                if known and _verified_receipt(project, known):
                    task.update(status="reused", artifacts=[ledger._artifact(known)])
                    ledger._save(db, dict(row), task)
        store._event(db, pid, "已在安全边界启用团队控制；可验证成果作为原有依赖导入。", "team_control")
    return {"team": ledger.team_snapshot(pid)}


def _verified_receipt(project, receipt):
    from studio import core
    if not isinstance(receipt, dict) or not isinstance(receipt.get("path"), str) or not isinstance(receipt.get("sha256"), str):
        return False
    try:
        path = core.within(core.ROOT / "production" / project["slug"], core.ROOT / receipt["path"])
        return path.is_file() and core.sha256(path) == receipt["sha256"] and ("bytes" not in receipt or path.stat().st_size == receipt["bytes"])
    except (ValueError, OSError, KeyError):
        return False


def dismiss(pid, ident, payload):
    _request(payload)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        project = store._read(db, pid)
        _require(project)
        row = db.execute("SELECT * FROM team_instructions WHERE project_id=? AND id=?", (pid, ident)).fetchone()
        if not row:
            raise KeyError("指令不存在")
        if row["status"] == "dismissed":
            return {"instruction": _instruction(row), "control_revision": ledger._revision(db, pid)}
        _cas(db, pid, payload)
        if row["status"] not in {"queued", "needs_decision"}:
            raise ValueError("指令已开始应用，不能撤回；请提交新要求")
        db.execute("UPDATE team_instructions SET status='dismissed' WHERE id=?", (ident,))
        revision = _bump(db, pid)
        if project.get("team_wait") and not project.get("pause_requested"):
            project.update(status="queued", team_wait=None, needs_decision=None, error=None)
            store._write(db, project)
            store._enqueue(db, pid, kind="team_instruction" if _pending(db, pid) else "workflow")
        return {"instruction": {**_instruction(row), "status": "dismissed"}, "control_revision": revision}


def _direct_plan(command, tasks):
    return {"summary": "按出品人要求重新完成指定任务", "operations": [
        {"action": command.get("operation", "revise"), "task_id": ident, "requirements": command["instruction"]} for ident in command["task_ids"]]}


def _shot(project, sid):
    episode = project.get("episode") or project.get("script") or {}
    for shot in episode.get("shots", []):
        if shot["id"] == sid:
            return shot
    raise ValueError("镜头尚未定稿，暂不能执行此制作修改")


def _apply_direct(db, project, command, plan):
    from .production_edits import mark_visual_edit, require_resolved_cloud
    from .series_bindings import dependent_closure
    roots = command["task_ids"]
    tasks = _tasks(db, project["id"])
    affected_shots = set()
    stage = None
    latest_pause = db.execute("SELECT COALESCE(MAX(id),0) FROM events WHERE project_id=? AND kind='pause'", (project["id"],)).fetchone()[0]
    old_pause = bool(project.get("pause_requested") and latest_pause > command.get("pause_event_at_submit", 0))
    for operation in plan["operations"]:
        task = tasks[operation["task_id"]]
        kind, scope = task["kind"], task["scope"]
        requirements = operation.get("requirements", "")
        row = ledger._stored(db, project["id"], task["id"])
        task["requirements"] = requirements
        ledger._save(db, row, task)
        if kind in {"image", "video"}:
            shot = _shot(project, scope)
            require_resolved_cloud(db, project["id"], dependent_closure(project, [scope]) if kind == "image" else [scope])
            if operation["action"] == "revise":
                field = "prompt" if kind == "image" else "motion_prompt"
                shot[field] = str(shot.get(field, "")) + "\n出品人本次修改要求：" + requirements
                if len(shot[field]) > (1600 if kind == "image" else 800):
                    raise ValueError("叠加后的镜头要求过长，请整理原提示词后再修改")
            shot.setdefault("render_revisions", {})[kind] = shot.get("render_revisions", {}).get(kind, 0) + 1
            shot["shot_revision"] = shot.get("shot_revision", 1) + 1
            affected_shots.update(dependent_closure(project, [scope]) if kind == "image" else [scope])
            project.get("shot_selections", {}).get(scope, {}).pop(kind, None)
            if kind == "image":
                project.get("shot_selections", {}).get(scope, {}).pop("video", None)
        elif kind == "speech":
            found = False
            episode = project.get("episode") or project.get("script") or {}
            for shot in episode.get("shots", []):
                for index, line in enumerate(shot.get("dialogue", [])):
                    if (line.get("id") or f"{shot['id']}_D{index + 1:02d}") != scope:
                        continue
                    found = True
                    line["team_revision"] = line.get("team_revision", 0) + 1
                    if operation["action"] == "revise":
                        from .config import load_config
                        if load_config().get("audio_profiles"):
                            from .media_profiles import resolve
                            if resolve(load_config(), "audio").get("provider") == "dashscope_tts":
                                raise ValueError("当前云端配音接口不支持逐句情绪要求，请修改对白或选择重新生成")
                        line["emotion"] = requirements
                    affected_shots.add(shot["id"])
            if not found:
                raise ValueError("对白已变化，请重新选择任务")
        elif kind in {"image_review", "video_review", "dialogue", "technical_review"}:
            # Retry inspection against retained inputs, never erase its verdict.
            revisions = project.setdefault("team_review_revisions", {})
            revisions[task["id"]] = revisions.get(task["id"], 0) + 1
            if task.get("shot_id"):
                affected_shots.add(task["shot_id"])
        elif kind == "character":
            episode = project.get("episode") or project.get("script") or {}
            if scope not in episode.get("cast", {}):
                raise ValueError("角色已变化，请重新选择")
            if any(r.get("role") == scope and r.get("kind") == "character" for r in project.get("material_package", {}).get("references", [])):
                raise ValueError("角色使用已固定的素材版本，请在素材页制作新版本并明确替换")
            if operation["action"] == "revise":
                value = episode["cast"][scope]
                if not isinstance(value, str):
                    raise ValueError("该角色设定需在素材页面修改")
                episode["cast"][scope] = value + "\n本次要求：" + requirements
            project.setdefault("team_character_revisions", {})[scope] = project.get("team_character_revisions", {}).get(scope, 0) + 1
            from .series_bindings import shot_roles
            affected_shots.update(s["id"] for s in episode.get("shots", []) if scope in shot_roles(s, episode.get("cast", {})))
        elif kind == "stage":
            stage = scope
            project.setdefault("team_stage_requirements", {})[scope] = requirements
            revisions = project.setdefault("team_stage_revisions", {})
            revisions[scope] = revisions.get(scope, 0) + 1
        elif kind == "edit":
            if operation["action"] == "revise":
                project["edit_feedback"] = requirements
                project["reedit_requested"] = True
                stage = "revision"
            affected_shots.update(s["id"] for s in (project.get("episode") or project.get("script") or {}).get("shots", []))
        elif kind in {"script", "dialogue_text"}:
            stage = "revision"
            feedback = {"notes": requirements, "shot_ids": [task["shot_id"]]}
            project["revision_request"] = {"choice": "revise", "feedback": feedback}
            project["script_feedback"] = feedback
            project["production_revision"] += 1
            project.pop("revision_prepared", None)
        else:
            raise ValueError("该成果请通过素材或剧本入口修改，不能直接重做")
    if stage:
        from .attention_actions import _invalidate as invalidate_stage, _restart
        if stage not in {"research", "proposals", "story_resolution"}:
            if project.get("delivery"):
                project.setdefault("delivery_history", []).append({"production_revision": project["production_revision"],
                                                                  "delivery": copy.deepcopy(project["delivery"]), "at": store.now()})
            project["production_revision"] += 1
            project.update(delivery=None)
            project.pop("delivery_complete_revision", None)
            for decision in project.get("decisions", []):
                if decision.get("type") == "final" and decision.get("status") == "pending":
                    decision["status"] = "superseded"
        if stage in {"research", "proposals", "story_resolution"}:
            invalidate_stage(project, stage)
        elif stage == "screenplay":
            project["script_generation"] += 1
            project["story_resolution_complete_generation"] = project["script_generation"]
            project.pop("script_complete_generation", None)
            project.pop("visual_design_complete_generation", None)
            project.pop("style_previews_generation", None)
            project["script_review"] = None
            project.pop("screenplay_complete_round", None)
            project.pop("storyboard_complete_round", None)
            project.pop("script_review_complete_round", None)
            _restart(project, stage)
        elif stage == "storyboard":
            project["script_review"] = None
            project.pop("storyboard_complete_round", None)
            project.pop("script_review_complete_round", None)
            _restart(project, stage)
        elif stage == "script_review":
            project["script_review"] = None
            project.pop("script_review_complete_round", None)
            _restart(project, stage)
        elif stage in {"visual_design", "visual_development"}:
            project["style_generation"] = project.get("style_generation", project["script_generation"]) + 1
            project.pop("visual_design_complete_generation", None)
            project.pop("style_previews_generation", None)
            project["style_feedback"] = command["instruction"]
            _restart(project, "visual_design")
        elif stage == "revision":
            if not project.get("revision_request"):
                project["revision_request"] = {"choice": "reedit", "feedback": command["instruction"]}
            project.pop("revision_prepared", None)
            _restart(project, "revision")
        elif stage == "production":
            project["production_edit_revision"] = project["production_revision"]
        else:
            raise ValueError("该阶段不能直接重启")
    elif affected_shots:
        mark_visual_edit(db, project, sorted(affected_shots), "team_intervention", {"task_ids": roots, "instruction_id": command["id"]})
    # Keep both canonical planning copies consistent without rewriting other shots.
    if project.get("episode"):
        project["script"] = copy.deepcopy(project["episode"])
    wait = command.get("after", "auto") == "wait" or old_pause
    project.update(status="paused" if wait else "queued", pause_requested=bool(old_pause), team_wait="instruction" if wait else None,
                   error=None, needs_decision=None)
    if command.get("after") == "wait":
        # Wait after the requested work, not before starting it.
        if not old_pause:
            project.update(status="queued", team_wait=None)
        for ident in roots:
            row = ledger._stored(db, project["id"], ident)
            data = ledger._data(row)
            data.update(hold_after=True, awaiting_ack=False)
            ledger._save(db, row, data)
    store._write(db, project)


def requirements_payload(project, payload):
    from .execution_context import current_execution
    context = current_execution.get() or {}
    stage = context.get("stage")
    requirement = project.get("team_stage_requirements", {}).get(stage)
    if not requirement or not isinstance(payload, dict):
        return payload
    return {**payload, "producer_task_requirements": {
        "stage": stage, "requirements": requirement, "revision": project.get("team_stage_revisions", {}).get(stage, 0),
        "rule": "在现有任务职责与明确范围内落实本次出品人要求，不改变预算、审核或终片权限。"}}


def process_pending(pid):
    """Called by the single durable worker after the old graph job has drained."""
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        project = store._read(db, pid)
        if not ledger.enabled(project):
            return None
        if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='running' AND kind!='team_instruction'", (pid,)).fetchone():
            return None
        row = _pending(db, pid)
        if row is None:
            return None
        command = _instruction(row)
        if _draining_remote(db, pid):
            # Resume only the already submitted supplier work. Admission still
            # rejects every new unit, and publication of old affected work is fenced.
            latest_pause = db.execute("SELECT COALESCE(MAX(id),0) FROM events WHERE project_id=? AND kind='pause'", (pid,)).fetchone()[0]
            if not project.get("pause_requested") or latest_pause <= command.get("pause_event_at_submit", 0):
                project.update(status="queued", pause_requested=False)
                store._write(db, project)
                store._enqueue(db, pid)
            return {"status": "draining"}
        conflicts = _conflicts(db, pid, command["affected_tasks"], command.get("preserved_tasks", []))
        if conflicts:
            reason = "；".join(c["reason"] + " · " + c["task_id"] for c in conflicts)
            command.update(reason=reason, conflicts=conflicts)
            db.execute("UPDATE team_instructions SET status='needs_decision',data=? WHERE id=?", (store.encoded(command), command["id"]))
            project.update(status="paused", team_wait="instruction", needs_decision={"type": "team", "message": reason})
            store._write(db, project)
            return {"status": "needs_decision", "reason": reason}
        if command.get("accepted_generations") and any(ledger._stored(db, pid, ident)["generation"] != gen for ident, gen in command["accepted_generations"].items()):
            command["reason"] = "后续指令改变了任务版本，请核对原要求后重新提交"
            db.execute("UPDATE team_instructions SET status='needs_decision',data=? WHERE id=?", (store.encoded(command), command["id"]))
            project.update(status="paused", team_wait="instruction", needs_decision={"type": "team", "message": command["reason"]})
            store._write(db, project)
            return {"status": "needs_decision", "reason": command["reason"]}
        if not command.get("accepted_generations") and command.get("operation") != "replan":
            _invalidate(db, pid, command["affected_tasks"])
            command["accepted_generations"] = {t: ledger._stored(db, pid, t)["generation"] for t in command["affected_tasks"]}
        db.execute("UPDATE team_instructions SET status='planning',data=? WHERE id=?", (store.encoded(command), command["id"]))
        tasks = _tasks(db, pid)
    # Planning has no open SQLite writer transaction and cannot publish changes.
    try:
        if command.get("operation") == "replan" or command.get("target_type") in {"leader", "department"}:
            from .team_planning import plan_instruction
            plan = plan_instruction(project, command, tasks)
        else:
            plan = _direct_plan(command, tasks)
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            project = store._read(db, pid)
            current = db.execute("SELECT * FROM team_instructions WHERE id=?", (command["id"],)).fetchone()
            if current["status"] != "planning":
                return None
            if any(ledger._stored(db, pid, ident)["generation"] != gen for ident, gen in command["accepted_generations"].items()):
                raise ValueError("安排期间任务版本发生变化，请核对后重试")
            applying = _APPLYING.set(pid)
            try:
                _apply_direct(db, project, command, plan)
            finally:
                _APPLYING.reset(applying)
            command.update(plan=plan, applied_at=store.now(), reason="")
            db.execute("UPDATE team_instructions SET status='applied',data=? WHERE id=?", (store.encoded(command), command["id"]))
            _bump(db, pid)
            store._event(db, pid, "新安排已应用：" + plan["summary"], "team_instruction", {"instruction_id": command["id"], "task_id": command["task_ids"][0]})
            if not project.get("pause_requested"):
                store._enqueue(db, pid, kind="team_instruction" if _pending(db, pid) else "workflow")
        return {"status": "applied", "plan": plan}
    except Exception as exc:
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            project = store._read(db, pid)
            command["reason"] = str(exc)[:1500] if isinstance(exc, ValueError) else "安排暂不可用，请调整要求或检查负责人配置"
            db.execute("UPDATE team_instructions SET status='needs_decision',data=? WHERE id=?", (store.encoded(command), command["id"]))
            project.update(status="paused", team_wait="instruction", needs_decision={"type": "team", "message": command["reason"]})
            store._write(db, project)
        return {"status": "needs_decision", "reason": command["reason"]}


def task_descriptors(db, project, task):
    if not ledger.enabled(project):
        return []
    locked = bool(db.execute("SELECT 1 FROM team_locks WHERE project_id=? AND task_id=?", (project["id"], task["id"])).fetchone())
    labels = [("resume", "恢复任务")] if task.get("paused") else [("pause", "暂停任务")]
    labels += [("unlock", "解锁成果")] if locked else [("lock", "锁定成果")]
    labels += [("release", "确认并继续")] if task.get("awaiting_ack") or task.get("hold_after") else [("hold_after", "完成后等我确认")]
    labels += [("intervene", "介入并下达要求")]
    return [{"id": action, "label": label, "enabled": action != "lock" or task["status"] in {"completed", "reused"},
             "disabled_reason": "尚无已完成的具体成果" if action == "lock" and task["status"] not in {"completed", "reused"} else None} for action, label in labels]
