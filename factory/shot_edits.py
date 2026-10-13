"""Whitelisted per-shot mutations; producer consent never issues a cloud POST."""
from __future__ import annotations

import copy
import time

from . import store
from .production_edits import require_idle, require_resolved_cloud, mark_visual_edit
from .series_bible import project_cas
from .series_bindings import dependent_closure
from .series_production import character_states
from .shot_workbench import candidate, plan_signature, shot_definition

SUPPORTED = {"prompt", "motion_prompt", "camera_motion", "character_states"}


def mutate(pid, shot_id, payload):
    action = payload.get("action")
    allowed = ({"action", "expected_revision", "stage", "take", "confirmed"} if action == "select" else
               {"action", "expected_revision", "changes", "regenerate", "stage"})
    if action not in {"select", "edit"} or set(payload) - allowed:
        raise ValueError("不支持的镜头操作或字段")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        project = project_cas(db, pid, payload.get("expected_revision"))
        require_idle(db, project)
        if not project.get("selected_style") or not project.get("selected_story") or (project.get("script_review") or {}).get("passed") is not True:
            raise ValueError("必须先完成故事、画风和剧本评审")
        shot = shot_definition(project, shot_id)
        if action == "select":
            if payload.get("confirmed") is not True:
                raise ValueError("确认选片需要明确同意")
            value = candidate(project, shot_id, payload.get("stage"), payload.get("take"), verify=True)
            if not value["selectable"]:
                raise ValueError("候选未通过验证或属于旧制作参数，不能选为正式素材")
            stage = value["stage"]
            affected = dependent_closure(project, [shot_id]) if stage == "image" else [shot_id]
            require_resolved_cloud(db, pid, affected)
            pin = {"take": value["take"], "stage": stage, "receipt": value["receipt"],
                   "plan_signature": value["plan_signature"], "at": time.time(), "actor": "producer",
                   "source_revision": project["revision"]}
            mark_visual_edit(db, project, affected, "select_candidate", {"shot_id": shot_id, **pin})
            for sid in affected:
                for key in (("video",) if stage == "video" or sid == shot_id else ("image", "video")):
                    project.get("shot_selections", {}).get(sid, {}).pop(key, None)
            project.setdefault("shot_selections", {}).setdefault(shot_id, {})[stage] = pin
        else:
            changes = copy.deepcopy(payload.get("changes", {}))
            regenerate = payload.get("regenerate", False)
            if type(regenerate) is not bool or not isinstance(changes, dict) or set(changes) - SUPPORTED:
                raise ValueError("只能修改明确支持的提示词、运镜和逐镜人物状态")
            for key in ("prompt", "motion_prompt"):
                if key in changes:
                    limit = 1600 if key == "prompt" else 800
                    if not isinstance(changes[key], str) or not 1 <= len(changes[key].strip()) <= limit:
                        raise ValueError(key + " 不能为空或超过长度限制")
                    changes[key] = changes[key].strip()
            if "camera_motion" in changes and changes["camera_motion"] not in {"push_in", "pull_out", "pan_left", "pan_right", "hold"}:
                raise ValueError("不支持的运镜参数")
            if "character_states" in changes:
                character_states({"character_states": changes["character_states"]}, (project.get("episode") or project["script"])["cast"])
            changes = {k: v for k, v in changes.items() if shot.get(k) != v}
            if not changes and not regenerate:
                raise ValueError("没有实际修改；相同参数重制须明确选择重新生成")
            stage = payload.get("stage", "image")
            if stage not in {"image", "video"}:
                raise ValueError("重制类型只能是 image 或 video")
            image_changed = bool(set(changes) & {"prompt", "character_states"}) or (regenerate and stage == "image")
            affected = dependent_closure(project, [shot_id]) if image_changed else [shot_id]
            require_resolved_cloud(db, pid, affected)
            before = copy.deepcopy(shot)
            updated = {**copy.deepcopy(shot), **changes, "shot_revision": shot.get("shot_revision", 1) + 1}
            if regenerate:
                updated.setdefault("render_revisions", {})[stage] = updated.get("render_revisions", {}).get(stage, 0) + 1
            for field in ("episode", "script"):
                if isinstance(project.get(field), dict):
                    project[field] = copy.deepcopy(project[field])
                    project[field]["shots"] = [copy.deepcopy(updated) if s["id"] == shot_id else s for s in project[field]["shots"]]
            project["shots"] = [{**s, **copy.deepcopy(updated)} if s.get("id") == shot_id else s for s in project.get("shots", [])]
            mark_visual_edit(db, project, affected, "edit_shot", {"shot_id": shot_id, "before": before, "after": updated,
                                                                 "regenerate": regenerate, "stage": stage})
            for sid in affected:
                for key in (("image", "video") if image_changed else ("video",)):
                    project.get("shot_selections", {}).get(sid, {}).pop(key, None)
        store._write(db, project)
        return project
