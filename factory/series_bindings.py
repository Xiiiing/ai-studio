"""Pinned approved references and exact, transitive shot dependency impact."""
from __future__ import annotations

import copy

from . import store
from .production_edits import require_idle, require_resolved_cloud, mark_visual_edit
from .series_assets import reference_path
from .series_bible import asset, project_cas, require_series


def shot_roles(shot, cast):
    roles = shot.get("cast") or shot.get("characters")
    if not isinstance(roles, list):
        group = shot.get("reference_group", "")
        roles = ([role for role in ("female", "male") if role in cast] if group == "two" else
                 [group] if group in cast else
                 list(dict.fromkeys(line.get("role") for line in shot.get("dialogue", []) if line.get("role") in cast))) or list(cast)[:1]
    return [role for role in roles if role in cast]


def dependent_closure(project, direct):
    episode = project.get("episode") or project.get("script") or {}
    affected = set(direct)
    changed = True
    while changed:
        changed = False
        for shot in episode.get("shots", []):
            if shot["id"] not in affected and set(shot.get("reference_shots", [])) & affected:
                affected.add(shot["id"])
                changed = True
    return sorted(affected)


def affected_shots(db, asset_id, version):
    rows = db.execute("SELECT project_id,shot_id,version FROM series_dependencies WHERE asset_id=? AND version<>? ORDER BY project_id,shot_id",
                      (asset_id, version)).fetchall()
    projects = {}
    for row in rows:
        projects.setdefault(row["project_id"], []).append(dict(row))
    result = []
    for pid, dependencies in projects.items():
        direct = {value["shot_id"] for value in dependencies}
        project = store._read(db, pid)
        result.append({"project_id": pid, "direct_shot_ids": sorted(direct),
                       "affected_shot_ids": dependent_closure(project, direct),
                       "pinned_versions": sorted({value["version"] for value in dependencies})})
    return result


def bind_asset(pid, payload):
    if set(payload) - {"expected_revision", "asset_id", "version", "shot_ids", "role"}:
        raise ValueError("未知资产引用字段")
    ids = payload.get("shot_ids")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 80 or any(not isinstance(value, str) for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("请明确选择1–80个不重复镜头")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        project = project_cas(db, pid, payload.get("expected_revision"))
        require_idle(db, project)
        value = asset(db, payload.get("asset_id"), payload.get("version"))
        if value["series_key"] != require_series(project):
            raise ValueError("只能引用同系列的资产")
        reference_path(value)
        episode = project.get("episode") or project.get("script") or {}
        shots = {shot["id"]: shot for shot in episode.get("shots", [])}
        if not set(ids) <= set(shots):
            raise ValueError("所选镜头不属于当前剧集")
        role = payload.get("role", value["entity_id"])
        if not isinstance(role, str):
            raise ValueError("引用角色标识无效")
        if value["kind"] == "character" and any(role not in shot_roles(shots[shot_id], episode.get("cast", {})) for shot_id in ids):
            raise ValueError("角色资产必须绑定为所选镜头实际出现的 cast 角色")
        if value["kind"] != "character" and role != value["entity_id"]:
            raise ValueError("场景和道具使用其稳定实体 ID")
        receipt = value["data"]["receipt"]
        reference = {"asset_id": value["asset_id"], "version": value["version"], "sha256": receipt["sha256"],
                     "kind": value["kind"], "entity_id": value["entity_id"], "role": role}
        affected = dependent_closure(project, ids)
        require_resolved_cloud(db, pid, affected)
        bindings = project.setdefault("series_references", {})
        previous = {shot_id: copy.deepcopy(bindings.get(shot_id, [])) for shot_id in ids}
        for shot_id in ids:
            # One reference per role/entity. Switching stable assets is explicit,
            # and removes only that role's obsolete dependency, not other assets.
            keep = [ref for ref in bindings.get(shot_id, []) if not (
                ref["asset_id"] == value["asset_id"] or (ref["kind"] == value["kind"] and ref["role"] == role))]
            if len(keep) >= 6:
                raise ValueError("每镜最多绑定6个系列参考，不会静默丢弃引用")
            bindings[shot_id] = keep + [copy.deepcopy(reference)]
            db.execute("DELETE FROM series_dependencies WHERE project_id=? AND shot_id=? AND asset_id NOT IN (" +
                       ",".join("?" for _ in bindings[shot_id]) + ")",
                       (pid, shot_id, *[ref["asset_id"] for ref in bindings[shot_id]]))
            db.execute("INSERT INTO series_dependencies VALUES(?,?,?,?,?,?) ON CONFLICT(project_id,shot_id,asset_id) "
                       "DO UPDATE SET version=excluded.version,sha256=excluded.sha256,role=excluded.role",
                       (pid, shot_id, value["asset_id"], value["version"], receipt["sha256"], role))
        if all(previous[shot_id] == bindings[shot_id] for shot_id in ids):
            return project
        mark_visual_edit(db, project, affected, "series_reference", {"previous": previous, "reference": reference})
        store._write(db, project)
        return project


def resolved_references(project, shot):
    bindings = project.get("series_references", {}).get(shot["id"], [])
    if not bindings:
        return []  # No DB access or signature changes for legacy standalone shots.
    result = []
    with store.connection() as db:
        for reference in bindings:
            value = asset(db, reference["asset_id"], reference["version"])
            if value["series_key"] != project.get("series_key"):
                raise ValueError("镜头的系列引用归属不匹配")
            path = reference_path(value)
            if value["data"]["receipt"]["sha256"] != reference["sha256"]:
                raise ValueError("镜头固定的资产校验值不匹配")
            result.append({**copy.deepcopy(reference), "receipt": copy.deepcopy(value["data"]["receipt"]),
                           "entity": copy.deepcopy(value["data"]["entity"]), "path": path})
    return result


def reference_receipts(values):
    return [{key: value[key] for key in ("asset_id", "version", "sha256", "kind", "entity_id", "role")} for value in values]
