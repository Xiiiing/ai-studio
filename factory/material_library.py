"""Two-level, immutable material versions on the existing control database."""
from __future__ import annotations

import base64
import copy
import io
import json
import math
import time
import uuid

from PIL import Image
from studio import core
from . import store
from .series_bible import identifier, integer, project_cas
from .series_assets import MAX_REFERENCE_BYTES, _immutable_reference

KINDS = {"character", "scene", "style", "composition"}


def initialize(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS material_assets(
      asset_id TEXT PRIMARY KEY, library_key TEXT NOT NULL, kind TEXT NOT NULL,
      latest_version INTEGER NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS material_library ON material_assets(library_key,kind);
    CREATE TABLE IF NOT EXISTS material_versions(
      asset_id TEXT NOT NULL REFERENCES material_assets(asset_id), version INTEGER NOT NULL,
      status TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL,
      PRIMARY KEY(asset_id,version));
    CREATE TABLE IF NOT EXISTS material_packages(
      library_key TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL,
      PRIMARY KEY(library_key,version));
    CREATE TABLE IF NOT EXISTS material_dependencies(
      project_id TEXT NOT NULL REFERENCES projects(id), shot_id TEXT NOT NULL,
      asset_id TEXT NOT NULL, version INTEGER NOT NULL, role TEXT NOT NULL,
      PRIMARY KEY(project_id,shot_id,asset_id,role),
      FOREIGN KEY(asset_id,version) REFERENCES material_versions(asset_id,version));
    """)


def library_key(project):
    return "series:" + project["series_key"] if project.get("series_key") else "project:" + project["id"]


def get(db, aid, version=None):
    identifier(aid)
    head = db.execute("SELECT * FROM material_assets WHERE asset_id=?", (aid,)).fetchone()
    if not head:
        raise KeyError("素材不存在")
    version = head["latest_version"] if version is None else integer(version, "素材版本", 1)
    row = db.execute("SELECT * FROM material_versions WHERE asset_id=? AND version=?", (aid, version)).fetchone()
    if not row:
        raise KeyError("素材版本不存在")
    return {**dict(head), **dict(row), "data": json.loads(row["data"])}


def accessible(value, project=None):
    if value["library_key"] != "global" and (not project or value["library_key"] != library_key(project)):
        raise ValueError("不能访问其他剧的私有素材")
    return value


def image_path(receipt):
    path = core.within(core.ROOT / "production/series-assets", core.ROOT / receipt["path"])
    if not path.is_file() or path.stat().st_size != receipt["bytes"] or core.sha256(path) != receipt["sha256"]:
        raise ValueError("素材文件缺失或校验失败")
    return path


def validate_document(document, kind):
    if not isinstance(document, dict) or set(document) - {"name", "description", "identity_constraints", "default_wardrobe", "visual_rules", "palette", "members", "scene", "states"}:
        raise ValueError("素材设定包含未知字段")
    result = copy.deepcopy(document)
    if not isinstance(result.get("name"), str) or not 1 <= len(result["name"].strip()) <= 100:
        raise ValueError("请填写1–100字的素材名称")
    for field in ("description", "identity_constraints", "default_wardrobe", "visual_rules", "states"):
        if field in result and (not isinstance(result[field], str) or len(result[field]) > 4000):
            raise ValueError("素材描述须为至多4000字的文本")
    palette = result.get("palette", [])
    if not isinstance(palette, list) or len(palette) > 12 or any(not isinstance(c, str) or len(c) > 50 for c in palette):
        raise ValueError("色彩设定无效")
    members = result.get("members", [])
    if not isinstance(members, list) or len(members) > 4 or (members and kind != "composition"):
        raise ValueError("组合参考支持至多4个人物")
    roles = set()
    for member in members:
        if not isinstance(member, dict) or set(member) - {"asset_id", "version", "role", "box"}:
            raise ValueError("同框人物对应字段无效")
        identifier(member.get("asset_id")); integer(member.get("version"), "人物版本", 1)
        role = identifier(member.get("role"))
        if role in roles:
            raise ValueError("同框人物角色不能重复")
        roles.add(role)
        if "box" in member:
            box = member["box"]
            if (not isinstance(box, list) or len(box) != 4 or any(type(n) not in (int, float) or not math.isfinite(n) for n in box)
                    or not 0 <= box[0] < box[2] <= 1 or not 0 <= box[1] < box[3] <= 1):
                raise ValueError("人物区域须为归一化的左上、右下坐标")
    if "scene" in result:
        scene = result["scene"]
        if not isinstance(scene, dict) or set(scene) != {"asset_id", "version"}:
            raise ValueError("场景引用须指定素材和版本")
        identifier(scene["asset_id"]); integer(scene["version"], "场景版本", 1)
    return result


def _validate_links(db, document, project):
    for link in document.get("members", []) + ([document["scene"]] if document.get("scene") else []):
        value = accessible(get(db, link["asset_id"], link["version"]), project)
        expected = "character" if "role" in link else "scene"
        if value["kind"] != expected:
            raise ValueError("组合中的人物或场景类型不匹配")


def _upload(value, encoded, label):
    if not isinstance(encoded, str) or len(encoded) > (MAX_REFERENCE_BYTES + 2) // 3 * 4:
        raise ValueError("参考图不能超过8 MiB")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise ValueError("图片编码无效") from None
    if not 0 < len(content) <= MAX_REFERENCE_BYTES:
        raise ValueError("参考图不能超过8 MiB")
    with Image.open(io.BytesIO(content)) as image:
        if image.format not in {"PNG", "JPEG", "WEBP"} or min(image.size) < 64 or max(image.size) > 4096:
            raise ValueError("仅支持64–4096像素的PNG/JPEG/WebP")
        extension = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[image.format]
        image.verify()
    # One independently addressed immutable file per uploaded view.
    file_value = {"asset_id": value["asset_id"] + "_" + uuid.uuid4().hex[:8], "version": value["version"]}
    return {**_immutable_reference(file_value, content, extension), "label": str(label)[:100] or "参考图"}


def _append(db, value, data, status="candidate"):
    version = value["latest_version"] + 1
    db.execute("INSERT INTO material_versions VALUES(?,?,?,?,?)", (value["asset_id"], version, status, store.encoded(data), time.time()))
    db.execute("UPDATE material_assets SET latest_version=? WHERE asset_id=?", (version, value["asset_id"]))
    return get(db, value["asset_id"], version)


def mutate(payload):
    if not isinstance(payload, dict):
        raise ValueError("素材操作须为对象")
    action = payload.get("action")
    allowed = {"action", "project_id", "asset_id", "expected_version", "kind", "document", "scope", "source", "image_base64", "label", "confirmed", "task_id"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("素材操作包含未知字段")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        project = store._read(db, payload["project_id"]) if payload.get("project_id") else None
        if action in {"create", "clone"}:
            key = "global" if payload.get("scope", "global") == "global" else library_key(project) if project else None
            if not key:
                raise ValueError("创建剧内素材需要项目")
            source = accessible(get(db, payload["asset_id"], payload.get("expected_version")), project) if action == "clone" else None
            kind = source["kind"] if source else payload.get("kind")
            if kind not in KINDS:
                raise ValueError("素材类型无效")
            document = validate_document(payload.get("document", source["data"]["document"] if source else None), kind)
            _validate_links(db, document, None if key == "global" else project)
            aid = "mat_" + uuid.uuid4().hex
            db.execute("INSERT INTO material_assets VALUES(?,?,?,0,0)", (aid, key, kind))
            data = {"document": document, "images": copy.deepcopy(source["data"].get("images", [])) if source else []}
            if source:
                data["source"] = {"asset_id": source["asset_id"], "version": source["version"]}
            return _append(db, get_head(db, aid), data)
        value = accessible(get(db, payload.get("asset_id")), project)
        if value["latest_version"] != integer(payload.get("expected_version"), "expected_version", 1):
            raise ValueError("素材已更新，请刷新版本")
        data = copy.deepcopy(value["data"])
        if project and value["library_key"] == "global" and action in {"revise", "upload", "archive"}:
            raise ValueError("请先复制为剧内造型，避免修改统一库")
        if action == "revise":
            data["document"] = validate_document(payload.get("document"), value["kind"])
            _validate_links(db, data["document"], None if value["library_key"] == "global" else project)
            data.pop("review", None); data.pop("approval", None)
            return _append(db, value, data)
        if action == "upload":
            if len(data["images"]) >= 16:
                raise ValueError("每个素材版本最多保存16张参考图，请建立独立造型")
            data["images"].append(_upload(value, payload.get("image_base64"), payload.get("label", "参考图")))
            data.pop("review", None); data.pop("approval", None)
            return _append(db, value, data)
        if action == "approve":
            if payload.get("confirmed") is not True:
                raise ValueError("请明确确认正式参考")
            approve_version(db, value)
            return get(db, value["asset_id"], value["version"])
        if action == "archive":
            db.execute("UPDATE material_assets SET archived=1 WHERE asset_id=?", (value["asset_id"],))
            return get(db, value["asset_id"])
        raise ValueError("不支持的素材操作")


def get_head(db, aid):
    return dict(db.execute("SELECT * FROM material_assets WHERE asset_id=?", (aid,)).fetchone())


def approve_version(db, value):
    from .quality import inspect_image
    data = copy.deepcopy(value["data"])
    if not data["images"]:
        raise ValueError("请先上传或生成参考图")
    technical = [inspect_image(image_path(receipt)) for receipt in data["images"]]
    if any(result["status"] != "pass" for result in technical):
        raise ValueError("参考图技术验证失败")
    if data.get("review", {}).get("status") == "fail":
        raise ValueError("视觉审核未通过，请修改或重新生成候选")
    data["approval"] = {"actor": "producer", "at": time.time(), "technical": technical,
                        "visual": data.get("review", {"status": "human_confirmed", "scope": "producer_visual_review"})}
    db.execute("UPDATE material_versions SET status='approved',data=? WHERE asset_id=? AND version=? AND status!='approved'",
               (store.encoded(data), value["asset_id"], value["version"]))


def inventory(project_id=None, query="", kind=None, offset=0):
    if not isinstance(query, str) or len(query) > 200 or kind and kind not in KINDS:
        raise ValueError("素材搜索参数无效")
    integer(offset, "offset")
    with store.connection() as db:
        project = store._read(db, project_id) if project_id else None
        keys = ["global"] + ([library_key(project)] if project else [])
        rows = db.execute("SELECT a.asset_id FROM material_assets a JOIN material_versions v ON a.asset_id=v.asset_id AND a.latest_version=v.version "
                          "WHERE a.archived=0 AND a.library_key IN (" + ",".join("?" for _ in keys) + ") "
                          "AND (? IS NULL OR a.kind=?) AND instr(lower(json_extract(v.data,'$.document.name') || ' ' || coalesce(json_extract(v.data,'$.document.description'),'')),lower(?))>0 "
                          "ORDER BY v.created DESC LIMIT 51 OFFSET ?", (*keys, kind, kind, query, offset)).fetchall()
        values = [get(db, row[0]) for row in rows[:50]]
        for value in values:
            value["affected_shots"] = impact(db, value["asset_id"], value["version"])
        return {"assets": values, "has_more": len(rows) > 50, "offset": offset, "library_key": library_key(project) if project else "global"}


def impact(db, aid, version):
    from .series_bindings import dependent_closure
    projects = {}
    for row in db.execute("SELECT project_id,shot_id FROM material_dependencies WHERE asset_id=? AND version<>?", (aid, version)):
        projects.setdefault(row[0], []).append(row[1])
    return [{"project_id": pid, "shot_ids": dependent_closure(store._read(db, pid), ids)} for pid, ids in projects.items()]


def reference(db, project, ref, approved=False):
    if not isinstance(ref, dict) or set(ref) - {"asset_id", "version", "role", "shot_ids"}:
        raise ValueError("素材引用字段无效")
    value = accessible(get(db, ref.get("asset_id"), ref.get("version")), project)
    if value["archived"] and not approved:
        raise ValueError("归档素材不能新增引用")
    if approved and value["status"] != "approved":
        raise ValueError("请先确认全部素材版本")
    for receipt in value["data"]["images"]:
        image_path(receipt)
    role = identifier(ref.get("role", value["asset_id"]))
    result = {"asset_id": value["asset_id"], "version": value["version"], "role": role, "kind": value["kind"],
              "sha256": core.fingerprint({"document": value["data"]["document"], "images": value["data"]["images"]})}
    if "shot_ids" in ref:
        ids = ref["shot_ids"]
        if not isinstance(ids, list) or len(ids) > 80 or any(not isinstance(s, str) for s in ids) or len(set(ids)) != len(ids):
            raise ValueError("镜头选择无效")
        result["shot_ids"] = ids
    return result, value


def snapshot(pid):
    with store.connection() as db:
        project = store._read(db, pid)
        selected = []
        for ref in project.get("material_selection", []):
            selected.append({"binding": ref, "asset": accessible(get(db, ref["asset_id"], ref["version"]), project)})
        return {"revision": project["revision"], "library_key": library_key(project), "selected": selected,
                "package": project.get("material_package"), "shots": (project.get("episode") or project.get("script") or {}).get("shots", [])}


def require_editable(db, project):
    if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='running'", (project["id"],)).fetchone():
        raise ValueError("请暂停制作并等待当前步骤结束")
    if project["status"] not in {"queued", "paused", "blocked", "awaiting_story", "awaiting_style", "awaiting_final", "approved"}:
        raise ValueError("请先暂停制作")


def mutate_project(pid, payload):
    if not isinstance(payload, dict) or set(payload) - {"action", "expected_revision", "references", "confirmed"}:
        raise ValueError("项目素材操作字段无效")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        project = project_cas(db, pid, payload.get("expected_revision"))
        require_editable(db, project)
        if payload.get("action") == "select":
            refs = payload.get("references")
            if not isinstance(refs, list) or len(refs) > 64:
                raise ValueError("请指定至多64个素材引用")
            result = [reference(db, project, ref)[0] for ref in refs]
            identities = [(ref["kind"], ref["role"]) for ref in result]
            if len(set(identities)) != len(identities) or sum(ref["kind"] == "style" for ref in result) > 1:
                raise ValueError("同一角色/场景只能选一个造型，项目只能选一种画风")
            from .production_profiles import roles
            if any(ref["kind"] == "character" and ref["role"] not in roles(project) for ref in result):
                raise ValueError("人物须关联项目已定义的稳定角色")
            project["material_selection"] = result
        elif payload.get("action") == "confirm":
            if payload.get("confirmed") is not True:
                raise ValueError("请明确确认本批人物、场景和画风")
            refs = project.get("material_selection", [])
            if not refs:
                raise ValueError("请先选择本剧素材")
            for ref in refs:
                value = accessible(get(db, ref["asset_id"], ref["version"]), project)
                approve_version(db, value)
            references = [reference(db, project, {k: v for k, v in ref.items() if k in {"asset_id", "version", "role", "shot_ids"}}, True)[0] for ref in refs]
            package = {"references": references, "confirmed_at": time.time(), "signature": core.fingerprint(references)}
            previous = project.get("material_package", {})
            project["material_package"] = package
            project["material_selection"] = references
            if previous.get("signature") != package["signature"]:
                rebind(db, project, previous)
            key = library_key(project)
            latest = db.execute("SELECT max(version) FROM material_packages WHERE library_key=?", (key,)).fetchone()[0] or 0
            db.execute("INSERT INTO material_packages VALUES(?,?,?)", (key, latest + 1, store.encoded(package)))
        else:
            raise ValueError("不支持的项目素材操作")
        store._write(db, project)
        store._event(db, pid, "本剧素材选择已保存；定稿版本用于后续制作。", "materials")
        return project


def inherit_package(db, project):
    row = db.execute("SELECT data FROM material_packages WHERE library_key=? ORDER BY version DESC LIMIT 1", (library_key(project),)).fetchone()
    if row:
        package = json.loads(row[0])
        project["material_package"] = package
        project["material_selection"] = copy.deepcopy(package["references"])


def shot_references(project, shot):
    from .series_bindings import shot_roles
    episode = project.get("episode") or project.get("script") or {}
    roles = shot_roles(shot, episode.get("cast", {}))
    refs = []
    for ref in project.get("material_package", {}).get("references", []):
        if "shot_ids" in ref and shot["id"] not in ref["shot_ids"]:
            continue
        if ref["kind"] == "character" and ref["role"] not in roles:
            continue
        if ref["kind"] == "scene" and "shot_ids" not in ref and shot.get("scene_id") != ref["role"]:
            continue
        if ref["kind"] == "composition" and "shot_ids" not in ref and shot.get("composition_id") != ref["role"]:
            continue
        refs.append(ref)
    return refs


def rebind(db, project, previous=None):
    from .series_bindings import dependent_closure
    from .production_edits import mark_visual_edit, require_resolved_cloud
    shots = (project.get("episode") or project.get("script") or {}).get("shots", [])
    before = {shot["id"]: shot_references({**project, "material_package": previous or {}}, shot) for shot in shots}
    affected = [shot["id"] for shot in shots if before[shot["id"]] != shot_references(project, shot)]
    if affected:
        affected = dependent_closure(project, affected)
        require_resolved_cloud(db, project["id"], affected)
        mark_visual_edit(db, project, affected, "material_reference", {"previous": previous, "package": project["material_package"]})
    record_dependencies(db, project)


def record_dependencies(db, project):
    db.execute("DELETE FROM material_dependencies WHERE project_id=?", (project["id"],))
    for shot in (project.get("episode") or project.get("script") or {}).get("shots", []):
        for ref in shot_references(project, shot):
            db.execute("INSERT INTO material_dependencies VALUES(?,?,?,?,?)", (project["id"], shot["id"], ref["asset_id"], ref["version"], ref["role"]))


def context(project):
    refs = project.get("material_package", {}).get("references", [])
    if not refs:
        return None
    with store.connection() as db:
        return [{**ref, "document": reference(db, project, {k: v for k, v in ref.items() if k in {"asset_id", "version", "role", "shot_ids"}}, True)[1]["data"]["document"]} for ref in refs]
