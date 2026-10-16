"""Model-backed editorial advice; never an approval or workflow transition.

A durable intent precedes every call. An interrupted, unknown request is not
reposted. A human can still use the ordinary feedback/recovery boundary.
"""
from __future__ import annotations

import copy
import re
import uuid

from jsonschema import validate
from studio.core import ROOT

from . import store
from .config import load_config, resolve_llm
from .providers import redact_secrets
from .schemas import array_schema, cached_json_call, fingerprint, object_schema, text_schema

ROLE = "executive_producer"
SYSTEM = (
    "你是协助出品人处理独立审核失败的总制片顾问。基于完整审核问题、当前稿件和用户约束，"
    "给出最小范围且可执行的返修建议，并解释理由、风险及必须由用户决定的事项。"
    "若问题可在已批准创作目标、现有预算和授权范围内通过局部改写解决，必须选择recommendation=repair，decisions=[]；"
    "不能仅因为作品需要返修、需要重新审核或出品人最终可采纳建议，就选择human_decision。"
    "只有具体且尚未解决的目标冲突或授权缺口确实阻止返修时，才选择human_decision；"
    "此时decisions必须至少一项，逐项写清实际冲突、可选择方案及需要用户决定什么，使用完整问题并以问号结束。"
    "decisions不是动作枚举列表，严禁填repair、human_decision、retry等标签或笼统的请确认；"
    "例如是否保留现有角色关系，还是按你的新目标调整为陌生人？属于具体问题，repair不属于问题。"
    "不得替用户改变创作目标、增加预算或授权新费用、批准终片、宣布独立审核已通过；"
    "不能通过删除审核要求规避问题。不确定事项明确说明。feedback为可交给创作角色的返修指令，"
    "必须保留未涉及的设定和素材并要求修改后重新独立审核。仅给建议，不执行操作。"
)
SCHEMA = object_schema({
    "recommendation": {"type": "string", "enum": ["repair", "human_decision"],
                       "description": "现有目标、预算和授权内的局部返修用repair；仅具体未解决的目标冲突或授权缺口用human_decision。"},
    "rationale": text_schema(1, 1200),
    "feedback": text_schema(1, 3000),
    "risks": array_schema(text_schema(1, 400), 0, 6),
    "decisions": {**array_schema({**text_schema(8, 400), "pattern": "[?？]$",
                                  "description": "完整具体的待决定问题，写明冲突及可选方案，以问号结束；禁止repair、human_decision等枚举标签。"}, 0, 6),
                  "description": "repair时必须为空数组；human_decision时至少一个实际未解决、需要用户选择的问题。"},
})
PUBLIC_KEYS = frozenset({"status", "run_id", "input_signature", "source_revision", "started_at", "finished_at",
                         "profile_id", "requested_model", "model_source", "reason_code", "error_type", "proposal"})


def validate_proposal(proposal):
    """Validate structure and authority-dependent semantics across providers."""
    validate(proposal, SCHEMA)
    tokens = {"repair", "humandecision", "retry", "approve", "reject", "select", "escalate"}
    if any(re.sub(r"[\W_]+", "", question).casefold() in tokens for question in proposal["decisions"]):
        raise ValueError("Decision items must be concrete questions, not action tokens")
    if proposal["recommendation"] == "human_decision" and not proposal["decisions"]:
        raise ValueError("human_decision requires a substantive unresolved question")
    if proposal["recommendation"] == "repair" and proposal["decisions"]:
        raise ValueError("Authorized repair must not invent required human decisions")


def eligible(project):
    need = project.get("needs_decision") or {}
    return bool(project.get("status") == "blocked" and not project.get("pause_requested")
                and need.get("type") not in {"runtime", "budget", "production"}
                and need.get("kind") not in {"source_rights", "visual_review", "style_review", "final"}
                and not str(need.get("code", "")).startswith("cloud_")
                and (need.get("type") in {"review", "script_review", "creative_review", "edit_feedback"}
                     or need.get("kind") in {"creative_review", "script_review", "story_review"}))


def can_continue_auto_review(project, config=None):
    """Resume only a persisted, failed pre-production v2 screenplay review."""
    if (not eligible(project) or project.get("workflow_version") != 2
            or project.get("stage") != "script_review" or not project.get("selected_story")
            or project.get("selected_style") or project.get("revision_request") or project.get("delivery")
            or project.get("workflow_restart_from") or project.get("script_review_route") != "repair"):
        return False
    need = project.get("needs_decision") or {}
    if need.get("kind") != "script_review" and need.get("type") != "script_review":
        return False
    generation, index = project.get("script_generation"), project.get("creative_round")
    if type(generation) is not int or type(index) is not int or min(generation, index) < 1:
        return False
    marker = [generation, index]
    if (project.get("script_review_complete_round") != marker or project.get("screenplay_complete_round") != marker
            or (project.get("draft_review") or {}).get("passed") is not False
            or (project.get("script_review") or {}).get("passed") is not False
            or not project.get("screenplay_draft") or project.get("script_complete_generation") == generation):
        return False
    from .review_policy import max_review_rounds
    try:
        return index < max_review_rounds(load_config() if config is None else config)
    except (OSError, ValueError, TypeError):
        return False


def ensure_no_gpu_intents(project):
    """Local durable evidence only; an unknown remote intent must be reconciled."""
    import re
    from .creative_evidence_io import read_json
    ident = project.get("id", "")
    if not isinstance(ident, str) or not re.fullmatch(r"[a-f0-9]{12}", ident):
        raise ValueError("项目编号无效")
    folder = ROOT / "production" / ("factory-" + ident)
    if not folder.exists():
        return
    if folder.is_symlink():
        raise ValueError("请先核对已有 GPU 提交记录")
    for index, path in enumerate(folder.rglob("submission.json")):
        if index >= 500:
            raise ValueError("已有 GPU 提交记录过多，请先核对")
        try:
            submission = read_json(path)
            if not isinstance(submission, dict):
                raise ValueError("invalid_submission")
            job_id = submission.get("gpu_service_job_id")
            if job_id:
                result = read_json(path.parent / "remote_result.json")
                terminal = isinstance(result, dict) and result.get("id") == job_id and result.get("status") in {"completed", "failed"}
            else:
                terminal = submission.get("state") in {"completed", "failed"}
            if not terminal:
                raise ValueError("unresolved_submission")
        except (OSError, ValueError, TypeError):
            raise ValueError("已有 GPU 任务尚未确认完成，请先核对原提交；不会启动新的自动返修") from None


def retry_unconfigured(record):
    """A newly configured binding can retry a call that provably never started."""
    if record.get("status") != "manual" or record.get("reason_code") != "supervisor_not_configured" or record.get("model_source") != "not_called":
        return False
    try:
        config = load_config()
        if ROLE not in config.get("llm_roles", {}):
            return False
        profile = resolve_llm(config, ROLE)
        return bool(profile.get("configured") and profile.get("enabled", True))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def context_for(project):
    # No historic events, API profiles or production credentials enter the prompt.
    # Keep the entire current review and drafts; oversized evidence fails clearly
    # at the ordinary model context boundary instead of silently losing issues.
    keys = ("id", "brief", "format", "duration_seconds", "creation_mode", "production_profile", "budget",
            "proposal_generation", "script_generation", "production_revision", "selected_story", "selected_style",
            "needs_decision", "proposals", "draft_review",
            "creation_review", "script_review", "resolved_story_brief", "story_feedback", "script_feedback",
            "style_feedback", "revision_request")
    current = {key: copy.deepcopy(project.get(key)) for key in keys}
    current["draft"] = copy.deepcopy(project.get("screenplay_draft") or project.get("script") or project.get("episode"))
    from .creative import _producer_feedback
    revision = project.get("revision_request") or {}
    current["producer_feedback"] = _producer_feedback(project, revision.get("feedback"))
    return {"project": current,
            "authority": {"advice_only": True, "independent_review_required": True,
                          "human_required": ["brief_change", "budget_increase", "new_fees", "final_approval"]}}


def signature(project):
    return fingerprint(context_for(project))


def queued_record(project):
    return {"status": "queued", "run_id": uuid.uuid4().hex, "input_signature": signature(project),
            "source_revision": project["revision"], "model_source": "not_called"}


def snapshot(project):
    record = project.get("review_advice")
    if not isinstance(record, dict):
        return None
    result = {key: copy.deepcopy(value) for key, value in record.items() if key in PUBLIC_KEYS}
    result["current"] = eligible(project) and record.get("input_signature") == signature(project)
    proposal = result.get("proposal")
    if proposal:
        try:
            validate_proposal(proposal)
        except Exception:
            result.pop("proposal", None)
    return result


def _owns(db, job):
    row = db.execute("SELECT status,lease FROM jobs WHERE id=?", (job["id"],)).fetchone()
    return bool(row and row["status"] == "running" and row["lease"] == job.get("lease"))


def run(pid, job, config=None):
    """Called by the owning worker only; leaves status/needs/budget untouched."""
    profile, problem = {}, None
    try:
        config = load_config() if config is None else config
        if ROLE not in config.get("llm_roles", {}):
            raise ValueError("missing_explicit_binding")
        profile = resolve_llm(config, ROLE)
        if not profile.get("configured") or not profile.get("enabled", True):
            raise ValueError("disabled_binding")
    except (OSError, ValueError, KeyError, TypeError):
        problem = "supervisor_not_configured"
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        project = store._read(db, pid)
        if not _owns(db, job) or not eligible(project):
            return None
        payload = context_for(project)
        sig = fingerprint(payload)
        previous = project.get("review_advice") or {}
        if previous.get("input_signature") == sig and previous.get("status") != "queued":
            # Restart is not evidence that the provider did nothing. A fresh
            # lease must expose uncertainty, not a permanently running spinner.
            if previous.get("status") == "requested" and (
                    previous.get("job_id") != job["id"] or previous.get("job_lease") != job.get("lease")):
                previous.update(status="manual", reason_code="previous_request_unresolved", finished_at=store.now())
                previous.pop("proposal", None)
                project["review_advice"] = previous
                store._write(db, project)
                store._event(db, pid, "此前 AI 分析请求结果尚未确认，不会重复调用；可继续人工处理。", "review_advice",
                             {"run_id": previous.get("run_id"), "status": "manual", "reason_code": "previous_request_unresolved"})
            return snapshot(project)
        record = copy.deepcopy(previous) if previous.get("input_signature") == sig else queued_record(project)
        record.update(status="manual" if problem else "requested", started_at=store.now(),
                      job_id=job["id"], job_lease=job["lease"], reason_code=problem, profile_id=profile.get("profile"), requested_model=profile.get("model"),
                      model_source="not_called" if problem else "request")
        if problem:
            record["finished_at"] = store.now()
        project["review_advice"] = record
        store._write(db, project)
        expected_revision = project["revision"]
        store._event(db, pid, "总制片未配置，可直接提交修改意见。" if problem else "总制片开始分析当前审核问题。",
                     "review_advice", {"run_id": record["run_id"], "status": record["status"]})
        if problem:
            return snapshot(project)
    proposal, error_type = None, None
    try:
        proposal = cached_json_call(project, config, ROLE, "review-advice-" + sig[:24], SYSTEM,
                                    payload, SCHEMA, max_tokens=3072, temperature=0.2)
        validate_proposal(proposal)
        proposal = redact_secrets(proposal, profile)
        validate_proposal(proposal)
    except Exception as exc:
        error_type = type(exc).__name__[:100]
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        current = store._read(db, pid)
        saved = current.get("review_advice") or {}
        if saved.get("run_id") != record["run_id"] or saved.get("status") != "requested":
            return None
        stable = (_owns(db, job) and eligible(current) and current["revision"] == expected_revision
                  and signature(current) == sig)
        saved.update(finished_at=store.now(), error_type=error_type)
        if not stable:
            saved.update(status="superseded", reason_code="project_or_evidence_changed")
        elif error_type:
            saved.update(status="manual", reason_code="model_failure")
        else:
            saved.update(status="ready", reason_code=None, proposal=proposal)
        current["review_advice"] = saved
        store._write(db, current)
        store._event(db, pid, "已保存 AI 返修建议，请出品人决定是否采纳。" if saved["status"] == "ready"
                     else "AI 建议未应用，现有审核问题和人工处理入口保留。", "review_advice",
                     {"run_id": saved["run_id"], "status": saved["status"], "reason_code": saved.get("reason_code")})
        return snapshot(current)
