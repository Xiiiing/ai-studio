"""Read-only role handoffs and execution evidence for one factory project.

Assignments describe the current configuration. Execution rows describe recorded
project history; they never borrow a model or timestamp from the current config.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from urllib.parse import quote

from studio.core import ROOT
from .config import ROLE_LABELS, load_config, resolve_llm

MAX_DIRECTORY_ENTRIES = 512
MAX_RECEIPTS = 96
MAX_RECEIPT_BYTES = 65536
MAX_EXECUTIONS_PER_ROLE = 24
MAX_SHOTS = 120
CALL_DIRECTORIES = ("planning/llm/calls", "planning/calls", "quality/semantic/calls",
                    "planning/h3-prompts/llm/calls")
ENGINE_LABELS = {
    "orchestrator": "流程编排", "image_generator": "图像制作",
    "video_generator": "视频生成", "speech": "角色配音",
    "editor": "剪辑交付", "producer": "出品人",
}
HANDOFFS = (
    ("orchestrator", "researcher", "创作任务与来源"),
    ("researcher", "creator", "题材研究与可用素材"),
    ("creator", "independent_critic", "候选故事"),
    ("independent_critic", "producer", "提案与剧本评审"),
    ("producer", "story_editor", "选定故事与修改意见"),
    ("story_editor", "story_critic", "修订后的故事目标"),
    ("story_critic", "screenwriter", "经评审的故事目标"),
    ("producer", "screenwriter", "选定故事与制作约束"),
    ("screenwriter", "storyboard_director", "剧本与镜头要求"),
    ("storyboard_director", "independent_critic", "逐镜分镜与连续性依据"),
    ("independent_critic", "visual_designer", "通过评审的剧本"),
    ("visual_designer", "image_generator", "画风与人物方案"),
    ("prompt_style_compiler", "image_generator", "画风与人物视觉约束"),
    ("image_generator", "producer", "视觉预览"),
    ("producer", "image_generator", "选定视觉方向"),
    ("image_generator", "video_generator", "镜头关键帧"),
    ("image_generator", "visual_critic", "图像与角色参考"),
    ("video_generator", "visual_critic", "生成镜头与分镜依据"),
    ("screenwriter", "speech", "对白与角色"),
    ("speech", "dialogue_editor", "对白时长与超时证据"),
    ("dialogue_editor", "speech", "修订后的对白"),
    ("image_generator", "editor", "漫画关键帧与运镜"),
    ("video_generator", "editor", "视频镜头"),
    ("speech", "editor", "配音素材"),
    ("visual_critic", "editor", "素材质量检查"),
    ("editor", "producer", "成片与交付清单"),
    ("producer", "edit_director", "重剪意见"),
    ("edit_director", "editor", "重剪计划"),
)
STAGE_ROLES = {
    "research": "researcher", "proposals": "creator", "story_creation": "creator",
    "story_review": "independent_critic", "story_resolution": "story_editor",
    "story_resolution_review": "story_critic", "screenplay": "screenwriter",
    "script_creation": "screenwriter", "storyboard_direction": "storyboard_director",
    "storyboard": "storyboard_director", "visual_design": "visual_designer",
    "script_review": "independent_critic", "visual_development": "visual_designer",
    "style_previews": "image_generator", "story_decision": "producer",
    "style_decision": "producer", "final_decision": "producer",
}
PHASE_ROLES = {
    "visual_previews": "image_generator", "character_references": "image_generator",
    "story_frames": "image_generator", "diffusion_video": "video_generator",
    "h3_prompt_preparation": "storyboard_director",
    "quality_review": "visual_critic", "semantic_review_pending": "visual_critic",
    "voice": "speech", "voice_rendering": "speech", "voice_review": "speech",
    "dialogue_timing_review": "dialogue_editor", "dialogue_rewriting": "dialogue_editor",
    "dialogue_rewrite_failed": "dialogue_editor", "camera_motion": "editor",
    "sound_edit": "editor", "edit": "editor", "lip_sync": "editor",
    "lip_sync_review": "visual_critic", "final_technical_review": "editor",
    "delivery_ready": "editor",
}
STATUS_MAP = {
    "starting": "running", "rendering": "running", "reviewing": "running",
    "rewriting": "running", "generated": "waiting", "semantic_pending": "waiting",
    "passed": "succeeded", "pass": "succeeded", "completed": "succeeded",
    "completed_with_process_error": "succeeded", "applied": "succeeded",
    "success": "succeeded", "fail": "failed", "timed_out": "failed",
    "preparing": "waiting", "queueing": "waiting", "processing": "running",
}
STATES = {"pending", "running", "succeeded", "failed", "waiting", "paused", "cancelled"}


def _object(value):
    return value if isinstance(value, dict) else {}


def _rows(value, limit=MAX_SHOTS):
    return [row for row in value[:limit] if isinstance(row, dict)] if isinstance(value, list) else []


def _text(value, limit=300):
    return value[:limit] if isinstance(value, str) else None


def _state(value):
    raw = value.lower() if isinstance(value, str) else ""
    return raw if raw in STATES else STATUS_MAP.get(raw, "waiting")


def _timestamp(value):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
        if isinstance(value, str) and len(value) <= 64:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        pass
    return None


def _project_root(project):
    ident = project.get("id")
    if not isinstance(ident, str) or not re.fullmatch(r"[a-f0-9]{12}", ident):
        raise ValueError("Invalid factory project ID")
    slug = project.get("slug", "factory-" + ident)
    if slug != "factory-" + ident:
        raise ValueError("Invalid factory project directory")
    base = (ROOT / "production").resolve()
    folder = base / slug
    if folder.is_symlink() or not folder.resolve().is_relative_to(base):
        raise ValueError("Factory project directory escapes its scope")
    return folder


def _relative(root, value):
    """Accept only a recorded path or media URL inside this exact project."""
    if not isinstance(value, str) or len(value) > 2048:
        return None
    raw = value.removeprefix("/media/")
    if any(char in raw for char in ("\\", "\x00", "?", "#", "%", ":")):
        return None
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        return None
    prefix = ("production", root.name)
    if tuple(path.parts[:2]) != prefix or len(path.parts) < 3:
        return None
    return Path(*path.parts[2:])


def _open_directory(root, parts=()):
    """Do not follow symlinks at any component, including during a rename race."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(root, flags)
    try:
        for part in parts:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _artifact_url(root, value):
    relative = _relative(root, value)
    if relative is None:
        return None
    directory = None
    try:
        directory = _open_directory(root, relative.parts[:-1])
        details = os.stat(relative.name, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(details.st_mode):
            return None
    except (OSError, ValueError):
        return None
    finally:
        if directory is not None:
            os.close(directory)
    return "/media/" + quote("production/" + root.name + "/" + relative.as_posix(), safe="/")


def _read_receipt(root, relative, evidence):
    if evidence["receipts_read"] >= MAX_RECEIPTS:
        evidence["truncated"] = True
        return None
    evidence["receipts_read"] += 1
    directory = descriptor = None
    try:
        directory = _open_directory(root, relative.parts[:-1])
        descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_size > MAX_RECEIPT_BYTES:
            evidence["unreadable_receipts"] += 1
            return None
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw = stream.read(MAX_RECEIPT_BYTES + 1)
        if len(raw) > MAX_RECEIPT_BYTES:
            evidence["unreadable_receipts"] += 1
            return None
        value = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        if not isinstance(value, dict):
            raise ValueError("Receipt must be an object")
        return value
    except (OSError, ValueError, UnicodeError, RecursionError):
        evidence["unreadable_receipts"] += 1
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


def _execution(record, ident, receipt=None):
    from .context_manifest import public as public_context
    response_model = _text(record.get("response_model"))
    requested_model = _text(record.get("configured_model")) or _text(record.get("model"))
    attempts = _rows(record.get("attempts"), 16)
    number = record.get("attempt")
    if type(number) is not int or number < 1:
        numbers = [row["attempt"] for row in attempts
                   if type(row.get("attempt")) is int and row["attempt"] > 0]
        number = max(numbers) if numbers else None
    remote = _object(record.get("remote_task"))
    context = _object(record.get("execution_context")) or _object(_object(record.get("context_manifest")).get("execution"))
    return {
        "id": ident, "profile_id": _text(record.get("profile") or record.get("profile_id")),
        "model": response_model or requested_model, "configured_model": requested_model,
        "response_model": response_model,
        "model_source": "response" if response_model else "request" if requested_model else "unrecorded",
        "provider": _text(record.get("provider")), "status": _state(record.get("status") or record.get("state")),
        "recorded_status": _text(record.get("status") or record.get("state")),
        "attempt": number, "started_at": _timestamp(record.get("started_at")),
        "finished_at": _timestamp(record.get("finished_at")), "receipt": receipt,
        "scope": "project_history",
        "execution_context": {k: _text(context.get(k)) for k in ("project_id", "stage", "run_id")},
        "task_id": _text(remote.get("task_id") or record.get("task_id")),
        "file_id": _text(remote.get("file_id") or record.get("file_id")),
        "context_manifest": public_context(record.get("context_manifest")),
        "reused": record.get("status") == "reused",
        "origin_state": record.get("origin_state"),
        "origin_call_id": _text(_object(record.get("origin")).get("call_id")),
    }


def _call_executions(root, evidence):
    result = []
    candidates = []
    for location in CALL_DIRECTORIES:
        descriptor = None
        try:
            descriptor = _open_directory(root, Path(location).parts)
            with os.scandir(descriptor) as entries:
                for index, entry in enumerate(entries):
                    if index >= MAX_DIRECTORY_ENTRIES:
                        evidence["truncated"] = True
                        break
                    if entry.is_dir(follow_symlinks=False):
                        candidates.append(Path(location) / entry.name / "receipt.json")
        except OSError:
            continue
        finally:
            if descriptor is not None:
                os.close(descriptor)
    for location in ("planning/context-reuse", "planning/h3-prompts/context-reuse"):
        descriptor = None
        try:
            descriptor = _open_directory(root, Path(location).parts)
            with os.scandir(descriptor) as entries:
                for index, entry in enumerate(entries):
                    if index >= MAX_DIRECTORY_ENTRIES:
                        evidence["truncated"] = True
                        break
                    if entry.name.endswith(".json") and entry.is_file(follow_symlinks=False):
                        candidates.append(Path(location) / entry.name)
        except OSError:
            pass
        finally:
            if descriptor is not None:
                os.close(descriptor)
    # Names generated by llm._folder begin with a UTC timestamp.
    for relative in sorted(candidates, key=lambda path: path.parent.name if path.name == "receipt.json" else path.stem, reverse=True):
        if evidence["receipts_read"] >= MAX_RECEIPTS:
            evidence["truncated"] = True
            break
        record = _read_receipt(root, relative, evidence)
        if not record or not isinstance(record.get("role"), str) or record["role"] not in ROLE_LABELS:
            continue
        receipt = _artifact_url(root, "production/" + root.name + "/" + relative.as_posix())
        if record.get("schema") == "ai-studio.context-reuse.v1":
            execution = _execution(record, "reuse:" + relative.stem, receipt)
            execution.update(status="succeeded", stage=_text(record.get("stage")),
                             artifacts=[{"label": "缓存复用记录", "artifact_url": receipt}])
            result.append((record["role"], execution))
            continue
        execution = _execution(record, "llm:" + relative.parent.name, receipt)
        prefix = "production/" + root.name + "/" + relative.parent.as_posix() + "/"
        execution["artifacts"] = []
        for filename, label in (("receipt.json", "执行回执"), ("parsed.json", "解析结果")):
            url = _artifact_url(root, prefix + filename)
            if url:
                execution["artifacts"].append({"label": label, "artifact_url": url, "kind": filename.split(".")[0]})
        execution["attempts"] = []
        for attempt in range(1, 17):
            files = []
            for kind, label in (("request", "请求原文"), ("response", "响应原文"), ("context", "本次输入清单")):
                url = _artifact_url(root, prefix + f"{kind}-{attempt:02d}.json")
                if url:
                    files.append({"label": label, "artifact_url": url, "kind": kind})
            if files:
                execution["attempts"].append({"attempt": attempt, "artifacts": files})
        result.append((record["role"], execution))
    return result


def _bindings(config):
    result = []
    for role, label in ROLE_LABELS.items():
        if role == "executive_producer" and role not in config.get("llm_roles", {}):
            result.append({"id": role, "role": role, "label": label, "profile_id": None,
                           "model": None, "provider": None, "enabled": False,
                           "assignment_source": "optional_unbound"})
            continue
        try:
            model = resolve_llm(config, role)
            result.append({"id": role, "role": role, "label": label,
                           "profile_id": model.get("profile"), "model": model.get("model"),
                           "provider": model.get("provider", "llama_cpp"),
                           "enabled": model.get("enabled", True), "assignment_source": "configuration"})
        except (ValueError, TypeError, KeyError):
            result.append({"id": role, "role": role, "label": label, "profile_id": None,
                           "model": None, "provider": None, "enabled": False,
                           "assignment_source": "invalid_configuration"})
    from .video_providers import resolve_video
    try:
        video = resolve_video(config, require_enabled=False)
    except (ValueError, TypeError, KeyError):
        video = {}
    engines = {
        "orchestrator": (None, "LangGraph", None),
        "image_generator": ("FLUX.2-klein-4B", "comfyui", None),
        "video_generator": (video.get("model"), video.get("provider"), video.get("profile")),
        "speech": ("Qwen3-TTS 1.7B", "local_subprocess", None),
        "editor": (None, "ffmpeg_pyav", None),
        "producer": (None, "human", None),
    }
    for role, label in ENGINE_LABELS.items():
        model, provider, profile = engines[role]
        result.append({"id": role, "role": role, "label": label, "profile_id": profile,
                       "model": model, "provider": provider,
                       "assignment_source": "orchestrator" if role != "producer" else "human"})
    return result


def team_spec(config=None):
    """Annotate the existing graph without replacing its executable topology."""
    config = load_config() if config is None else config
    return {
        "assignment_mode": "orchestrator",
        "assignment_semantics": ("LangGraph 执行既有任务图；delegated 项目可委托总制片选择故事和视觉，预算与终片仍须人工确认。"
                                 if "executive_producer" in config.get("llm_roles", {}) else
                                 "LangGraph 编排器按任务调用已绑定的角色；未配置额外的 LLM 领导。"),
        "supervisor_policy": {"role": "executive_producer", "opt_in": False,
                              "automatic_choices": ["story", "style"], "final_advisory_only": True},
        "teams": _bindings(config),
        "handoffs": [{"source": source, "target": target, "label": label}
                     for source, target, label in HANDOFFS],
    }


def _current(project, complete, generation):
    value = project.get(generation)
    return type(value) is int and value > 0 and type(project.get(complete)) is int and project[complete] == value


def _active_role(project):
    if str(project.get("status", "")).startswith("awaiting_"):
        return "producer"
    stage = project.get("stage")
    production = _object(project.get("production"))
    if stage == "production":
        phase = production.get("phase")
        if phase == "waiting_resources":
            phase = production.get("resource_resume_phase")
        return PHASE_ROLES.get(phase, "orchestrator")
    if stage == "revision":
        role = "edit_director" if _object(project.get("revision_request")).get("choice") == "reedit" else "story_editor"
    else:
        role = STAGE_ROLES.get(stage, "orchestrator")
    if project.get("workflow_version", 1) in {2, 3} and project.get("team_control_version") == 1:
        from .team_tasks import V2_TASKS
        canonical = {"story_creation": "proposals", "script_creation": "screenplay", "storyboard_direction": "storyboard", "style_previews": "visual_development"}.get(stage, stage)
        assigned = _object(project.get("team_assignments")).get("stage:" + str(canonical))
        if role == V2_TASKS.get(canonical, {}).get("owner") and assigned in ROLE_LABELS:
            return assigned
    return role


def build_flow(project, config=None):
    """Build a bounded projection; this function never executes, writes or loads a model."""
    root = _project_root(project)
    specification = team_spec(config)
    nodes = {row["role"]: {"id": row["role"], "role": row["role"], "label": row["label"],
                          "status": "pending", "status_source": "not_recorded",
                          "inputs": [], "outputs": [], "executions": []}
             for row in specification["teams"]}
    evidence = {"scope": "project_history", "receipts_read": 0, "unreadable_receipts": 0,
                "truncated": False, "max_receipts": MAX_RECEIPTS,
                "semantics": "节点状态来自当前项目；回执包含项目历史，不推测未记录的阶段、版本或时间。"}
    artifacts = _object(project.get("planning_artifacts"))
    research = _object(project.get("research"))
    resolved = _object(project.get("resolved_story_brief"))
    script = _object(project.get("script") or project.get("episode"))
    production = _object(project.get("production"))
    assets = _object(project.get("assets"))
    audio = _object(assets.get("audio"))
    delivery = _object(project.get("delivery"))
    shots = _rows(project.get("shots"))
    proposals = _rows(project.get("proposals"), 12)
    styles = _rows(project.get("styles"), 12)
    if isinstance(project.get("shots"), list) and len(project["shots"]) > MAX_SHOTS:
        evidence["truncated"] = True
    selected = project.get("selected_story")
    selected_id = selected.get("id") if isinstance(selected, dict) else selected
    proposal = next((row for row in proposals if row.get("id") == selected_id), {})

    def item(role, side, label, value=None, artifact=None):
        row = {"label": label}
        if isinstance(value, (str, int, float, bool)):
            row["value"] = _text(value, 65536) if isinstance(value, str) else value
            if isinstance(value, str) and len(value) > 65536:
                row["truncated"] = True
        elif isinstance(value, (dict, list)) and len(json.dumps(value, ensure_ascii=False).encode("utf-8")) <= 65536:
            row["value"] = value
        elif isinstance(value, (dict, list)):
            row["truncated"] = True
        url = _artifact_url(root, artifact)
        if url:
            row["artifact_url"] = url
        if len(row) > 1:
            nodes[role][side].append(row)

    def done(role, condition):
        if condition:
            nodes[role].update(status="succeeded", status_source="project_snapshot")

    item("orchestrator", "inputs", "创作任务", project.get("brief"))
    item("orchestrator", "outputs", "当前阶段", project.get("stage"))
    item("orchestrator", "outputs", "运行状态", project.get("status"))
    item("researcher", "inputs", "创作方向", project.get("brief"))
    item("researcher", "inputs", "提供的来源", _rows(project.get("submitted_sources"), 24) or None)
    item("researcher", "outputs", "研究状态", research or None, artifacts.get("research"))
    item("researcher", "outputs", "研究来源", research.get("sources") or research.get("source_count"), artifacts.get("sources"))
    item("creator", "inputs", "题材研究", research or None, artifacts.get("research"))
    item("creator", "inputs", "创作方向", project.get("brief"))
    for value in proposals:
        item("creator", "outputs", str(value.get("id", "提案")), value, artifacts.get("proposals"))
    for role in ("story_editor", "screenwriter"):
        item(role, "inputs", "已选故事", proposal or selected_id)
        item(role, "inputs", "出品人修改意见", project.get("script_feedback") or project.get("story_feedback"))
    item("story_editor", "outputs", "已存故事目标", resolved or None, artifacts.get("resolved_story_brief"))
    item("story_critic", "inputs", "故事目标", resolved or None, artifacts.get("resolved_story_brief"))
    resolution_review = _object(resolved.get("resolution_review"))
    if "passed" in resolution_review:
        item("story_critic", "outputs", "故事目标评审",
             "通过" if resolution_review["passed"] is True else "未通过",
             artifacts.get("resolved_story_review") or "production/" + root.name + "/planning/resolved-story-review.json")
    item("screenwriter", "inputs", "故事目标", resolved or None, artifacts.get("resolved_story_brief"))
    item("screenwriter", "outputs", "剧本草稿", project.get("screenplay_draft"), artifacts.get("screenplay_draft"))
    item("screenwriter", "outputs", "剧本", script or None, artifacts.get("script"))
    item("screenwriter", "outputs", "镜头数", len(_rows(script.get("shots"))), artifacts.get("episode"))
    item("storyboard_director", "inputs", "剧本草稿", project.get("screenplay_draft"), artifacts.get("screenplay_draft"))
    item("storyboard_director", "inputs", "剧本", script or None, artifacts.get("script"))
    item("storyboard_director", "outputs", "分镜表", artifact=artifacts.get("storyboard"))
    item("storyboard_director", "outputs", "导演回执", project.get("storyboard_receipt"), artifact=artifacts.get("storyboard_direction"))
    item("independent_critic", "inputs", "故事方案", proposals or None, artifacts.get("proposals"))
    item("independent_critic", "inputs", "已存剧本", script or None, artifacts.get("script"))
    for key, label in (("creation_review", "提案评审"), ("script_review", "剧本评审")):
        review = _object(project.get(key))
        if "passed" in review:
            item("independent_critic", "outputs", label, review,
                 artifacts.get(key))
    item("visual_designer", "inputs", "剧本", script or None, artifacts.get("script"))
    for value in styles:
        item("visual_designer", "outputs", "视觉方案", value, artifacts.get("styles"))
        item("image_generator", "inputs", "视觉方案", value, artifacts.get("styles"))
        item("image_generator", "outputs", "视觉预览", value.get("name"),
             value.get("preview_url") or _object(value.get("preview")).get("path"))
    selected_style = project.get("selected_style")
    style_id = selected_style.get("id") if isinstance(selected_style, dict) else selected_style
    chosen_style = next((value for value in styles if value.get("id") == style_id), selected_style)
    item("prompt_style_compiler", "inputs", "已选画风", chosen_style, artifacts.get("styles"))
    item("prompt_style_compiler", "outputs", "视觉约束记录", artifact=artifacts.get("visual_traits"))
    item("image_generator", "inputs", "镜头分镜", _rows(script.get("shots")) or None, artifacts.get("storyboard"))
    item("video_generator", "inputs", "视频形态", project.get("format"))
    pinned = _object(project.get("series_bible"))
    if pinned:
        item("screenwriter", "inputs", "固定系列设定版本", str(pinned.get("version")))
    for shot_id, references in _object(project.get("series_references")).items():
        for reference in _rows(references, 6):
            item("image_generator", "inputs", str(shot_id) + " 系列参考",
                 str(reference.get("asset_id")) + " v" + str(reference.get("version")) + " / " + str(reference.get("sha256")))
    image_count = video_count = 0
    for shot in shots:
        for kind, role in (("image", "image_generator"), ("video", "video_generator")):
            media = _object(shot.get(kind))
            recorded = media.get("path") or shot.get(kind + "_path")
            if not recorded:
                continue
            selected_media = bool(media.get("path") and _artifact_url(root, media["path"]))
            if kind == "image":
                image_count += int(selected_media)
                item("video_generator", "inputs", str(shot.get("id", "关键帧")), artifact=recorded)
            elif media.get("origin") == "procedural_camera_motion" or project.get("format") == "motion_comic":
                item("editor", "outputs", str(shot.get("id", "运镜片段")), "关键帧运镜", recorded)
                continue
            else:
                video_count += int(selected_media)
            item(role, "outputs", str(shot.get("id", "镜头")),
                 "已选素材" if selected_media else "候选素材", recorded)
        item("visual_critic", "inputs", str(shot.get("id", "镜头")), artifact=_object(shot.get("video") or shot.get("image")).get("path"))
        semantic = _object(_object(shot.get("qa")).get("semantic"))
        if semantic:
            item("visual_critic", "outputs", str(shot.get("id", "镜头")) + " 评审", semantic.get("status"))
        for index, record in enumerate(_rows(shot.get("attempts"), 20)):
            role = {"image": "image_generator", "video": "video_generator"}.get(record.get("stage"))
            if role is None:
                continue
            output = _object(record.get("output"))
            receipt = _artifact_url(root, output.get("provider_receipt") or record.get("provider_receipt"))
            if not receipt:
                relative = _relative(root, output.get("path"))
                if relative is not None:
                    receipt = _artifact_url(root, "production/" + root.name + "/" +
                                            (relative.parent / "attempt.json").as_posix())
            ident = "media:" + str(shot.get("id", "")) + ":" + str(record.get("stage")) + ":" + str(record.get("take") or index)
            nodes[role]["executions"].append(_execution(record, ident, receipt))
    if project.get("format") == "motion_comic":
        item("video_generator", "outputs", "本项目视频方式", "漫画关键帧运镜由剪辑器执行；视频模型未必调用")
    item("speech", "inputs", "对白条数", sum(len(_rows(shot.get("dialogue"), 30)) for shot in shots))
    item("speech", "outputs", "已保存配音", len(_rows(audio.get("lines"))))
    if audio.get("speech"):
        item("speech", "outputs", "对白处理", audio["speech"])
    item("dialogue_editor", "inputs", "对白时长问题", production.get("phase") if "dialogue" in str(production.get("phase", "")) else None)
    item("dialogue_editor", "outputs", "对白修改记录", sum(len(_rows(shot.get("dialogue_repairs"), 10)) for shot in shots))
    item("edit_director", "inputs", "重剪意见", project.get("edit_feedback"))
    plan = _object(project.get("edit_plan"))
    if plan:
        item("edit_director", "outputs", "剪辑计划", "镜头顺序 / 时长 / 配乐")
    item("editor", "inputs", "镜头素材", len(shots))
    item("editor", "inputs", "配音素材", len(_rows(audio.get("lines"))))
    for key, label in (("video_url", "成片"), ("subtitle_url", "字幕"), ("timeline_url", "剪辑时间线"), ("manifest_url", "交付清单")):
        item("editor", "outputs", label, artifact=delivery.get(key))
    item("producer", "inputs", "交付成片", artifact=delivery.get("video_url"))
    gate = _object(project.get("needs_decision"))
    item("producer", "inputs", "待确认事项", gate.get("message"))
    item("producer", "outputs", "已选故事", proposal or selected_id)
    item("producer", "outputs", "已选视觉", project.get("selected_style"))

    for role, execution in _call_executions(root, evidence):
        nodes[role]["executions"].append(execution)
    for index, reference in enumerate(production.get("voice_attempts", [])[:24] if isinstance(production.get("voice_attempts"), list) else []):
        relative = _relative(root, reference)
        if relative is not None and relative.parts[:3] == ("audio", "tts", "batches") and relative.suffix == ".json":
            record = _read_receipt(root, relative, evidence)
            if record:
                nodes["speech"]["executions"].append(_execution(record, "speech:" + str(index),
                                                                 _artifact_url(root, reference)))

    proposals_current = _current(project, "proposals_complete_generation", "proposal_generation")
    script_current = _current(project, "script_complete_generation", "script_generation")
    delivery_current = _current(project, "delivery_complete_revision", "production_revision")
    done("researcher", project.get("research_complete") is True and research.get("status") in {"ready", "degraded"})
    done("creator", proposals_current and bool(proposals))
    done("screenwriter", script_current and bool(script))
    done("story_editor", script_current and bool(resolved))
    done("story_critic", script_current and resolution_review.get("passed") is True)
    done("storyboard_director", script_current and any(row.get("storyboard") for row in _rows(_object(project.get("script_review")).get("rounds"), 5)))
    done("independent_critic", (proposals_current and bool(project.get("creation_review"))) or (script_current and bool(project.get("script_review"))))
    done("visual_designer", script_current and bool(styles))
    def media_passed(shot, stage):
        selected=_object(shot.get(stage))
        if selected.get('state',selected.get('status')) == 'passed':
            return True
        return bool(selected) and any(
            row.get('stage')==stage and row.get('state',row.get('status'))=='passed'
            and (not selected.get('source_signature') or row.get('source_signature')==selected.get('source_signature'))
            for row in _rows(shot.get('attempts'),20))
    done("image_generator", bool(shots) and all(media_passed(shot,'image') for shot in shots))
    done("video_generator", project.get('format')!='motion_comic' and bool(shots) and all(media_passed(shot,'video') for shot in shots))
    done("speech", production.get("phase") == "voice_complete" or bool(audio.get("source_signature")) or audio.get("speech") == "no_dialogue_in_script")
    done("visual_critic", bool(shots) and all(_object(_object(shot.get("qa")).get("semantic")).get("status") == "pass" for shot in shots))
    done("edit_director", bool(plan) and _current(project, "revision_prepared", "production_revision"))
    done("editor", delivery_current and bool(delivery) and bool(delivery.get("technical_checks") or delivery.get("qa") or delivery.get("manifest_url")))
    done("producer", project.get("status") == "approved")

    if project.get('workflow_version',1) in {2,3}:
        # V2 roles are tied to completed executable steps, not artifact presence.
        from .team_tasks import snapshot as current_tasks
        task_rows=current_tasks(project)['tasks']
        for role_name in ('story_editor','story_critic','screenwriter','storyboard_director','independent_critic','visual_designer'):
            owned=[r for r in task_rows if r['owner']==role_name or (role_name=='story_critic' and r['id']=='story_resolution')]
            if owned:
                successful=[r for r in owned if r['current'] and r['status']=='succeeded']
                calls_recorded=any(r.get('status')=='succeeded' and not r.get('reused') for r in nodes[role_name]['executions'])
                explicit_review=(role_name=='story_critic' and resolution_review.get('passed') is True) or (
                    role_name=='independent_critic' and script_current and _object(project.get('script_review')).get('passed') is True)
                director_receipt=role_name=='storyboard_director' and bool(project.get('storyboard_receipt'))
                verified=bool(successful) and (calls_recorded or explicit_review or director_receipt)
                nodes[role_name].update(status='succeeded' if verified else 'pending',status_source='execution_or_review_receipt' if verified else 'not_recorded')
    status = str(project.get("status", "queued"))
    active = _active_role(project)
    if status == "approved":
        overall = "succeeded"
    elif status == "cancelled":
        overall = "cancelled"
    elif status == "paused":
        overall = "paused"
    elif status == "failed":
        overall = "failed"
    elif status == "blocked" or gate or status.startswith("awaiting_") or production.get("phase") in {"waiting_resources", "semantic_review_pending", "needs_attention"}:
        overall = "waiting"
    elif status == "queued":
        overall = "pending"
    else:
        overall = "running"
    nodes["orchestrator"].update(status=overall, status_source="project_snapshot")
    if status != "approved":
        nodes[active].update(status=overall, status_source="project_stage")
    if status == "cancelled":
        for node in nodes.values():
            if node["status"] != "succeeded":
                node.update(status="cancelled", status_source="project_snapshot")
    for node in nodes.values():
        records = sorted(node["executions"], key=lambda row: (row["started_at"] or "", row["id"]), reverse=True)
        if len(records) > MAX_EXECUTIONS_PER_ROLE:
            evidence["truncated"] = True
        node["executions"] = records[:MAX_EXECUTIONS_PER_ROLE]
    from .supervision import snapshot as supervision_snapshot
    supervision = supervision_snapshot(project)
    current = [row for row in supervision["records"] if row["current_gate_version"]]
    if current and project.get("status") != "cancelled":
        last = current[-1]
        nodes["executive_producer"].update(
            status="succeeded" if last["status"] in {"selected", "advisory"} else "waiting",
            status_source="supervision_receipt_not_live_heartbeat")
    from .delivery_readiness import snapshot as delivery_snapshot
    delivery_readiness = delivery_snapshot(project)
    teams = [{**row, "status": nodes[row["role"]]["status"]} for row in specification["teams"]]
    edges = [{**edge, "status": nodes[edge["source"]]["status"]}
             for edge in specification["handoffs"]]
    from .team_tasks import snapshot as task_snapshot
    task_board = task_snapshot(project)
    current_runs = {row["id"]: _object(row.get("record")).get("run_id")
                    for row in task_board["tasks"] if row["current"]}
    current_runs.update({record["kind"] + "_decision": record.get("run_id")
                         for record in supervision["records"] if record["current_gate_version"]})
    valid_stages = {row["id"] for row in task_board["tasks"]} | {"story_decision", "style_decision", "final_decision"}
    for node in nodes.values():
        for execution in node["executions"]:
            context = execution["execution_context"]
            if context["project_id"] == project["id"] and context["stage"] in valid_stages and context["run_id"]:
                execution["stage"] = context["stage"]
                execution["run_id"] = context["run_id"]
                if current_runs.get(context["stage"]) == context["run_id"]:
                    execution["scope"] = "current_run"
    from .workflow_content import step_materials
    return {"project_id": project["id"], "revision": project.get("revision"),
            "task_board": task_board, "steps": step_materials(project, root, _artifact_url),
            "production_tasks":list(_object(project.get("production_tasks")).values()),
            "assignment_mode": "orchestrator", "orchestrator": "LangGraph",
            "assignment_semantics": specification["assignment_semantics"],
            "teams": teams, "nodes": list(nodes.values()), "edges": edges,
            "delivery_readiness": delivery_readiness,
            "evidence": evidence, "supervision": supervision}
