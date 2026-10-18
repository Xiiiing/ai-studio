"""Compile approved shot facts into a frozen, auditable H3 I2V request.

This is a director task, not a new model role or a quality verdict. It performs
no vision/model installation or video execution and must run before GPU leases.
"""
from __future__ import annotations

import copy
import hashlib
import math
from pathlib import Path

from jsonschema import validate, ValidationError

from studio.core import fingerprint, read_json, write_json
from .config import llm_identity, resolve_llm
from .llm import json_call
from .profile_creative import adapt_schema, adapt_system
from .prompt_registry import load_prompt, prompt_receipt
from .scheduler import ProductionNeedsDecision, ProductionPaused
from .schemas import planning_dir
from .team_control import requirements_payload
from .team_planning import task_config

ROLE = "storyboard_director"
VERSION = "3.1.0"
PROTOCOL = "h3.automatic-shot-prompt.v1"


def _object(properties):
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(properties)}


def _text(maximum=1600):
    return {"type": "string", "maxLength": maximum}


PROMPT_SCHEMA = _object({
    "status": {"type": "string", "enum": ["ready", "blocked"]},
    "blocked_reason": _text(600),
    "visual_description": _text(),
    "initial_state": _text(800),
    "timeline": {"type": "array", "minItems": 0, "maxItems": 4,
                 "items": _object({"start_seconds": {"type": "number", "minimum": 0, "maximum": 15},
                                   "end_seconds": {"type": "number", "minimum": 0, "maximum": 15},
                                   "action": _text(800)})},
    "end_state": _text(800),
    "camera": _text(600),
    "continuity": _text(1000),
    "audio": {"type": "string", "enum": ["silent", "ambient_only"]},
})


def identity(config):
    """Return the actual configured director and immutable compiler identity."""
    try:
        routed = task_config(config, ROLE)
        model = resolve_llm(routed, ROLE)
        if model.get("enabled") is False or any(
                not isinstance(model.get(key), str) or not model[key].strip()
                for key in ("base_url", "model")):
            raise ValueError("Director requires an enabled configured model")
        template = load_prompt(ROLE, VERSION)
        return {"protocol": PROTOCOL, "role": ROLE, "version": VERSION,
                "template_sha256": template.spec.template_sha256,
                "contract_sha256": template.spec.contract_sha256,
                "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "model_identity": llm_identity(routed, ROLE)}
    except ProductionNeedsDecision:
        raise
    except Exception as exc:
        raise ProductionNeedsDecision("请先配置并启用分镜导演模型，才能自动编写 H3 提示词。",
                                      "h3_prompt_unavailable", {"error_type": type(exc).__name__}) from exc


def _references(values):
    result = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("H3 prompt references require media receipts")
        checksum = value.get("sha256")
        if not isinstance(checksum, str) or len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
            raise ValueError("H3 prompt reference checksum is required")
        item = {key: copy.deepcopy(value[key]) for key in ("sha256", "bytes", "width", "height") if key in value}
        # Existing QA is evidence to reconcile against approved facts. It is
        # never an instruction, a new vision call, or proof of physical truth.
        if isinstance(value.get("qa"), dict):
            item["untrusted_existing_qa"] = copy.deepcopy(value["qa"])
        result.append(item)
    if not result:
        raise ValueError("H3 image-to-video requires an approved first frame")
    return result


def compatible_compiler(previous, current):
    """Recognize an archived writer policy without ignoring model changes.

    A finished video's prompt is frozen. A newer writing policy applies to new
    renders, while an old render may be reviewed using its original compiler.
    Callers must still reconstruct the entire generation signature.
    """
    if not isinstance(previous, dict) or set(previous) != set(current):
        return False
    if (previous.get("role") != ROLE or previous.get("protocol") != PROTOCOL
            or not isinstance(previous.get("version"), str)):
        return False
    if any(previous.get(key) != current.get(key) for key in ("protocol", "role", "model_identity")):
        return False
    checksum = previous.get("implementation_sha256")
    if not isinstance(checksum, str) or len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
        return False
    try:
        archived = load_prompt(ROLE, previous.get("version"))
    except (ValueError, OSError):
        return False
    return (previous.get("template_sha256") == archived.spec.template_sha256
            and previous.get("contract_sha256") == archived.spec.contract_sha256)


def _repair(value):
    value = value or {}
    diagnosis = value.get("diagnosis", {})
    # Free-form descriptions of defective output must not become scene facts.
    return {"action": value.get("action"),
            "diagnosis": {key: copy.deepcopy(diagnosis[key]) for key in
                          ("primary_code", "codes", "severity") if key in diagnosis},
            "positive_constraints": copy.deepcopy(value.get("positive_constraints", [])),
            "regenerate_keyframe": bool(value.get("regenerate_keyframe")),
            "simplify_motion": bool(value.get("simplify_motion"))}


def _check_plan(value, seconds):
    validate(value, PROMPT_SCHEMA)
    if value["status"] == "blocked":
        raise ProductionNeedsDecision("分镜导演认为该镜头无法在当前 H3 约束内忠实执行。",
                                      "h3_prompt_infeasible", {"reason": value["blocked_reason"]})
    if value["blocked_reason"].strip() or any(not value[key].strip() for key in
            ("visual_description", "initial_state", "end_state", "camera", "continuity")):
        raise ValueError("Ready H3 prompt has missing or contradictory plan fields")
    beats = value["timeline"]
    if not beats:
        raise ValueError("H3 prompt requires visible action over time")
    previous_end = 0.0
    for beat in beats:
        start, end = beat["start_seconds"], beat["end_seconds"]
        if (isinstance(start, bool) or isinstance(end, bool) or
                not math.isfinite(start) or not math.isfinite(end) or
                not 0 <= start < end <= seconds or abs(start - previous_end) > .001 or
                not beat["action"].strip()):
            raise ValueError("H3 action timeline must be ordered and fit the approved shot duration")
        previous_end = end
    if abs(previous_end - seconds) > .001:
        raise ValueError("H3 action timeline must cover the complete shot")


def _render(value, seconds):
    timeline = "\n".join(f"{beat['start_seconds']:.2f}s-{beat['end_seconds']:.2f}s: {beat['action'].strip()}"
                        for beat in value["timeline"])
    audio = "Silent scene." if value["audio"] == "silent" else "Subtle diegetic scene ambience only."
    return ("For the target video, at 0.00 seconds into the target video, "
            "<Picture 1> (from [Shot 1]) is fully referenced.\n\n"
            f"integrated_multimodal_description: [Shot 1] {value['visual_description'].strip()} "
            f"Initial state: {value['initial_state'].strip()} "
            f"Preserve the supplied first frame's approved cast, identities, wardrobe, props and spatial relationships. "
            f"One continuous {seconds:.2f}-second shot.\n{timeline}\n"
            f"End state: {value['end_state'].strip()}\nCamera: {value['camera'].strip()} "
            f"Continuity: {value['continuity'].strip()} Visible mouths remain naturally closed; "
            "clean unobstructed picture with naturally unmarked surfaces.\n\n"
            f"overall_soundscape: {audio}\n\n"
            "non_diegetic_music: None.")


def compile_prompt(project, shot, *, config, references, style, seconds,
                   repair=None, previous_prompt=None, output_dir=None, attempt=1):
    """Return a validated prompt plus receipt; never fall back on compiler error."""
    try:
        if (isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or
                not math.isfinite(seconds) or not 1 <= seconds <= 15):
            raise ValueError("Approved shot duration must be finite and bounded")
        if type(attempt) is not int or not 1 <= attempt <= 5:
            raise ValueError("H3 prompt attempt must use the bounded production attempt")
        if shot.get("lip_sync") is True:
            raise ProductionNeedsDecision("当前 H3 自动提示词使用外部配音与自然闭口画面，无法执行已批准的同步口型要求。",
                                          "h3_prompt_infeasible", {"shot_id": shot.get("id")})
        if not any(isinstance(shot.get(field), str) and shot[field].strip()
                   for field in ("action", "motion_prompt")):
            raise ProductionNeedsDecision("该镜头缺少已批准动作或运动说明，不能自行编造 H3 人物动作。",
                                          "h3_prompt_infeasible", {"shot_id": shot.get("id")})
        compiler = identity(config)
        fields = ("id", "action", "continuity", "prompt", "motion_prompt", "camera_motion",
                  "characters", "cast", "reference_group", "character_states", "lip_sync",
                  "material_constraints", "render_style_prompt", "visual_direction")
        approved = {key: copy.deepcopy(shot[key]) for key in fields if key in shot}
        payload = {"approved_shot": approved, "first_frame_references": _references(references),
                   "approved_style": copy.deepcopy(style), "seconds": seconds, "attempt": attempt,
                   "repair": _repair(repair), "previous_prompt_untrusted": previous_prompt,
                   "constraints": {"protocol": PROTOCOL, "preserve_approved_action": True,
                                   "can_change_story": False, "external_voiceover": True,
                                   "native_score": False, "single_continuous_take": True}}
        payload = requirements_payload(project, payload)
        system = adapt_system(load_prompt(ROLE, VERSION), project)
        schema = adapt_schema(PROMPT_SCHEMA, project)
        metadata = prompt_receipt(system, ROLE, payload, schema)
        signature = fingerprint({"compiler": compiler, "system": system, "payload": payload,
                                 "schema": schema, "temperature": .25, "max_tokens": 2400})
        folder = Path(output_dir) if output_dir is not None else planning_dir(project) / "h3-prompts"
        cache = folder / ("h3-prompt-" + signature + ".json")
        saved = None
        if cache.is_file():
            try:
                candidate = read_json(cache)
                if candidate.get("signature") == signature:
                    _check_plan(candidate["plan"], seconds)
                    if candidate.get("prompt") == _render(candidate["plan"], seconds):
                        saved = candidate
            except (OSError, ValueError, KeyError, ValidationError):
                pass
        if saved is None:
            routed = task_config(config, ROLE)
            from .context_manifest import capture, cache_origin
            with capture(project, "h3_prompt_preparation") as context:
                plan = json_call(config=routed, role=ROLE, system=system, payload=payload,
                                 schema=schema, output_dir=folder / "llm", max_tokens=2400, temperature=.25)
            _check_plan(plan, seconds)
            saved = {"signature": signature, "plan": plan, "prompt": _render(plan, seconds),
                     "origin": cache_origin(context, folder), "prompt_metadata": metadata}
            write_json(cache, saved)
        else:
            from .context_manifest import record_reuse
            record_reuse(folder, project, ROLE, "h3_prompt_preparation",
                         {**saved, "value": saved["plan"]})
        receipt = {"schema": PROTOCOL, "compiler": compiler, "source_signature": signature,
                   "prompt": metadata, "plan": copy.deepcopy(saved["plan"]),
                   "rendered_prompt_sha256": hashlib.sha256(saved["prompt"].encode()).hexdigest(),
                   "first_frame_sha256": payload["first_frame_references"][0]["sha256"],
                   "attempt": attempt, "quality_status": "pending_actual_media_review"}
        return {"prompt": saved["prompt"], "receipt": receipt}
    except (ProductionNeedsDecision, ProductionPaused):
        raise
    except Exception as exc:
        # Transport call receipts retain diagnostics. Do not leak private model
        # configuration/payload through the public needs-attention message.
        raise ProductionNeedsDecision("H3 自动提示词编写未完成；未提交视频生成，请检查分镜导演连接或返回格式。",
                                      "h3_prompt_unavailable", {"error_type": type(exc).__name__}) from exc
