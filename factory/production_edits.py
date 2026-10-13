"""CAS-safe visual production edits and routing through the existing graph."""
from __future__ import annotations

import copy
import time
import uuid

from . import store


def require_idle(db, project):
    if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='running'", (project["id"],)).fetchone():
        raise ValueError("制作正在更新，请先暂停并等待当前执行单元结束")
    if project["status"] not in {"paused", "blocked", "awaiting_style", "awaiting_final", "approved"}:
        raise ValueError("请在暂停或人工决策状态下修改镜头")
    if project.get("revision_request") and project.get("revision_prepared") != project.get("production_revision"):
        raise ValueError("已有剧本返修尚未完成，不能覆盖其制作版本")


def require_resolved_cloud(db, pid, shot_ids):
    for shot_id in shot_ids:
        row = db.execute("SELECT intent_id,task_id FROM cloud_tasks WHERE project_id=? AND shot_id=? "
                         "AND status NOT IN ('downloaded','failed','rejected') LIMIT 1", (pid, shot_id)).fetchone()
        if row:
            raise ValueError("镜头 " + shot_id + " 尚有未核对云任务；不能用编辑绕过恢复保护")


def mark_visual_edit(db, project, shot_ids, operation, details):
    """Retain all media and accounting. An explicit resume makes the next version."""
    if project.get("selected_style") and ((project.get("script_review") or {}).get("passed") is not True or not project.get("selected_story")):
        raise ValueError("故事、画风和剧本评审尚未通过，不能绕过人工决策门")
    revision = int(project.get("production_revision", 1)) + 1
    previous = project.get("delivery")
    if previous:
        project.setdefault("delivery_history", []).append({"production_revision": project["production_revision"],
                                                           "delivery": copy.deepcopy(previous), "at": time.time()})
    record = {"id": uuid.uuid4().hex, "operation": operation, "shot_ids": sorted(shot_ids),
              "source_revision": project["revision"], "production_revision": revision,
              "details": copy.deepcopy(details), "actor": "producer", "at": time.time()}
    project.setdefault("production_edits", []).append(record)
    project.update(production_revision=revision, delivery=None)
    if project.get("selected_style"):
        project.update(production_edit_revision=revision, status="paused", pause_requested=True,
                       error=None, needs_decision=None)
        project.pop("revision_request", None)
        for decision in project.get("decisions", []):
            if decision.get("type") == "final" and decision.get("status") == "pending":
                decision["status"] = "superseded"
        db.execute("UPDATE jobs SET status='cancelled',finished=? WHERE project_id=? AND status='queued'",
                   (time.time(), project["id"]))
    store._event(db, project["id"], "已保存局部制作版本；旧素材和累计账本保留，恢复时只重制变更依赖。", "production_edit", record)


def resume_edited_graph(graph, snapshot, project, configuration):
    version = project.get("production_edit_revision")
    if not version or snapshot.values.get("direct_production_revision") == version:
        return snapshot
    if (not project.get("selected_story") or not project.get("selected_style") or
            (project.get("script_review") or {}).get("passed") is not True or
            any(d.get("status") == "pending" and d.get("type") in {"story", "style"} for d in project.get("decisions", []))):
        raise ValueError("局部制作不能跳过故事、画风或剧本评审")
    # The routing marker is itself in the same durable graph checkpoint. A crash
    # before or after update_state re-enters this exact version, never a new take.
    graph.update_state(configuration, {"project_id": project["id"], "route": "continue",
                                       "direct_production_revision": version}, as_node="apply_style")
    return graph.get_state(configuration)
