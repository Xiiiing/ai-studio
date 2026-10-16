"""Resolve confirmed material snapshots into existing planning/rendering inputs."""
from __future__ import annotations

import copy
import json

from studio import core
from . import store, material_library as library
from .scheduler import ProductionNeedsDecision


def resolved(project, refs=None):
    refs = refs if refs is not None else project.get("material_package", {}).get("references", [])
    if not refs:
        return []
    result = []
    with store.connection() as db:
        for ref in refs:
            clean = {key: value for key, value in ref.items() if key in {"asset_id", "version", "role", "shot_ids"}}
            actual, value = library.reference(db, project, clean, approved=True)
            if actual != ref:
                raise ValueError("素材固定引用与已确认版本不匹配")
            result.append({**actual, "library_key": value["library_key"], "document": value["data"]["document"],
                           "images": value["data"]["images"], "path": library.image_path(value["data"]["images"][0])})
    return result


def styles(project):
    if project.get("style_feedback"):
        return None  # A deliberate restyle request reopens the existing direction workflow.
    value = next((item for item in resolved(project) if item["kind"] == "style"), None)
    if not value:
        return None
    document = value["document"]
    return [{"id": "library_" + value["asset_id"] + "_v" + str(value["version"]), "name": document["name"],
             "description": document.get("description", ""), "prompt": document.get("visual_rules") or document.get("description") or document["name"],
             "palette": document.get("palette", []), "format": project.get("format", "motion_comic"),
             "material_reference": {key: value[key] for key in ("asset_id", "version", "sha256", "kind", "role")}}]


def preview(engine):
    from .quality import inspect_image, semantic_review, combine
    from .creative_contract import resolve as resolve_contract
    from .media_contract import visual_direction
    chosen = styles(engine.project)
    if not chosen:
        return None
    style = chosen[0]
    value = next(item for item in resolved(engine.project) if item["kind"] == "style")
    context = {"id": style["id"], "prompt": style["prompt"], "review_purpose": "visual_direction_preview", "visual_direction": visual_direction(style)}
    technical = inspect_image(value["path"])
    semantic = semantic_review(engine.evaluator, engine.project, context, value["path"], []) if engine.evaluator else None
    qa = combine(technical, semantic, semantic_required=bool(engine.creative_contract))
    if technical["status"] != "pass" or semantic and semantic.get("status") == "fail":
        raise ProductionNeedsDecision("已选素材画风未通过当前项目视觉审核", "material_style_mismatch")
    if engine.creative_contract and (not semantic or semantic.get("status") != "pass" or semantic.get("style_alignment", {}).get("status") != "match"):
        raise ProductionNeedsDecision("素材画风与本剧媒介的匹配尚未验证，请检查视觉审核连接", "semantic_unavailable")
    qa["eligible_for_selection"] = technical["status"] == "pass" and (not engine.creative_contract or semantic.get("status") == "pass")
    style["preview"] = {**value["images"][0], "qa": qa, "material_reference": style["material_reference"],
                        "visual_direction_signature": core.fingerprint(visual_direction(style))}
    if engine.creative_contract:
        style["preview"]["creative_contract_signature"] = core.fingerprint(resolve_contract(engine.project))
    engine.styles = [style]
    engine.emit("visual_previews_complete", styles=engine.styles)
    return engine.styles


class MaterialProductionMixin:
    def _material_character(self, role):
        refs = [item for item in resolved(self.project) if item["kind"] == "character" and item["role"] == role]
        if not refs:
            return None
        value = refs[0]
        from .series_bindings import shot_roles
        relevant = [shot["id"] for shot in self.shots if role in shot_roles(shot, self.cast)]
        if "shot_ids" in value and not set(relevant) <= set(value["shot_ids"]):
            return None
        return {**value["images"][0], "material_reference": {key: value[key] for key in ("asset_id", "version", "sha256", "kind", "role")}}

    def _material_shot_inputs(self, shot, roles, references):
        values = resolved(self.project, library.shot_references(self.project, shot))
        if not values:
            return references, "", []
        characters = {item["role"]: item for item in values if item["kind"] == "character"}
        compositions = [item for item in values if item["kind"] == "composition"]
        refs = [characters[role]["path"] if role in characters else path for role, path in zip(roles, references)] + references[len(roles):]
        if compositions:
            if len(compositions) != 1:
                raise ValueError("每镜只采用一套已确认的同框构图")
            composition = compositions[0]
            members = composition["document"].get("members", [])
            if {member["role"] for member in members} != set(roles):
                raise ValueError("同框参考的人物对应与镜头实际出镜角色不一致")
            for member in members:
                selected = characters.get(member["role"])
                if selected and (selected["asset_id"], selected["version"]) != (member["asset_id"], member["version"]):
                    raise ValueError("同框参考中的人物与本剧正式造型不一致，请重新准备组合候选")
            # A human-confirmed composite represents all explicitly mapped identities.
            refs = [composition["path"]] + references[len(roles):]
        refs += [item["path"] for item in values if item["kind"] in {"scene", "style"}]
        constraints = [{key: item[key] for key in ("asset_id", "version", "role", "kind", "document")} for item in values]
        shot["material_constraints"] = constraints
        prompt = (" Preserve the confirmed character identities, scene structure and common visual style. "
                  "Style references guide art only; do not copy their depicted person or scene. "
                  "Composition members identify each corresponding person and normalized image region. "
                  "Per-shot wardrobe, expression, action, daylight and story state can vary without changing identity. "
                  + json.dumps({"confirmed_materials": constraints, "character_states": shot.get("character_states", {})}, ensure_ascii=False))
        receipts = [{key: item[key] for key in ("asset_id", "version", "sha256", "kind", "role", "library_key")} for item in values]
        return refs, prompt, receipts

    def _material_preview_reference(self):
        if not self.selected_style or not self.selected_style.get("material_reference"):
            return False
        actual = styles(self.project)
        if not actual or actual[0]["material_reference"] != self.selected_style["material_reference"]:
            raise ProductionNeedsDecision("画风素材与本剧定稿不匹配", "material_style_changed")
        from .media_contract import preview_contract_valid
        if self.creative_contract and not preview_contract_valid(self.project, self.selected_style):
            prepared = preview(self)
            self.selected_style = copy.deepcopy(prepared[0])
        return True

    def _check_material_reference_limit(self, references):
        from .material_tasks import reference_limit
        profile = self.config.get("image_connection", {"provider": "legacy", "model": "FLUX.2-klein-4B"})
        maximum = reference_limit(profile)
        if len(references) > maximum:
            raise ProductionNeedsDecision(f"本镜需要{len(references)}张参考图，当前连接最多支持{maximum}张。请在素材库分阶段生成并确认同框组合，或选择支持此数量的工作流。", "material_reference_limit")
