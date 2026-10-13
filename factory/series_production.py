"""Narrow production integration; no second orchestration or cache system."""
from __future__ import annotations

import json

from .series_bindings import resolved_references, reference_receipts, shot_roles


def character_states(shot, cast):
    value = shot.get("character_states", {})
    if not isinstance(value, dict) or len(value) > 16 or not set(value) <= set(cast):
        raise ValueError("逐镜人物状态只能引用已定义角色")
    for role, state in value.items():
        if not isinstance(state, dict) or set(state) - {"wardrobe", "held_props", "position", "story_state"}:
            raise ValueError("身份特征不可作为逐镜状态修改")
        for name, content in state.items():
            if name == "held_props":
                if not isinstance(content, list) or len(content) > 12 or any(not isinstance(v, str) or len(v) > 100 for v in content):
                    raise ValueError("手持道具应为至多12项文本")
            elif not isinstance(content, str) or len(content) > 500:
                raise ValueError("逐镜人物状态文本过长或无效")
    return value


class SeriesProductionMixin:
    def _series_character(self, role):
        relevant = [shot for shot in self.shots if role in shot_roles(shot, self.cast)]
        if not relevant or not self.project.get("series_references"):
            return None
        values = []
        for shot in relevant:
            match = next((value for value in resolved_references(self.project, shot)
                          if value["kind"] == "character" and value["role"] == role), None)
            if not match:
                return None
            values.append(match)
        first = values[0]
        # Each shot still resolves its own pinned version below. This global
        # receipt is only a fallback for existing character-reference consumers.
        return {**first["receipt"], **reference_receipts([first])[0], "series_approved": True}

    def _series_shot_inputs(self, shot, roles, references):
        values = resolved_references(self.project, shot)
        states = character_states(shot, self.cast)
        if not values and not states:
            return references, "", []
        by_role = {value["role"]: value for value in values if value["kind"] == "character"}
        refs = [by_role[role]["path"] if role in by_role else path for role, path in zip(roles, references)]
        refs += [value["path"] for value in values if value["kind"] != "character"]
        constraints = [{"role": value["role"], "kind": value["kind"], "entity": value["entity"]} for value in values]
        prompt = (" Preserve the immutable identity and appearance constraints of the approved series references. "
                  "Reference wardrobe is the default only; apply explicit per-shot wardrobe, held props, position and story state. "
                  + json.dumps({"approved_references": constraints, "character_states": states}, ensure_ascii=False))
        return refs, prompt, reference_receipts(values)
