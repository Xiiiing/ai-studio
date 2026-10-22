"""One cached director handoff, restricted to shot production fields."""
from __future__ import annotations

import copy
import re

from .prompt_registry import load_prompt, derive_prompt

from studio.core import write_json
from factory.config import llm_identity
from .profile_creative import adapt_system
from factory.schemas import (
    CreativeReviewError, cached_json_call, episode_schema,
    fingerprint, object_schema, planning_dir, relative_path, report, text_schema,
    utc_now,
)

DIRECTOR_FIELDS = ("prompt", "motion_prompt", "camera_motion", "reference_group")
REPAIR_FIELDS = ("code", "severity", "description", "evidence", "suggestion", "shot_ids")
DIRECTOR_SYSTEM = load_prompt("storyboard_director")


def storyboard_schema(shot_ids, project=None):
    source = episode_schema(project=project)["properties"]["shots"]["items"]["properties"]
    fields = object_schema({key: copy.deepcopy(source[key]) for key in DIRECTOR_FIELDS})
    if project and project.get("material_package"):
        from .material_library import context
        materials = context(project)
        for field, kind in (("scene_id", "scene"), ("composition_id", "composition")):
            choices = [item["role"] for item in materials if item["kind"] == kind]
            if choices:
                fields["properties"][field] = {"type": "string", "enum": ["", *choices]}
                fields["required"].append(field)
    problem = object_schema({"reason": text_schema(8, 500),
                             "suggestion": text_schema(8, 500)})
    # Required object keys make missing, extra and out-of-scope IDs invalid
    # before the shared cache can persist a response.
    return object_schema({
        "shots": object_schema({sid: copy.deepcopy(fields) for sid in shot_ids}),
        "blocked_shots": object_schema(
            {sid: copy.deepcopy(problem) for sid in shot_ids}, required=[]),
    })


def model_capabilities(project, config, episode):
    from .video_providers import resolve_video
    video = resolve_video(config, require_enabled=False)
    production = config.get("production", {})
    mode = project.get("format", episode.get("format", "motion_comic"))
    capabilities = {
        "format": mode,
        "image_engine": "FLUX.2 Klein 4B keyframes through the existing ComfyUI workflow",
        "video_engine": ("No diffusion video; generated still plus FFmpeg camera movement"
                         if mode == "motion_comic" else
                         video["model"] + " image-to-video; generated shots require quality review"),
        "video_provider": None if mode == "motion_comic" else video["provider"],
        "video_profile": None if mode == "motion_comic" else production.get("video_profile"),
        "image_size": [production.get("image_width", episode.get("generation", {}).get("width", 736)),
                       production.get("image_height", episode.get("generation", {}).get("height", 1312))],
        "aspect_ratio": "9:16",
        "camera_motions": ["push_in", "pull_out", "pan_left", "pan_right", "hold"],
        "dialogue_delivery": "off-screen voice-over; no lip sync required" if project.get("production_mode") == "one_sentence" else "off-screen voice-over; visible mouths stay closed",
        "synchronized_lip_motion": False,
        "continuous_character_action_in_motion_comic": False,
        "constraints": [
            "A keyframe depicts one observable instant, not a sequence of actions.",
            "Keep cast, prop ownership, approved action and the complete shot order unchanged.",
            "Do not promise reliable complex multi-person choreography.",
            "Production feasibility remains provisional until actual media is evaluated.",
        ],
    }
    if "creative_contract" in project:
        from .creative_contract import resolve as resolve_contract, style_prompt
        capabilities["creative_contract"] = resolve_contract(project)
        capabilities["visual_constraints"] = style_prompt(project)
        capabilities["continuous_character_action_required"] = mode != "motion_comic"
        if mode != "motion_comic":
            capabilities["constraints"].append(
                "Every video shot needs one simple continuous character/object action; "
                "camera movement alone does not satisfy animation or live-action delivery.")
    return capabilities


def _episode_context(episode):
    keys = tuple(episode_schema()["properties"])
    value = {key: copy.deepcopy(episode[key]) for key in keys if key in episode and key != "shots"}
    value.update({key: copy.deepcopy(episode[key]) for key in (
        "format", "total_seconds", "fps", "width", "height", "production_profile", "characters", "creative_contract") if key in episode})
    shot_keys = tuple(episode_schema()["properties"]["shots"]["items"]["properties"]) + (
        "id", "seconds", "lip_sync", "delivery_method", "render_style_prompt", "characters", "character_states", "scene_id", "composition_id")
    value["shots"] = [{key: copy.deepcopy(shot[key]) for key in shot_keys if key in shot}
                      for shot in episode["shots"]]
    return value


def direct_storyboard(project, config, episode, checkpoint=None, *,
                      approved_story, shot_ids=None, round_index=1,
                      producer_feedback=None, repair_issues=None, previous_attempt=None,
                      supplement_request_id=None):
    """Direct one draft; immutable story fields and unselected shots survive verbatim."""
    from .review_policy import max_review_rounds
    if type(round_index) is not int or not 1 <= round_index <= max_review_rounds(config):
        raise CreativeReviewError("分镜导演轮次超出配置的有界自动返修范围")
    shots = episode.get("shots", [])
    ordered = [shot.get("id") for shot in shots]
    if not ordered or any(not isinstance(sid, str) or not re.fullmatch(r"S\d{2,3}", sid)
                          for sid in ordered) or len(set(ordered)) != len(ordered):
        raise CreativeReviewError("分镜输入须含唯一、有效的镜头ID")
    if shot_ids is not None and (not isinstance(shot_ids, list) or not shot_ids
                                or any(not isinstance(sid, str) for sid in shot_ids)
                                or len(set(shot_ids)) != len(shot_ids)
                                or not set(shot_ids).issubset(ordered)):
        raise CreativeReviewError("分镜修改范围须为当前稿中不重复的镜头ID")
    if repair_issues is not None and (not isinstance(repair_issues, list)
                                    or any(not isinstance(issue, dict) for issue in repair_issues)):
        raise CreativeReviewError("导演返修线索须为问题列表，不能传入整份评分报告")
    hints = [{key: copy.deepcopy(issue[key]) for key in REPAIR_FIELDS if key in issue}
             for issue in (repair_issues or [])]
    allowed = set(ordered if shot_ids is None else shot_ids)
    selected = [sid for sid in ordered if sid in allowed]
    from . import role_handoffs as handoffs
    folder = planning_dir(project)
    material = _episode_context(episode)
    transfer = handoffs.submit(folder, project, source='screenwriter', target='storyboard_director',
        source_stage='screenplay', target_stage='storyboard', contract='episode_direction',
        value=material, source_sha256=fingerprint(episode), scope=selected, resolves=supplement_request_id)
    received = handoffs.receive(folder, project, transfer['id'], role='storyboard_director',
                                stage='storyboard', value=material)
    if received['record'].get('return_context'):
        return copy.deepcopy(received['record']['return_context'])
    payload = {
        "approved_story": copy.deepcopy(approved_story),
        "producer_feedback": copy.deepcopy(producer_feedback),
        "repair_issues": hints,
        "current_episode": material,
        "editable_shot_ids": selected,
        "locked_shot_ids": [sid for sid in ordered if sid not in allowed],
        "editable_fields": list(DIRECTOR_FIELDS),
        "model_capabilities": model_capabilities(project, config, episode),
        "instruction": "只编排指定镜头的四个制作字段；完整剧情与未指定镜头保持原样。",
    }
    if project.get("material_package"):
        from .material_library import context
        payload["confirmed_materials"] = context(project)
        payload["editable_fields"] += ["scene_id", "composition_id"]
        payload["material_instruction"] = "保持人物身份与已批准剧情，scene_id/composition_id仅从定稿素材的role中选取；不适用填空串。人物参考按实际出镜角色，包括无对白人物。"
    if previous_attempt is not None:
        payload["previous_attempt"] = _episode_context(previous_attempt)
    stage = "storyboard-direction-%d" % round_index
    report(checkpoint, stage="storyboard_direction", progress=32 + round_index,
           message="独立分镜导演正在编排%d镜的构图、运镜和人物参考（第%d版）" %
                   (len(selected), round_index))
    schema = storyboard_schema(selected, project)
    system = adapt_system(DIRECTOR_SYSTEM, project)
    if received['accepted']:
        from .agent_loop import SupplementRequested
        try:
            with handoffs.bind(folder, transfer):
                raw = cached_json_call(project, config, "storyboard_director", stage,
                                   system, payload, schema, checkpoint,
                                       max_tokens=6500, temperature=0.35)
        except SupplementRequested as exc:
            if not exc.request or not exc.scope:
                raise  # External material needs a producer, not a fictional repair.
            received = {**received, 'accepted': False, 'request': exc.request}
            raw = {'agent_requested_supplement': True, 'validation_issues': exc.request['issues']}
    else:
        raw = {'not_submitted': True, 'validation_issues': received['request']['issues']}
    directed = copy.deepcopy(episode)
    issues = [] if received['accepted'] else copy.deepcopy(received['request']['issues'])
    applied = []
    for shot in directed["shots"] if received['accepted'] else []:
        sid = shot["id"]
        if sid not in allowed:
            continue
        problem = raw["blocked_shots"].get(sid)
        if problem:
            issues.append({
                "severity": "blocking", "code": "storyboard_feasibility",
                "description": problem["reason"],
                "evidence": sid + ".action: " + shot["action"],
                "suggestion": problem["suggestion"], "shot_ids": [sid],
            })
            continue
        shot.update({key: copy.deepcopy(raw["shots"][sid][key]) for key in DIRECTOR_FIELDS})
        for key in ("scene_id", "composition_id"):
            if key in raw["shots"][sid]:
                shot[key] = raw["shots"][sid][key]
        suffix = " No visible text, captions, logos, or watermarks."
        if payload["model_capabilities"]["format"] == "motion_comic":
            suffix += " Single illustrated keyframe; every visible person\'s mouth is closed."
            motion_suffix = " Keep all faces, bodies and closed mouths still; voice-over only."
            motion_prompt = shot["motion_prompt"].rstrip()
            if not motion_prompt.endswith(motion_suffix.strip()):
                motion_prompt += motion_suffix
            shot["motion_prompt"] = motion_prompt
        else:
            from .drama_intent import enabled
            suffix += " All dialogue is off-screen voice-over; no lip sync is required." if enabled(project) else " All dialogue is off-screen voice-over; visible mouths stay closed."
        if not shot["prompt"].rstrip().endswith(suffix.strip()):
            shot["prompt"] = shot["prompt"].rstrip() + suffix
        applied.append(sid)
    identity = llm_identity(config, "storyboard_director")
    signature = fingerprint({"stage": stage, "system": system, "payload": payload,
                             "schema": schema, "model": identity, "max_tokens": 6500,
                             "temperature": 0.35})
    request = None
    if issues:
        request = received['request'] or handoffs.return_for_supplement(folder, project, transfer['id'], issues)
    receipt = {
        "role": "storyboard_director", "round": round_index, "model_identity": identity,
        "source_signature": signature, "input_episode_signature": fingerprint(episode),
        "output_episode_signature": fingerprint(directed),
        "editable_fields": list(payload["editable_fields"]), "requested_shot_ids": selected,
        "applied_shot_ids": applied, "blocked_shot_ids": list(raw.get("blocked_shots", {})),
        "issues": copy.deepcopy(issues),
        "status": "needs_supplement" if not received['accepted'] else "feasibility_blocked" if issues else "directed_pending_review",
        "controlled_handoff_id": transfer['id'],
        "submission": "result_obtained_or_cached" if received['accepted'] else "agent_handoff_recorded" if raw.get("agent_requested_supplement") else "not_submitted",
        **({'supplement_request_id': request['id']} if request else {}),
    }
    snapshot_key = fingerprint({"receipt": receipt, "output": raw})[:16]
    artifact = planning_dir(project) / ("storyboard-director-%d-%s.json" % (round_index, snapshot_key))
    if not artifact.is_file():
        write_json(artifact, {**receipt, "created_at": utc_now(), "output": raw})
    receipt["artifact"] = relative_path(artifact)
    result = {"episode": directed, "issues": issues, "receipt": receipt,
              "artifact": receipt["artifact"], "supplement_request": request}
    if request:
        handoffs.remember_return(folder, project, transfer['id'], result)
    return result
