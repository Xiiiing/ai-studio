"""Durable identities and evidence for the existing production executors.

The graph remains the stage authority. A planned task is never evidence of a
model call, an approved asset or a producer decision.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
import hashlib
import json
import re
import uuid

from . import store

DEPARTMENTS = (
    {"id": "direction", "label": "制片统筹", "lead": "executive_producer", "roles": ["executive_producer", "producer"], "responsibility": "跨部门安排、风险与制作汇报；出品人决定费用和终片。"},
    {"id": "story", "label": "故事编剧", "lead": "story_editor", "roles": ["researcher", "creator", "story_editor", "screenwriter"], "responsibility": "研究题材、提出故事、落实目标并创作剧本。"},
    {"id": "directing", "label": "导演分镜", "lead": "storyboard_director", "roles": ["storyboard_director"], "responsibility": "设计分镜、角色动作、机位和运镜。"},
    {"id": "visual", "label": "美术与影像", "lead": "visual_designer", "roles": ["visual_designer", "prompt_style_compiler", "image_generator", "video_generator"], "responsibility": "制作角色与场景参考、首帧和视频镜头。"},
    {"id": "sound", "label": "声音制作", "lead": "dialogue_editor", "roles": ["speech", "dialogue_editor"], "responsibility": "角色配音、对白调整与时长适配。"},
    {"id": "post", "label": "后期制作", "lead": "edit_director", "roles": ["edit_director", "editor"], "responsibility": "剪辑计划、字幕、混音与成片交付。"},
    {"id": "review", "label": "独立质检", "lead": "independent_critic", "roles": ["story_critic", "independent_critic", "visual_critic"], "responsibility": "独立检查故事、画面及连续性，提出有依据的返修。"},
)
ROLE_DEPARTMENT = {role: group["id"] for group in DEPARTMENTS for role in group["roles"]}
KINDS = {
    "character": ("角色参考", "image_generator"), "scene": ("场景参考", "image_generator"),
    "image": ("镜头首帧", "image_generator"), "video": ("镜头视频", "video_generator"),
    "image_review": ("首帧审核", "visual_critic"), "video_review": ("视频审核", "visual_critic"),
    "speech": ("角色配音", "speech"), "dialogue": ("对白时长检查", "dialogue_editor"),
    "edit": ("后期交付", "editor"), "technical_review": ("成片技术检查", "editor"),
    "management": ("制作安排", "executive_producer"),
    "script": ("剧本镜头单元", "screenwriter"), "dialogue_text": ("对白正文", "screenwriter"),
}
STATUSES = frozenset({"pending", "running", "waiting", "completed", "failed", "blocked", "paused", "superseded", "reused", "unknown"})
TASK_ID = re.compile(r"[a-z_]+:[A-Za-z0-9_.-]{1,120}\Z")


def initialize(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS team_control(
      project_id TEXT PRIMARY KEY REFERENCES projects(id), revision INTEGER NOT NULL DEFAULT 0,
      enabled INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS team_work_items(
      project_id TEXT NOT NULL REFERENCES projects(id), id TEXT NOT NULL,
      ordinal INTEGER NOT NULL, generation INTEGER NOT NULL DEFAULT 1,
      kind TEXT NOT NULL, scope TEXT NOT NULL, owner TEXT NOT NULL,
      department TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
      priority INTEGER NOT NULL DEFAULT 0, hold_after INTEGER NOT NULL DEFAULT 0,
      data TEXT NOT NULL, PRIMARY KEY(project_id,id));
    CREATE INDEX IF NOT EXISTS team_work_status ON team_work_items(project_id,status,ordinal);
    CREATE TABLE IF NOT EXISTS team_unit_attempts(
      run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
      generation INTEGER NOT NULL, attempt INTEGER NOT NULL, status TEXT NOT NULL,
      data TEXT NOT NULL, FOREIGN KEY(project_id,task_id) REFERENCES team_work_items(project_id,id));
    CREATE INDEX IF NOT EXISTS team_attempt_task ON team_unit_attempts(project_id,task_id,generation,attempt);
    """)


def enabled(project):
    return project.get("team_control_version") == 1 and project.get("workflow_version", 1) in {2, 3}


def fingerprint(value):
    return hashlib.sha256(store.encoded(value).encode()).hexdigest()


def _inputs(value):
    if isinstance(value, dict):
        return [{"label": key, "text": item if isinstance(item, str) else store.encoded(item)} for key, item in value.items()]
    return copy.deepcopy(value or [])


def _definition(kind, scope, owner=None, dependencies=(), inputs=None):
    if kind == "stage":
        from .team_tasks import V2_TASKS
        if scope not in V2_TASKS:
            raise ValueError("未知制作阶段")
        spec = V2_TASKS[scope]
        label, default = spec["label"], spec["owner"]
    elif kind in KINDS:
        label, default = KINDS[kind]
    else:
        raise ValueError("未知任务类型")
    ident = kind + ":" + str(scope)
    if not TASK_ID.fullmatch(ident):
        raise ValueError("任务标识无效")
    owner = owner or default
    if owner not in ROLE_DEPARTMENT:
        raise ValueError("未知团队成员")
    row = {"id": ident, "kind": kind, "scope": scope, "label": label + (" · " + scope if kind != "stage" else ""),
           "owner": owner, "department": ROLE_DEPARTMENT[owner], "dependencies": list(dependencies),
           "inputs": _inputs(inputs), "artifacts": [], "review_status": "pending" if kind in {"image", "video"} else "not_required",
           "adoption_status": "unselected" if kind in {"image", "video", "speech"} else "not_applicable",
           "input_version": None, "attempt": 0, "started_at": None, "finished_at": None, "model": None,
           "requirements": "", "blocked_reason": None}
    if re.fullmatch(r"S\d{2,3}(?:_.*)?", str(scope)):
        row["shot_id"] = scope.split("_", 1)[0]
    if kind == "speech":
        row["line_id"] = scope
    return row


def _insert(db, pid, definition):
    if db.execute("SELECT 1 FROM team_work_items WHERE project_id=? AND id=?", (pid, definition["id"])).fetchone():
        return
    ordinal = db.execute("SELECT COALESCE(MAX(ordinal),-1)+1 FROM team_work_items WHERE project_id=?", (pid,)).fetchone()[0]
    db.execute("INSERT OR IGNORE INTO team_work_items(project_id,id,ordinal,kind,scope,owner,department,data) VALUES(?,?,?,?,?,?,?,?)",
               (pid, definition["id"], ordinal, definition["kind"], definition["scope"], definition["owner"], definition["department"], store.encoded(definition)))


def ensure_project(db, project):
    """Called only on writes. Populate planned identities without inventing work."""
    if not enabled(project):
        return
    pid = project["id"]
    db.execute("INSERT OR IGNORE INTO team_control(project_id) VALUES(?)", (pid,))
    def insert_plan(db, pid, definition):
        _insert(db, pid, definition)
        row = _stored(db, pid, definition["id"])
        data = _data(row)
        # Current script dependencies are authoritative; historical attempts stay immutable.
        impact = [dep for dep in definition["dependencies"] if not (definition["kind"] == "image" and dep.startswith("dialogue:"))]
        if data.get("dependencies") != definition["dependencies"] or data.get("impact_dependencies") != impact:
            data["dependencies"] = definition["dependencies"]
            # Timing must finish before frame inspection, but changing a voice
            # alone doesn't replace the frame or its video candidate.
            data["impact_dependencies"] = impact
            _save(db, row, data)
    from .team_tasks import V2_TASKS
    for stage, spec in V2_TASKS.items():
        inputs = [{"label": key, "version": project.get("script_generation")} for key in spec["inputs"]]
        insert_plan(db, pid, _definition("stage", stage, dependencies=["stage:" + dep for dep in spec["depends_on"]], inputs=inputs))
    episode = project.get("episode") or project.get("script") or {}
    if not isinstance(episode, dict):
        return
    cast = episode.get("cast") or {}
    for role in cast:
        insert_plan(db, pid, _definition("character", role, dependencies=["stage:visual_development"]))
    delivery_inputs = []
    for shot in episode.get("shots", [])[:80]:
        sid = shot["id"]
        script_definition = _definition("script", sid, dependencies=["stage:script_review"], inputs=[{"label": "镜头剧本", "text": store.encoded(shot)}])
        insert_plan(db, pid, script_definition)
        if (project.get("script_review") or {}).get("passed") is True:
            _import_text(db, pid, "script:" + sid, shot)
        from .series_bindings import shot_roles
        dependencies = ["script:" + sid] + ["character:" + role for role in shot_roles(shot, cast)]
        dependencies += ["image:" + ref for ref in shot.get("reference_shots", [])]
        if shot.get("dialogue"):
            dependencies.append("dialogue:" + sid)
        from .material_library import shot_references
        dependencies += ["scene:" + r["asset_id"] for r in shot_references(project, shot) if r.get("kind") == "scene"]
        insert_plan(db, pid, _definition("image", sid, dependencies=dependencies,
                                    inputs=[{"label": "镜头要求", "text": shot.get("prompt"), "version": project.get("script_generation")}]))
        insert_plan(db, pid, _definition("image_review", sid, dependencies=["image:" + sid]))
        insert_plan(db, pid, _definition("video", sid, dependencies=["image_review:" + sid]))
        insert_plan(db, pid, _definition("video_review", sid, dependencies=["video:" + sid]))
        dialogue = []
        for index, line in enumerate(shot.get("dialogue", [])):
            lid = line.get("id") or f"{sid}_D{index + 1:02d}"
            insert_plan(db, pid, _definition("dialogue_text", lid, dependencies=["script:" + sid], inputs=[{"label": "对白正文", "text": line.get("text")}]))
            if (project.get("script_review") or {}).get("passed") is True:
                _import_text(db, pid, "dialogue_text:" + lid, line)
            insert_plan(db, pid, _definition("speech", lid, dependencies=["stage:script_review", "dialogue_text:" + lid],
                                        inputs=[{"label": "对白", "text": line.get("text"), "role": line.get("role")}]))
            dialogue.append("speech:" + lid)
        if dialogue:
            insert_plan(db, pid, _definition("dialogue", sid, dependencies=dialogue))
        delivery_inputs += ["video_review:" + sid] + (["dialogue:" + sid] if dialogue else [])
    for reference in project.get("material_package", {}).get("references", []):
        if reference.get("kind") == "scene":
            scope = reference.get("asset_id")
            if isinstance(scope, str) and TASK_ID.fullmatch("scene:" + scope):
                insert_plan(db, pid, _definition("scene", scope, inputs=[{"label": "已固定场景素材", "text": store.encoded(reference)}]))
                _import_text(db, pid, "scene:" + scope, reference)
    if episode.get("shots"):
        insert_plan(db, pid, _definition("edit", "delivery", dependencies=delivery_inputs))
        insert_plan(db, pid, _definition("technical_review", "delivery", dependencies=["edit:delivery"]))


def _import_text(db, pid, ident, value):
    row = _stored(db, pid, ident)
    data = _data(row)
    signature = fingerprint(value)
    if data["input_version"] == signature:
        return
    if data["input_version"] is not None:
        data["generation"] += 1
    data.update(status="reused", input_version=signature, inputs=[{"label": "已审核文本或已固定素材", "text": store.encoded(value)}],
                review_status="passed" if data["kind"] != "scene" else "not_required")
    _save(db, row, data)


def _stored(db, pid, ident):
    if not isinstance(ident, str) or not TASK_ID.fullmatch(ident):
        raise ValueError("任务标识无效")
    row = db.execute("SELECT * FROM team_work_items WHERE project_id=? AND id=?", (pid, ident)).fetchone()
    if row is None:
        raise KeyError("任务不存在")
    return dict(row)


def _data(row):
    return {**json.loads(row["data"]), **{key: row[key] for key in ("id", "kind", "scope", "generation", "owner", "department", "status", "priority")},
            "hold_after": bool(row["hold_after"]), "locked": False}


def _save(db, row, data):
    db.execute("UPDATE team_work_items SET generation=?,owner=?,department=?,status=?,priority=?,hold_after=?,data=? WHERE project_id=? AND id=?",
               (data["generation"], data["owner"], data["department"], data["status"], data["priority"], int(data["hold_after"]), store.encoded(data), row["project_id"], row["id"]))


def _existing(pid):
    # Adapters are also used without the studio DB by CPU fixtures and CLI tools.
    return isinstance(pid, str) and bool(re.fullmatch(r"[a-f0-9]{12}", pid)) and store.DATABASE.is_file()


def begin_unit(pid, kind, scope, *, owner=None, input_version=None, dependencies=(), inputs=None):
    if not _existing(pid):
        return None
    from .team_control import admit
    admit(pid, kind, scope)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            project = store._read(db, pid)
        except KeyError:
            return None
        if not enabled(project):
            return None
        ensure_project(db, project)
        spec = _definition(kind, scope, owner, dependencies, inputs)
        _insert(db, pid, spec)
        row = _stored(db, pid, spec["id"])
        data = _data(row)
        from .team_control import _pending, _instruction, _draining_remote
        pending = _pending(db, pid)
        if pending and spec["id"] in _instruction(pending).get("affected_tasks", []) and _draining_remote(db, pid, kind, scope):
            # A resumed supplier job still belongs to its original input generation.
            previous = db.execute("SELECT generation FROM team_unit_attempts WHERE project_id=? AND task_id=? ORDER BY rowid DESC LIMIT 1", (pid, spec["id"])).fetchone()
            generation = previous[0] if previous else max(1, data["generation"] - 1)
            number = db.execute("SELECT COALESCE(MAX(attempt),0)+1 FROM team_unit_attempts WHERE project_id=? AND task_id=? AND generation=?", (pid, spec["id"], generation)).fetchone()[0]
            token = {"project_id": pid, "task_id": spec["id"], "generation": generation, "run_id": uuid.uuid4().hex,
                     "input_version": input_version, "attempt": number}
            db.execute("INSERT INTO team_unit_attempts VALUES(?,?,?,?,?,?,?)", (token["run_id"], pid, spec["id"], generation, number, "running", store.encoded({**token, "started_at": store.now(), "draining": True})))
            return token
        if input_version is not None and data["input_version"] not in (None, input_version):
            data.update(generation=data["generation"] + 1, attempt=0, artifacts=[], review_status=spec["review_status"])
        if data["status"] == "running":
            db.execute("UPDATE team_unit_attempts SET status='superseded' WHERE project_id=? AND task_id=? AND status='running'", (pid, data["id"]))
        if inputs is not None:
            data["inputs"] = _inputs(inputs)
        if dependencies:
            data["dependencies"] = list(dependencies)
        token = {"project_id": pid, "task_id": data["id"], "generation": data["generation"], "run_id": uuid.uuid4().hex,
                 "input_version": input_version or data["input_version"], "attempt": data["attempt"] + 1}
        data.update(status="running", attempt=token["attempt"], input_version=token["input_version"],
                    started_at=store.now(), finished_at=None, active_run=token["run_id"], blocked_reason=None)
        _save(db, row, data)
        db.execute("INSERT INTO team_unit_attempts VALUES(?,?,?,?,?,?,?)", (token["run_id"], pid, data["id"], data["generation"], token["attempt"], "running", store.encoded({**token, "started_at": data["started_at"]})))
        store._event(db, pid, data["label"] + " · 开始执行", "team_unit", {"task_id": data["id"], "run_id": token["run_id"]})
        return token


def _artifact(value, label="交付成果"):
    if value is None:
        return None
    value = {"path": value} if isinstance(value, str) else copy.deepcopy(value)
    if not isinstance(value, dict):
        raise ValueError("成果应为回执或路径")
    path = value.get("path")
    url = value.get("artifact_url") or value.get("media_url") or value.get("url")
    if isinstance(path, str) and path.startswith("production/") and ".." not in path.split("/") and not any(c in path for c in "\\?#%\x00"):
        from urllib.parse import quote
        url = "/media/" + quote(path, safe="/")
    return {"label": value.get("label", label), "artifact_url": url if isinstance(url, str) and url.startswith("/media/") else None,
            "path": path, "sha256": value.get("sha256"), "receipt": value}


def finish_unit(token, *, status="completed", artifact=None, review_status=None, model=None, details=None):
    if token is None:
        return True
    if status not in STATUSES:
        raise ValueError("未知任务状态")
    review_status = {"pass": "passed", "fail": "failed"}.get(review_status, review_status)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _stored(db, token["project_id"], token["task_id"])
        data = _data(row)
        attempt = db.execute("SELECT * FROM team_unit_attempts WHERE run_id=?", (token["run_id"],)).fetchone()
        if attempt is None or attempt["status"] not in {"running", "superseded"}:
            return False
        if json.loads(attempt["data"]).get("finished_at"):
            return False
        current = data["generation"] == token["generation"] and data.get("active_run") == token["run_id"]
        pending = any(token["task_id"] in json.loads(r[0]).get("affected_tasks", []) for r in db.execute(
            "SELECT data FROM team_instructions WHERE project_id=? AND status IN ('queued','planning','needs_decision')", (token["project_id"],)))
        current = current and not pending
        item = _artifact(artifact)
        evidence = {**json.loads(attempt["data"]), "status": status if current else "superseded", "finished_at": store.now(),
                    "artifact": item, "review_status": review_status, "model": model, "details": copy.deepcopy(details or {})}
        db.execute("UPDATE team_unit_attempts SET status=?,data=? WHERE run_id=?", (evidence["status"], store.encoded(evidence), token["run_id"]))
        if not current:
            if pending and data.get("active_run") == token["run_id"]:
                data.update(status="pending", active_run=None)
                _save(db, row, data)
            return False
        data.update(status=status, finished_at=evidence["finished_at"], model=model if model is not None else data.get("model"))
        if data.get("paused"):
            data["status"] = "paused"
        if data["hold_after"] and status in {"completed", "reused"}:
            data["awaiting_ack"] = True
        if item is not None:
            data["artifacts"] = [item]
        if review_status is not None:
            data["review_status"] = review_status
        if details:
            data["details"] = copy.deepcopy(details)
        _save(db, row, data)
        store._event(db, token["project_id"], data["label"] + " · " + status, "team_unit", {"task_id": data["id"], "run_id": token["run_id"]})
        from .team_control import settle_instructions
        settle_instructions(db, token["project_id"])
        return True


def is_current(token):
    if token is None:
        return True
    with store.connection() as db:
        data = _data(_stored(db, token["project_id"], token["task_id"]))
        return data["generation"] == token["generation"] and data.get("active_run") == token["run_id"]


def record_call(context, role, receipt, path):
    """Attach actual call provenance, never the current role's default model."""
    if not isinstance(context, dict) or not _existing(context.get("project_id")):
        return
    pid, stage = context["project_id"], context.get("stage")
    if not isinstance(stage, str):
        return
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        project = store._read(db, pid)
        if not enabled(project):
            return
        row = db.execute("SELECT * FROM team_work_items WHERE project_id=? AND id=?", (pid, context.get("task_id") or "stage:" + stage)).fetchone()
        if row is None:
            return
        row = dict(row)
        data = _data(row)
        from studio.core import ROOT
        try:
            relative = str(path.relative_to(ROOT))
        except ValueError:
            relative = None
        call = {"id": receipt["call_id"], "role": role, "actual_member": receipt.get("actual_member", role), "model": receipt.get("response_model") or receipt.get("configured_model"),
                "model_source": "response" if receipt.get("response_model") else "request", "status": receipt["status"],
                "profile_id": receipt.get("profile"), "started_at": receipt.get("started_at"), "finished_at": receipt.get("finished_at"),
                "receipt": _artifact(relative) if relative else None,
                "usage": [a.get("usage") for a in receipt.get("attempts", []) if a.get("usage")]}
        data["executions"] = ([c for c in data.get("executions", []) if c["id"] != call["id"]] + [call])[-30:]
        if receipt.get("actual_member", role) == data["owner"]:
            data["model"] = call["model"]
        _save(db, row, data)


def record_adoption(pid, kind, scope, artifact):
    if not _existing(pid):
        return
    with store.connection() as db:
        row = db.execute("SELECT * FROM team_work_items WHERE project_id=? AND id=?", (pid, kind + ":" + str(scope))).fetchone()
        if row is None:
            return
        row = dict(row)
        task = _data(row)
        if task["status"] not in {"completed", "reused"}:
            return
        task.update(adoption_status="selected", artifacts=[_artifact(artifact)])
        _save(db, row, task)


@contextmanager
def unit(pid, kind, scope, **kwargs):
    token = begin_unit(pid, kind, scope, **kwargs)
    try:
        yield token
    except BaseException as exc:
        state = {"ProductionDeferred": "waiting", "ProductionPaused": "paused", "ProjectPaused": "paused", "ProductionNeedsDecision": "blocked", "ProductionBudgetExceeded": "blocked", "ReviewBlocked": "blocked", "CreativeReviewError": "blocked"}.get(type(exc).__name__, "failed")
        finish_unit(token, status=state, details={"error_type": type(exc).__name__})
        raise
    else:
        finish_unit(token)


def mark_reused(pid, kind, scope, *, artifact=None, input_version=None, review_status=None):
    review_status = {"pass": "passed", "fail": "failed"}.get(review_status, review_status)
    if not _existing(pid):
        return
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            project = store._read(db, pid)
        except KeyError:
            return
        if not enabled(project):
            return
        ensure_project(db, project)
        spec = _definition(kind, scope)
        _insert(db, pid, spec)
        row = _stored(db, pid, spec["id"])
        data = _data(row)
        # Re-reading a completed current result is not another execution.
        if data["status"] == "completed" and data["input_version"] == input_version:
            return
        data.update(status="paused" if data.get("paused") else "reused", input_version=input_version)
        if data.get("hold_after"):
            data["awaiting_ack"] = True
        if artifact is not None:
            data["artifacts"] = [_artifact(artifact)]
        if review_status is not None:
            data["review_status"] = review_status
        _save(db, row, data)


def _attempts(db, pid, ident):
    return [{**json.loads(row["data"]), "status": row["status"]} for row in db.execute(
        "SELECT status,data FROM team_unit_attempts WHERE project_id=? AND task_id=? ORDER BY generation DESC,attempt DESC LIMIT 30", (pid, ident))]


def _public_task(db, row, *, priorities=None, project=None):
    data = _data(row)
    data["attempts"] = _attempts(db, row["project_id"], row["id"])
    from .team_control import effective_priorities, task_descriptors
    from .team_planning import allowed_owners
    from .team_tasks import V2_TASKS
    project = project if project is not None else store._read(db, row["project_id"])
    if priorities is None:
        tasks = {_row["id"]: _data(dict(_row)) for _row in db.execute("SELECT * FROM team_work_items WHERE project_id=?", (row["project_id"],))}
        priorities = effective_priorities(tasks)
    data["effective_priority"] = priorities.get(data["id"], data["priority"])
    data["allowed_owners"] = allowed_owners(data)
    data["default_owner"] = (V2_TASKS.get(data["scope"], {}).get("owner", data["owner"]) if data["kind"] == "stage"
                             else data["owner"] if data["kind"] == "management" else KINDS.get(data["kind"], (None, data["owner"]))[1])
    data["assignment_reason"] = None if any(owner != data["owner"] for owner in data["allowed_owners"]) else "当前任务没有其他能力匹配的成员可接手。"
    data["actions"] = task_descriptors(db, project, data)
    data["locked"] = bool(db.execute("SELECT 1 FROM team_locks WHERE project_id=? AND task_id=?", (row["project_id"], row["id"])).fetchone())
    if data["kind"] in {"image", "video", "character"}:
        kind = "video_review" if data["kind"] == "video" else "image_review"
        review = db.execute("SELECT data,status FROM team_work_items WHERE project_id=? AND id=?", (row["project_id"], kind + ":" + row["scope"])).fetchone()
        if review and review["status"] in {"completed", "reused"}:
            data["review_status"] = json.loads(review["data"]).get("review_status", "pending")
    return data


def _revision(db, pid):
    row = db.execute("SELECT revision FROM team_control WHERE project_id=?", (pid,)).fetchone()
    return row[0] if row else 0


def task_page(pid, offset=0, limit=30, department=None, status=None, shot_id=None, owner=None):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("任务分页无效")
    if department is not None and department not in {g["id"] for g in DEPARTMENTS}:
        raise ValueError("未知部门")
    if status is not None and status not in STATUSES:
        raise ValueError("未知任务状态")
    if shot_id is not None and not re.fullmatch(r"S\d{2,3}", shot_id):
        raise ValueError("镜头编号无效")
    if owner is not None and owner not in ROLE_DEPARTMENT:
        raise ValueError("未知成员")
    with store.connection() as db:
        db.execute("BEGIN")
        store._read(db, pid)
        clauses, args = ["project_id=?"], [pid]
        for column, value in (("department", department), ("status", status), ("owner", owner)):
            if value is not None:
                clauses.append(column + "=?")
                args.append(value)
        if shot_id is not None:
            clauses.append("(scope=? OR scope LIKE ? ESCAPE '\\')")
            args += [shot_id, shot_id + r"\_%"]
        where = " AND ".join(clauses)
        total = db.execute("SELECT COUNT(*) FROM team_work_items WHERE " + where, args).fetchone()[0]
        from .team_control import effective_priorities
        priorities = effective_priorities({r["id"]: _data(dict(r)) for r in db.execute("SELECT * FROM team_work_items WHERE project_id=?", (pid,))})
        project = store._read(db, pid)
        values = [_public_task(db, dict(row), priorities=priorities, project=project) for row in db.execute("SELECT * FROM team_work_items WHERE " + where + " ORDER BY priority DESC,ordinal LIMIT ? OFFSET ?", (*args, limit, offset))]
        return {"tasks": values, "total": total, "offset": offset, "limit": limit, "control_revision": _revision(db, pid)}


def task_detail(pid, ident):
    with store.connection() as db:
        db.execute("BEGIN")
        store._read(db, pid)
        return {"task": _public_task(db, _stored(db, pid, ident)), "control_revision": _revision(db, pid)}


def team_snapshot(pid):
    with store.connection() as db:
        db.execute("BEGIN")
        project = store._read(db, pid)
        tasks = [_data(dict(row)) for row in db.execute("SELECT * FROM team_work_items WHERE project_id=? ORDER BY ordinal", (pid,))]
        events = []
        for row in db.execute("SELECT * FROM events WHERE project_id=? AND kind IN ('team_unit','team_instruction','team_control') ORDER BY id DESC LIMIT 50", (pid,)):
            extra = json.loads(row["data"])
            events.append({"id": row["id"], "at": row["at"], "kind": row["kind"], "message": row["message"], "task_id": extra.get("task_id")})
        revision = _revision(db, pid)
        from .team_control import _instruction
        commands = [_instruction(r) for r in db.execute("SELECT * FROM team_instructions WHERE project_id=? ORDER BY rowid DESC LIMIT 20", (pid,))]
        locks = [{"task_id": r["task_id"], "generation": r["generation"], "created_at": r["created_at"]} for r in db.execute("SELECT * FROM team_locks WHERE project_id=?", (pid,))]
        running = bool(db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='running'", (pid,)).fetchone())
    from .config import load_config
    from .team_flow import team_spec
    from .team_planning import leader_availability
    config = load_config()
    assignments = {r["role"]: r for r in team_spec(config)["teams"]}
    availability = {group["lead"]: leader_availability(group["lead"], config) for group in DEPARTMENTS}
    control_enabled = enabled(project)
    live = control_enabled and project["status"] != "cancelled"
    control_reason = "项目已取消" if project["status"] == "cancelled" else "请先在安全边界启用团队控制"
    departments = [{**group, "members": group["roles"],
                    "management_available": live and availability[group["lead"]]["available"],
                    "management_unavailable_reason": availability[group["lead"]]["reason"] if live else control_reason}
                   for group in DEPARTMENTS]
    members = []
    for group in DEPARTMENTS:
        for role in group["roles"]:
            binding = assignments.get(role, {})
            leader = group["lead"] == role
            manager = availability.get(role, {}) if leader else {}
            owned = [t for t in tasks if t["owner"] == role]
            active = [t["id"] for t in owned if t["status"] == "running"]
            members.append({"id": role, "role": role, "label": binding.get("label", role), "department": group["id"], "leader": leader,
                            "model": binding.get("model"), "profile_id": binding.get("profile_id"),
                            "leader_available": bool(manager.get("available")), "leader_unavailable_reason": manager.get("reason"),
                            "leader_model": manager.get("model"), "leader_profile_id": manager.get("profile_id"),
                            "status": "running" if active else "blocked" if any(t["status"] == "blocked" for t in owned) else "waiting" if any(t["status"] in {"waiting", "paused"} for t in owned) else "idle",
                            "current_tasks": active})
    root = availability["executive_producer"]
    actions = ([{"id": "intervene", "label": "介入并下达要求", "enabled": live, "disabled_reason": None if live else control_reason},
                {"id": "replan", "label": "向总制片下达要求", "enabled": live and root["available"], "disabled_reason": root["reason"] if live else control_reason}]
               if control_enabled else [{"id": "enable_control", "label": "启用团队控制", "enabled": not running and project.get("workflow_version", 1) in {2, 3} and project["status"] in {"paused", "blocked", "awaiting_final"}, "disabled_reason": "请在安全边界启用；v1 项目仅支持观察"}])
    return {"schema": "ai-studio.team.v1", "enabled": control_enabled,
            "enable_available": not enabled(project) and project.get("workflow_version", 1) in {2, 3} and not running and project["status"] in {"paused", "blocked", "awaiting_final"},
            "control_revision": revision, "departments": departments, "members": members,
            "task_summary": {"total": len(tasks), "running": sum(t["status"] == "running" for t in tasks),
                             "waiting": sum(t["status"] in {"pending", "waiting", "paused"} for t in tasks), "blocked": sum(t["status"] in {"blocked", "failed"} for t in tasks),
                             "completed": sum(t["status"] in {"completed", "reused"} for t in tasks)},
            "events": events, "instructions": commands, "locks": locks,
            "actions": actions}
