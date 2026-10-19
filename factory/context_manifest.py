"""Evidence of selected inputs, never an execution or approval authority.

Manifests describe exact inputs at the call boundary. Project facts are captured
by the caller, not looked up later using today's mutable project/configuration.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path
import re
import time
import uuid

from .creative_evidence_io import canonical, digest, read_json

SCHEMA = "ai-studio.context-manifest.v1"
_scope = ContextVar("factory_call_materials", default=None)
VERSIONS = ("revision", "workflow_version", "script_generation", "proposal_generation",
            "style_generation", "production_revision", "plan_version", "plan_cycle",
            "plan_attempt", "creative_round")
LABELS = {
    "project": "本次项目要求", "brief": "创作要求", "producer_feedback": "出品人反馈",
    "story_feedback": "故事修改意见", "script_feedback": "剧本修改意见",
    "producer_task_requirements": "本次任务要求", "revision_feedback": "返修意见",
    "series_bible": "全剧共享设定", "material_package": "已确认参考素材",
    "selected_proposal": "所选故事", "selected_story": "所选故事",
    "resolved_story_brief": "已落实创作目标", "screenplay_draft": "剧本稿件",
    "episode": "本次剧本", "previous_episode": "返修前剧本", "previous_attempt": "上一轮稿件",
    "independent_feedback": "独立审核意见", "review_feedback": "审核返修意见",
    "repair_issues": "待修问题", "technical_findings": "结构检查结果",
    "approved_shot": "已批准镜头", "approved_style": "已批准画风",
    "first_frame_references": "批准首帧参考", "repair": "本轮返修依据",
    "world": "全剧设定", "outline": "本集大纲", "episodes": "分集大纲",
    "scenes": "场景方案", "previous": "前序资料", "source": "来源资料",
    "sources": "来源资料", "research": "研究资料", "source_excerpt": "来源摘录",
    "authorized_adaptation_material": "授权改编资料", "context": "审核依据",
    "timeline": "时长安排", "only_revise_shot_ids": "限定返修镜头",
    "dependency_context": "前集固定版本", "previous_unit_state": "上一单元结尾",
    "draft_plan": "规划稿件", "plan_review": "规划审核意见",
    "memory": "既有项目记忆", "source_text": "来源原文",
    "supplement_request": "定向补料单", "locked_shot_ids": "保留不变的镜头",
}
SOURCE_KEYS = tuple(k for k in LABELS if k not in {"project", "context", "previous", "episode"})
TEXT_FIELDS = {"brief", "producer_feedback", "story_feedback", "script_feedback", "revision_feedback",
               "world", "synopsis", "action", "motion_prompt", "notes", "requirements", "visual_rules"}


def _present(value):
    return value is not None and value != "" and value != [] and value != {}


@contextmanager
def capture(project, stage):
    state = {"project_id": project.get("id"), "stage": stage,
             "project_version": {k: project[k] for k in VERSIONS if k in project},
             "sources": {k: digest(project[k]) for k in SOURCE_KEYS if _present(project.get(k))}}
    token = _scope.set(state)
    try:
        yield state
    finally:
        _scope.reset(token)


def _summary(key, value):
    # Only explicit product text can enter the polling projection. Arbitrary
    # private prompt/config fields remain available solely in original files.
    if isinstance(value, str) and key in TEXT_FIELDS:
        return value[:360]
    if isinstance(value, dict):
        parts = [str(value[k])[:120] for k in ("title", "brief", "notes", "action")
                 if isinstance(value.get(k), str)]
        return " · ".join(parts)[:360] or f"{len(value)} 个字段；原文见请求文件"
    if isinstance(value, list):
        return f"{len(value)} 项；原文见请求文件"
    return f"{len(value)} 字；原文见请求文件" if isinstance(value, str) else "原值见请求文件"


def build(role, payload, prompt, execution=None, *, content=None):
    state = _scope.get() or {}
    context = copy.deepcopy(execution or {})
    for key in ("project_id", "stage"):
        if key not in context and state.get(key):
            context[key] = state[key]
    result = {"schema": SCHEMA, "role": role, "execution": context,
              "project_version": copy.deepcopy(state.get("project_version", {})),
              "prompt": copy.deepcopy(prompt), "input_sha256": digest(payload),
              "materials": [], "source_checks": [], "truncated": False}
    from .role_handoffs import current as current_handoffs
    if current_handoffs():
        result['controlled_handoff_ids'] = current_handoffs()
    values = payload if isinstance(payload, dict) else {}
    if content is not None:
        # In VLM calls payload is None. The actual multimodal content is the
        # authority; never extract system/user-prefix wrappers by splitting text.
        result["content_sha256"] = digest(content)
        values = {}
        for index, part in enumerate(content):
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                try:
                    value = json.loads(part.get("text", ""))
                except (ValueError, TypeError):
                    value = part.get("text", "")
            else:
                value = part
            values[f"content[{index}]"] = value
    included = set()

    def material(path, key, value):
        if len(result["materials"]) >= 96:
            result["truncated"] = True
            return
        selected = _present(value)
        identity = digest(value)
        if selected:
            included.add(identity)
        sources = [k for k, sha in state.get("sources", {}).items() if sha == identity and selected]
        row = {"field": path, "label": LABELS.get(key, key[:100]), "sha256": identity,
               "bytes": len(canonical(value)), "state": "selected" if selected else "empty",
               "reason": "包含在本次输入" if selected else "本次输入为空，未补写资料",
               "summary": _summary(key, value) if selected else "未提供",
               "source_fields": sources}
        # These are versions carried in the selected value, not inferred from
        # the current project version when passing an older draft.
        if isinstance(value, dict):
            row["version"] = {k: value[k] for k in ("version", "plan_version", "sha256", "source_signature")
                              if isinstance(value.get(k), (str, int)) and not isinstance(value[k], bool)}
            if value.get("truncated") is True:
                row["reason"] = "调用方标记此材料已截取；清单记录实际选入部分，未推断省略内容"
        result["materials"].append(row)

    for key, value in values.items():
        if isinstance(value, dict) and (key == "project" or key.startswith("content[")):
            for child, item in value.items():
                material(f"{key}.{child}", child, item)
        else:
            material(key, key, value)
        if key == "producer_feedback" and isinstance(value, dict):
            for index, item in enumerate(value.get("instructions", [])[:8]):
                if isinstance(item, dict) and item.get("source") in TEXT_FIELDS and "content" in item:
                    material(f"producer_feedback.instructions[{index}].content", item["source"], item["content"])
    for key, sha in state.get("sources", {}).items():
        result["source_checks"].append({"field": key, "label": LABELS[key], "sha256": sha,
            "state": "exact_match" if sha in included else "not_confirmed",
            "reason": "完整同值材料已选入" if sha in included else
                      "未确认完整同值材料进入输入；可能使用了摘录或改写，不能据此推断已读取"})
    return result


def _project_root(folder, pid):
    if not isinstance(pid, str) or not re.fullmatch(r"[a-f0-9]{12}", pid):
        return None
    return next((p for p in Path(folder).parents
                 if p.name == "factory-" + pid and p.parent.name == "production"), None)


def completed(folder, receipt):
    state = _scope.get()
    if state is not None:
        state["completed_call"] = {"folder": Path(folder), "receipt": copy.deepcopy(receipt)}
    pid = (state or {}).get("project_id") or receipt.get("execution_context", {}).get("project_id")
    root = _project_root(folder, pid)
    if root is not None:
        from studio.core import write_json
        try:
            # Production's per-shot H3/VLM output folders are intentionally not
            # recursively scanned by the read API. Index only actual calls.
            write_json(root / "planning/context-calls" / (receipt["call_id"] + ".json"),
                       {"schema": "ai-studio.context-call-ref.v1", "call_id": receipt["call_id"],
                        "receipt": (Path(folder) / "receipt.json").relative_to(root).as_posix()})
        except OSError:
            pass  # The original receipt and task ledger still own the evidence.


def cache_origin(state, base):
    call = state.get("completed_call")
    if not call or call["receipt"].get("status") != "succeeded":
        return None
    try:
        relative = (call["folder"] / "receipt.json").relative_to(base).as_posix()
    except ValueError:
        return None
    return {"receipt": relative, "call_id": call["receipt"]["call_id"],
            "output_sha256": call["receipt"].get("output_sha256")}


def record_reuse(base, project, role, stage, saved):
    """A cache observation is not a model call; never rewrite the old cache."""
    from studio.core import write_json
    from .execution_context import current_execution
    origin = saved.get("origin")
    original = None
    if isinstance(origin, dict) and isinstance(origin.get("receipt"), str):
        relative = Path(origin["receipt"])
        if not relative.is_absolute() and ".." not in relative.parts:
            try:
                candidate = read_json(base / relative, maximum=512 * 1024)
                expected_prompt = saved.get("prompt_metadata", saved.get("prompt"))
                prompt_matches = (isinstance(expected_prompt, dict)
                    and all(candidate.get("prompt", {}).get(k) == expected_prompt.get(k)
                            for k in ("input_sha256", "rendered_sha256", "output_schema_sha256")))
                if (candidate.get("status") == "succeeded" and candidate.get("call_id") == origin.get("call_id")
                        and candidate.get("role") == role
                        and prompt_matches
                        and candidate.get("output_sha256") == origin.get("output_sha256") == digest(saved["value"])):
                    original = candidate
            except (ValueError, OSError):
                pass
    record = {"schema": "ai-studio.context-reuse.v1", "role": role, "status": "reused",
              "stage": stage, "started_at": time.time(), "model_call_started": False,
              "execution_context": copy.deepcopy(current_execution.get() or {"project_id": project.get("id"), "stage": stage}),
              "cache_signature": saved["signature"], "output_sha256": digest(saved["value"]),
              "origin": origin if original else None,
              "context_manifest": original.get("context_manifest") if original else None,
              "configured_model": original.get("configured_model") if original else None,
              "response_model": original.get("response_model") if original else None,
              "origin_state": "verified" if original else "unknown"}
    root = _project_root(base, project.get("id"))
    folder = (root / "planning" if root else base) / "context-reuse"
    name = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:12]
    write_json(folder / (name + ".json"), record)
    return record


def public(value):
    """Closed bounded projection; no raw model instructions or credentials."""
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        return None
    result = {k: copy.deepcopy(value[k]) for k in
              ("schema", "role", "execution", "project_version", "input_sha256", "content_sha256",
               "truncated", "budget", "delivery", "attempt", "request_sha256", "controlled_handoff_ids") if k in value}
    result["prompt"] = {k: value.get("prompt", {}).get(k) for k in ("version", "status", "input_sha256")}
    result["materials"] = [{k: copy.deepcopy(row[k]) for k in
                            ("field", "label", "sha256", "bytes", "state", "reason", "summary", "source_fields", "version") if k in row}
                           for row in value.get("materials", [])[:96] if isinstance(row, dict)]
    result["source_checks"] = [{k: row[k] for k in ("field", "label", "sha256", "state", "reason") if k in row}
                               for row in value.get("source_checks", [])[:48] if isinstance(row, dict)]
    return result
