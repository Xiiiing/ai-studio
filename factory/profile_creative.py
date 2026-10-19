"""Apply the single production profile to schema and static agent instructions."""
from __future__ import annotations

import copy
import json

from .production_profiles import explicit, resolve, roles, speech_limit


def adapt_schema(schema, project):
    if not explicit(project):
        return schema
    identifiers = roles(project)
    def adapt(node):
        if isinstance(node, list):
            return [adapt(value) for value in node]
        if not isinstance(node, dict):
            return node
        value = {k: adapt(v) for k, v in node.items()}
        if node.get("enum") == ["female", "male"]:
            value["enum"] = identifiers
        elif node.get("enum") == ["female", "male", "two"]:
            value["enum"] = identifiers + (["two"] if resolve(project)["preset"] == "legacy15" else ["ensemble", "none"])
        properties = node.get("properties", {})
        for field in ("cast", "character_names"):
            source = properties.get(field, {}).get("properties", {})
            if set(source) == {"female", "male"}:
                replacement = value["properties"][field]
                replacement["properties"] = {role: copy.deepcopy(source["female"]) for role in identifiers}
                replacement["required"] = identifiers
        if node.get("type") == "array" and "role" in node.get("items", {}).get("properties", {}) and "identity" in node["items"]["properties"]:
            value.update(minItems=len(identifiers), maxItems=len(identifiers))
        if "reference_group" in properties and "dialogue" in properties:
            if resolve(project)["preset"] != "legacy15":
                value["properties"]["characters"] = {"type": "array", "items": {"type": "string", "enum": identifiers},
                                                       "minItems": 0, "maxItems": len(identifiers), "uniqueItems": True}
                value["required"] = list(dict.fromkeys([*value.get("required", []), "characters"]))
            value["properties"]["dialogue"]["items"]["properties"]["text"]["maxLength"] = speech_limit(project)
        return value
    return adapt(copy.deepcopy(schema))


def _adapt_profile_system(system, project):
    if not explicit(project):
        return system
    from .prompt_registry import derive_prompt
    source = system
    profile = resolve(project)
    system = system.split("\nCREATIVE_PROFILE_JSON:")[0]
    ids = "/".join(roles(project))
    constraint = f"{profile['shot_count']}镜、总长{profile['duration_seconds']:g}秒、{len(profile['characters'])}位虚构成年角色，稳定ID为{ids}"
    replacements = {
        "60至90秒竖屏、15镜、两位虚构成年角色（female/male）": constraint + "，竖屏",
        "characters 恰好包含 female 和 male 各一位": "characters 必须精确覆盖规格中的全部稳定角色ID",
        "恰好15镜": "恰好" + str(profile["shot_count"]) + "镜",
        "两位虚构成年角色，female和male仅作为固定制作角色键；姓名自由。": constraint + "。身份键不代表性别；姓名沿用规格。",
        "female/male/two": "规格中的单角色ID或ensemble/none（旧规格保留two）",
        "female或male的声音": "所选character_id绑定的voice_id声音",
        "最多10个字符": "最多" + str(speech_limit(project)) + "个字符",
        "两位虚构成年角色恰为female和male，15镜60至90秒能讲完": constraint + "且须在该规格内讲完",
        "15镜60至90秒、两名成年角色": constraint,
        "不得将两位角色混合": "不得混合不同角色身份",
    }
    if not getattr(source, "assembly", None):
        for old, new in replacements.items():
            system = system.replace(old, new)
    rendered = system + "\nCREATIVE_PROFILE_JSON:\n" + json.dumps(profile, ensure_ascii=False, sort_keys=True) + (
        "\n严格遵循此规格的镜头数量、逐镜秒数、稳定人物ID及声线映射。不要添加角色或扩展片长。"
        "对新规格，characters明确列出每镜可见的角色；reference_group仅为概要，不替代人物列表。"
        "逐镜服装、手持物、位置和剧情状态独立于不变身份。短对白仅作时长预估，实际声音须另行实测。")
    return derive_prompt(source, rendered, "production_profile")


def adapt_system(system, project):
    """Apply optional intent while preserving byte-exact historical prompts."""
    from .creative_contract import instructions, resolve as resolve_contract
    from .prompt_workbench import select
    system = select(system, project)
    from .prompt_registry import derive_prompt
    if resolve_contract(project) is None:
        return _adapt_profile_system(system, project)
    source = system
    # Called by both individual roles and the shared cache; never append twice.
    base = (derive_prompt(source, system.split("\nCREATIVE_CONTRACT_JSON:")[0], "creative_contract")
            if "\nCREATIVE_CONTRACT_JSON:" in system else source)
    rendered = _adapt_profile_system(base, project)
    return derive_prompt(rendered, str(rendered) + instructions(project), "creative_contract")
