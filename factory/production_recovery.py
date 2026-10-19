"""Durable, bounded production repairs grounded in approved facts.

This module plans only. It neither submits media/model requests nor mutates a
shot, a verdict, a budget or a public prompt. Callers must use ``applicable`` at
execution time, and continue enforcing their normal per-source attempt limit.
"""
from __future__ import annotations

import copy
import fcntl
import math
import json
from pathlib import Path
import re
import time

from studio.core import ROOT, sha256, write_json
from .creative_evidence_io import digest, read_json
from .review_contracts import starting_frame
from .scheduler import ProductionNeedsDecision

VERSION = "ai-studio.production-recovery.v1"
# Hard ceilings cannot be expanded by a model response or changing input hashes.
MAX_ASSET_CYCLES, MAX_PROJECT_CYCLES = 2, 8
_REPAIRABLE = {"action", "continuity", "identity", "composition", "black", "black_frame", "blur", "possible_blur"}
_UPSTREAM = {"reference_conflict", "reference_missing", "reference_mismatch", "capability", "unsupported_motion"}
_UNSAFE = {"unknown_submit", "unknown_submission", "unresolved_submission", "unsafe", "tampered", "unpriced", "cost_unknown", "budget_exceeded", "budget_exhausted", "paused"}
_SHOT_FIELDS = ("id", "seconds", "prompt", "action", "motion_prompt", "continuity", "character_states", "characters", "camera_motion", "dialogue", "material_constraints", "reference_group")
_CLOSED = re.compile(r"visible mouths remain naturally closed|external voiceover; naturally closed mouths|(?:mouths? (?:stay|remain|stays|remains) (?:naturally )?closed)|(?:mouths? (?:are |is )?closed|closed[- ]mouths?|lips (?:stay |remain )?together).{0,30}(?:throughout|entire clip|all frames)|(?:throughout|entire clip|all frames).{0,30}(?:mouths? (?:are |is )?closed|closed[- ]mouths?|lips (?:stay |remain )?together)|(?:全程|始终|从头到尾).{0,12}(?:闭口|闭嘴|嘴唇闭合|嘴巴闭合|嘴部闭合)", re.I)


def _folder(project, root):
    ident = project.get("id")
    if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", ident):
        raise ValueError("Invalid recovery project identity")
    slug = project.get("slug") or "factory-" + ident
    if slug != "factory-" + ident:
        raise ValueError("Recovery folder must belong to this project")
    base = Path(root or ROOT) / "production" / slug / "recovery"
    if any(p.is_symlink() for p in (base, *base.parents)):
        raise ValueError("Recovery directory cannot contain symlinks")
    return base


def _references(target, root):
    refs = target.get("references") or []
    if len(refs) > 16:
        raise ValueError("Too many recovery references")
    result = []
    for item in refs:
        path = Path(item)
        if not path.is_absolute():
            path = Path(root or ROOT) / path
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
            raise ValueError("Recovery requires real, unchanged reference files")
        if not path.resolve().is_relative_to((Path(root or ROOT) / "production").resolve()):
            raise ValueError("Recovery reference is outside production")
        result.append({"path": str(path), "sha256": sha256(path)})
    return result


def _configuration(config):
    result = {k: copy.deepcopy(config[k]) for k in (
        "max_gpu_seconds", "max_wall_seconds", "max_attempts", "production_recovery") if k in config}
    # Credentials are not prompt-repair evidence. Retain adapter semantics and a
    # pricing digest without copying secrets/headers into the recovery artifact.
    for name in ("image_connection", "video_connection", "audio_connection"):
        profile = config.get(name)
        if isinstance(profile, dict):
            result[name] = {k: copy.deepcopy(profile[k]) for k in (
                "provider", "profile", "model", "revision", "enabled", "steps", "duration", "resolution",
                "workflow_template", "reference_templates", "voice", "voices", "language") if k in profile}
            result[name]["profile_sha256"] = digest(profile)
            result[name]["pricing_sha256"] = digest(profile.get("pricing"))
    return result


def _source(project, config, target, assessment, previous, root):
    key, signature = target.get("key"), target.get("signature")
    if not isinstance(key, str) or not key or len(key) > 200 or not isinstance(signature, str) or not signature:
        raise ValueError("Recovery requires a stable asset key and source signature")
    prompt = target.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 48000:
        raise ValueError("Recovery requires a bounded approved generation prompt")
    shot = target.get("shot") or {}
    if not isinstance(shot, dict):
        raise ValueError("Invalid approved shot")
    previous = previous or {}
    # Exclude mutable accounting: it is checked afresh before every application.
    # Include authorization maxima and provider/model identity: a changed grant or
    # adapter can never silently inherit an old application authorization.
    return {"project_id": project["id"], "asset": key, "kind": target.get("kind"),
            "source_signature": signature,
            "production_revision": project.get("production_revision"),
            "shot": {k: copy.deepcopy(shot[k]) for k in _SHOT_FIELDS if k in shot},
            "prompt": prompt, "seconds": target.get("seconds", shot.get("seconds")),
            "references": _references(target, root),
            "reference_roles": copy.deepcopy(list(target.get("reference_roles") or [])),
            "review_context": copy.deepcopy(target.get("review_context") or {}),
            "first_frame_assessment": copy.deepcopy(target.get("first_frame_assessment")),
            "generation_plan": copy.deepcopy(target.get("generation_plan")),
            "budget": {k: copy.deepcopy(project["budget"][k]) for k in (
                "gpu_minutes", "wall_minutes", "max_attempts") if k in (project.get("budget") or {})},
            "configuration": _configuration(config),
            "failed_take": {k: copy.deepcopy(previous[k]) for k in (
                "take", "attempt", "source_signature", "state", "output", "remote_task") if k in previous},
            "assessment": copy.deepcopy(assessment)}


def _gate(project, config, previous):
    if (config.get("production_recovery") or {}).get("enabled") is False:
        return "recovery_disabled"
    if project.get("pause_requested") or project.get("status") == "paused":
        return "paused"
    previous = previous or {}
    for value in (project, project.get("production") or {}, previous):
        for field in ("reason", "code", "stop_reason", "diagnostic_code"):
            if value.get(field) in _UNSAFE:
                return value[field]
        if any(value.get(flag) is True for flag in ("unknown_submit", "unsafe", "tampered", "unpriced", "cost_unknown")):
            return "unsafe_or_unresolved_submission"
    if previous.get("state") in {"rendering", "submitted", "waiting_remote", "prompt_preparing"}:
        return "unresolved_submission"
    maximum_attempts = config.get("max_attempts", (project.get("budget") or {}).get("max_attempts", 3))
    consumed = previous.get("attempt", 0)
    if type(maximum_attempts) is not int or not 1 <= maximum_attempts <= 5 or type(consumed) is not int or consumed < 0:
        return "attempt_budget_unverified"
    if consumed >= maximum_attempts:
        return "retry_limit"
    state = project.get("production") or {}
    budget = project.get("budget") or {}
    for used_key, maximum_key, budget_key in (("gpu_seconds", "max_gpu_seconds", "gpu_minutes"), ("elapsed_seconds", "max_wall_seconds", "wall_minutes")):
        maximum = config.get(maximum_key, budget.get(budget_key, 0) * 60)
        used = state.get(used_key, 0)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in (used, maximum)) or maximum <= 0:
            return "budget_unverified"
        if used >= maximum:
            return "budget_exhausted"
    return None


def _stop(reason, source=None):
    return {"schema": VERSION, "action": "stop", "reason": reason,
            "source_signature": (source or {}).get("source_signature"), "prompt_appendix": "", "constraints": [],
            "temporal_constraints": [], "first_frame_constraints": []}


def _priced_boundary(config, target):
    # This execution policy authorizes GPU time, not an unpriced currency spend.
    # Paid vendor repairs require a separate verified monetary authorization.
    kind = target.get("kind")
    profile = config.get(kind + "_connection") or {}
    provider = profile.get("provider", "legacy")
    if provider not in {"legacy", "comfyui_h3", "comfyui_wan", "comfyui_local", "comfyui_external", "comfyui_qwen_image21"}:
        return "paid_repair_authorization_unverified"
    return None


def _issues(assessment):
    if not isinstance(assessment, dict) or assessment.get("status") != "fail":
        return [], "review_not_failed"
    semantic = assessment.get("semantic")
    if isinstance(semantic, dict) and semantic.get("temporal_pending"):
        return [], "review_evidence_pending"
    issues = [item for item in assessment.get("issues", []) if isinstance(item, dict)
              and item.get("severity", "major") not in {"warning", "minor", "suggestion"}]
    if not issues:
        return [], "review_has_no_blocking_diagnosis"
    if any(str(i.get("code", "")).lower() in _UNSAFE for i in issues):
        return [], "unsafe_or_unresolved_submission"
    return issues, None


def _plan(source):
    issues, stop = _issues(source["assessment"])
    if stop:
        return _stop(stop, source)
    codes = {str(issue.get("code", "")).lower() for issue in issues}
    if codes & _UPSTREAM:
        return {**_stop("approved_inputs_require_upstream_revision", source), "action": "request_upstream"}
    if not codes <= _REPAIRABLE:
        return _stop("diagnosis_requires_operator_or_technical_repair", source)
    shot, kind = source["shot"], source["kind"]
    if kind not in {"image", "video"}:
        return _stop("unsupported_recovery_stage", source)
    if kind == "video" and not shot:
        return _stop("approved_motion_contract_missing", source)
    if not source["references"] and codes & {"identity", "continuity"}:
        return {**_stop("actual_reference_evidence_missing", source), "action": "request_upstream"}
    # A defect later in the generated video cannot revoke an approved frame.
    frame = source.get("first_frame_assessment") or {}
    if kind == "video" and frame.get("status") == "fail":
        return {**_stop("first_frame_requires_independent_upstream_repair", source), "action": "request_upstream"}
    if kind == "video" and (frame.get("status") != "pass" or frame.get("eligible_for_selection") is False):
        return _stop("first_frame_review_unverified", source)
    if kind == "video" and not any(shot.get(k) for k in ("action", "motion_prompt")):
        return _stop("approved_motion_contract_missing", source)
    facts = starting_frame(shot) if kind == "image" else shot
    fields = ("prompt", "character_states") if kind == "image" else ("action", "motion_prompt", "continuity", "character_states")
    constraints = [{"field": "approved_shot." + field, "value": copy.deepcopy(facts[field])}
                   for field in fields if facts.get(field)]
    if not constraints:
        constraints = [{"field": "approved_target.prompt", "value": source["prompt"]}]
    lines = ["DIRECTED REPAIR — retain the approved scene, cast, duration, voice track and reference identities."]
    if kind == "image":
        lines.append("Render the single approved starting instant. Make its approved placement and visible states readable.")
    else:
        lines.extend([
            "START: retain the actual supplied starting frame and its approved subject, prop and mouth states.",
            "MIDDLE: perform the complete approved action below. Keep each unchanged state stable while that action unfolds.",
            "END: complete that same approved action, preserving approved continuity and each unchanged state.",
            "Keep the approved camera behavior; camera movement does not replace the required subject action.",
        ])
    # Never infer a closed-mouth requirement from the failure description. Only
    # an already approved contract permits this temporal positive constraint.
    mouth_constraint = kind == "video" and bool(_CLOSED.search(source["prompt"]))
    if mouth_constraint:
        lines.append("START, MIDDLE AND END: lips stay together and the jaw rests in the approved closed-mouth state; preserve the approved voice handling.")
        constraints.append({"field": "approved_target.prompt.closed_mouth", "value": "lips together throughout start, middle and end"})
    if codes & {"identity", "composition"}:
        lines.append("Preserve each subject's distinct approved reference face, age, hair and clothing within the approved framing.")
    for item in constraints:
        value = item["value"]
        lines.append(item["field"] + ": " + (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)))
    # Role bindings are actual adapter reference order, never inferred from QA.
    for index, role in enumerate(source["reference_roles"], 1):
        lines.append(f"Actual reference {index}: {role}. Identity-reference poses do not override the approved scene's prop placement.")
    start = starting_frame(shot)
    temporal = []
    if kind == "video":
        for phase, fields, instruction in (
            ("start", ("prompt", "character_states"), "Render only the approved starting instant; future actions have not completed."),
            ("middle", ("action", "motion_prompt", "continuity"), "Perform the approved subject action with visible progression; preserve approved continuity."),
            ("end", ("action", "motion_prompt", "continuity"), "Complete the approved action and retain its required resulting state and continuity."),
        ):
            facts = start if phase == "start" else shot
            required = [{"field": "approved_shot." + field, "value": copy.deepcopy(facts[field])}
                        for field in fields if facts.get(field)]
            if mouth_constraint:
                required.append({"field": "approved_target.prompt.closed_mouth", "value": "lips together"})
            temporal.append({"phase": phase, "instruction": instruction, "required": required})
    return {"schema": VERSION, "action": "refine_motion" if kind == "video" else "refine_start_frame",
            "reason": "approved_facts_reasserted_for_failed_" + "_".join(sorted(codes)),
            "source_signature": source["source_signature"], "prompt_appendix": "\n".join(lines),
            "constraints": constraints,
            "temporal_constraints": temporal,
            "first_frame_constraints": copy.deepcopy(constraints) if kind == "image" else []}


def _load(path):
    value = read_json(path)
    if value.get("sha256") != digest(value.get("record")):
        raise ValueError("Recovery record digest mismatch")
    record = value["record"]
    if (record.get("schema") != VERSION or record.get("id") != path.stem
            or record.get("source_sha256") != digest(record.get("source"))
            or record.get("id") != digest({"schema": VERSION, "source": record.get("source")})
            or record.get("action") not in {"stop", "request_upstream", "refine_motion", "refine_start_frame"}):
        raise ValueError("Recovery record identity mismatch")
    return record


def prepare_repair(project, config, target, last_assessment, *, previous_attempt=None,
                   root=None, checkpoint=None, current=None):
    """Return one durable repair plan; repeated calls never consume extra cycles.

    ``current`` may return fresh keyword arguments (project/config/target/
    last_assessment/previous_attempt) after planning. A stale result is never
    committed. ``checkpoint`` is called only after the immutable result exists.
    No retry allowance, budget, review verdict or media bytes are changed here.
    """
    gate = _gate(project, config, previous_attempt) or _priced_boundary(config, target)
    if gate:
        return _stop(gate)
    source = _source(project, config, target, last_assessment, previous_attempt, root)
    identity = digest({"schema": VERSION, "source": source})
    base = _folder(project, root)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (base / "planner.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return _stop("recovery_in_progress", source)
        try:
            records = [_load(path) for path in sorted(base.glob("*.json")) if not path.name.endswith(".intent.json")]
            unresolved = [path for path in base.glob("*.intent.json") if not (base / (path.name.removesuffix(".intent.json") + ".json")).exists()]
            if unresolved:
                return _stop("recovery_result_unknown", source)
            saved = next((record for record in records if record["id"] == identity), None)
            if saved:
                if current is not None:
                    fresh = current()
                    current_source = _source(fresh["project"], fresh["config"], fresh["target"], fresh["last_assessment"], fresh.get("previous_attempt"), root)
                    gate = _gate(fresh["project"], fresh["config"], fresh.get("previous_attempt"))
                    if gate or digest(current_source) != digest(source):
                        return _stop(gate or "recovery_source_changed", source)
                return copy.deepcopy(saved)
            plan = _plan(source)
            active = [r for r in records if r["action"] in {"refine_motion", "refine_start_frame"}]
            asset_count = sum(r["scope"]["asset"] == target["key"] and r["scope"]["stage"] == target["kind"] for r in active)
            setting = config.get("production_recovery") or {}
            asset_limit = min(MAX_ASSET_CYCLES, max(0, int(setting.get("max_asset_cycles", MAX_ASSET_CYCLES))))
            project_limit = min(MAX_PROJECT_CYCLES, max(0, int(setting.get("max_project_cycles", MAX_PROJECT_CYCLES))))
            if plan["action"] in {"refine_motion", "refine_start_frame"} and (asset_count >= asset_limit or len(active) >= project_limit):
                return _stop("recovery_cycle_limit", source)
            if any(r["scope"]["asset"] == target["key"] and r.get("prompt_appendix") == plan["prompt_appendix"]
                   for r in active):
                return _stop("recovery_has_no_new_directed_change", source)
            if current is not None:
                fresh = current()
                fresh_source = _source(fresh["project"], fresh["config"], fresh["target"], fresh["last_assessment"], fresh.get("previous_attempt"), root)
                gate = _gate(fresh["project"], fresh["config"], fresh.get("previous_attempt"))
                if gate or digest(fresh_source) != digest(source):
                    return _stop(gate or "recovery_source_changed", source)
            intent = base / (identity + ".intent.json")
            write_json(intent, {"id": identity, "source_sha256": digest(source), "created_at": time.time()})
            intent.chmod(0o600)
            record = {**plan, "id": identity, "created_at": time.time(),
                      "scope": {"project_id": project["id"], "asset": target["key"], "stage": target["kind"]},
                      "source_sha256": digest(source), "source": source,
                      "cycles": {"asset": asset_count + 1, "project": len(active) + 1,
                                 "max_asset": asset_limit, "max_project": project_limit},
                      "preserves": ["approved_story", "cast", "duration", "dialogue", "voice", "actual_references", "provider", "authorization", "qa_history", "cost_history", "attempt_limit"]}
            path = base / (identity + ".json")
            write_json(path, {"sha256": digest(record), "record": record})
            path.chmod(0o600)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ProductionNeedsDecision("Production recovery evidence could not be verified; no new submission is authorized", "production_recovery_corrupt", {"error": str(exc)[:200]}) from exc
    if checkpoint:
        checkpoint({"production_recovery": {"last_record": identity, "asset": target["key"], "action": record["action"]}})
    return copy.deepcopy(record)


def applicable(record, project, config, target, last_assessment, *, previous_attempt=None, root=None):
    """Revalidate a saved plan against fresh authoritative state before applying."""
    if (record.get("action") not in {"refine_motion", "refine_start_frame"}
            or _gate(project, config, previous_attempt) or _priced_boundary(config, target)):
        return False
    try:
        saved = _load(_folder(project, root) / (record["id"] + ".json"))
        source = _source(project, config, target, last_assessment, previous_attempt, root)
        return digest(record) == digest(saved) and digest(source) == record["source_sha256"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
