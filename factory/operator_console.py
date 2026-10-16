"""Consistent, bounded operator views; the existing graph remains execution authority."""
from __future__ import annotations

import copy
import json
import math
import re
import time
from collections import Counter
from urllib.parse import quote

from . import store
from .review_feedback import feedback_choices, review_issues
from .review_advice import snapshot as advice_snapshot
from .attention_actions import descriptors, generation, validate_gate, delegation_status

TERMINAL = {"approved", "cancelled"}
GENERATIONS = {"story": "proposal_generation", "style": "style_generation", "final": "production_revision"}
PHASES = {
    "queued": "等待执行器领取", "research": "检索题材与来源", "proposals": "创作并评审故事提案",
    "story_creation": "生成故事方案", "story_review": "独立审查故事", "story_decision": "确定故事方向",
    "screenplay": "编写剧本与分镜", "script_creation": "编写逐镜剧本", "script_review": "独立审查剧本",
    "storyboard_direction": "编排镜头与运镜", "story_resolution": "落实创作修改意见",
    "visual_development": "设计人物与画风", "style_previews": "生成视觉预览", "style_decision": "确定视觉方向",
    "character_references": "生成人物参考", "story_frames": "制作镜头关键帧", "diffusion_video": "生成视频镜头",
    "h3_prompt_preparation": "自动编写视频镜头提示词",
    "voice": "合成角色对白", "voice_rendering": "合成角色对白", "dialogue_rewriting": "调整对白时长",
    "camera_motion": "制作动态运镜", "sound_edit": "混音与声音编辑", "edit": "剪辑与字幕合成",
    "quality_review": "检查画面与连续性", "semantic_review_pending": "等待视觉审核", "waiting_resources": "等待可用 GPU",
    "final_technical_review": "检查最终音画文件", "delivery_ready": "等待出品人审片", "final_decision": "等待出品人审片",
    "production": "制作镜头与声音", "revision": "处理指定返修", "delivered": "终版已交付",
    "story_resolution_review": "独立审查故事目标", "storyboard": "编排分镜与运镜",
    "visual_design": "设计画风方案", "remote_submit": "提交云任务", "remote_poll": "等待云端生成",
    "remote_download": "下载云素材",
}


def obj(value):
    return value if isinstance(value, dict) else {}


def rows(value, limit=120):
    return [v for v in value[:limit] if isinstance(v, dict)] if isinstance(value, list) else []


def text(value, limit=1200):
    return value[:limit] if isinstance(value, str) else ""


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else 0


def media_url(project, value):
    """Only this project's recorded relative media; no stat, scan, or network."""
    if not isinstance(value, str) or len(value) > 1024:
        return None
    raw = value.removeprefix("/media/")
    ident = project.get("id")
    if not isinstance(ident, str) or not re.fullmatch(r"[a-f0-9]{12}", ident):
        return None
    if not raw.startswith("production/factory-" + ident + "/") or any(c in raw for c in ("\\", "%", "?", "#", ":", "\x00")):
        return None
    if any(part in ("", ".", "..") for part in raw.split("/")):
        return None
    return "/media/" + quote(raw, safe="/")


def current_gate(project):
    if project.get("status") in TERMINAL:
        return None
    decisions = project.get("decisions")
    for gate in reversed(rows(decisions[-200:] if isinstance(decisions, list) else [], 200)):
        kind = gate.get("type")
        if gate.get("status") != "pending" or kind not in GENERATIONS:
            continue
        try:
            validate_gate(project, gate)
        except ValueError:
            continue
        return copy.deepcopy(gate)
    return None


def attention(project, *, job_running=False):
    status = project.get("status")
    if status in TERMINAL:
        return None
    gate = current_gate(project)
    if gate and status not in {"paused", "blocked", "failed"} and not project.get("pause_requested"):
        kind = gate["type"]
        # During automatic supervision the producer may observe the pending gate,
        # but it is not a human inbox item until the active worker yields.
        if project.get("team_mode") == "delegated" and kind in {"story", "style"} and job_running:
            return None
        candidates = []
        allowed = {v.get("id") for v in rows(gate.get("options"), 20)}
        source = project.get("proposals" if kind == "story" else "styles") if kind != "final" else []
        for item in rows(source, 20):
            if item.get("id") not in allowed or (kind == "story" and item.get("eligible") is False):
                continue
            try:
                validate_gate(project, gate, item.get("id"))
            except ValueError:
                continue
            candidate = {k: text(item.get(k), 2400) for k in ("id", "title", "name", "logline", "synopsis", "description", "hook", "ending")}
            candidate["preview_url"] = media_url(project, item.get("preview_url") or obj(item.get("preview")).get("path"))
            candidate["review_summary"] = text(obj(item.get("review")).get("summary"))
            candidates.append(candidate)
        names = {"story": "选择故事方向", "style": "选择人物与画风", "final": "审看并确认终片"}
        return {"id": gate.get("id"), "kind": kind, "title": names[kind], "summary": text(gate.get("title")),
                "owner": "producer", "blocking": True, "gate": gate, "options": candidates, "issues": [],
                "action_label": names[kind], "target_tab": "decisions", "revision": project.get("revision"),
                "can_choose": bool(candidates) if kind != "final" else bool(media_url(project, obj(project.get("delivery")).get("video_url"))),
                "suggested_feedback": "", "feedback_choices": feedback_choices(project), "actions": descriptors(project, job_running=job_running),
                "delegation": delegation_status(project)}
    if status == "paused" or project.get("pause_requested"):
        return {"kind": "paused", "title": "制作已暂停" if not job_running else "正在安全暂停",
                "summary": "已有素材与进度保留。" if not job_running else "当前执行单元结束后暂停，不强制中断生成。",
                "action_label": "恢复制作" if not job_running else "查看执行进度", "target_tab": "overview", "blocking": False,
                "can_resume": not job_running, "issues": [], "feedback_choices": [], "revision": project.get("revision"),
                "actions": descriptors(project, job_running=job_running)}
    if status not in {"blocked", "failed"}:
        return None
    need = obj(project.get("needs_decision"))
    kind = ("budget" if need.get("type") == "budget" else "cloud" if str(need.get("code", "")).startswith("cloud_")
            else "production" if need.get("type") == "production" else "creative" if need.get("kind") in {"creative_review", "script_review"}
            or need.get("type") in {"review", "script_review", "edit_feedback", "creative_review"} else "runtime")
    titles = {"creative": "审核未通过，请选择处理方式", "production": "镜头制作受阻，需要处理素材", "budget": "制作预算已达到上限", "cloud": "云任务等待授权或核对", "runtime": "模型或执行服务需要处理"}
    labels = {"creative": "查看问题并修改", "production": "选择镜头返修", "budget": "调整预算", "cloud": "核对云任务", "runtime": "诊断并重试"}
    issues = review_issues(project)
    significant = [i for i in issues if i["severity"] in {"blocking", "major", "error"}] or issues
    suggestions = [(("、".join(i["shot_ids"]) + "：") if i["shot_ids"] else "") + (i["suggestion"] or i["description"]) for i in significant]
    feedback = ("请按以下审核问题修改，保留未涉及的角色与剧情设定；修改后重新独立审核：\n" + "\n".join(suggestions))[:3900] if suggestions else ""
    return {"kind": kind, "title": titles[kind], "summary": text(need.get("message") or project.get("error")),
            "code": text(need.get("code"), 120), "blocking": True, "owner": "producer", "revision": project.get("revision"),
            "action_label": labels[kind], "target_tab": "costs" if kind == "cloud" else "decisions", "issues": issues,
            "suggested_feedback": feedback, "feedback_choices": feedback_choices(project), "can_retry": kind in {"creative", "production", "runtime"},
            "actions": descriptors(project, job_running=job_running), "review_advice": advice_snapshot(project)}


def project_summary(project, *, job_running=False):
    issue = attention(project, job_running=job_running)
    phase = obj(project.get("production")).get("phase") if project.get("stage") == "production" else project.get("stage")
    result = {k: copy.deepcopy(project.get(k)) for k in ("id", "title", "brief", "format", "status", "stage", "progress", "revision", "created_at", "updated_at", "duration_seconds", "team_mode", "is_validation", "budget")}
    result["workflow_version"] = project.get("workflow_version", 1)
    shots = rows(project.get("shots"))
    result.update(current_activity=PHASES.get(phase, text(phase, 80) or "项目已建立"), job_running=job_running,
                  attention=issue, pending_decisions=int(bool(issue and issue.get("blocking"))),
                  delivery_ready=bool(project.get("delivery")), shot_total=len(shots),
                  shot_images=sum(bool(obj(s.get("image")).get("path")) for s in shots),
                  shot_videos=sum(bool(obj(s.get("video")).get("path")) for s in shots))
    advice = advice_snapshot(project)
    if job_running and advice and advice.get("current") and advice.get("status") in {"queued", "requested"}:
        result["current_activity"] = "等待总制片分析审核问题" if advice["status"] == "queued" else "总制片正在分析返修方案"
    result["poster"] = next((u for s in shots for u in [media_url(project, obj(s.get("image")).get("path") or s.get("image_url"))] if u), None)
    return result


def dashboard(*, offset=0, limit=100, attention_offset=0, attention_limit=100, q="", filter="all"):
    if (any(type(v) is not int or v < 0 for v in (offset, attention_offset))
            or any(type(v) is not int or not 1 <= v <= 100 for v in (limit, attention_limit))):
        raise ValueError("分页 offset 应为非负整数，limit 应为 1 至 100")
    if not isinstance(q, str) or len(q) > 200:
        raise ValueError("项目搜索 q 须为不超过 200 个字符的文字")
    if not isinstance(filter, str) or filter not in {"all", "active", "attention", "delivered"}:
        raise ValueError("项目筛选 filter 应为 all、active、attention 或 delivered")
    search = q.strip().casefold()
    timestamp = time.time()
    with store.connection() as db:
        db.execute("BEGIN")
        live_jobs = {r["project_id"]: dict(r) for r in db.execute("SELECT project_id,started,phase FROM jobs WHERE status='running'")}
        live = set(live_jobs)
        projects, attention_projects, running_tasks, completed = [], [], [], []
        metrics = Counter()
        reasons = Counter()
        gpu_minutes = 0
        matched = 0
        titles = {}
        # Aggregate the same full snapshot even when the project list is filtered.
        for row in db.execute("SELECT snapshot,revision FROM projects ORDER BY updated DESC"):
            project = json.loads(row[0]); project["revision"] = row[1]
            summary = project_summary(project, job_running=project["id"] in live)
            titles[project["id"]] = project["title"]
            metrics["projects"] += 1
            if summary["pending_decisions"]:
                if attention_offset <= metrics["attention"] < attention_offset + attention_limit:
                    attention_projects.append(summary)
                metrics["attention"] += 1
            metrics["blocked"] += int(project["status"] in {"blocked", "failed"})
            metrics["delivered"] += int(project["status"] == "approved" and not project.get("is_validation"))
            phase = obj(project.get("production")).get("phase")
            metrics["waiting_resources"] += int(phase == "waiting_resources")
            metrics["waiting_remote"] += int(project["status"] == "waiting_remote")
            metrics["queued"] += int(project["status"] == "queued" and project["id"] not in live)
            gpu_minutes += number(obj(project.get("budget")).get("spent_gpu_minutes"))
            if summary["attention"] and summary["attention"].get("blocking"):
                reasons[summary["attention"]["kind"]] += 1
            matches_search = not search or any(isinstance(project.get(key), str) and search in project[key].casefold()
                                               for key in ("title", "id", "brief"))
            matches_filter = (filter == "all"
                              or filter == "active" and project["status"] not in {"approved", "cancelled", "blocked", "paused"}
                              or filter == "attention" and bool(summary["attention"] and summary["attention"].get("blocking"))
                              or filter == "delivered" and project["status"] == "approved")
            if matches_search and matches_filter:
                if offset <= matched < offset + limit:
                    projects.append(summary)
                matched += 1
            if project["id"] in live and len(running_tasks) < 20:
                job = live_jobs[project["id"]]
                running_tasks.append({"project_id": project["id"], "title": project["title"], "stage": project.get("stage"),
                                      "activity": summary["current_activity"], "started_at": job["started"],
                                      "elapsed_seconds": max(0, timestamp - job["started"]) if job["started"] else None})
            if project["status"] == "approved" and not project.get("is_validation") and len(completed) < 8:
                completed.append({"project_id": project["id"], "title": project["title"], "accepted_at": project.get("accepted_at"),
                                  "video_url": media_url(project, obj(project.get("delivery")).get("video_url"))})
        activity = [{"project_id": r[0], "at": r[1], "kind": r[2], "message": r[3][:1200]}
                    for r in db.execute("SELECT project_id,at,kind,message FROM events ORDER BY id DESC LIMIT 36")]
        queue = {r[0]: r[1] for r in db.execute("SELECT status,count(*) FROM jobs GROUP BY status")}
        ready = db.execute("SELECT count(*) FROM jobs WHERE status='queued' AND next_poll_at<=?", (timestamp,)).fetchone()[0]
    for item in activity:
        item["title"] = titles.get(item["project_id"], item["project_id"])
    def pagination(start, size, total):
        more = start + size < total
        return {"offset": start, "limit": size, "total": total, "has_more": more, "next_offset": start + size if more else None}
    return {"schema": "ai-studio.console.v1", "projects": projects, "attention_projects": attention_projects, "activity": activity,
            "metrics": {**{key: metrics[key] for key in ("projects", "attention", "blocked", "delivered", "waiting_resources", "waiting_remote", "queued")},
                        "running": len(live), "gpu_minutes": round(gpu_minutes, 1)},
            "attention_breakdown": dict(reasons), "running_tasks": running_tasks, "recent_deliveries": completed,
            "queue": {"running": queue.get("running", 0), "queued": queue.get("queued", 0), "ready": ready,
                      "waiting_remote": metrics["waiting_remote"], "waiting_resources": metrics["waiting_resources"],
                      "completed": queue.get("completed", 0), "failed": queue.get("failed", 0), "worker_concurrency": 1, "durable": True},
            "pagination": pagination(offset, limit, matched),
            "attention_pagination": pagination(attention_offset, attention_limit, metrics["attention"]),
            "project_limit": limit, "bounded": matched > limit,
            "attention_project_limit": attention_limit, "attention_bounded": metrics["attention"] > attention_limit}


def workspace(pid):
    from .team_blueprint import project_graph
    from .team_flow import build_flow
    with store.connection() as db:
        db.execute("BEGIN")
        project = store._read(db, pid)
        running = bool(db.execute("SELECT 1 FROM jobs WHERE project_id=? AND (status='running' OR (status='queued' AND kind='review_advice')) LIMIT 1", (pid,)).fetchone())
        project["events"] = [{"id": r[0], "at": r[1], "kind": r[2], "message": r[3], "data": json.loads(r[4])}
                             for r in reversed(db.execute("SELECT id,at,kind,message,data FROM events WHERE project_id=? ORDER BY id DESC LIMIT 120", (pid,)).fetchall())]
    issue = attention(project, job_running=running)
    flow = build_flow(project)
    policy = delegation_status(project)
    return {"schema": "ai-studio.workspace.v1", "project": project,
            "operator": {"attention": issue, "job_running": running, "current_activity": project_summary(project, job_running=running)["current_activity"],
                         "actions": descriptors(project, job_running=running), "delegation": policy},
            "workflow": project_graph(project, issue, running), "flow": flow, "task_board": flow.get("task_board"),
            "reviews": {key: copy.deepcopy(project.get(key)) for key in ("creation_review", "script_review", "resolved_story_brief")},
            "revision": project["revision"]}
