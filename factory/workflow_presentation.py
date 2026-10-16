"""Reading order and business vocabulary for the existing executable workflows."""
from __future__ import annotations

STAGES = (
    ("story", "故事策划", ("research", "proposals", "story_decision")),
    ("script", "剧本与分镜", ("story_resolution", "screenplay", "storyboard", "script_review")),
    ("visual", "视觉确认", ("visual_design", "visual_development", "style_decision")),
    ("production", "镜头与后期", ("production",)),
    ("delivery", "终审交付", ("final_decision", "delivery")),
)
LABELS = {
    "brief": "创作需求", "submitted_sources": "提供的来源", "research": "选题研究",
    "sources": "研究来源", "proposals": "候选故事", "creation_review": "提案审核",
    "selected_story": "已选故事", "producer_feedback": "出品人修改意见",
    "story_feedback": "故事修改意见", "script_feedback": "剧本修改意见",
    "series_bible": "系列设定", "resolved_story_brief": "故事目标",
    "resolution_review": "故事目标审核", "screenplay_draft": "剧本草稿",
    "script": "剧本正文", "script_review": "剧本与分镜审核", "storyboard": "分镜表",
    "storyboard_receipt": "分镜导演回执", "styles": "视觉方案", "style_feedback": "视觉修改意见",
    "style_previews": "视觉预览", "selected_style": "已选视觉方案", "shots": "镜头素材",
    "audio": "配音素材", "delivery": "交付成片", "technical_checks": "技术质检",
    "cloud_authorization": "云端制作授权", "revision_request": "返修要求",
    "approved_assets": "已确认素材", "revised_script": "修订剧本", "edit_plan": "剪辑计划",
    "final_answer": "终审决定", "accepted_delivery": "正式交付",
}
DESCRIPTIONS = {
    "research": "根据创作需求与来源整理题材、事实和使用范围。",
    "proposals": "创作候选故事，独立审核后交由出品人或总制片选择。",
    "story_decision": "比较合格故事并确认本次制作方向。",
    "story_resolution": "落实故事目标与修改意见，由独立角色检查人物动机和因果。",
    "screenplay": "把故事目标写成可执行的剧本；旧版流程还包含分镜与审核。",
    "storyboard": "将剧本转为逐镜分镜，记录时长、动作和连续性要求。",
    "script_review": "独立检查剧本与分镜；未通过则返回剧本创作。",
    "visual_design": "依据剧本与反馈设计人物和画面方案。",
    "visual_development": "制作视觉预览，核对画风、人物和画面质量。",
    "style_decision": "确认视觉方向；重新设计时返回视觉方案。",
    "production": "制作镜头、配音、剪辑与字幕，并进行质量检查。",
    "final_decision": "由出品人审看片、确认交付或提出返修要求。",
    "delivery": "保存终审决定并交付已确认作品；返修请求继续进入制作。",
    "revision": "按返修要求修改剧本或剪辑计划，再返回镜头与后期。",
}


def presentation(nodes, workflow_version):
    ids = {node["id"] for node in nodes}
    stages = [{"id": ident, "label": label, "node_ids": [n for n in members if n in ids]}
              for ident, label, members in STAGES]
    return {"stages": stages,
            "branches": [{"id": "revision", "label": "定向返修", "node_ids": ["revision"],
                          "return_to": "production"}] if "revision" in ids else [],
            "workflow_version": workflow_version}


def decorate(nodes, workflow_version):
    order = [n for _, _, members in STAGES for n in members] + ["revision"]
    for node in nodes:
        node.update(description=DESCRIPTIONS.get(node["id"], node["label"]),
                    input_keys=node.pop("_input_keys"), output_keys=node.pop("_output_keys"),
                    conditional=workflow_version == 1 and node["id"] == "visual_design")
        node["input"] = "、".join(LABELS.get(k, k) for k in node["input_keys"])
        node["output"] = "、".join(LABELS.get(k, k) for k in node["output_keys"])
        node["display_order"] = order.index(node["id"]) if node["id"] in order else len(order)
    return sorted(nodes, key=lambda node: node["display_order"])
