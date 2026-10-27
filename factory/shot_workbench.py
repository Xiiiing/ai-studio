"""Bounded, read-only shot/candidate projections and immutable producer choices."""
from __future__ import annotations
from .material_library import shot_references as material_references

import copy
import json
import re
from pathlib import Path

from studio import core
from . import store

STAGES = {"image": "assets/shots", "video": "renders"}
MAX_CANDIDATES = 40
SHOT_RE = re.compile(r"S\d{2,3}\Z")
TAKE_RE = re.compile(r"(?:take_\d{3,9}|camera_[a-f0-9]{16})\Z")


def retained_video_results(root):
    """Protect existing files even when a result journal needs restoration."""
    folder = root / "renders"
    paths = set(folder.glob("*/take_*/result.json"))
    paths.update(path.parent / "result.json" for path in folder.glob("*/take_*/*")
                 if path.is_file() and path.suffix.lower() in {".mp4", ".webm", ".mov"})
    return sorted(paths)


def shot_definition(project, shot_id):
    if not isinstance(shot_id, str) or not SHOT_RE.fullmatch(shot_id):
        raise ValueError("镜头编号无效")
    episode = project.get("episode") or project.get("script") or {}
    shot = next((s for s in episode.get("shots", []) if s.get("id") == shot_id), None)
    if shot is None:
        raise KeyError("镜头不存在")
    return shot


def plan_signature(project, shot, stage):
    """A local edit is not a global production revision/cache invalidation."""
    fields = ("prompt", "cast", "characters", "reference_group", "reference_shots", "character_states", "render_style_prompt")
    if stage == "video":
        fields += ("motion_prompt", "camera_motion", "seconds", "lip_sync")
        fields += tuple(key for key in ("action", "continuity") if key in shot)
    inputs = {k: shot.get(k) for k in fields}
    if stage == "video" and inputs.get("seconds") is not None:
        # Match the renderer's 24 fps normalization, including JSON 6 vs 6.0.
        inputs["seconds"] = round(float(inputs["seconds"]) * 24) / 24
    return core.fingerprint({"shot": inputs,
                             "series": project.get("series_references", {}).get(shot["id"], []),
                             **({"materials": material_references(project, shot)} if project.get("material_package") else {}),
                             "style": project.get("selected_style"),
                             **({"creative_contract": project["creative_contract"]} if "creative_contract" in project else {}),
                             "render_revision": shot.get("render_revisions", {}).get(stage, 0)})


def project_root(project):
    slug = project.get("slug", "")
    if not re.fullmatch(r"factory-[A-Za-z0-9_-]{1,80}", slug):
        raise ValueError("项目素材目录无效")
    root = core.within(core.ROOT / "production", slug)
    owner = root / "factory_owner.json"
    if root.exists() and (not owner.is_file() or read_record(owner).get("project_id") != project["id"]):
        raise ValueError("素材目录归属校验失败")
    return root


def read_record(path):
    if not path.is_file() or path.stat().st_size > 512 * 1024:
        raise ValueError("回执缺失或过大")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("素材回执应为对象")
    return value


def candidate(project, shot_id, stage, take, *, verify=False):
    shot = shot_definition(project, shot_id)
    if stage not in STAGES or not isinstance(take, str) or not TAKE_RE.fullmatch(take):
        raise ValueError("候选版本或素材类型无效")
    root = project_root(project)
    folder = core.within(root, STAGES[stage] + "/" + shot_id + "/" + take)
    if folder.relative_to(root).as_posix() != STAGES[stage] + "/" + shot_id + "/" + take:
        raise ValueError("候选目录不允许通过符号链接改变镜头归属")
    attempt_path, result_path = folder / "attempt.json", folder / "result.json"
    attempt = read_record(core.within(root, attempt_path)) if attempt_path.exists() else {}
    result = read_record(core.within(root, result_path)) if result_path.exists() else {}
    path = None
    if result.get("path"):
        path = core.within(folder, core.ROOT / result["path"])
        suffixes = {".png", ".jpg", ".jpeg", ".webp"} if stage == "image" else {".mp4", ".webm"}
        if path.suffix.lower() not in suffixes or not path.is_file():
            raise ValueError("候选媒体缺失或类型不符合")
        if path.stat().st_size != result.get("bytes"):
            raise ValueError("候选媒体大小校验失败")
        if verify and core.sha256(path) != result.get("sha256"):
            raise ValueError("候选素材校验失败，不允许确认")
    qa = result.get("qa") or attempt.get("qa") or {}
    bound_plan = result.get("plan_signature") or attempt.get("plan_signature")
    current_plan = plan_signature(project, shot, stage)
    # Old receipts can be selected only within the exact currently selected
    # source family; execution will still check the full target/model signature.
    legacy_current = next((s for s in project.get("shots", []) if s.get("id") == shot_id), {}).get(stage) or {}
    compatible = (bound_plan == current_plan if bound_plan else bool(
        not shot.get("render_revisions") and result.get("source_signature") and
        result.get("source_signature") == legacy_current.get("source_signature")))
    eligible = bool(path and compatible and qa.get("eligible_for_selection") is True and
                    qa.get("technical", {}).get("status") == "pass")
    return {"id": stage + ":" + take, "take": take, "stage": stage,
            "attempt": attempt.get("attempt", result.get("attempt")),
            "status": attempt.get("state", "generated" if result else "planned"),
            "prompt": attempt.get("prompt"), "model": attempt.get("model"),
            "provider": attempt.get("provider"), "parameters": attempt.get("parameters", {}),
            **({"h3_prompt": copy.deepcopy(attempt["h3_prompt"])} if attempt.get("h3_prompt") else {}),
            "qa": qa, "error": attempt.get("error"), "selectable": eligible,
            "plan_current": compatible, "plan_signature": current_plan,
            "source_signature": result.get("source_signature") or attempt.get("source_signature"),
            "media_url": "/media/" + path.relative_to(core.ROOT).as_posix() if path else None,
            "checksum_verified": verify and path is not None, "receipt": result,
            "asset_references": result.get("asset_references", attempt.get("asset_references", []))}


def snapshot(pid):
    with store.connection() as db:
        project = store._read(db, pid)
        costs = {(r["shot_id"], r["take"]): dict(r) for r in db.execute(
            "SELECT shot_id,take,estimated_cost_micros,confirmed_cost_micros,currency FROM cloud_tasks WHERE project_id=?", (pid,))}
        running = bool(db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='running'", (pid,)).fetchone())
        unresolved = [dict(r) for r in db.execute(
            "SELECT shot_id,status FROM cloud_tasks WHERE project_id=? AND status NOT IN ('downloaded','failed','rejected')", (pid,))]
    episode = project.get("episode") or project.get("script") or {}
    runtime = {s["id"]: s for s in project.get("shots", [])}
    rows, errors = [], []
    try:
        root = project_root(project)
    except (ValueError, OSError) as exc:
        root = None
        errors.append({"error": str(exc)[:300]})
    for shot in episode.get("shots", [])[:80]:
        sid = shot["id"]
        shot_definition(project, sid)
        versions = []
        count = 0
        for stage, prefix in STAGES.items():
            if root is None:
                continue
            folder = core.within(root, prefix + "/" + sid)
            takes = sorted((p for p in folder.glob("*") if TAKE_RE.fullmatch(p.name)), key=lambda p: p.name, reverse=True)
            count += len(takes)
            for take in takes[:MAX_CANDIDATES]:
                try:
                    item = candidate(project, sid, stage, take.name)
                    item["cost"] = costs.get((sid, take.name)) if stage == "video" else None
                    selected = project.get("shot_selections", {}).get(sid, {}).get(stage)
                    item["producer_selected"] = bool(selected and selected["take"] == take.name)
                    versions.append(item)
                except (ValueError, OSError, KeyError) as exc:
                    errors.append({"shot_id": sid, "stage": stage, "take": take.name, "error": str(exc)[:300]})
        rows.append({**copy.deepcopy(shot), "status": runtime.get(sid, {}).get("status", "planned"),
                     "operation_impact": operation_impact(project, sid, unresolved),
                     "shot_revision": shot.get("shot_revision", 1), "candidates": versions,
                     "asset_references": project.get("series_references", {}).get(sid, []) + material_references(project, shot),
                     "truncated": count > len(versions),
                     "selections": project.get("shot_selections", {}).get(sid, {})})
    return {"project_id": pid, "revision": project["revision"], "production_revision": project.get("production_revision", 1),
            "shots": rows, "errors": errors, "truncated": len(episode.get("shots", [])) > 80,
            "editable": not running and project["status"] in {"paused", "blocked", "awaiting_final", "approved"},
            "execution_busy": running}


def operation_impact(project, shot_id, unresolved=()):
    """Read-only preview of the same dependency rules used by shot_edits.

    A preview is bound to the project revision and never grants permission to
    mutate, submit, or reuse an old candidate. All server write guards still run.
    """
    from .series_bindings import dependent_closure
    episode = project.get('episode') or project.get('script') or {}
    order = [s['id'] for s in episode.get('shots', [])]
    result = {}
    for operation in ('edit_image', 'edit_video', 'select_image', 'select_video'):
        image = operation.endswith('_image')
        affected = dependent_closure(project, [shot_id]) if image else [shot_id]
        stages = [{'shot_id': sid, 'stages': (['video'] if operation == 'select_image' and sid == shot_id
                                            or not image else ['image', 'video'])} for sid in affected]
        result[operation] = {'source_revision': project['revision'], 'affected': stages,
            'preserved_shot_ids': [sid for sid in order if sid not in affected],
            'unresolved_tasks': [r for r in unresolved if r['shot_id'] in affected],
            'original_candidates_preserved': True, 'generation_submitted': False}
    return result


def pinned_selection(project, target):
    pin = project.get("shot_selections", {}).get(target["key"], {}).get(target["kind"])
    if not pin:
        return None
    from .scheduler import ProductionNeedsDecision
    shot = target.get("shot") or shot_definition(project, target["key"])
    from .production import _matching_source
    if pin["plan_signature"] != plan_signature(project, shot, target["kind"]) or not _matching_source(pin["receipt"], target):
        raise ProductionNeedsDecision("人工选片的参数已变化，请重新选片或明确重制", "manual_selection_stale")
    result = copy.deepcopy(pin["receipt"])
    result["producer_selection"] = {k: v for k, v in pin.items() if k != "receipt"}
    return result
