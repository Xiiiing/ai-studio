"""One bounded creative specification shared by planning, rendering and export.

Legacy snapshots are interpreted, never rewritten. New profiles are explicit
and fixed in project snapshots; no setting silently changes an older episode.
"""
from __future__ import annotations

import copy
import math
import re

from studio.core import fingerprint

FPS = 24
PRESETS = {
    "legacy15": {"label": "经典短剧 · 15 镜 / 60–90 秒", "shot_count": 15, "duration_seconds": 60,
                 "shot_seconds": [4] * 15, "characters": [
                     {"character_id": "female", "name": "角色一", "voice_id": "female"},
                     {"character_id": "male", "name": "角色二", "voice_id": "male"}]},
    "compact6": {"label": "紧凑短片 · 6 镜 / 30 秒", "shot_count": 6, "duration_seconds": 30,
                 "shot_seconds": [4, 5, 6, 4, 5, 6], "characters": [
                     {"character_id": "char_lead", "name": "主角", "voice_id": "female"},
                     {"character_id": "char_partner", "name": "搭档", "voice_id": "male"}]},
    "ensemble10": {"label": "多角色短片 · 10 镜 / 50 秒", "shot_count": 10, "duration_seconds": 50,
                   "shot_seconds": [4, 5, 6, 4, 5, 6, 4, 5, 5, 6], "characters": [
                       {"character_id": "char_lead", "name": "主角", "voice_id": "female"},
                       {"character_id": "char_partner", "name": "搭档", "voice_id": "male"},
                       {"character_id": "char_witness", "name": "见证者", "voice_id": "narrator"}]},
}
ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")


def catalog():
    return {"schema_version": 1, "presets": copy.deepcopy(PRESETS),
            "bounds": {"shots": [3, 24], "seconds": [15, 90], "characters": [1, 4], "shot_seconds": [3, 10]}, "fps": FPS}


def _number(value, label, minimum, maximum, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum or (integer and value != int(value)):
        raise ValueError(label + " 超出允许范围")
    return int(value) if integer else float(value)


def normalize(value=None, duration_seconds=None):
    if value is None:
        value = {"preset": "legacy15"}
    elif isinstance(value, str):
        value = {"preset": value}
    if not isinstance(value, dict) or set(value) - {"schema_version", "preset", "shot_count", "duration_seconds", "shot_seconds", "characters"}:
        raise ValueError("production_profile 包含不支持的字段")
    if type(value.get("schema_version", 1)) is not int or value.get("schema_version", 1) != 1:
        raise ValueError("创作规格版本无效")
    preset = value.get("preset", "legacy15")
    if not isinstance(preset, str) or preset not in PRESETS:
        raise ValueError("创作规格预设不存在")
    profile = {k: copy.deepcopy(v) for k, v in PRESETS[preset].items() if k != "label"}
    profile.update(copy.deepcopy(value), schema_version=1, preset=preset)
    count = _number(profile["shot_count"], "镜头数量", 3, 24, True)
    total = profile["duration_seconds"]
    if duration_seconds is not None:
        if "duration_seconds" in value and value["duration_seconds"] != duration_seconds:
            raise ValueError("项目时长与 production_profile 时长冲突")
        total = duration_seconds
    total = _number(total, "总时长", 60 if preset == "legacy15" else 15, 90)
    if preset == "legacy15" and (count != 15 or total != int(total)):
        raise ValueError("legacy15 须为15镜、60–90秒整数时长")
    raw_plan = profile["shot_seconds"]
    if "shot_seconds" not in value and (count != PRESETS[preset]["shot_count"] or total != PRESETS[preset]["duration_seconds"]):
        frames, remainder = divmod(round(total * FPS), count)
        raw_plan = [(frames + (index < remainder)) / FPS for index in range(count)]
        if preset == "legacy15":
            seconds, remainder = divmod(int(total), 15)
            raw_plan = [seconds + (index < remainder) for index in range(15)]
    if not isinstance(raw_plan, list) or len(raw_plan) != count:
        raise ValueError("shot_seconds 必须逐一覆盖全部镜头")
    plan = []
    for raw in raw_plan:
        seconds = _number(raw, "单镜时长", 4 if preset == "legacy15" else 3, 6 if preset == "legacy15" else 10)
        if abs(seconds * FPS - round(seconds * FPS)) > 1e-7:
            raise ValueError("单镜时长须对齐24fps的帧边界")
        plan.append(int(seconds) if seconds.is_integer() else round(seconds * FPS) / FPS)
    if abs(sum(plan) - total) > 1e-7:
        raise ValueError("逐镜时长之和与规格总时长不一致")
    characters = profile["characters"]
    if not isinstance(characters, list) or not 1 <= len(characters) <= 4:
        raise ValueError("第一阶段支持1–4位稳定角色")
    roles = []
    for character in characters:
        if not isinstance(character, dict) or set(character) - {"character_id", "name", "voice_id", "reference_asset"}:
            raise ValueError("角色规格包含未知字段")
        role, voice = character.get("character_id"), character.get("voice_id")
        if not isinstance(role, str) or not ID.fullmatch(role) or role in roles:
            raise ValueError("character_id 必须唯一且格式安全")
        if not isinstance(voice, str) or not ID.fullmatch(voice):
            raise ValueError("每个角色必须明确指定 voice_id")
        if not isinstance(character.get("name", role), str) or not 1 <= len(character.get("name", role)) <= 30:
            raise ValueError("角色显示名称无效")
        character.setdefault("name", role)
        ref = character.get("reference_asset")
        if ref is not None:
            if not isinstance(ref, dict) or set(ref) != {"asset_id", "version"} or not isinstance(ref.get("asset_id"), str) or not ID.fullmatch(ref["asset_id"]):
                raise ValueError("reference_asset 必须指定固定资产ID和版本")
            _number(ref["version"], "参考资产版本", 1, 100000, True)
        roles.append(role)
    if preset == "legacy15" and roles != ["female", "male"]:
        raise ValueError("legacy15 保留 female/male；其他稳定角色请使用新增规格")
    profile.update(shot_count=count, duration_seconds=int(total) if total.is_integer() else total, shot_seconds=plan, characters=characters)
    return profile


def resolve(project):
    value = project.get("production_profile")
    if value is None and isinstance(project.get("episode"), dict):
        value = project["episode"].get("production_profile")
    duration = project.get("duration_seconds")
    if value is None and isinstance(duration, str):
        duration = float(duration)  # Preserve the old read-only legacy parser.
    return normalize(value, duration)


def explicit(project):
    return project.get("production_profile") is not None or isinstance(project.get("episode"), dict) and project["episode"].get("production_profile") is not None


def roles(project):
    return [item["character_id"] for item in resolve(project)["characters"]]


def profile_signature(project):
    return fingerprint(resolve(project))


def speech_limit(project, seconds=None):
    if not explicit(project) or resolve(project)["preset"] == "legacy15":
        return 10
    return min(8, max(1, math.floor(((seconds or min(resolve(project)["shot_seconds"])) - .45) * 3.5)))


def voices_for(project, registered):
    """An identity never determines a voice implicitly by gender or position."""
    profile = resolve(project)
    result = copy.deepcopy(registered)
    for character in profile["characters"]:
        voice_id = character["voice_id"]
        if voice_id not in registered:
            raise ValueError("角色 " + character["character_id"] + " 的 voice_id 尚未登记：" + voice_id)
        value = registered[voice_id]
        if not isinstance(value, (str, dict)) or isinstance(value, dict) and not value.get("speaker"):
            raise ValueError("登记声线缺少 speaker")
        result[character["character_id"]] = copy.deepcopy(value)
    return result


def validate_episode(episode, project, video=None, *, allow_trim=False):
    """Strict only for explicitly opted-in profiles; old partial callers survive."""
    if not explicit(project):
        return
    profile = resolve(project)
    shots = episode.get("shots", [])
    expected = {"S%02d" % (index + 1): seconds for index, seconds in enumerate(profile["shot_seconds"])}
    if len(shots) != len(expected) or {s.get("id") for s in shots} != set(expected):
        raise ValueError("实际镜头数量或编号不符合 production_profile")
    if set(episode.get("cast", {})) != set(roles(project)):
        raise ValueError("实际人物必须精确对应稳定 character_id")
    for shot in shots:
        seconds = _number(shot.get("seconds"), "实际镜长", .5 if allow_trim else 3, 10)
        if seconds > expected[shot["id"]] + 1e-7 or not allow_trim and abs(seconds - expected[shot["id"]]) > 1e-7:
            raise ValueError("实际镜长与固定规格不符")
        if video and video.get("provider") == "minimax" and seconds > video["duration"]:
            raise ValueError("镜头时长超过所选云视频模型支持的生成时长")
        present = shot.get("characters")
        if present is not None and (not isinstance(present, list) or len(present) != len(set(present)) or not set(present) <= set(roles(project))):
            raise ValueError("画中人物必须是已定义的稳定角色")
        for line in shot.get("dialogue", []):
            if line.get("role") not in roles(project):
                raise ValueError("对白的 character_id 未定义")
            start = _number(line.get("start", .2), "对白起点", 0, seconds)
            characters = len(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", str(line.get("text", ""))))
            if start + characters / 3.5 + .25 > seconds + 1e-7:
                raise ValueError("对白预估占用超过镜头槽位；实际音频仍需实测")
