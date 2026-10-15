"""Logical view of the executable graph, not an independent scheduler."""
from __future__ import annotations

from .operator_console import obj, rows, PHASES, current_gate
from .team_tasks import snapshot as tasks_snapshot

GROUPS = (
    {"id": "direction", "label": "统筹与决策", "lead": "executive_producer", "roles": ["executive_producer", "producer"], "responsibility": "比较合格方案、汇总风险；出品人掌握费用与终审。"},
    {"id": "story", "label": "故事开发", "lead": "screenwriter", "roles": ["researcher", "creator", "story_editor", "screenwriter", "storyboard_director"], "responsibility": "把需求转成有因果、有人物动机的可执行剧本和分镜。"},
    {"id": "media", "label": "视觉与声音", "lead": "visual_designer", "roles": ["visual_designer", "prompt_style_compiler", "image_generator", "video_generator", "speech", "dialogue_editor", "editor"], "responsibility": "制作人物、镜头与对白，并完成剪辑、字幕和混音。"},
    {"id": "review", "label": "独立质量审核", "lead": "independent_critic", "roles": ["story_critic", "independent_critic", "visual_critic"], "responsibility": "独立检查叙事、画面与连续性；未通过则返回指定阶段修复。"},
)
NODES = (
    ("research", "选题研究", "researcher", ["researcher"], ["research"], "创作需求与来源", "研究结论与材料"),
    ("proposals", "故事提案", "creator", ["creator", "independent_critic"], ["proposals"], "研究结论", "合格故事候选"),
    ("story_decision", "故事决策", "producer", ["executive_producer", "producer"], ["story_decision", "apply_story"], "通过审核的候选", "选定故事与意见"),
    ("screenplay", "剧本与分镜", "screenwriter", ["story_editor", "screenwriter", "storyboard_director", "independent_critic"], ["screenplay"], "故事与出品人意见", "剧本、分镜与审核"),
    ("visual_development", "视觉预览", "visual_designer", ["visual_designer", "image_generator"], ["visual_development"], "剧本、人物与画风", "人物与画风预览"),
    ("style_decision", "视觉决策", "producer", ["executive_producer", "producer"], ["style_decision", "apply_style"], "已生成的视觉预览", "确认的视觉方向"),
    ("production", "镜头与后期", "editor", ["image_generator", "video_generator", "speech", "dialogue_editor", "visual_critic", "editor"], ["production"], "已确认分镜与视觉", "镜头、声音和初版"),
    ("final_decision", "终片审阅", "producer", ["executive_producer", "producer"], ["final_decision"], "成片与质检证据", "通过或定向返修"),
    ("delivery", "正式交付", "producer", ["editor", "producer"], ["apply_final"], "出品人终审决定", "版本绑定的交付包"),
    ("revision", "定向返修", "story_editor", ["story_editor", "screenwriter", "storyboard_director", "independent_critic", "edit_director"], ["revision"], "指定修改意见", "修订剧本或剪辑单"),
)
EDGES = [(NODES[i][0], NODES[i+1][0], "continue") for i in range(8)] + [
    ("story_decision", "proposals", "重新提案"), ("final_decision", "revision", "修改 / 重剪"),
    ("revision", "production", "重新审核与制作"), ("production", "revision", "待处理返修")]


def project_graph(project, attention=None, running=False):
    records = {row["id"]: row for row in tasks_snapshot(project)["tasks"]}
    gate = current_gate(project)
    stage = project.get("stage")
    aliases = {"story_creation": "proposals", "story_review": "proposals", "script_creation": "screenplay", "script_review": "screenplay", "storyboard_direction": "screenplay", "story_resolution": "screenplay", "style_previews": "visual_development", "delivered": "delivery"}
    active = aliases.get(stage, stage)
    if gate and project.get("status") not in {"blocked", "failed"} and not project.get("pause_requested"):
        active = gate["type"] + "_decision"
    if project.get("status") == "approved":
        active = "delivery"
    decisions = project.get("decisions")
    history = rows(decisions[-200:] if isinstance(decisions, list) else [], 200)
    gates = {kind: next((d for d in reversed(history) if d.get("type") == kind), {}) for kind in ("story", "style", "final")}
    nodes = []
    for ident, label, owner, members, executable, inputs, outputs in NODES:
        status, evidence = "pending", "not_recorded"
        row = records.get(ident)
        if row and row["current"]:
            status, evidence = row["status"], "task_receipt"
        if ident.endswith("_decision"):
            kind = ident.split("_", 1)[0]; d = gates.get(kind, {})
            version = project.get({"story": "proposal_generation", "style": "script_generation", "final": "production_revision"}[kind])
            if d.get("status") == "answered" and (version is None or d.get("id") == f"{project.get('id')}:{kind}:{version}"):
                status, evidence = "succeeded", "decision_receipt"
        if ident == "delivery" and project.get("status") == "approved":
            status, evidence = "succeeded", "producer_approval"
        if ident == active:
            if project.get("status") in {"blocked", "failed"}: status = "blocked"
            elif project.get("status") == "paused" or project.get("pause_requested"): status = "paused"
            elif attention and attention.get("kind") in {"story", "style", "final"}: status = "human"
            elif running: status = "running"
            elif project.get("status") == "approved": status = "succeeded"
            elif project.get("status") == "waiting_remote": status = "waiting"
            evidence = "current_project_state"
        if project.get("status") == "cancelled" and status != "succeeded": status = "cancelled"
        delegated = ident in {"story_decision", "style_decision"} and project.get("team_mode") == "delegated"
        if delegated: owner = "executive_producer"
        record = obj(obj(row).get("record"))
        nodes.append({"id": ident, "label": label, "owner": owner, "members": members, "execution_nodes": executable,
                      "input": inputs, "output": outputs, "status": status, "evidence": evidence,
                      "attempt": record.get("attempt"), "started_at": record.get("started_at"), "finished_at": record.get("finished_at"),
                      "decision_mode": "delegated" if delegated else "human" if ident.endswith("decision") else None})
    phase = obj(project.get("production")).get("phase") if stage == "production" else stage
    return {"schema": "ai-studio.production-graph.v1", "project_id": project.get("id"), "revision": project.get("revision"),
            "active_node": active, "current_activity": PHASES.get(phase, phase), "nodes": nodes,
            "edges": [{"source": a, "target": b, "label": label, "loop": label != "continue"} for a, b, label in EDGES],
            "groups": list(GROUPS), "semantics": "execution_nodes map to the existing LangGraph; no editable runtime wiring or inferred approvals"}
