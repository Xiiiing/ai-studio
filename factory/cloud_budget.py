"""Durable, fail-closed consent for paid cloud video; estimates are not bills.

Amounts are integer millionths of one currency unit. Reservations never expire
or reset on pause/retry: a crash without a confirmed outcome requires reconciliation.
"""
from __future__ import annotations

import copy
import json
import re
import time
import uuid

from studio.core import fingerprint
from . import store
from .minimax_video import video_identity
from .scheduler import ProductionNeedsDecision

SCHEMA = """
CREATE TABLE IF NOT EXISTS cloud_batches(
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 production_revision INTEGER NOT NULL, source_signature TEXT NOT NULL,
 scope TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS cloud_batches_project ON cloud_batches(project_id,created);
CREATE TABLE IF NOT EXISTS cloud_authorizations(
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES cloud_batches(id),
 project_id TEXT NOT NULL REFERENCES projects(id), source_revision INTEGER NOT NULL,
 limits_json TEXT NOT NULL, actor TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS cloud_authorizations_batch ON cloud_authorizations(batch_id,created);
CREATE TABLE IF NOT EXISTS cloud_tasks(
 intent_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 batch_id TEXT REFERENCES cloud_batches(id), authorization_id TEXT REFERENCES cloud_authorizations(id),
 shot_id TEXT NOT NULL, attempt INTEGER NOT NULL, take TEXT NOT NULL,
 provider TEXT NOT NULL, model TEXT NOT NULL, request_signature TEXT NOT NULL,
 task_id TEXT, file_id TEXT, status TEXT NOT NULL,
 estimated_cost_micros INTEGER, confirmed_cost_micros INTEGER, currency TEXT NOT NULL,
 created REAL NOT NULL, updated REAL NOT NULL, UNIQUE(project_id,shot_id,take));
CREATE INDEX IF NOT EXISTS cloud_tasks_project ON cloud_tasks(project_id,created);
CREATE TABLE IF NOT EXISTS cloud_cost_events(
 id TEXT PRIMARY KEY, intent_id TEXT NOT NULL REFERENCES cloud_tasks(intent_id),
 amount_micros INTEGER NOT NULL, currency TEXT NOT NULL, evidence TEXT NOT NULL,
 actor TEXT NOT NULL, created REAL NOT NULL);
"""


def initialize(db):
    db.executescript(SCHEMA)
    db.execute("CREATE TABLE IF NOT EXISTS cloud_media_receipts(intent_id TEXT PRIMARY KEY REFERENCES cloud_tasks(intent_id), media_kind TEXT NOT NULL, request_id TEXT, usage_json TEXT NOT NULL)")


def _integer(value, label, low=0, high=10**15):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(label + " 必须是范围内的整数")
    return value


def pricing(value=None):
    value = {} if value is None else copy.deepcopy(value)
    if not isinstance(value, dict) or set(value) - {"unit_cost_micros", "currency", "source"}:
        raise ValueError("视频估价仅支持 unit_cost_micros、currency、source")
    currency = value.get("currency", "CNY")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("费用币种应为三个大写字母")
    amount = value.get("unit_cost_micros")
    if amount is not None:
        _integer(amount, "单次估价", high=10**12)
    source = value.get("source", "")
    if not isinstance(source, str) or len(source) > 500:
        raise ValueError("请使用至多500字的估价来源说明")
    return {"unit_cost_micros": amount, "currency": currency, "source": source,
            "status": "unconfirmed" if amount is None else "estimate", "billing_hard_limit": False}


def connection_identity(profile):
    if profile.get("_media_kind"):
        from .media_profiles import identity
        return {**identity(profile), "api_key_env": profile.get("api_key_env")}
    return video_identity(profile)


def source_signature(project, profile):
    return fingerprint({"production_revision": project.get("production_revision", 1),
                        "episode": project.get("episode") or project.get("script") or {},
                        "production_profile": project.get("production_profile"),
                        "selected_style": project.get("selected_style"), "styles": [
                            {k: v for k, v in style.items() if not k.startswith("preview")}
                            for style in project.get("styles", [])
                            if style.get("id") == project.get("selected_style")],
                        "video": connection_identity(profile), "pricing": pricing(profile.get("pricing"))})


def request_line(pid, shot_id, record, frame_sha256, profile, timeline_seconds):
    signature = fingerprint({"profile": video_identity(profile), "prompt": record["prompt"],
                             "first_frame_sha256": frame_sha256})
    return {"intent_id": fingerprint({"project_id": pid, "shot_id": shot_id, "take": record["take"]}),
            "shot_id": shot_id, "attempt": record["attempt"], "take": record["take"],
            "provider": profile["provider"], "model": profile["model"],
            "request_signature": signature, "source_signature": record["source_signature"],
            "prompt": record["prompt"], "first_frame_sha256": frame_sha256,
            "duration": profile.get("duration"), "resolution": profile.get("resolution"),
             **({"media_kind":profile["_media_kind"]} if profile.get("_media_kind") else {}),
            "timeline_seconds": timeline_seconds}


def _authorization(db, batch_id):
    row = db.execute("SELECT * FROM cloud_authorizations WHERE batch_id=? ORDER BY created DESC,rowid DESC LIMIT 1",
                     (batch_id,)).fetchone()
    return {**dict(row), "limits": json.loads(row["limits_json"])} if row else None


def _batch(db, batch_id):
    row = db.execute("SELECT * FROM cloud_batches WHERE id=?", (batch_id,)).fetchone()
    if row is None:
        raise KeyError("云任务批次不存在")
    return {**dict(row), "scope": json.loads(row["scope"])}


def _consumed(db, batch_id):
    rows = db.execute("SELECT estimated_cost_micros,confirmed_cost_micros FROM cloud_tasks WHERE batch_id=?",
                      (batch_id,)).fetchall()
    return len(rows), sum(max(row["estimated_cost_micros"] or 0, row["confirmed_cost_micros"] or 0) for row in rows)


def prepare_batch(project, profile, lines):
    """Persist exact requests, or reuse an authorized superset after restart."""
    if not lines or len(lines) > 1000:
        raise ValueError("云任务批次应包含1至1000次明确提交")
    if len({v["intent_id"] for v in lines}) != len(lines):
        raise ValueError("云任务批次包含重复的生成意图")
    for line in lines:
        _integer(line["attempt"], "生成次数", 1, 1000)
    tariff = pricing(profile.get("pricing"))
    context = source_signature(project, profile)
    scope = {"project_id": project["id"], "production_revision": project.get("production_revision", 1),
             "source_signature": context, "connection": connection_identity(profile),
             "shot_count": len({v["shot_id"] for v in lines}), "estimated_submissions": len(lines),
             "duration": profile.get("duration"), "resolution": profile.get("resolution"),
             **({"media_kind":profile["_media_kind"]} if profile.get("_media_kind") else {}), "pricing": tariff,
             "estimated_total_micros": None if tariff["unit_cost_micros"] is None else tariff["unit_cost_micros"] * len(lines),
             "lines": sorted(copy.deepcopy(lines), key=lambda v: v["intent_id"])}
    ident = fingerprint(scope)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        current = store._read(db, project["id"])
        if source_signature(current, profile) != context:
            raise ProductionNeedsDecision("制作参数已变化，请重新预估费用", "cloud_parameters_changed")
        candidates = db.execute("SELECT id FROM cloud_batches WHERE project_id=? AND source_signature=? ORDER BY created DESC",
                                (project["id"], context)).fetchall()
        for candidate in candidates:
            existing = _batch(db, candidate["id"])
            previous = {v["intent_id"]: v for v in existing["scope"]["lines"]}
            if _authorization(db, existing["id"]) and all(previous.get(v["intent_id"]) == v for v in lines):
                return existing
        db.execute("INSERT OR IGNORE INTO cloud_batches VALUES(?,?,?,?,?,?)",
                   (ident, project["id"], scope["production_revision"], context, store.encoded(scope), time.time()))
        if (current.get("cloud_quote") or {}).get("id") != ident:
            current["cloud_quote"] = {"id": ident, **scope}
            store._write(db, current)
            store._event(db, project["id"], "云媒体批次估价已保存，新的付费提交等待出品人授权。", "cloud_quote",
                         {"batch_id": ident, "estimated_submissions": len(lines)})
        return _batch(db, ident)


def ensure_batch(project, profile, lines):
    batch = prepare_batch(project, profile, lines)
    with store.connection() as db:
        auth = _authorization(db, batch["id"])
        used, cost = _consumed(db, batch["id"])
        reserved = {r["intent_id"] for r in db.execute("SELECT intent_id FROM cloud_tasks WHERE batch_id=?", (batch["id"],))}
        fresh = [line for line in lines if line["intent_id"] not in reserved]
        unit = batch["scope"]["pricing"]["unit_cost_micros"]
        if not auth:
            raise ProductionNeedsDecision("付费云媒体尚未授权；请核对模型、镜头参数与费用预估", "cloud_authorization_required",
                                          {"batch_id": batch["id"], "quote": batch["scope"]})
        limits = auth["limits"]
        if (used + len(fresh) > limits["max_submissions"] or (unit is None and not limits["confirm_unknown_cost"]) or
                (unit is not None and limits["max_estimated_cost_micros"] is not None and
                 cost + len(fresh) * unit > limits["max_estimated_cost_micros"])):
            raise ProductionNeedsDecision("本批次剩余授权不足；未发出新的付费提交", "cloud_budget_exceeded",
                                          {"batch_id": batch["id"], "consumed_submissions": used})
    return batch


def authorize(pid, payload, profile):
    allowed = {"batch_id", "expected_revision", "max_submissions", "max_estimated_cost_micros", "confirm_unknown_cost"}
    if set(payload) - allowed:
        raise ValueError("未知云任务授权字段")
    expected = _integer(payload.get("expected_revision"), "expected_revision", 1)
    cap = _integer(payload.get("max_submissions"), "最多提交次数", 1, 1000)
    money = payload.get("max_estimated_cost_micros")
    if money is not None:
        _integer(money, "预估费用额度")
    unknown = payload.get("confirm_unknown_cost", False)
    if type(unknown) is not bool:
        raise ValueError("未知费用确认必须为布尔值")
    limits = {"max_submissions": cap, "max_estimated_cost_micros": money, "confirm_unknown_cost": unknown}
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        project = store._read(db, pid)
        batch = _batch(db, payload.get("batch_id"))
        if batch["project_id"] != pid:
            raise ValueError("授权批次不属于此项目")
        if batch["source_signature"] != source_signature(project, profile):
            raise ValueError("模型、制作版本或镜头参数已变化，原估价不能再授权")
        prior = _authorization(db, batch["id"])
        if prior and prior["source_revision"] == expected and prior["limits"] == limits:
            return project
        if project["revision"] != expected:
            raise ValueError("项目已更新，请刷新后授权")
        if project["status"] in store.TERMINAL:
            raise ValueError("已完成项目不能授权新的云任务")
        if batch["scope"]["pricing"]["unit_cost_micros"] is None and not unknown:
            raise ValueError("价格未确认，必须明确确认未知费用风险")
        db.execute("INSERT INTO cloud_authorizations VALUES(?,?,?,?,?,?,?)",
                   (uuid.uuid4().hex, batch["id"], pid, expected, store.encoded(limits), "producer", time.time()))
        need = project.get("needs_decision") or {}
        if need.get("code", "").startswith("cloud_"):
            project.update(status="queued", pause_requested=False, error=None, needs_decision=None)
            store._enqueue(db, pid)
        store._write(db, project)
        store._event(db, pid, "已授权指定云媒体批次；费用仅为估算，不代表供应商账单硬限额。",
                     "cloud_authorized", {"batch_id": batch["id"], **limits})
        return project


def validate_live_profile(profile):
    # Read the configuration file rather than the graph's frozen ContextVar.
    # A settings edit invalidates permission for the next, not an in-flight POST.
    from .config import config_read_path
    from .video_providers import resolve_video
    from studio.core import read_json
    live=read_json(config_read_path())
    if profile.get("_media_kind"):
        from .media_profiles import resolve
        current={**resolve(live,profile["_media_kind"]),"_media_kind":profile["_media_kind"]}
    else:
        current = resolve_video(live)
    if connection_identity(current) != connection_identity(profile) or pricing(current.get("pricing")) != pricing(profile.get("pricing")):
        raise ProductionNeedsDecision("已保存的视频连接发生变化，请重新核对并授权", "cloud_parameters_changed")


def reserve(pid, batch_id, line, profile, request):
    """Last boundary before POST, serialized across competing processes."""
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        project = store._read(db, pid)
        batch = _batch(db, batch_id)
        if project.get("pause_requested") or project["status"] in store.TERMINAL | {"paused"}:
            raise ProductionNeedsDecision("项目已暂停，禁止新增付费提交", "cloud_project_paused")
        if batch["project_id"] != pid or source_signature(project, profile) != batch["source_signature"]:
            raise ProductionNeedsDecision("授权的生产版本或参数已变化", "cloud_parameters_changed")
        authorized = next((v for v in batch["scope"]["lines"] if v["intent_id"] == line["intent_id"]), None)
        if (authorized != line or request.get("request_fingerprint") != line["request_signature"] or
                request.get("source_signature") != line["source_signature"]):
            raise ProductionNeedsDecision("请求不属于已授权的模型和镜头参数", "cloud_request_changed")
        existing = db.execute("SELECT * FROM cloud_tasks WHERE intent_id=? OR (project_id=? AND shot_id=? AND take=?)",
                              (line["intent_id"], pid, line["shot_id"], line["take"])).fetchone()
        if existing:
            raise ProductionNeedsDecision("该生成意图已持久化；必须恢复或人工核对，不能再次 POST", "cloud_submission_unknown",
                                          {"intent_id": existing["intent_id"], "task_id": existing["task_id"]})
        unresolved = db.execute("SELECT intent_id,task_id FROM cloud_tasks WHERE project_id=? AND shot_id=? "
                                "AND status NOT IN ('downloaded','failed','rejected') LIMIT 1", (pid, line["shot_id"])).fetchone()
        if unresolved:
            raise ProductionNeedsDecision("同一镜头尚有未核对的云任务；不能用新版本绕过恢复保护", "cloud_unresolved_prior_task",
                                          dict(unresolved))
        auth = _authorization(db, batch_id)
        if not auth:
            raise ProductionNeedsDecision("云媒体批次未获授权", "cloud_authorization_required")
        limits = auth["limits"]
        count, cost = _consumed(db, batch_id)
        unit = batch["scope"]["pricing"]["unit_cost_micros"]
        if count >= limits["max_submissions"] or (unit is None and not limits["confirm_unknown_cost"]) or (
                unit is not None and limits["max_estimated_cost_micros"] is not None and cost + unit > limits["max_estimated_cost_micros"]):
            raise ProductionNeedsDecision("云媒体提交超出授权范围", "cloud_budget_exceeded")
        from .remote_tasks import MAX_REMOTE_INFLIGHT, ProductionDeferred
        active = db.execute("SELECT count(*) FROM cloud_tasks WHERE status NOT IN ('downloaded','failed','rejected')").fetchone()[0]
        if active >= MAX_REMOTE_INFLIGHT:
            raise ProductionDeferred(time.time() + 5, "remote_submit", {"reason": "remote_capacity", "limit": MAX_REMOTE_INFLIGHT})
        now = time.time()
        db.execute("""INSERT INTO cloud_tasks(intent_id,project_id,batch_id,authorization_id,shot_id,attempt,take,
                   provider,model,request_signature,status,estimated_cost_micros,currency,created,updated)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (line["intent_id"], pid, batch_id, auth["id"], line["shot_id"], line["attempt"], line["take"],
                    line["provider"], line["model"], line["request_signature"], "submitting", unit,
                    batch["scope"]["pricing"]["currency"], now, now))
        return {"intent_id": line["intent_id"], "batch_id": batch_id, "authorization_id": auth["id"]}


def reconcile_task(pid, line, task, profile):
    """Import legacy observations lazily; journal writes can recover a lagging DB."""
    if not task:
        return
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        store._read(db, pid)
        row = db.execute("SELECT * FROM cloud_tasks WHERE intent_id=?", (line["intent_id"],)).fetchone()
        for field in ("task_id", "file_id"):
            if row and row[field] and task.get(field) and row[field] != str(task[field]):
                raise ValueError("账本 " + field + " 不匹配，拒绝覆盖")
        now = time.time()
        if not row:
            db.execute("""INSERT INTO cloud_tasks(intent_id,project_id,shot_id,attempt,take,provider,model,
                       request_signature,status,currency,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (line["intent_id"], pid, line["shot_id"], line["attempt"], line["take"], line["provider"],
                        line["model"], line["request_signature"], "legacy_observed", pricing(profile.get("pricing"))["currency"], now, now))
        status = task.get("status", "submission_unknown")
        if row and row["status"] in {"downloaded", "failed", "rejected"}:
            status = row["status"]
        db.execute("UPDATE cloud_tasks SET task_id=COALESCE(?,task_id),file_id=COALESCE(?,file_id),status=?,updated=? WHERE intent_id=?",
                   (str(task["task_id"]) if task.get("task_id") else None,
                    str(task["file_id"]) if task.get("file_id") else None, status, now, line["intent_id"]))
        from .remote_tasks import phase_for
        phase = phase_for({**task, "status": status})
        db.execute("UPDATE cloud_tasks SET phase=?,next_poll_at=?,poll_failures=? WHERE intent_id=?",
                   (phase, task.get("next_poll_at"), int(task.get("poll_failures") or 0), line["intent_id"]))


def confirm_cost(pid, payload):
    amount = _integer(payload.get("amount_micros"), "已确认费用")
    evidence = payload.get("evidence")
    if not isinstance(evidence, str) or not 1 <= len(evidence.strip()) <= 2000:
        raise ValueError("确认费用需要账单依据说明")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM cloud_tasks WHERE intent_id=? AND project_id=?",
                         (payload.get("intent_id"), pid)).fetchone()
        if not row:
            raise KeyError("云任务不存在")
        if payload.get("currency") != row["currency"]:
            raise ValueError("已确认费用币种与账本不一致")
        db.execute("INSERT INTO cloud_cost_events VALUES(?,?,?,?,?,?,?)",
                   (uuid.uuid4().hex, row["intent_id"], amount, row["currency"], evidence.strip(), "producer", time.time()))
        db.execute("UPDATE cloud_tasks SET confirmed_cost_micros=?,updated=? WHERE intent_id=?",
                   (amount, time.time(), row["intent_id"]))
    return summary(pid)


def summary(pid):
    with store.connection() as db:
        project = store._read(db, pid)
        rows = [dict(r) for r in db.execute("SELECT * FROM cloud_tasks WHERE project_id=? ORDER BY created,intent_id", (pid,))]
        authorizations = [{k: v for k, v in dict(r).items() if k != "limits_json"} | {"limits": json.loads(r["limits_json"])}
                          for r in db.execute("SELECT * FROM cloud_authorizations WHERE project_id=? ORDER BY created", (pid,))]
        totals = {currency: {"estimated_cost_micros": sum(r["estimated_cost_micros"] or 0 for r in rows if r["currency"] == currency),
                            "confirmed_cost_micros": sum(r["confirmed_cost_micros"] or 0 for r in rows if r["currency"] == currency),
                            "unconfirmed_tasks": sum(r["confirmed_cost_micros"] is None for r in rows if r["currency"] == currency)}
                  for currency in sorted({r["currency"] for r in rows})}
        for row in rows:
            media = db.execute("SELECT media_kind,request_id,usage_json FROM cloud_media_receipts WHERE intent_id=?",(row["intent_id"],)).fetchone()
            row["media_kind"] = media["media_kind"] if media else row["shot_id"].split(":",1)[0] if row["shot_id"].startswith(("image:","audio:")) else "video"
            if media: row["usage"] = json.loads(media["usage_json"])
        return {"project_id": pid, "quote": project.get("cloud_quote"), "tasks": rows,
                "authorizations": authorizations, "totals": totals, "billing_hard_limit": False}
