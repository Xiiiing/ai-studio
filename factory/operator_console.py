"""Consistent, bounded operator views; the existing graph remains execution authority."""
from __future__ import annotations

import copy
import json
import math
import re
from collections import Counter
from urllib.parse import quote

from . import store

TERMINAL = {"approved", "cancelled"}
GENERATIONS = {"story": "proposal_generation", "style": "script_generation", "final": "production_revision"}
PHASES = {
    "queued": "等待执行器领取", "research": "检索题材与来源", "proposals": "创作并评审故事提案",
    "story_creation": "生成故事方案", "story_review": "独立审查故事", "story_decision": "确定故事方向",
    "screenplay": "编写剧本与分镜", "script_creation": "编写逐镜剧本", "script_review": "独立审查剧本",
    "storyboard_direction": "编排镜头与运镜", "story_resolution": "落实创作修改意见",
    "visual_development": "设计人物与画风", "style_previews": "生成视觉预览", "style_decision": "确定视觉方向",
    "character_references": "生成人物参考", "story_frames": "制作镜头关键帧", "diffusion_video": "生成视频镜头",
    "voice": "合成角色对白", "voice_rendering": "合成角色对白", "dialogue_rewriting": "调整对白时长",
    "camera_motion": "制作动态运镜", "sound_edit": "混音与声音编辑", "edit": "剪辑与字幕合成",
    "quality_review": "检查画面与连续性", "semantic_review_pending": "等待视觉审核", "waiting_resources": "等待可用 GPU",
    "final_technical_review": "检查最终音画文件", "delivery_ready": "等待出品人审片", "final_decision": "等待出品人审片",
    "production": "制作镜头与声音", "revision": "处理指定返修", "delivered": "终版已交付",
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


def review_issues(project):
    """Expose actionable current evidence, not historic rounds or model thoughts."""
    need = obj(project.get("needs_decision"))
    sources = [need]
    if not need.get("issues"):
        review = obj(project.get("script_review") if project.get("selected_story") else project.get("creation_review"))
        sources.append(review.get("review") or review)
    found, seen, visited = [], set(), 0

    def visit(value, depth=0):
        nonlocal visited
        visited += 1
        if depth > 6 or visited > 180 or len(found) >= 24:
            return
        if isinstance(value, list):
            for item in value[:30]:
                visit(item, depth + 1)
        elif isinstance(value, dict):
            description = text(value.get("description") or value.get("detail"), 1400)
            if description and description not in seen:
                seen.add(description)
                ids = value.get("shot_ids", [])
                found.append({"code": text(value.get("code"), 80), "description": description,
                              "severity": text(value.get("severity"), 32) or "blocking",
                              "suggestion": text(value.get("suggestion") or value.get("repair"), 1000),
                              "evidence": text(value.get("evidence"), 1000),
                              "shot_ids": [v for v in ids[:120] if isinstance(v, str) and re.fullmatch(r"S\d{2,3}", v)] if isinstance(ids, list) else []})
            for key in ("issues", "details", "review", "blockers"):
                if value.get(key):
                    visit(value[key], depth + 1)
            rounds = value.get("rounds")
            if isinstance(rounds, list) and rounds:
                visit(rounds[-1], depth + 1)
    for source in sources:
        visit(source)
    return found


def current_gate(project):
    if project.get("status") in TERMINAL:
        return None
    decisions = project.get("decisions")
    for gate in reversed(rows(decisions[-200:] if isinstance(decisions, list) else [], 200)):
        kind = gate.get("type")
        if gate.get("status") != "pending" or kind not in GENERATIONS:
            continue
        version = project.get(GENERATIONS[kind])
        if version is not None and gate.get("id") != f"{project.get('id')}:{kind}:{version}":
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
            candidate = {k: text(item.get(k), 2400) for k in ("id", "title", "name", "logline", "synopsis", "description", "hook", "ending")}
            candidate["preview_url"] = media_url(project, item.get("preview_url") or obj(item.get("preview")).get("path"))
            candidate["review_summary"] = text(obj(item.get("review")).get("summary"))
            candidates.append(candidate)
        names = {"story": "选择故事方向", "style": "选择人物与画风", "final": "审看并确认终片"}
        return {"id": gate.get("id"), "kind": kind, "title": names[kind], "summary": text(gate.get("title")),
                "owner": "producer", "blocking": True, "gate": gate, "options": candidates, "issues": [],
                "action_label": names[kind], "target_tab": "decisions", "revision": project.get("revision"),
                "can_choose": bool(candidates) if kind != "final" else bool(media_url(project, obj(project.get("delivery")).get("video_url"))),
                "suggested_feedback": ""}
    if status == "paused" or project.get("pause_requested"):
        return {"kind": "paused", "title": "制作已暂停" if not job_running else "正在安全暂停",
                "summary": "已有素材与进度保留。" if not job_running else "当前执行单元结束后暂停，不强制中断生成。",
                "action_label": "恢复制作" if not job_running else "查看执行进度", "target_tab": "overview", "blocking": False,
                "can_resume": not job_running, "issues": [], "revision": project.get("revision")}
    if status not in {"blocked", "failed"}:
        return None
    need = obj(project.get("needs_decision"))
    kind = ("budget" if need.get("type") == "budget" else "cloud" if str(need.get("code", "")).startswith("cloud_")
            else "production" if need.get("type") == "production" else "creative" if need.get("kind") in {"creative_review", "script_review"}
            or need.get("type") in {"review", "script_review", "edit_feedback", "creative_review"} else "runtime")
    titles = {"creative": "审核未通过，需要明确修改意见", "production": "镜头制作受阻，需要处理素材", "budget": "制作预算已达到上限", "cloud": "云任务等待授权或核对", "runtime": "模型或执行服务需要处理"}
    labels = {"creative": "查看问题并修改", "production": "选择镜头返修", "budget": "调整预算", "cloud": "核对云任务", "runtime": "诊断并重试"}
    issues = review_issues(project)
    significant = [i for i in issues if i["severity"] in {"blocking", "major", "error"}] or issues
    suggestions = [(("、".join(i["shot_ids"]) + "：") if i["shot_ids"] else "") + (i["suggestion"] or i["description"]) for i in significant]
    feedback = ("请按以下审核问题修改，保留未涉及的角色与剧情设定；修改后重新独立审核：\n" + "\n".join(suggestions))[:3900] if suggestions else ""
    return {"kind": kind, "title": titles[kind], "summary": text(need.get("message") or project.get("error")),
            "code": text(need.get("code"), 120), "blocking": True, "owner": "producer", "revision": project.get("revision"),
            "action_label": labels[kind], "target_tab": "costs" if kind == "cloud" else "decisions", "issues": issues,
            "suggested_feedback": feedback, "can_retry": kind in {"creative", "production", "runtime"}}


def project_summary(project, *, job_running=False):
    issue = attention(project, job_running=job_running)
    phase = obj(project.get("production")).get("phase") if project.get("stage") == "production" else project.get("stage")
    result = {k: copy.deepcopy(project.get(k)) for k in ("id", "title", "brief", "format", "status", "stage", "progress", "revision", "created_at", "updated_at", "duration_seconds", "team_mode", "is_validation", "budget")}
    shots = rows(project.get("shots"))
    result.update(current_activity=PHASES.get(phase, text(phase, 80) or "项目已建立"), job_running=job_running,
                  attention=issue, pending_decisions=int(bool(issue and issue.get("blocking"))),
                  delivery_ready=bool(project.get("delivery")), shot_total=len(shots),
                  shot_images=sum(bool(obj(s.get("image")).get("path")) for s in shots),
                  shot_videos=sum(bool(obj(s.get("video")).get("path")) for s in shots))
    result["poster"] = next((u for s in shots for u in [media_url(project, obj(s.get("image")).get("path") or s.get("image_url"))] if u), None)
    return result


def dashboard():
    with store.connection() as db:
        db.execute("BEGIN")
        live = {r[0] for r in db.execute("SELECT project_id FROM jobs WHERE status='running'")}
        projects = []
        for row in db.execute("SELECT snapshot,revision FROM projects ORDER BY updated DESC LIMIT 100"):
            project = json.loads(row[0]); project["revision"] = row[1]
            projects.append(project_summary(project, job_running=project["id"] in live))
        activity = [{"project_id": r[0], "at": r[1], "kind": r[2], "message": r[3][:1200]}
                    for r in db.execute("SELECT project_id,at,kind,message FROM events ORDER BY id DESC LIMIT 36")]
        queue = {r[0]: r[1] for r in db.execute("SELECT status,count(*) FROM jobs GROUP BY status")}
    titles = {p["id"]: p["title"] for p in projects}
    for item in activity:
        item["title"] = titles.get(item["project_id"], item["project_id"])
    counts = Counter(p["status"] for p in projects)
    return {"schema": "ai-studio.console.v1", "projects": projects, "activity": activity,
            "metrics": {"projects": len(projects), "running": len(live), "attention": sum(p["pending_decisions"] for p in projects),
                        "blocked": counts["blocked"] + counts["failed"], "delivered": sum(p["status"] == "approved" and not p.get("is_validation") for p in projects),
                        "gpu_minutes": round(sum(number(obj(p.get("budget")).get("spent_gpu_minutes")) for p in projects), 1)},
            "queue": {"running": queue.get("running", 0), "queued": queue.get("queued", 0), "completed": queue.get("completed", 0), "failed": queue.get("failed", 0), "worker_concurrency": 1, "durable": True},
            "project_limit": 100, "bounded": len(projects) == 100}


def workspace(pid):
    from .team_blueprint import project_graph
    with store.connection() as db:
        db.execute("BEGIN")
        project = store._read(db, pid)
        running = bool(db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='running' LIMIT 1", (pid,)).fetchone())
        project["events"] = [{"id": r[0], "at": r[1], "kind": r[2], "message": r[3], "data": json.loads(r[4])}
                             for r in reversed(db.execute("SELECT id,at,kind,message,data FROM events WHERE project_id=? ORDER BY id DESC LIMIT 120", (pid,)).fetchall())]
    issue = attention(project, job_running=running)
    return {"schema": "ai-studio.workspace.v1", "project": project,
            "operator": {"attention": issue, "job_running": running, "current_activity": project_summary(project, job_running=running)["current_activity"]},
            "workflow": project_graph(project, issue, running), "revision": project["revision"]}
