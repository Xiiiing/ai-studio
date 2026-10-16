"""Bounded review evidence and optional text feedback shortcuts.

Choices describe feedback only. Existing action/gate validation and independent
review remain authoritative; choosing an item never accepts an unreviewed output.
"""
from __future__ import annotations

import hashlib
import re


def obj(value):
    return value if isinstance(value, dict) else {}


def text(value, limit=1200):
    return value[:limit] if isinstance(value, str) else ""


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



def _review_context(project):
    """Do not turn service, budget or source-rights failures into editorial work."""
    need = obj(project.get("needs_decision"))
    kind = need.get("kind")
    if (need.get("type") == "budget" or kind == "source_rights"
            or str(need.get("code", "")).startswith("cloud_")):
        return None
    status = project.get("status")
    if status in {"approved", "cancelled", "paused"} or project.get("pause_requested"):
        return None
    if status in {"blocked", "failed"}:
        if need.get("type") == "production":
            return "final"
        if (need.get("type") not in {"review", "script_review", "creative_review", "edit_feedback"}
                and kind not in {"creative_review", "script_review", "story_review"}):
            return None
        if need.get("type") == "edit_feedback" or project.get("revision_request"):
            return "final"
        if kind in {"visual_review", "style_review"}:
            return "style"
        return "script" if project.get("selected_story") else "story"
    return {"awaiting_story": "story", "awaiting_style": "style", "awaiting_final": "final"}.get(status)


_DIRECTIONS = {
    "story": (
        ("motivation", "让人物动机更清楚", "补齐主角目标、采取行动的理由与关键因果，让选择和结局自然成立。"),
        ("conflict", "加强冲突与转折", "让核心冲突更集中，关键转折有前置铺垫，结尾回应开场问题。"),
        ("feasible", "简化故事，便于制作", "减少非必要人物、场景与支线，保留核心冲突和情感落点，控制制作复杂度。"),
    ),
    "script": (
        ("continuity", "补齐镜头因果与连续性", "补齐人物行动、道具来源及前后镜头的因果，明确位置和状态变化，保留未涉及的镜头。"),
        ("dialogue", "让对白更自然精炼", "精简重复解释，按人物身份重写对白，让语气自然、信息明确且适合镜头时长。"),
        ("visual_action", "把关键情节拍清楚", "用明确的可见动作与镜头表现关键情节，补齐景别、主体和动作衔接，避免只靠旁白解释。"),
    ),
    "style": (
        ("identity", "人物形象更统一", "保持角色年龄、面部、发型、服装与道具一致，重新设计符合剧本人物设定的视觉方案。"),
        ("clarity", "画面更清晰，减少杂乱", "突出人物和关键动作，简化背景与装饰，统一色彩和光线，让构图更易读。"),
        ("restrained", "画风更自然克制", "降低夸张和过度装饰，采用自然光线与协调色彩，保留故事时代和人物设定。"),
    ),
    "final": (
        ("pacing", "调整剪辑节奏", "调整镜头停留与转场，缩短重复空镜，保留对白完整和关键情节，使叙事节奏更连贯。"),
        ("sound", "改善对白与音量", "检查对白可懂度、音画同步及音量平衡，修复爆音、截断和不自然停顿。"),
        ("subtitles", "检查字幕与镜头衔接", "修正字幕文字、时间与遮挡，检查镜头衔接和人物道具连续性，保留未涉及的素材。"),
    ),
}


def feedback_choices(project):
    """Return a small immutable-by-convention menu; arbitrary notes remain valid."""
    context = _review_context(project)
    if context is None:
        return []
    # A choice gate is already reviewed. Older script/proposal reviews must not
    # appear as current failures in style or final approval menus.
    if project.get("status") in {"blocked", "failed"}:
        evidence_project = project
        if obj(project.get("needs_decision")).get("type") == "production":
            evidence_project = {**project, "creation_review": None, "script_review": None}
        issues = review_issues(evidence_project)
    else:
        issues = []
    significant = [i for i in issues if i["severity"] in {"blocking", "major", "error"}] or issues
    choices = []
    preserve = "同时处理原审核中其余阻塞问题，保留未涉及的设定和素材；修改后重新独立审核。"
    if significant:
        # Keep every normalized issue in the all-issues instruction, even when a
        # verbose source needs a compact excerpt. The full review remains the basis.
        allowance = min(600, (3000 - 180) // len(significant))
        lines = []
        for issue in significant:
            scope = "、".join(issue["shot_ids"][:12])
            line = (scope + "：" if scope else "") + issue["description"]
            if issue["suggestion"]:
                line += "；建议：" + issue["suggestion"]
            lines.append(line[:allowance])
        choices.append({"id": "all_review_issues", "label": "按全部审核建议修改", "kind": "all_issues",
                        "feedback": "依据当前完整审核记录，逐项修复全部阻塞问题：\n" + "\n".join(lines)
                        + "\n保留未涉及的设定和素材；以上为问题摘要，不免除完整审核记录中的其他要求。修改后重新独立审核。"})
        for issue in significant[:8]:
            scope = "、".join(issue["shot_ids"][:8])
            content = (scope + "：" if scope else "") + issue["description"][:280]
            if issue["suggestion"]:
                content += "；建议：" + issue["suggestion"][:320]
            identity = "\0".join((issue["code"], issue["description"], ",".join(issue["shot_ids"])))
            choices.append({"id": "issue_" + hashlib.sha256(identity.encode()).hexdigest()[:12],
                            "label": ((scope + " · ") if scope else "") + issue["description"][:72],
                            "kind": "issue", "feedback": ("重点修改：" + content + "。" + preserve)[:800]})
    for ident, label, instruction in _DIRECTIONS[context]:
        choices.append({"id": context + "_" + ident, "label": label, "kind": "direction",
                        "feedback": instruction + (preserve if significant else "保留未涉及的设定和素材；修改后按现有流程重新审核。")})
    return choices
