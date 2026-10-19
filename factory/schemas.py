"""Schemas and durable helpers for the autonomous creative pipeline.

Method references (adapted concepts, not installed/running upstream agents):
https://github.com/zenstory-ai/oh-story-claudecode (MIT)
https://github.com/zenstory-ai/drama-skills (MIT)
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

from studio.core import ROOT, write_json

SCHEMA_VERSION = "2026-10-12.1"
SCORE_KEYS = ("hook", "causality", "motivation", "originality", "closure", "feasibility")
UPSTREAM_REFERENCES = [
    {"name": "Oh Story", "url": "https://github.com/zenstory-ai/oh-story-claudecode",
     "license": "MIT", "use": "研究来源、故事结构与分层连续性的方法参考；未声称运行其宿主 Agent"},
    {"name": "Drama Skills", "url": "https://github.com/zenstory-ai/drama-skills",
     "license": "MIT", "use": "剧本、视觉设定、逐镜依据与剪辑交付结构的方法参考；生产执行器独立实现"},
]


class CreativeReviewError(ValueError):
    def __init__(self, message, issues=None):
        super().__init__(message)
        self.issues = issues or []


def revision_shot_ids(feedback, available_ids):
    """Resolve explicit or narrowly worded edit scope without unlocking kept shots."""
    available = list(dict.fromkeys(available_ids))
    explicit = isinstance(feedback, dict) and "shot_ids" in feedback
    text = feedback.get("notes", feedback.get("feedback", "")) if isinstance(feedback, dict) else (feedback or "")
    if not isinstance(text, str):
        raise CreativeReviewError("镜头修改说明必须是文本")
    selected = []
    if explicit:
        values = feedback["shot_ids"]
        if not isinstance(values, list) or not values or any(
                not isinstance(value, str) or not re.fullmatch(r"S\d{2,3}", value.strip().upper())
                for value in values):
            raise CreativeReviewError("shot_ids 必须是非空的镜头编号数组，例如 [\"S07\"]")
        selected = list(dict.fromkeys(value.strip().upper() for value in values))
    identifiers = re.compile(
        r"(?<![A-Za-z0-9_])S(\d{1,3})(?!\d)"
        r"(?:\s*(?:至|到|[-–—~～]|\.\.)\s*S?(\d{1,3})(?!\d))?", re.I)
    cues = re.compile(
        r"(?P<keep>保留|不(?:要|用|需)?(?:再)?(?:修改|改写|改动|调整|改变|改)|"
        r"保持(?=[^，,。；;\n]*不变)|不变)|"
        r"(?P<edit>修改|改写|改动|重写|重排|重做|调整|优化|修复|替换|重构|删除|删掉|只改|仅改|改为)|"
        r"(?P<reference>参考|参照|对照|例如|比如|提及|引用)")
    edited, kept, carry = [], [], None
    for clause in re.split(r"[，,。；;！!\n]+", text):
        matches, markers = list(identifiers.finditer(clause)), list(cues.finditer(clause))
        if not matches:
            carry = markers[-1].lastgroup if markers else None
            continue
        bare = re.fullmatch(r"[\s、及和与]*", identifiers.sub("", clause)) is not None
        previous_direction = None
        for index, match in enumerate(matches):
            previous_end = matches[index - 1].end() if index else 0
            next_start = matches[index + 1].start() if index + 1 < len(matches) else len(clause)
            before = [cue for cue in markers if previous_end <= cue.start() and cue.end() <= match.start()]
            after = [cue for cue in markers if match.end() <= cue.start() and cue.end() <= next_start]
            connected = index and re.fullmatch(r"[\s、及和与]*", clause[previous_end:match.start()])
            direction = (before[-1].lastgroup if before else
                         previous_direction if connected else
                         after[0].lastgroup if after else carry if bare else None)
            previous_direction = direction
            if direction not in {"keep", "edit"}:
                continue
            first, last = int(match[1]), int(match[2] or match[1])
            if last < first:
                raise CreativeReviewError("镜头范围必须按正序填写")
            target = kept if direction == "keep" else edited
            target.extend("S%02d" % number for number in range(first, last + 1))
        carry = previous_direction
    edited, kept = list(dict.fromkeys(edited)), set(kept)
    unknown = (set(selected) | set(edited) | kept) - set(available)
    if unknown:
        raise CreativeReviewError("反馈引用了不存在的镜头编号：" + "、".join(sorted(unknown)))
    conflict = kept & (set(edited) | set(selected))
    if conflict:
        raise CreativeReviewError("镜头修改与保留要求冲突：" + "、".join(sorted(conflict)))
    if explicit:
        outside = set(edited) - set(selected)
        if outside:
            raise CreativeReviewError("反馈要求修改的镜头超出显式 shot_ids：" + "、".join(sorted(outside)))
        return selected
    if edited:
        return edited
    if kept:
        remaining = [sid for sid in available if sid not in kept]
        if not remaining:
            raise CreativeReviewError("全部镜头均要求保留，没有可修改的镜头")
        return remaining
    return []


def object_schema(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


def text_schema(min_length=1, max_length=4000):
    return {"type": "string", "minLength": min_length, "maxLength": max_length}


def array_schema(items, minimum=0, maximum=20):
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}


SCORES_SCHEMA = object_schema({k: {"type": "integer", "minimum": 0, "maximum": 10}
                               for k in SCORE_KEYS})
ISSUE_SCHEMA = object_schema({
    "severity": {"type": "string", "enum": ["blocking", "major", "minor"]},
    "code": text_schema(1, 80),
    "description": text_schema(1, 800),
    "evidence": text_schema(1, 1000),
    "suggestion": text_schema(1, 800),
    "shot_ids": array_schema(text_schema(1, 8), 0, 15),
})
CHARACTER_SCHEMA = object_schema({
    "role": {"type": "string", "enum": ["female", "male"]},
    "name": text_schema(1, 30),
    "identity": text_schema(1, 200),
    "motivation": text_schema(1, 300),
})
PROPOSAL_ITEM_SCHEMA = object_schema({
    "title": text_schema(2, 40),
    "logline": text_schema(10, 220),
    "genre": text_schema(1, 40),
    "hook": text_schema(5, 220),
    "synopsis": text_schema(60, 1400),
    "ending": text_schema(10, 400),
    "production_notes": text_schema(10, 800),
    "characters": array_schema(CHARACTER_SCHEMA, 2, 2),
})

RESOLVED_SOURCE_ID = {"type": "string", "enum": [
    "selected_proposal", "story_feedback", "script_feedback", "revision_feedback"]}
RESOLVED_STORY_SCHEMA = object_schema({
    "title": text_schema(2, 40),
    "logline": text_schema(10, 220),
    "synopsis": text_schema(60, 1000),
    "ending": text_schema(10, 350),
    "characters": array_schema(CHARACTER_SCHEMA, 2, 2),
    "causal_beats": array_schema(text_schema(8, 250), 5, 8),
    "retained_elements": array_schema(text_schema(2, 180), 0, 8),
    "creative_plan": object_schema({
        "character_need": text_schema(8, 220),
        "setup": text_schema(8, 220),
        "choice": text_schema(8, 220),
        "payoff": text_schema(8, 220),
        "causal_difference": text_schema(8, 240),
    }),
    "requirements": array_schema(object_schema({
        "instruction": {**text_schema(5, 100),
            "description": "一个可由正文验证的用户要求，不粘贴整段反馈，不锁定AI自选的礼物或互动"},
        "source_id": {"type": "string", "enum": [
            "story_feedback", "script_feedback", "revision_feedback"]},
        "source_quote": text_schema(3, 120),
    }), 1, 12),
    "excluded_elements": array_schema(object_schema({
        "element": text_schema(2, 200),
        "source_id": RESOLVED_SOURCE_ID,
        "source_quote": text_schema(3, 200),
        "forbidden_terms": array_schema(text_schema(2, 40), 0, 8),
    }), 0, 8),
})


BRIEF_CHECK_SCHEMA = object_schema({
    "status": {"type": "string", "enum": ["pass", "fail"]},
    "evidence_quotes": array_schema(text_schema(3, 260), 1, 3),
    "reason": text_schema(5, 600),
})
RESOLVED_REVIEW_SCHEMA = object_schema({
    "status": {"type": "string", "enum": ["pass", "fail"]},
    "summary": text_schema(10, 1200),
    "checks": object_schema({key: BRIEF_CHECK_SCHEMA for key in (
        "causal_chain", "misunderstanding_and_responsibility", "ownership_evidence",
        "producer_requirements", "closure", "production_feasibility")}),
    "requirements_review": object_schema({"REQUIREMENT": BRIEF_CHECK_SCHEMA}),
    "originality": object_schema({
        "score": {"type": "integer", "minimum": 0, "maximum": 10},
        "reason": text_schema(8, 600),
        "evidence_quotes": array_schema(text_schema(3, 260), 1, 4),
    }),
    "issues": array_schema(ISSUE_SCHEMA, 0, 10),
})


def resolved_review_schema(evidence_ids, requirement_ids=None):
    """Reviewer selects verified narrative IDs; application reconstructs quotes."""
    choices = list(dict.fromkeys(evidence_ids))
    if not choices:
        raise ValueError("故事证据目录不能为空")
    schema = copy.deepcopy(RESOLVED_REVIEW_SCHEMA)
    references = {**array_schema({"type": "string", "enum": choices}, 1, 4),
                  "uniqueItems": True}
    required = list(dict.fromkeys(requirement_ids or ["REQUIREMENT"]))
    schema["properties"]["requirements_review"] = object_schema({
        ident: copy.deepcopy(BRIEF_CHECK_SCHEMA) for ident in required})
    entries = list(schema["properties"]["checks"]["properties"].values())
    entries.extend(schema["properties"]["requirements_review"]["properties"].values())
    entries.append(schema["properties"]["originality"])
    entries.append(schema["properties"]["issues"]["items"])
    # The six checks share one schema object; deepcopy preserves that alias.
    for entry in {id(item): item for item in entries}.values():
        old_field = "evidence_quotes" if "evidence_quotes" in entry["properties"] else "evidence"
        entry["properties"].pop(old_field)
        entry["properties"]["evidence_ids"] = copy.deepcopy(references)
        entry["required"] = [name for name in entry["required"] if name != old_field] + ["evidence_ids"]
    return schema

PROPOSALS_SCHEMA = object_schema({
    "proposals": array_schema(PROPOSAL_ITEM_SCHEMA, 3, 3)
})
PROPOSAL_REVIEW_SCHEMA = object_schema({
    "reviews": array_schema(object_schema({
        "proposal_id": {"type": "string", "enum": ["A", "B", "C"]},
        "scores": SCORES_SCHEMA,
        "summary": text_schema(10, 1000),
        "strengths": array_schema(text_schema(1, 400), 0, 4),
        "issues": array_schema(ISSUE_SCHEMA, 0, 12),
    }), 3, 3),
    "comparison": text_schema(10, 1000),
})
SCRIPT_REVIEW_SCHEMA = object_schema({
    "scores": SCORES_SCHEMA,
    "summary": text_schema(10, 1500),
    "strengths": array_schema(text_schema(1, 400), 0, 5),
    "issues": array_schema(ISSUE_SCHEMA, 0, 20),
})
RESEARCH_QUERIES_SCHEMA = object_schema({
    "queries": array_schema(text_schema(8, 25), 2, 3)
})
RESEARCH_SCHEMA = object_schema({
    "observations": array_schema(object_schema({
        "finding": text_schema(1, 700),
        "source_ids": array_schema(text_schema(1, 40), 1, 10),
        "limitation": text_schema(1, 500),
    }), 0, 8),
    "narrative_patterns": array_schema(text_schema(1, 500), 0, 6),
    "creative_directions": array_schema(text_schema(1, 600), 1, 4),
    "originality_constraints": array_schema(text_schema(1, 500), 1, 8),
    "uncertainties": array_schema(text_schema(1, 500), 1, 8),
})
STYLES_SCHEMA = object_schema({
    "styles": array_schema(object_schema({
        "name": text_schema(2, 50),
        "description": text_schema(20, 600),
        "prompt": text_schema(30, 1400),
        "palette": array_schema({"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"}, 3, 5),
    }), 3, 3)
})


def episode_schema(shot_count=15, project=None):
    if project is not None:
        from .production_profiles import explicit, resolve
        if explicit(project):
            shot_count = resolve(project)["shot_count"]
    dialogue = object_schema({
        "role": {"type": "string", "enum": ["female", "male"]},
        "text": text_schema(1, 10),
        "start": {"type": "number", "const": 0.2},
        "emotion": text_schema(1, 40),
    })
    shot = object_schema({
        "dramatic_beat": {**text_schema(4, 300), "pattern": r"[\u3400-\u9fff]"},
        "action": text_schema(4, 400),
        "location": text_schema(1, 100),
        "prompt": text_schema(30, 1600),
        "motion_prompt": text_schema(10, 800),
        "camera_motion": {"type": "string", "enum": [
            "push_in", "pull_out", "pan_left", "pan_right", "hold"]},
        "reference_group": {"type": "string", "enum": ["female", "male", "two"]},
        "dialogue": array_schema(dialogue, 0, 1),
        "continuity": text_schema(1, 500),
        "sfx": text_schema(0, 200),
    })
    schema = object_schema({
        "title": text_schema(2, 40),
        "logline": text_schema(10, 250),
        "synopsis": text_schema(60, 1600),
        "ending": text_schema(10, 500),
        "story_structure": object_schema({k: text_schema(5, 500) for k in
            ("setup", "inciting_incident", "turning_point", "climax", "resolution")}),
        "character_names": object_schema({"female": text_schema(1, 30),
                                           "male": text_schema(1, 30)}),
        "visual_style": text_schema(20, 1400),
        "cast": object_schema({"female": text_schema(40, 1600),
                               "male": text_schema(40, 1600)}),
        "shots": array_schema(shot, shot_count, shot_count),
    })
    from .profile_creative import adapt_schema
    return adapt_schema(schema, project or {})


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def planning_dir(project):
    ident = str(project.get("id", ""))
    slug = str(project.get("slug", ""))
    if not re.fullmatch(r"[a-f0-9]{12}", ident) or slug != "factory-" + ident:
        raise ValueError("创作项目 ID 或目录不符合约定")
    base = (ROOT / "production").resolve()
    directory = (base / slug / "planning").resolve()
    if not directory.is_relative_to(base):
        raise ValueError("规划目录超出项目范围")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def relative_path(path):
    return str(Path(path).resolve().relative_to(ROOT.resolve()))


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def report(checkpoint, **patch):
    if callable(checkpoint):
        checkpoint(patch)


def series_bible_schema():
    """The optional series contract does not alter legacy episode JSON."""
    ident = {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"}
    character = object_schema({"character_id": ident, "name": text_schema(1, 100),
                     "identity_constraints": text_schema(1, 2000), "default_wardrobe": text_schema(1, 1000)})
    entity = object_schema({"entity_id": ident, "name": text_schema(1, 100), "description": text_schema(1, 2000)})
    return object_schema({"schema_version": {"const": 1}, "world": text_schema(1, 8000),
                "visual_rules": text_schema(1, 4000), "characters": array_schema(character, 1, 16),
                "scenes": array_schema(entity, 0, 32), "props": array_schema(entity, 0, 32)})


def project_context(project):
    context = {key: copy.deepcopy(project.get(key)) for key in (
        "id", "title", "brief", "format", "duration_seconds", "creation_mode",
        "memory", "proposal_generation", "script_generation", "story_feedback", "script_feedback")}
    if project.get("production_profile") is not None:
        from .production_profiles import resolve
        context["production_profile"] = resolve(project)
    if "creative_contract" in project:
        from .creative_contract import resolve as resolve_contract
        context["creative_contract"] = resolve_contract(project)
    if project.get("series_bible"):
        context["series_bible"] = copy.deepcopy(project["series_bible"])
    if project.get("material_package"):
        from .material_library import context as material_context
        context["confirmed_materials"] = material_context(project)
    return context


def cached_json_call(project, config, role, stage, system, payload, schema,
                     checkpoint=None, max_tokens=4096, temperature=0.65):
    """Cache validated role outputs, keyed by exact inputs and model identity."""
    from jsonschema import validate, ValidationError
    from factory.llm import json_call
    from factory.config import llm_identity
    from .team_planning import task_config
    config = task_config(config, role)
    model_identity = llm_identity(config, role)
    from .profile_creative import adapt_schema, adapt_system
    from .team_control import requirements_payload
    payload = requirements_payload(project, payload)
    if "producer_task_requirements" in payload:
        from .prompt_registry import derive_prompt
        system = derive_prompt(system, str(system) + "\n落实 producer_task_requirements 中本次出品人的任务要求；只在该任务职责与既有范围内修改，不跳过独立审核。", "producer_intervention")
    schema, system = adapt_schema(schema, project), adapt_system(system, project)
    from .prompt_registry import prompt_receipt
    prompt_meta = prompt_receipt(system, role, payload, schema)
    # Keep the original content-based signature and cache path. A byte-equal v1
    # extraction must reuse old steps without claiming those calls ran a version.
    signature = fingerprint({"schema_version": SCHEMA_VERSION,
        "context": project_context(project), "model": model_identity,
        "role": role, "stage": stage, "system": system, "payload": payload,
        "schema": schema, "max_tokens": max_tokens, "temperature": temperature})
    folder = planning_dir(project)
    cache = folder / "cache" / (stage + "-" + signature[:24] + ".json")
    hit = None
    if cache.is_file():
        try:
            saved = json.loads(cache.read_text(encoding="utf-8"))
            if saved.get("signature") == signature:
                validate(saved["value"], schema)
                hit = saved
        except (OSError, ValueError, KeyError, ValidationError):
            pass
    if hit is not None:
        # An evidence-write failure must never fall through to a paid new call.
        from .context_manifest import record_reuse
        reuse = record_reuse(folder, project, role, stage, hit)
        from .role_handoffs import observe_call
        observe_call({'call_id': ((reuse or {}).get('origin') or {}).get('call_id')}, reused=True)
        report(checkpoint, message="复用已完成的创作步骤：" + stage)
        return copy.deepcopy(hit["value"])
    from .context_manifest import capture, cache_origin
    with capture(project, stage) as context:
        value = json_call(config=config, role=role, system=system, payload=payload,
                          schema=schema, output_dir=folder / "llm",
                          max_tokens=max_tokens, temperature=temperature)
    origin = cache_origin(context, folder)
    validate(value, schema)
    write_json(cache, {"signature": signature, "generated_at": utc_now(),
                       "value": value, "prompt": prompt_meta, "origin": origin})
    return copy.deepcopy(value)


def duration_plan(project):
    from .production_profiles import resolve
    return resolve(project)["shot_seconds"]

def spoken_chars(text):
    return len(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", str(text)))


def review_passes(review):
    scores = review.get("scores", {})
    values = [scores.get(key, 0) for key in SCORE_KEYS]
    return (len(values) == len(SCORE_KEYS) and min(values) >= 6
            and sum(values) / len(values) >= 7
            and not any(i.get("severity") in ("blocking", "major")
                        for i in review.get("issues", [])))


def technical_issues(episode, project):
    issues = []
    def add(code, description, shot_ids=None):
        issues.append({"severity": "blocking", "code": code,
            "description": description, "evidence": description,
            "suggestion": "修正对应内容后重新进行独立评审",
            "shot_ids": shot_ids or []})
    shots = episode.get("shots", [])
    plan = duration_plan(project)
    if len(shots) != len(plan):
        add("shot_count", "镜头数量不等于规格的 %d" % len(plan))
    if sum(float(s.get("seconds", 0)) for s in shots) != sum(plan):
        add("duration", "逐镜时长之和不等于项目目标时长")
    seen = set()
    for i, shot in enumerate(shots):
        sid = shot.get("id", "")
        if sid != "S%02d" % (i + 1) or sid in seen:
            add("shot_id", "镜头编号无效或重复", [sid])
        seen.add(sid)
        from .production_profiles import explicit, roles, speech_limit
        if explicit(project):
            if i >= len(plan) or shot.get("seconds") != plan[i]:
                add("shot_duration", "单镜时长不符合固定规格", [sid])
        elif shot.get("seconds") not in (4, 5, 6):
            add("shot_duration", "单镜须为 4 至 6 秒", [sid])
        if len(shot.get("dialogue", [])) > 1:
            add("voice_overlap", "当前制作配置每镜最多一条画外音", [sid])
        for line in shot.get("dialogue", []):
            maximum = speech_limit(project, shot.get("seconds")) if explicit(project) else 10 if project.get("format") == "motion_comic" else 12
            if spoken_chars(line.get("text", "")) > maximum:
                add("voice_too_long", "台词超过当前音频槽位的 %d 字上限" % maximum, [sid])
            if line.get("role") not in roles(project):
                add("voice_role", "台词声音角色未定义", [sid])
        groups = roles(project) + (["ensemble", "none", "two"] if explicit(project) else ["two"])
        if shot.get("reference_group") not in groups:
            add("character_reference", "镜头人物参考组未定义", [sid])
        prompt = shot.get("prompt", "")
        if len(re.findall(r"[A-Za-z]", prompt)) < 25:
            add("image_prompt_language", "图片提示词缺少可执行的英文画面描述", [sid])
    from .production_profiles import validate_episode
    try:
        validate_episode(episode, project)
    except ValueError as exc:
        add("production_profile", str(exc))
    return issues
