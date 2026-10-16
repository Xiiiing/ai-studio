"""Bounded audit of existing graph steps, not another scheduler or approval gate.

A run token fences this audit row only. Worker leases, provider idempotency,
independent reviews and producer decisions remain the execution authorities.
"""
from __future__ import annotations

import copy
from functools import wraps
import hashlib
import json
import logging
from types import MappingProxyType
import uuid

from . import store

TASKS = MappingProxyType({
    "research": {"label": "选题研究", "owner": "researcher", "reviewer": None,
                 "members": ["researcher"], "depends_on": [],
                 "inputs": ["brief", "submitted_sources"], "outputs": ["research", "sources"]},
    "proposals": {"label": "故事提案与独立审稿", "owner": "creator", "reviewer": "independent_critic",
                  "members": ["creator", "independent_critic"], "depends_on": ["research"],
                  "inputs": ["research", "brief"], "outputs": ["proposals", "creation_review"]},
    "screenplay": {"label": "剧本与分镜", "owner": "screenwriter", "reviewer": "independent_critic",
                   "members": ["story_editor", "story_critic", "screenwriter", "storyboard_director", "independent_critic", "visual_designer"],
                   "depends_on": ["proposals"], "inputs": ["selected_story", "producer_feedback", "series_bible"],
                   "outputs": ["script", "script_review", "styles"]},
    "visual_design": {"label": "视觉方案设计", "owner": "visual_designer", "reviewer": "producer",
                      "members": ["visual_designer"], "depends_on": ["screenplay"],
                      "inputs": ["script", "style_feedback"], "outputs": ["styles"]},
    "visual_development": {"label": "视觉预览", "owner": "image_generator", "reviewer": "producer",
                           "members": ["image_generator", "visual_critic"], "depends_on": ["screenplay"],
                           "inputs": ["script", "styles"], "outputs": ["style_previews"]},
    "production": {"label": "镜头制作与后期交付", "owner": "editor", "reviewer": "visual_critic",
                   "members": ["image_generator", "video_generator", "visual_critic", "speech", "dialogue_editor", "editor"],
                   "depends_on": ["visual_development"], "inputs": ["selected_style", "script", "cloud_authorization"],
                   "outputs": ["shots", "audio", "delivery", "technical_checks"]},
    "revision": {"label": "定向返修", "owner": "story_editor", "reviewer": "independent_critic",
                 "members": ["story_editor", "screenwriter", "storyboard_director", "independent_critic", "edit_director"],
                 "depends_on": ["production"], "inputs": ["revision_request", "approved_assets"],
                 "outputs": ["revised_script", "edit_plan", "script_review"]},
})
V2_TASKS = MappingProxyType({
    **{name: TASKS[name] for name in ('research', 'proposals')},
    'story_resolution': {'label': '故事目标落实与独立审核', 'owner': 'story_editor', 'reviewer': 'story_critic',
                         'members': ['story_editor', 'story_critic'], 'depends_on': ['proposals'],
                         'inputs': ['selected_story', 'story_feedback', 'script_feedback'], 'outputs': ['resolved_story_brief', 'resolution_review']},
    'screenplay': {'label': '剧本创作', 'owner': 'screenwriter', 'reviewer': 'independent_critic',
                  'members': ['screenwriter'], 'depends_on': ['story_resolution'],
                  'inputs': ['resolved_story_brief', 'producer_feedback', 'series_bible'], 'outputs': ['screenplay_draft']},
    'storyboard': {'label': '分镜导演', 'owner': 'storyboard_director', 'reviewer': 'independent_critic',
                  'members': ['storyboard_director'], 'depends_on': ['screenplay'],
                  'inputs': ['screenplay_draft', 'resolved_story_brief'], 'outputs': ['storyboard', 'storyboard_receipt']},
    'script_review': {'label': '剧本与分镜独立审核', 'owner': 'independent_critic', 'reviewer': 'producer',
                      'members': ['independent_critic'], 'depends_on': ['storyboard'],
                      'inputs': ['screenplay_draft', 'storyboard_receipt', 'producer_feedback'], 'outputs': ['script_review', 'script']},
    'visual_design': {**TASKS['visual_design'], 'depends_on': ['script_review']},
    **{name: TASKS[name] for name in ('visual_development', 'production', 'revision')},
})


def definitions(workflow_version=1):
    return V2_TASKS if workflow_version == 2 else TASKS


def definition(stage, project):
    return definitions(project.get('workflow_version', 1))[stage]


TERMINAL = frozenset({"succeeded", "failed", "blocked", "paused", "waiting", "interrupted", "skipped"})
RECORD_KEYS = frozenset({"run_id", "task_id", "stage", "owner", "reviewer", "status", "attempt",
    "input_signature", "started_at", "finished_at", "error_type", "model", "completion_semantics"})
COMPLETION = "graph_node_returned_not_quality_approval"


def contracts(workflow_version=1):
    return [{"id": name, **copy.deepcopy(spec)} for name, spec in definitions(workflow_version).items()]


def _signature(project, stage):
    if stage not in TASKS and stage not in V2_TASKS:
        raise ValueError("Unknown team task")
    # These are command/version fences, not mutable outputs of the node itself.
    keys = ["id", "brief", "format", "creation_mode", "production_profile", "series_bible"]
    keys += {
        "research": ["submitted_sources"],
        "proposals": ["proposal_generation", "story_feedback"],
        "screenplay": ["proposal_generation", "script_generation", "selected_story", "story_feedback", "script_feedback"],
        "story_resolution": ["proposal_generation", "script_generation", "selected_story", "story_feedback", "script_feedback"],
        "storyboard": ["script_generation", "creative_round"],
        "script_review": ["script_generation", "creative_round"],
        "visual_design": ["script_generation", "style_generation", "style_feedback"],
        "visual_development": ["script_generation", "style_generation"],
        "production": ["script_generation", "production_revision", "selected_style", "production_edit_revision"],
        "revision": ["script_generation", "production_revision", "revision_request"],
    }[stage]
    inputs = {key: project.get(key) for key in keys}
    package = project.get("material_package") or {}
    if package.get("references"):
        # Confirmed materials enter the actual model context. Pending selections
        # do not; repeated confirmation of identical fixed versions stays current.
        inputs["confirmed_materials"] = package.get("signature") or package["references"]
    raw = json.dumps({"stage": stage, "inputs": inputs},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _public(record):
    if not isinstance(record, dict):
        return None
    return {key: copy.deepcopy(value) for key, value in record.items() if key in RECORD_KEYS}


def _save(db, project, row):
    project.setdefault("team_tasks", {})[row["stage"]] = row
    store._write(db, project)
    store._event(db, project["id"], definition(row["stage"],project)["label"] + " · " + row["status"],
                 "team_task", _public(row))


def begin(pid, stage):
    if stage not in TASKS and stage not in V2_TASKS:
        raise ValueError("Unknown team task")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        project = store._read(db, pid)
        if project.get("pause_requested") or project.get("status") in {"paused", "cancelled"}:
            from .workflows import ProjectPaused
            raise ProjectPaused("任务未启动：项目已暂停或取消")
        previous = project.get("team_tasks", {}).get(stage) or {}
        attempt = previous.get("attempt", 0)
        if type(attempt) is not int or attempt < 0:
            raise ValueError("Invalid recorded team attempt")
        if previous.get("status") == "running":
            interrupted = {**previous, "status": "interrupted", "finished_at": store.now()}
            store._event(db, pid, "恢复前的任务未记录完成", "team_task", _public(interrupted))
        signature = _signature(project, stage)
        row = {"run_id": uuid.uuid4().hex, "task_id": stage + ":" + signature,
               "stage": stage, "owner": definition(stage,project)["owner"], "reviewer": definition(stage,project)["reviewer"],
               "status": "running", "attempt": attempt + 1, "input_signature": signature,
               "started_at": store.now(), "finished_at": None, "error_type": None,
               "model": None, "completion_semantics": COMPLETION}
        _save(db, project, row)
        return copy.deepcopy(row)


def finish(pid, stage, run_id, status, *, error_type=None):
    if (stage not in TASKS and stage not in V2_TASKS) or status not in TERMINAL:
        raise ValueError("Invalid team task outcome")
    if error_type is not None and (not isinstance(error_type, str) or not error_type.isidentifier() or len(error_type) > 100):
        raise ValueError("Invalid exception type")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        project = store._read(db, pid)
        row = project.get("team_tasks", {}).get(stage)
        if not row or row.get("run_id") != run_id or row.get("status") != "running":
            return False
        current = row.get("input_signature") == _signature(project, stage)
        row.update(status=status if current else "superseded", finished_at=store.now(), error_type=error_type)
        _save(db, project, row)
        return current


def tracked_node(stage, function):
    if stage not in TASKS and stage not in V2_TASKS:
        raise ValueError("Unknown team task")
    @wraps(function)
    def execute(state):
        token = begin(state["project_id"], stage)
        from .team_ledger import begin_unit, finish_unit
        ledger_token = begin_unit(state["project_id"], "stage", stage, input_version=token["input_signature"])
        from .execution_context import current_execution
        context_token = current_execution.set({"project_id": state["project_id"], "stage": stage,
                                                "run_id": token["run_id"]})
        try:
            result = function(state)
        except BaseException as exc:
            status = {"ReviewBlocked": "blocked", "CreativeReviewError": "blocked",
                      "ProjectPaused": "paused", "ProductionPaused": "paused",
                      "ProductionDeferred": "waiting", "ProductionNeedsDecision": "blocked",
                      "ProductionBudgetExceeded": "blocked"}.get(type(exc).__name__,
                          "failed" if isinstance(exc, Exception) else "interrupted")
            try:
                finish(state["project_id"], stage, token["run_id"], status, error_type=type(exc).__name__)
                finish_unit(ledger_token, status=status if status in {"blocked", "paused", "waiting", "failed"} else "failed", details={"error_type": type(exc).__name__})
            except Exception:
                logging.getLogger(__name__).exception("Team audit failed; original task error is preserved")
            raise
        finally:
            current_execution.reset(context_token)
        status = "skipped" if isinstance(result,dict) and result.get("skipped") else ("waiting" if stage == "production" and isinstance(result, dict) and result.get("route") == "revision" else "succeeded")
        finish(state["project_id"], stage, token["run_id"], status)
        finish_unit(ledger_token, status="waiting" if status == "waiting" else "completed", details={"completion_semantics": COMPLETION, "skipped": status == "skipped"})
        return result
    return execute


def snapshot(project):
    recorded = project.get("team_tasks")
    recorded = recorded if isinstance(recorded, dict) else {}
    rows = []
    for spec in contracts(project.get("workflow_version",1)):
        old = _public(recorded.get(spec["id"]))
        current = bool(old and old.get("input_signature") == _signature(project, spec["id"]))
        rows.append({**spec, "current": current, "status": old.get("status", "pending") if current else "pending",
                     "record": old if current else None, "previous_run": old if not current else None})
    return {"schema": "ai-studio.team-tasks.v1", "authority": "observational_not_a_scheduler",
            "completion_semantics": COMPLETION, "tasks": rows,
            "history": "Older transitions remain in the existing project event log; this view is bounded."}
