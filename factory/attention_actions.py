"""Executable producer actions shared by operator views and the command boundary.

This registry describes commands, never model plans. Recovery mutates only the
project snapshot; the worker consumes the explicit graph restart marker.
"""

from __future__ import annotations

import copy
from urllib.parse import urlsplit

from .review_feedback import feedback_choices

GENERATIONS = {
    "story": "proposal_generation",
    "style": "style_generation",
    "final": "production_revision",
}
RECOVERY_ACTIONS = frozenset(
    {
        "provide_authorized_source",
        "switch_to_original",
        "revise_brief",
        "regenerate",
        "provide_feedback",
    }
)


def generation(project, kind):
    key = GENERATIONS.get(kind)
    return project.get(
        key, project.get("script_generation") if kind == "style" else None
    )


def delegation_status(project, config=None):
    """Describe manual fallback without changing the requested team mode."""
    if project.get("team_mode", "guided") != "delegated":
        return {
            "requested": False,
            "available": False,
            "mode": "human",
            "reason_code": None,
            "reason": "由出品人选择故事与视觉",
        }
    from .config import load_config, resolve_llm

    reason = None
    try:
        config = load_config() if config is None else config
        if "executive_producer" not in config.get("llm_roles", {}):
            reason = "supervisor_not_configured"
        else:
            profile = resolve_llm(config, "executive_producer")
            if not profile.get("enabled", True):
                reason = "supervisor_disabled"
    except (OSError, ValueError, TypeError, KeyError):
        reason = "supervisor_configuration_invalid"
    return {
        "requested": True,
        "available": reason is None,
        "mode": "delegated" if reason is None else "human_fallback",
        "reason_code": reason,
        "reason": "总制片代理尚未可用，故事与视觉选择交由出品人处理；项目可继续。"
        if reason
        else "总制片在授权范围内选择故事和视觉，终片仍由出品人确认。",
    }


def validate_gate(project, gate, choice=None):
    """Validate the business version as well as the optimistic project revision."""
    kind = gate.get("type")
    version = generation(project, kind)
    if (
        kind not in GENERATIONS
        or type(version) is not int
        or gate.get("id") != f"{project['id']}:{kind}:{version}"
    ):
        raise ValueError("该决策对应旧版本，请刷新当前方案后再选择")
    if gate.get("status") != "pending" or project.get("status") in {
        "approved",
        "cancelled",
    }:
        raise ValueError("当前没有对应的待办决策，请刷新后再选择")
    if choice is None:
        return
    valid = {o.get("id") if isinstance(o, dict) else o for o in gate.get("options", [])}
    special = {"story": "__repropose__", "style": "__restyle__"}.get(kind)
    if choice == special:
        return
    if choice not in valid:
        raise ValueError("所选方案不属于当前决策")
    modern = project.get("workflow_version", 1) >= 2
    if modern:
        if kind == "story" and (
            project.get("proposals_complete_generation") != version
            or (project.get("creation_review") or {}).get("passed") is not True
        ):
            raise ValueError("当前故事方案尚未通过本版本的独立审核")
        if kind == "style" and (
            project.get("script_complete_generation")
            != project.get("script_generation")
            or project.get("style_previews_generation") != version
            or (project.get("script_review") or {}).get("passed") is not True
        ):
            raise ValueError("当前剧本审核或视觉预览版本尚未完成")
        if kind == "final" and (
            project.get("delivery_complete_revision") != version
            or not project.get("delivery")
        ):
            raise ValueError("当前成片版本尚未完成，不能提交终审决定")
    if kind in {"story", "style"}:
        candidates = project.get("proposals" if kind == "story" else "styles") or []
        # Old imported gates can predate embedded candidate snapshots. Once a
        # snapshot exists, its eligibility is authoritative over the old options.
        if modern or candidates:
            selected = next(
                (item for item in candidates if item.get("id") == choice), None
            )
            if selected is None or (
                kind == "story"
                and (
                    selected.get("eligible") is False
                    or (modern and selected.get("eligible") is not True)
                )
            ):
                raise ValueError("所选方案已失效或未通过审核，请刷新后再选择")
    if kind == "final" and project.get("delivery_complete_revision") is not None:
        if project.get("delivery_complete_revision") != version or not project.get(
            "delivery"
        ):
            raise ValueError("当前成片版本尚未完成，不能提交终审决定")


def _field(name, label, kind="textarea", required=True, **extra):
    return {"name": name, "label": label, "type": kind, "required": required, **extra}


def _need(project):
    return project.get("needs_decision") or {}


def _creative(project):
    need = _need(project)
    return need.get("type") in {
        "review",
        "script_review",
        "creative_review",
        "edit_feedback",
    } or need.get("kind") in {
        "creative_review",
        "script_review",
        "story_review",
        "source_rights",
    }


def _rights(project):
    return _need(project).get("kind") == "source_rights"


def _editable(project):
    return project.get("status") in {
        "blocked",
        "failed",
        "awaiting_story",
        "awaiting_style",
    }


def _feedback_fields(project):
    return [_field("feedback", "修改要求", maxlength=4000, choices=feedback_choices(project))]


def _retry_fields(project):
    need = _need(project)
    if need.get("type") == "budget":
        fields = []
        for limit in need.get("exhausted_limits") or ["gpu"]:
            key = "gpu_minutes" if limit == "gpu" else "wall_minutes"
            fields.append(
                _field(
                    key,
                    "GPU 新上限（卡分钟）"
                    if limit == "gpu"
                    else "制作时间新上限（分钟）",
                    "number",
                    min=project.get("budget", {}).get(key, 0),
                    max=1440,
                )
            )
        return fields
    return _feedback_fields(project) if _creative(project) else []


def _revision_fields(project):
    return [
        _field(
            "shot_ids",
            "需要返修的镜头",
            "multiselect",
            options=[
                {
                    "id": s["id"],
                    "label": s["id"]
                    + " · "
                    + str(s.get("dramatic_beat") or s.get("action") or "")[:90],
                }
                for s in project.get("shots", [])
            ],
        ),
        *_feedback_fields(project),
    ]


def _source_fields(project):
    return [
        _field("title", "来源标题", "text", False, maxlength=200),
        _field(
            "url", "来源网址（网址或原文至少填写一项）", "text", False, maxlength=2000
        ),
        _field("text", "获准改编的原文", "textarea", False, maxlength=12000),
        _field(
            "rights",
            "素材权限",
            "select",
            options=[
                {"id": key, "label": label}
                for key, label in (
                    ("owned", "自有"),
                    ("licensed", "已授权"),
                    ("public_domain", "公有领域"),
                )
            ],
        ),
    ]


def _restart(project, target):
    for gate in project.get("decisions", []):
        if gate.get("status") == "pending":
            gate["status"] = "superseded"
    project.update(
        status="queued",
        stage=target,
        pause_requested=False,
        error=None,
        needs_decision=None,
        workflow_restart_from=target,
        workflow_restart_revision=project["revision"] + 1,
    )


def _invalidate(project, target):
    """Keep cumulative spend and historical files; invalidate only current output."""
    project["production_revision"] += 1
    for key in (
        "delivery_complete_revision",
        "revision_prepared",
        "production_edit_revision",
    ):
        project.pop(key, None)
    project.update(delivery=None, revision_request=None)
    if target in {"research", "proposals", "story_resolution"}:
        previous_style_generation = generation(project, "style")
        project["script_generation"] += 1
        project["style_generation"] = previous_style_generation + 1
        for key in (
            "script_complete_generation",
            "story_resolution_complete_generation",
            "screenplay_complete_generation",
            "storyboard_complete_generation",
            "script_review_complete_generation",
            "visual_design_complete_generation",
            "style_previews_generation",
        ):
            project.pop(key, None)
        project.update(selected_style=None, styles=[])
    if target in {"research", "proposals"}:
        project["proposal_generation"] += 1
        project.pop("proposals_complete_generation", None)
        project.update(
            selected_story=None,
            proposals=[],
            creation_review=None,
            script=None,
            episode=None,
            script_review=None,
            resolved_story_brief=None,
            shots=[],
        )
    if target == "research":
        project.pop("research_complete", None)
        project.pop("research", None)
    _restart(project, target)


def _provide_source(project, payload):
    sources = payload.get("sources")
    if sources is None:
        sources = [
            {key: payload.get(key, "") for key in ("title", "url", "text", "rights")}
        ]
    if (
        not isinstance(sources, list)
        or not sources
        or len(sources) > 12
        or any(not isinstance(s, dict) for s in sources)
    ):
        raise ValueError("请提供 1 至 12 个授权素材来源")
    normalized = []
    for source in sources:
        item = {key: source.get(key, "") for key in ("title", "url", "text", "rights")}
        if any(not isinstance(v, str) for v in item.values()):
            raise ValueError("素材标题、网址、原文与权限须为文本")
        item = {key: value.strip() for key, value in item.items()}
        if item["rights"] not in {"owned", "licensed", "public_domain"}:
            raise ValueError("请明确选择自有、已授权或公有领域素材")
        if not (item["url"].strip() or item["text"].strip()):
            raise ValueError("来源网址或获准改编的原文至少填写一项")
        if (
            len(item["text"]) > 12000
            or len(item["url"]) > 2000
            or len(item["title"]) > 200
        ):
            raise ValueError("素材来源超出长度限制")
        if item["url"]:
            url = urlsplit(item["url"])
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
            ):
                raise ValueError("素材网址须为有效 HTTP(S) 地址")
        normalized.append(item)
    retained = [s for s in project.get("submitted_sources", []) if s not in normalized]
    project["submitted_sources"] = (retained + copy.deepcopy(normalized))[-12:]
    project["sources"] = copy.deepcopy(project["submitted_sources"])
    _invalidate(project, "research")


def _switch_original(project, payload):
    project["creation_mode"] = "original"
    _invalidate(project, "research")


def _revise_brief(project, payload):
    brief = payload.get("brief")
    if not isinstance(brief, str) or not 5 <= len(brief.strip()) <= 6000:
        raise ValueError("请填写 5–6000 字的创作方向")
    project["brief"] = brief.strip()
    project.pop("story_feedback", None)
    project.pop("script_feedback", None)
    _invalidate(project, "research")


def _feedback(project, payload):
    from .store import _revision_feedback

    feedback = payload.get("feedback")
    if (
        not isinstance(feedback, str)
        or not feedback.strip()
        or len(feedback.strip()) > 4000
    ):
        raise ValueError("请提供 1 至 4000 字的修改要求")
    if project.get("revision_request"):
        project["revision_request"]["feedback"] = _revision_feedback(project, payload)
        project.pop("revision_prepared", None)
        project["script_generation"] += 1
        _restart(project, "revision")
    elif project.get("selected_story"):
        project["script_feedback"] = _revision_feedback(project, payload)
        _invalidate(project, "story_resolution")
    else:
        project["story_feedback"] = feedback.strip()
        _invalidate(project, "proposals")


def _regenerate(project, payload):
    if payload.get("feedback"):
        return _feedback(project, payload)
    _invalidate(
        project, "story_resolution" if project.get("selected_story") else "proposals"
    )


# Every exposed identifier must pass this registry at the store command boundary.
# Legacy handlers continue in store.action, preserving their existing API shape.
REGISTRY = {
    "take_control": {
        "label": "转为人工控制",
        "inputs": lambda p: [],
        "impact": "安全暂停正在执行的步骤，后续故事与视觉由你选择",
        "when": lambda p: (
            p.get("team_mode") == "delegated"
            and p.get("status") not in {"approved", "cancelled"}
        ),
    },
    "delegate_control": {
        "label": "交由总制片推进",
        "inputs": lambda p: [],
        "impact": "在授权范围内自动选择故事与视觉，费用与终片仍须你确认",
        "when": lambda p: (
            p.get("team_mode", "guided") == "guided"
            and p.get("status") not in {"approved", "cancelled", "awaiting_final"}
        ),
    },
    "pause": {
        "label": "暂停制作",
        "inputs": lambda p: [],
        "impact": "当前执行单元结束后保留进度并暂停",
        "when": lambda p: (
            p.get("status")
            not in {"approved", "cancelled", "paused", "blocked", "failed"}
        ),
    },
    "resume": {
        "label": "恢复制作",
        "inputs": _retry_fields,
        "impact": "从保存的检查点恢复，保留累计用量",
        "when": lambda p: p.get("status") == "paused" or bool(p.get("pause_requested")),
    },
    "retry": {
        "label": "重试当前阶段",
        "inputs": _retry_fields,
        "impact": "保留已完成产物，从失败步骤继续",
        "when": lambda p: p.get("status") in {"blocked", "failed"} and not _rights(p),
    },
    "revise": {
        "label": "返修指定镜头",
        "inputs": _revision_fields,
        "impact": "重新审核指定镜头，保留未修改素材与累计预算",
        "when": lambda p: (
            p.get("status") == "blocked"
            and _need(p).get("type") == "production"
            and bool(
                p.get("selected_story") and p.get("selected_style") and p.get("shots")
            )
        ),
    },
    "repropose": {
        "label": "重新创作故事提案",
        "inputs": _feedback_fields,
        "impact": "当前候选失效，按新意见重新提案",
        "when": lambda p: p.get("status") == "awaiting_story",
    },
    "provide_authorized_source": {
        "label": "补充授权素材",
        "inputs": _source_fields,
        "impact": "保存素材来源并重新研究和审查授权",
        "when": lambda p: _editable(p) and _rights(p),
        "handler": _provide_source,
    },
    "switch_to_original": {
        "label": "切换原创创作",
        "inputs": lambda p: [],
        "impact": "取消改编模式，重新研究并创作原创故事",
        "when": lambda p: _editable(p) and _rights(p),
        "handler": _switch_original,
    },
    "revise_brief": {
        "label": "修改创作方向",
        "inputs": lambda p: [
            _field("brief", "新的创作方向", maxlength=6000, value=p.get("brief", ""))
        ],
        "impact": "重新研究、提案和审稿，既有素材保留为历史版本",
        "when": lambda p: (
            _editable(p) and (_creative(p) or p.get("status") == "awaiting_story")
        ),
        "handler": _revise_brief,
    },
    "regenerate": {
        "label": "重新生成当前方案",
        "inputs": lambda p: [
            _field(
                "feedback",
                "新方案要求",
                required=p.get("status") == "awaiting_style",
                maxlength=4000,
                choices=feedback_choices(p),
            )
        ],
        "impact": "生成新版本并重新审核；视觉方案会重新生成预览",
        "when": lambda p: (
            _editable(p)
            and not _rights(p)
            and (
                _creative(p) or p.get("status") in {"awaiting_story", "awaiting_style"}
            )
        ),
        "handler": _regenerate,
    },
    "continue_auto_review": {
        "label": "继续自动返修",
        "inputs": lambda p: [],
        "impact": "保留当前剧本版本和已完成审核轮次，从原检查点继续剩余自动返修，不提高上限",
        "when": lambda p: _can_continue_auto_review(p),
    },
    "request_review_advice": {
        "label": "请 AI 分析并推荐",
        "inputs": lambda p: [],
        "impact": "总制片根据当前审核问题给出返修建议，由你决定是否采纳",
        "when": lambda p: _advice_eligible(p),
    },
    "provide_feedback": {
        "label": "提交意见并重新审核",
        "inputs": _feedback_fields,
        "impact": "把意见落实到当前故事或返修稿，重新独立审核",
        "when": lambda p: (
            p.get("status") in {"blocked", "failed"} and _creative(p) and not _rights(p)
        ),
        "handler": _feedback,
    },
}


def _can_continue_auto_review(project):
    from .review_advice import can_continue_auto_review
    return can_continue_auto_review(project)


def _advice_eligible(project):
    from .review_advice import eligible
    return eligible(project)


def descriptors(project, *, job_running=False):
    actions = []
    for ident, spec in REGISTRY.items():
        if not spec["when"](project):
            continue
        disabled_reason = (
            "当前任务正在收尾，完成后可提交"
            if job_running and ident not in {"pause", "take_control"}
            else None
        )
        if ident == "delegate_control":
            policy = delegation_status({**project, "team_mode": "delegated"})
            if not policy["available"]:
                disabled_reason = policy["reason"]
            elif str(_need(project).get("code", "")).startswith("cloud_"):
                disabled_reason = "请先完成云任务费用授权或核对不确定的提交"
            elif any(
                record.get("status") == "requested"
                for record in (project.get("supervision") or {}).values()
                if isinstance(record, dict)
            ):
                disabled_reason = "此前总制片调用结果尚未确认，请先人工处理当前方案"
        if ident == "request_review_advice":
            from .review_advice import snapshot as advice_snapshot, retry_unconfigured
            advice = advice_snapshot(project)
            if advice and advice.get("current") and not retry_unconfigured(advice):
                disabled_reason = {"queued": "AI 建议已排队", "requested": "此前 AI 请求结果尚未确认，请使用人工处理入口",
                                   "ready": "已有当前审核的 AI 建议", "manual": "本次 AI 分析未完成，可直接人工处理"}.get(advice.get("status"))
        label = (
            "调整预算并继续"
            if ident == "retry" and _need(project).get("type") == "budget"
            else spec["label"]
        )
        actions.append(
            {
                "id": ident,
                "label": label,
                "inputs": spec["inputs"](project),
                "impact": spec["impact"],
                "enabled": disabled_reason is None,
                "disabled_reason": disabled_reason,
            }
        )
    return actions


def recover(project, payload):
    ident = payload.get("action")
    spec = REGISTRY.get(ident)
    if ident not in RECOVERY_ACTIONS or not spec["when"](project):
        raise ValueError("当前阶段不支持此处理方式，请刷新项目状态")
    spec["handler"](project, payload)
