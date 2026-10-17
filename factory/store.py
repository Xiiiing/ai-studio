"""Transactional project snapshots, decision inbox, and a durable work queue."""
from __future__ import annotations

import copy
import json
import math
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import DATA, DATABASE
from .schemas import revision_shot_ids

TERMINAL = {"approved", "cancelled"}
ACTIVE = {"queued", "researching", "creating", "scripting", "visual_development", "producing", "reviewing", "revising"}
ALLOWED_FORMATS = {"motion_comic", "live_action", "animation"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


@contextmanager
def connection():
    DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DATABASE), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=30000")
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def initialize():
    with connection() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS projects(
          id TEXT PRIMARY KEY, updated REAL NOT NULL, revision INTEGER NOT NULL,
          snapshot TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
          at TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL,
          data TEXT NOT NULL DEFAULT '{}',
          FOREIGN KEY(project_id) REFERENCES projects(id));
        CREATE TABLE IF NOT EXISTS jobs(
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL, status TEXT NOT NULL,
          created REAL NOT NULL, started REAL, finished REAL, error TEXT,
          FOREIGN KEY(project_id) REFERENCES projects(id));
        CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status,created);
        CREATE INDEX IF NOT EXISTS events_project ON events(project_id,id);
        CREATE TABLE IF NOT EXISTS memories(
          key TEXT PRIMARY KEY, project_id TEXT NOT NULL, value TEXT NOT NULL,
          updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS idempotency(
          key TEXT PRIMARY KEY, project_id TEXT NOT NULL, request_hash TEXT NOT NULL);
        """)
        from .cloud_budget import initialize as initialize_cloud_budget
        initialize_cloud_budget(db)
        from .series_bible import initialize as initialize_series
        initialize_series(db)
        from .material_library import initialize as initialize_materials
        initialize_materials(db)
        from .material_tasks import initialize as initialize_material_tasks
        initialize_material_tasks(db)
        from .remote_tasks import migrate as migrate_remote_tasks
        migrate_remote_tasks(db)
        from .team_ledger import initialize as initialize_team
        initialize_team(db)
        from .team_control import initialize as initialize_team_control
        initialize_team_control(db)


def _read(db, pid):
    row = db.execute("SELECT snapshot,revision FROM projects WHERE id=?", (pid,)).fetchone()
    if row is None:
        raise KeyError("项目不存在")
    item = json.loads(row["snapshot"])
    item["revision"] = row["revision"]
    item.setdefault("workflow_version", 1)
    return item


def _write(db, project):
    from .team_control import guard_project_write
    guard_project_write(db, project)
    project["updated_at"] = now()
    project["revision"] = int(project.get("revision", 0)) + 1
    db.execute("UPDATE projects SET updated=?,revision=?,snapshot=? WHERE id=?",
               (time.time(), project["revision"], encoded(project), project["id"]))
    from .team_ledger import ensure_project
    ensure_project(db, project)


def _event(db, pid, message, kind="progress", data=None):
    db.execute("INSERT INTO events(project_id,at,kind,message,data) VALUES(?,?,?,?,?)",
               (pid, now(), kind, str(message)[:6000], encoded(data or {})))


def event(pid, message, kind="progress", data=None):
    with connection() as db:
        _event(db, pid, message, kind, data)


def _enqueue(db, pid, kind="workflow"):
    existing = db.execute(
        "SELECT id FROM jobs WHERE project_id=? AND status='queued' AND kind=?",
        (pid, kind)).fetchone()
    if existing:
        return existing["id"]
    jid = uuid.uuid4().hex
    db.execute("INSERT INTO jobs(id,project_id,status,created,kind) VALUES(?,?,?,?,?)",
               (jid, pid, "queued", time.time(), kind))
    return jid


def create(payload):
    if type(payload.get("prepare_materials", False)) is not bool:
        raise ValueError("prepare_materials须为布尔值")
    team_mode = payload.get("team_mode", "delegated")
    if not isinstance(team_mode, str) or team_mode not in {"guided", "delegated"}:
        raise ValueError("团队模式应为 guided 或 delegated")
    workflow_version = payload.get("workflow_version", 2)
    if type(workflow_version) is not int or workflow_version not in {1, 2}:
        raise ValueError("工作流版本应为 1 或 2")
    brief = str(payload.get("brief", "")).strip()
    if not 5 <= len(brief) <= 6000:
        raise ValueError("请填写 5–6000 字的创作方向")
    format_name = payload.get("format", "motion_comic")
    if format_name not in ALLOWED_FORMATS:
        raise ValueError("不支持的视频形态")
    contract = None
    if "creative_contract" in payload:
        from .creative_contract import normalize as normalize_contract
        contract = normalize_contract(payload["creative_contract"], format_name)
    profile = None
    if "production_profile" in payload:
        from .production_profiles import normalize
        profile = normalize(payload["production_profile"], payload.get("duration_seconds"))
        duration = profile["duration_seconds"]
    else:
        duration = payload.get("duration_seconds", 60)
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration != int(duration) or not 60 <= duration <= 90:
            raise ValueError("时长应为 60–90 秒的整数")
    raw_budget = payload.get("budget", {})
    if not isinstance(raw_budget, dict):
        raise ValueError("预算格式错误")
    minutes = raw_budget.get("gpu_minutes", 120)
    attempts = raw_budget.get("max_attempts", 3)
    wall = raw_budget.get("wall_minutes", 180)
    if isinstance(minutes, bool) or not isinstance(minutes, (float, int)) or not math.isfinite(minutes) or not 5 <= minutes <= 1440:
        raise ValueError("GPU 预算应为 5–1440 卡分钟")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or not 1 <= attempts <= 5:
        raise ValueError("每镜尝试次数应为 1–5")
    if not isinstance(wall, (int, float)) or isinstance(wall, bool) or not math.isfinite(wall) or not 10 <= wall <= 1440:
        raise ValueError("单次制作时间上限应为 10–1440 分钟")
    sources = payload.get("sources", [])
    if not isinstance(sources, list) or len(sources) > 12 or any(not isinstance(s, dict) for s in sources):
        raise ValueError("素材来源应为至多 12 个来源对象")
    for source in sources:
        if source.get("rights", "analysis_only") not in {"analysis_only", "owned", "licensed", "public_domain"}:
            raise ValueError("素材使用权限无效")
    mode = payload.get("creation_mode", "original")
    if mode not in {"original", "adaptation"}:
        raise ValueError("创作方式无效")
    if mode == "adaptation" and not any(s.get("rights") in {"owned", "licensed", "public_domain"} for s in sources):
        raise ValueError("改编项目需要提供自有、已授权或公版素材；普通检索来源仅可供题材分析")
    pid = uuid.uuid4().hex[:12]
    timestamp = now()
    project = {
        "id": pid, "slug": "factory-" + pid, "title": str(payload.get("title", "")).strip()[:100] or "未命名短剧",
        "brief": brief, "format": format_name, "duration_seconds": duration if profile else int(duration),
        "creation_mode": mode, "status": "queued", "stage": "queued", "progress": 0,
        "created_at": timestamp, "updated_at": timestamp, "revision": 1,
        "sources": sources, "submitted_sources": copy.deepcopy(sources),
        "proposals": [], "selected_story": None, "styles": [], "selected_style": None,
        "script": None, "shots": [], "decisions": [], "answers": {},
        "budget": {"gpu_minutes": float(minutes), "spent_gpu_minutes": 0.0,
                   "max_attempts": attempts, "wall_minutes": float(wall)},
        "production": {}, "delivery": None, "error": None, "pause_requested": False,
        "proposal_generation": 1, "script_generation": 1, "production_revision": 1,
        "team_mode": team_mode, "workflow_version": workflow_version,
        "is_validation": bool(payload.get("is_validation", False)),
        "series_key": str(payload.get("series_key", "")).strip()[:100],
    }
    if profile is not None:
        project["production_profile"] = profile
    if workflow_version == 2:
        project["team_control_version"] = 1
    if contract is not None:
        project["creative_contract"] = contract
    if payload.get("prepare_materials"):
        project.update(status="paused", pause_requested=True, material_setup_requested=True)
    from .attention_actions import delegation_status
    project["delegation"] = delegation_status(project)
    import hashlib
    request_key = payload.get("request_id")
    request_hash = hashlib.sha256(encoded({k:v for k,v in payload.items() if k != "request_id"}).encode()).hexdigest()
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if request_key:
            if not isinstance(request_key, str) or len(request_key) > 100:
                raise ValueError("无效的请求标识")
            previous = db.execute("SELECT * FROM idempotency WHERE key=?", (request_key,)).fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise ValueError("请求标识已被其他请求使用")
                return _read(db, previous["project_id"])
        if project["series_key"]:
            rows = db.execute("SELECT value FROM memories WHERE key LIKE ? ORDER BY updated DESC LIMIT 5",
                              ("series:" + project["series_key"] + ":%",)).fetchall()
            project["memory"] = [json.loads(r["value"]) for r in rows]
            from .series_bible import bible
            pinned = bible(db, project["series_key"])
            if pinned:
                project["series_bible"] = pinned
        if profile is not None:
            from .profile_references import validated
            validated(db, project)
        from .material_library import inherit_package
        inherit_package(db, project)
        db.execute("INSERT INTO projects(id,updated,revision,snapshot) VALUES(?,?,?,?)",
                   (pid, time.time(), 1, encoded(project)))
        from .team_ledger import ensure_project
        ensure_project(db, project)
        _event(db, pid, "项目已建立，等待准备并确认本剧素材。" if project.get("material_setup_requested") else "已建立制片项目，开始检索和原创提案。", "created")
        if not project.get("material_setup_requested"):
            _enqueue(db, pid)
        if request_key:
            db.execute("INSERT INTO idempotency VALUES(?,?,?)", (request_key, pid, request_hash))
    return project


def get(pid, include_events=True):
    with connection() as db:
        p = _read(db, pid)
        if include_events:
            rows = db.execute("SELECT * FROM events WHERE project_id=? ORDER BY id DESC LIMIT 120", (pid,)).fetchall()
            p["events"] = [{"id":r["id"],"at":r["at"],"kind":r["kind"],"message":r["message"],
                            "data":json.loads(r["data"])} for r in reversed(rows)]
        return p


def list_projects():
    with connection() as db:
        rows = db.execute("SELECT snapshot FROM projects ORDER BY updated DESC LIMIT 100").fetchall()
    keys = ["id","slug","title","brief","format","status","stage","progress","created_at","updated_at",
            "duration_seconds","budget","is_validation","error","needs_decision","production","selected_story","selected_style"]
    result = []
    for row in rows:
        p = json.loads(row["snapshot"])
        value = {key:p.get(key) for key in keys}
        value["pending_decisions"] = sum(d.get("status") == "pending" for d in p.get("decisions", []))
        if not value["pending_decisions"] and isinstance(p.get("needs_decision"),dict) and p["needs_decision"]:
            value["pending_decisions"] = 1
        value["delivery_ready"] = bool(p.get("delivery"))
        result.append(value)
    return result


def update(pid, patch):
    patch = copy.deepcopy(patch)
    message = patch.pop("message", None)
    patch.pop("id", None)
    patch.pop("slug", None)
    patch.pop("events", None)
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        p = _read(db, pid)
        for key, value in patch.items():
            if key in {"budget","production"} and isinstance(value, dict):
                p.setdefault(key, {}).update(value)
            else:
                p[key] = value
        if "episode" in patch and p.get("production_profile"):
            from .profile_references import synchronize
            synchronize(db, p)
        if "episode" in patch and p.get("material_package"):
            from .material_library import record_dependencies
            record_dependencies(db, p)
        gpu_seconds = p.get("production", {}).get("gpu_seconds")
        if isinstance(gpu_seconds, (float, int)) and math.isfinite(gpu_seconds):
            p["budget"]["spent_gpu_minutes"] = round(gpu_seconds / 60, 3)
        _write(db, p)
        if message:
            _event(db, pid, message)
        return p


def gate(pid, kind, options, title, version):
    options = copy.deepcopy(options)
    if kind == "style" and not any(o.get("id") == "__restyle__" for o in options if isinstance(o, dict)):
        options.append({"id": "__restyle__", "label": "全部不满意，重新设计"})
    ident = f"{pid}:{kind}:{version}"
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        p = _read(db, pid)
        current = next((d for d in p["decisions"] if d["id"] == ident), None)
        if current:
            return current
        for old in p["decisions"]:
            if old["status"] == "pending":
                old["status"] = "superseded"
        result = {"id": ident, "type": kind, "title": title, "options": options,
                  "status": "pending", "created_at": now()}
        p["decisions"].append(result)
        p["status"] = "paused" if p.get("pause_requested") else "awaiting_" + kind
        p["stage"] = kind + "_decision"
        _write(db, p)
        _event(db, pid, title, "decision_required", {"decision_id":ident,"type":kind})
        return result


def commit_decision(pid, answer, patch, route):
    """Apply a gate's effects and its receipt in one database transaction."""
    patch=copy.deepcopy(patch)
    message=patch.pop("message",None)
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        p=_read(db,pid)
        effects=p.setdefault("decision_effects",{})
        if answer["gate_id"] in effects:
            return {"route":effects[answer["gate_id"]]["route"]}
        if p.get("answers",{}).get(answer["gate_id"]) != answer:
            raise ValueError("决策回执不匹配")
        p.update(patch)
        effects[answer["gate_id"]]={"route":route,"applied_at":now()}
        _write(db,p)
        if message:
            _event(db,pid,message)
    return {"route":route}


def answer_for(pid, gate_id):
    return get(pid, False).get("answers", {}).get(gate_id)


def _check_revision(project, payload):
    expected = payload.get("expected_revision")
    if "expected_revision" in payload and (type(expected) is not int or expected != project["revision"]):
        raise ValueError("项目已更新，已保留你的意见；请刷新当前状态后再提交")


def decide(pid, payload, actor=None):
    kind = payload.get("type")
    choice = payload.get("choice")
    feedback = str(payload.get("feedback", "")).strip()[:4000]
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        p = _read(db, pid)
        if payload.get("decision_id"):
            d = next((d for d in p["decisions"] if d["id"] == payload["decision_id"]), None)
            if d and d.get("status") == "answered":
                answer = p["answers"].get(d["id"])
                if answer and answer["choice"] == choice and answer.get("feedback","") == feedback:
                    return p
                raise ValueError("该决策已经处理，请刷新项目状态")
        else:
            d = next((d for d in reversed(p["decisions"]) if d["type"] == kind and d["status"] == "pending"), None)
        _check_revision(p, payload)
        if not d or d["type"] != kind or d["status"] != "pending":
            raise ValueError("当前没有对应的待办决策，请刷新后再选择")
        from .attention_actions import validate_gate
        validate_gate(p, d, choice)
        if choice == "__restyle__" and not feedback:
            raise ValueError("请说明需要调整的人物或画风要求")
        if kind == "final" and choice in {"revise", "reedit"} and not feedback:
            raise ValueError("请说明需要修改的镜头或剪辑意见")
        answer = {"gate_id": d["id"], "type":kind, "choice":choice, "feedback":feedback,
                  "actor": actor or ("validation_agent" if p.get("is_validation") else "producer"),
                  "at":now()}
        p["answers"][d["id"]] = answer
        d.update(status="answered", answer=answer)
        p["pause_requested"] = False
        p["status"] = "queued"
        p["error"] = None
        _write(db, p)
        _event(db, pid, "已记录选择，制片团队继续执行。", "decision", answer)
        _enqueue(db, pid)
        return p


def _revision_feedback(project, payload):
    text = payload.get("feedback", "")
    if "shot_ids" in payload and not isinstance(text, str):
        raise ValueError("使用 shot_ids 时 feedback 必须是文本")
    text = str(text)[:4000]
    feedback = {"notes": text, "shot_ids": payload["shot_ids"]} if "shot_ids" in payload else text
    episode = project.get("episode") or project.get("script") or {}
    scope = revision_shot_ids(feedback, [shot["id"] for shot in episode.get("shots", [])])
    if isinstance(feedback, dict):
        feedback["shot_ids"] = scope
    return feedback


def _pause(db, project):
    """Shared transaction-local pause boundary; never cancel a provider request."""
    from .remote_tasks import settle_wait
    settle_wait(project)
    if project["status"] in TERMINAL:
        raise ValueError("已完成项目不需要暂停")
    pid = project["id"]
    project["pause_requested"] = True
    if not db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='running'", (pid,)).fetchone():
        project["status"] = "paused"
        db.execute("UPDATE jobs SET status='cancelled',finished=? WHERE project_id=? AND status='queued'",
                   (time.time(), pid))
    _event(db, pid, "暂停请求已记录；当前执行单元结束后保留进度。", "pause")


def action(pid, payload):
    from .attention_actions import REGISTRY, RECOVERY_ACTIONS, recover, _restart
    command = payload.get("action")
    if command not in REGISTRY:
        raise ValueError("不支持的项目操作")
    if command in RECOVERY_ACTIONS | {"take_control", "delegate_control", "request_review_advice", "continue_auto_review"} and type(payload.get("expected_revision")) is not int:
        raise ValueError("此操作需要当前项目的 expected_revision，请刷新后提交")
    if command == "repropose":
        return decide(pid, {**payload, "type":"story", "choice":"__repropose__"})
    if command == "regenerate":
        project = get(pid, False)
        if project.get("status") in {"awaiting_story", "awaiting_style"}:
            kind = "story" if project["status"] == "awaiting_story" else "style"
            return decide(pid, {**payload, "type": kind,
                               "choice": "__repropose__" if kind == "story" else "__restyle__"})
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        p = _read(db, pid)
        if command == "continue_auto_review":
            prior = p.get("auto_review_continuation") or {}
            if (p.get("status") == "queued" and payload.get("expected_revision") == prior.get("source_revision")
                    and p.get("script_generation") == prior.get("script_generation")
                    and p.get("creative_round") == prior.get("round")
                    and db.execute("SELECT 1 FROM jobs WHERE project_id=? AND kind='workflow' AND status IN ('queued','running')", (pid,)).fetchone()):
                return p
        if command == "request_review_advice":
            from .review_advice import eligible as advice_eligible, queued_record, signature, retry_unconfigured
            if not advice_eligible(p):
                raise ValueError("仅当前创作审核未通过时可请求 AI 建议")
            previous = p.get("review_advice") or {}
            if (previous.get("input_signature") == signature(p)
                    and payload.get("expected_revision") in {p["revision"], previous.get("source_revision")}
                    and not retry_unconfigured(previous)):
                return p
        if command != "revise":
            _check_revision(p, payload)
        if command not in {"pause", "take_control", "request_review_advice"} and db.execute(
                "SELECT 1 FROM jobs WHERE project_id=? AND kind='review_advice' AND status IN ('queued','running')", (pid,)).fetchone():
            raise ValueError("当前 AI 分析任务正在收尾，请完成后再提交创作修改")
        if command == "continue_auto_review":
            from .review_advice import can_continue_auto_review, ensure_no_gpu_intents
            if not can_continue_auto_review(p):
                raise ValueError("当前没有可继续的自动返修轮次；不会重置版本或提高审核上限")
            if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('running','queued')", (pid,)).fetchone():
                raise ValueError("当前项目仍有任务排队或执行，请完成后再继续自动返修")
            if db.execute("SELECT 1 FROM cloud_tasks WHERE project_id=? AND status NOT IN ('downloaded','failed','rejected')", (pid,)).fetchone():
                raise ValueError("已有云任务尚未确认完成，请先核对原提交")
            ensure_no_gpu_intents(p)
            p["auto_review_continuation"] = {"source_revision": p["revision"], "script_generation": p["script_generation"],
                                             "round": p["creative_round"], "at": now()}
            p.update(status="queued", pause_requested=False, error=None)
            p.pop("needs_decision", None)
            _enqueue(db, pid)
            _event(db, pid, "保留当前稿件和历史审核，从原检查点继续剩余自动返修。", "auto_review_continuation", p["auto_review_continuation"])
        elif command == "request_review_advice":
            if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('running','queued')", (pid,)).fetchone():
                raise ValueError("当前任务正在执行或等待执行，请完成后再请求建议")
            p["review_advice"] = queued_record(p)
            _enqueue(db, pid, kind="review_advice")
            _event(db, pid, "已安排总制片分析审核问题；完成后由你决定是否采纳。", "review_advice", {"status": "queued"})
        elif command in RECOVERY_ACTIONS:
            if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND (status='running' OR (status='queued' AND kind='review_advice'))", (pid,)).fetchone():
                raise ValueError("当前任务正在收尾，请完成后再提交创作修改")
            recover(p, payload)
            _enqueue(db, pid)
            _event(db, pid, REGISTRY[command]["label"] + "：已保存并安排重新审核。", "recovery_action",
                   {"action": command, "restart_from": p.get("workflow_restart_from")})
        elif command == "take_control":
            if p["status"] in TERMINAL:
                raise ValueError("已完成项目不能切换控制方式")
            p["team_mode"] = "guided"
            p["delegation"] = {"requested": False, "available": False, "mode": "human", "reason_code": "producer_took_control", "reason": "出品人已接管后续故事与视觉选择"}
            if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='running'", (pid,)).fetchone():
                _pause(db, p)
                p["control_change_reason"] = "出品人接管：当前安全步骤完成后暂停，恢复时采用人工控制"
            _event(db, pid, "出品人已接管后续创作决策。", "team_mode", {"team_mode": "guided"})
        elif command == "delegate_control":
            if p["status"] in TERMINAL or p["status"] == "awaiting_final":
                raise ValueError("终审和已完成项目不能转交自动决策")
            if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='running'", (pid,)).fetchone():
                raise ValueError("请等待当前执行步骤完成后再转交总制片")
            if db.execute("SELECT 1 FROM cloud_tasks WHERE project_id=? AND status IN ('submitting','submission_unknown')", (pid,)).fetchone() or str((p.get("needs_decision") or {}).get("code", "")).startswith("cloud_"):
                raise ValueError("请先核对云任务提交与费用授权，再转交总制片")
            from .attention_actions import delegation_status
            policy = delegation_status({**p, "team_mode": "delegated"})
            if not policy["available"]:
                raise ValueError(policy["reason"])
            p.update(team_mode="delegated", delegation=policy)
            if p["status"] in {"awaiting_story", "awaiting_style"}:
                kind = "story" if p["status"] == "awaiting_story" else "style"
                previous = p.get("supervision", {}).get(kind)
                if previous and previous.get("status") == "requested":
                    raise ValueError("此前总制片调用结果尚未确认，请先人工处理当前方案")
                if previous:
                    # Its full receipt remains in the event log. Explicitly
                    # handing control back permits a new bounded model review.
                    p["supervision"].pop(kind, None)
                    _event(db, pid, "出品人重新委托当前关卡的总制片评审。", "supervision_redelegated",
                           {"gate_id": previous.get("gate_id"), "previous_run_id": previous.get("run_id")})
                p["status"] = "queued"
                _enqueue(db, pid)
            _event(db, pid, "后续故事与视觉交由总制片在授权范围内推进。", "team_mode", {"team_mode": "delegated"})
        elif command == "pause":
            _pause(db, p)
        elif command == "revise":
            expected = payload.get("expected_revision")
            if isinstance(expected, bool) or not isinstance(expected, int):
                raise ValueError("局部修改需要当前项目的 expected_revision，请刷新后提交")
            text = payload.get("feedback")
            if not isinstance(text, str) or not text.strip() or len(text.strip()) > 4000:
                raise ValueError("请提供 1 至 4000 字的局部修改说明")
            if "shot_ids" not in payload:
                raise ValueError("制作阻断后的局部修改必须明确提供 shot_ids")
            feedback = _revision_feedback(p, {**payload, "feedback": text.strip()})
            previous = p.get("revision_request") or {}
            if previous.get("origin") == "production_block" and previous.get("source_revision") == expected:
                if previous.get("feedback") == feedback:
                    return p
                raise ValueError("该版本的局部修改已经受理，请刷新后再提交不同意见")
            if expected != p["revision"]:
                raise ValueError("项目状态已更新，请刷新后提交局部修改")
            need = p.get("needs_decision") or {}
            episode = p.get("episode") or p.get("script") or {}
            if (p["status"] != "blocked" or need.get("type") != "production"
                    or not p.get("selected_story") or not p.get("selected_style")
                    or not episode.get("shots")):
                raise ValueError("仅制作阻断且已选定故事、画风的现有剧本可在此局部修改")
            request = {
                "id": uuid.uuid4().hex, "origin": "production_block", "choice": "revise",
                "feedback": feedback, "source_revision": expected,
                "source_production_revision": p["production_revision"],
                "blocked_reason": copy.deepcopy(need), "at": now(),
                "actor": "validation_agent" if p.get("is_validation") else "producer",
            }
            p.update(revision_request=request, production_revision=p["production_revision"] + 1,
                     script_generation=p["script_generation"] + 1, pause_requested=False,
                     error=None, status="queued", stage="revision", delivery=None)
            p.pop("needs_decision", None)
            _enqueue(db, pid)
            _event(db, pid, "已记录指定镜头的局部修改；累计预算、历史素材与未修改镜头继续保留。",
                   "revision", request)
        elif command in {"resume","retry"}:
            if "review_only_existing_videos" in payload and type(payload["review_only_existing_videos"]) is not bool:
                raise ValueError("review_only_existing_videos 必须是布尔值")
            if p["status"] in TERMINAL:
                raise ValueError("项目已完成")
            if p["status"] not in {"blocked","failed","paused"} and not p.get("pause_requested"):
                return p
            need=p.get("needs_decision") or {}
            if need.get("type")=="budget":
                exhausted=need.get("exhausted_limits") or ["gpu"]
                for label,key,used in [
                    ("GPU","gpu_minutes",p.get("production",{}).get("gpu_seconds",0)/60),
                    ("制作时间","wall_minutes",p.get("production",{}).get("elapsed_seconds",0)/60)]:
                    required=("gpu" if key=="gpu_minutes" else "wall") in exhausted
                    value=payload.get(key)
                    if required and value is None:
                        raise ValueError(label+"预算已用尽，请提供新的 "+key+" 上限")
                    if value is not None:
                        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not max(p["budget"][key],used) < value <= 1440:
                            raise ValueError(label+"新上限必须大于当前额度和已用时间，且不超过1440分钟")
                        p["budget"][key]=float(value)
            creative_block=bool(need) and need.get("type") not in {"budget","production","runtime"}
            script_retry=creative_block and p.get("selected_story") and not p.get("selected_style")
            if "shot_ids" in payload and not (script_retry or need.get("type") == "edit_feedback"):
                raise ValueError("shot_ids 仅用于已有剧本的修改重试")
            if creative_block and not p.get("selected_story"):
                p["proposal_generation"] += 1
                p["story_feedback"] = str(payload.get("feedback", ""))[:4000]
            elif script_retry:
                feedback = _revision_feedback(p, payload)
                p["script_generation"] += 1
                p["script_feedback"] = feedback
            if need.get("type") == "edit_feedback" and (payload.get("feedback") or "shot_ids" in payload):
                p["revision_request"]["feedback"] = _revision_feedback(p, payload)
            # A creative failure can happen inside an existing revision before
            # it is wrapped as edit_feedback. Keep the new producer direction.
            elif creative_block and p.get("selected_style") and payload.get("feedback"):
                if p.get("revision_request"):
                    p["revision_request"]["feedback"] = _revision_feedback(p, payload)
                    p.pop("revision_prepared", None)
                else:
                    from .attention_actions import recover as recover_creative
                    recover_creative(p, {**payload, "action": "provide_feedback"})
            if creative_block and p.get("workflow_version", 1) >= 2:
                target = ("revision" if p.get("revision_request") else "story_resolution"
                          if p.get("selected_story") else "research" if need.get("kind") == "source_rights" else "proposals")
                _restart(p, target)
            if "review_only_existing_videos" in payload:
                p.setdefault("production", {})["review_only_existing_videos"] = payload["review_only_existing_videos"]
            if payload.get("review_only_existing_videos") is True:
                from .shot_workbench import project_root, retained_video_results
                from studio.core import ROOT
                p.setdefault("production", {})["review_only_video_results"] = [
                    str(path.relative_to(ROOT)) for path in retained_video_results(project_root(p))]
            p.update(pause_requested=False, error=None, status="queued")
            p.pop("needs_decision", None)
            _enqueue(db, pid)
            _event(db, pid, "从已保存的检查点继续。", "resume")
        else:
            raise ValueError("不支持的项目操作")
        _write(db, p)
        return p


def queue_summary():
    with connection() as db:
        counts = {r["status"]:r["n"] for r in db.execute("SELECT status,count(*) AS n FROM jobs GROUP BY status")}
    return {"queued": counts.get("queued",0), "running":counts.get("running",0),
            "completed":counts.get("completed",0), "worker_concurrency":1, "durable":True}


def recover_jobs():
    from .remote_tasks import recover_jobs as recover
    return recover()


def claim_job(project_id=None):
    from .remote_tasks import claim_job as claim
    return claim(project_id)


def finish_job(jid, error=None, lease=None):
    from .remote_tasks import finish_job as finish
    return finish(jid, error, lease)

def remember(pid):
    p = get(pid, False)
    if p.get("is_validation"):
        return
    story = next((v for v in p.get("proposals",[]) if v["id"] == p.get("selected_story")), {})
    data = {"project_id":pid,"title":p["title"],"format":p["format"],"story":story,
            "cast":(p.get("script") or {}).get("cast",{}),"style":p.get("selected_style"),
            "ending":story.get("ending"),"approved_at":now(),
            "decisions":[d.get("answer") for d in p["decisions"] if d.get("answer")]}
    key = ("series:" + p["series_key"] + ":" if p.get("series_key") else "project:") + pid
    with connection() as db:
        db.execute("INSERT OR REPLACE INTO memories VALUES(?,?,?,?)", (key,pid,encoded(data),time.time()))
