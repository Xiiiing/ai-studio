"""Resolve only explicitly pinned, approved same-series character references."""
from __future__ import annotations

import copy

from .production_profiles import explicit, resolve
from .series_assets import reference_path
from .series_bible import asset


def validated(db, project):
    if not explicit(project):
        return {}
    result = {}
    for character in resolve(project)["characters"]:
        reference = character.get("reference_asset")
        if not reference:
            continue
        value = asset(db, reference["asset_id"], reference["version"])
        if not project.get("series_key") or value["series_key"] != project["series_key"] or value["kind"] != "character" or value["entity_id"] != character["character_id"]:
            raise ValueError("规格参考必须属于同一系列和稳定角色身份")
        reference_path(value)
        result[character["character_id"]] = {"asset_id": value["asset_id"], "version": value["version"],
            "sha256": value["data"]["receipt"]["sha256"], "kind": "character", "entity_id": value["entity_id"],
            "role": character["character_id"], "origin": "production_profile"}
    return result


def synchronize(db, project):
    references = validated(db, project)
    if not references:
        return
    episode = project.get("episode") or project.get("script") or {}
    if not episode.get("shots"):
        return
    from .series_bindings import shot_roles
    mapping = project.setdefault("series_references", {})
    for shot in episode["shots"]:
        existing = [entry for entry in mapping.get(shot["id"], []) if entry.get("origin") != "production_profile"]
        chosen_roles = {entry.get("role") for entry in existing if entry.get("kind") == "character"}
        defaults = [copy.deepcopy(references[role]) for role in shot_roles(shot, episode.get("cast", {}))
                    if role in references and role not in chosen_roles]
        previous_defaults = [entry for entry in mapping.get(shot["id"], []) if entry.get("origin") == "production_profile"]
        for entry in previous_defaults:
            db.execute("DELETE FROM series_dependencies WHERE project_id=? AND shot_id=? AND asset_id=?",
                       (project["id"], shot["id"], entry["asset_id"]))
        mapping[shot["id"]] = existing + defaults
        for entry in defaults:
            db.execute("INSERT OR REPLACE INTO series_dependencies(project_id,shot_id,asset_id,version,sha256,role) VALUES(?,?,?,?,?,?)",
                       (project["id"], shot["id"], entry["asset_id"], entry["version"], entry["sha256"], entry["role"]))
