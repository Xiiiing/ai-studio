"""Attested, immutable image references with distinct review and human approval."""
from __future__ import annotations

import copy
import hashlib
import io
import os
import tempfile
import time
import uuid
from pathlib import Path

from PIL import Image
from studio import core
from . import store
from .quality import inspect_image
from .series_bible import (KINDS, asset, asset_cas, bible, identifier, integer,
                           project_cas, require_series, save_asset)

MAX_REFERENCE_BYTES = 8 * 1024 * 1024


def source_inventory(project):
    """Only server-recorded media belonging to this project can be promoted."""
    values = []
    def add(key, receipt):
        if not isinstance(receipt, dict) or not isinstance(receipt.get("path"), str) or not receipt.get("sha256"):
            return
        try:
            path = core.within(core.ROOT / "production" / project["slug"], core.ROOT / receipt["path"])
        except (ValueError, KeyError):
            return
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            return
        values.append({"key": key, "receipt": copy.deepcopy(receipt), "url": "/media/" + receipt["path"]})
    for role, receipt in project.get("assets", {}).get("characters", {}).items():
        add("character:" + role, receipt)
    for shot in project.get("shots", []):
        add("shot:" + shot["id"], shot.get("image"))
        for attempt in shot.get("attempts", []):
            if attempt.get("stage") == "image":
                add("take:" + shot["id"] + ":" + str(attempt.get("take")), attempt.get("output"))
    for style in project.get("styles", []):
        add("style:" + str(style.get("id")), style.get("preview"))
    return list({value["key"]: value for value in values}.values())[:100]


def reference_path(value, *, require_approved=True):
    if require_approved and value["status"] != "approved":
        raise ValueError("只能复用已明确批准的系列资产")
    receipt = value["data"].get("receipt") or {}
    base = core.within(core.ROOT / "production/series-assets",
                       value["asset_id"] + "/v%04d" % value["version"])
    path = core.within(base, core.ROOT / receipt.get("path", ""))
    if not path.is_file() or path.stat().st_size != receipt.get("bytes") or core.sha256(path) != receipt.get("sha256"):
        raise ValueError("系列参考文件缺失或校验失败，拒绝批准和复用")
    return path


def _snapshot_source(project, source_key):
    source = next((value for value in source_inventory(project) if value["key"] == source_key), None)
    if not source:
        raise ValueError("请指定当前项目已登记的图片素材")
    base = core.within(core.ROOT / "production", project["slug"])
    owner = base / "factory_owner.json"
    if not owner.is_file() or core.read_json(owner).get("project_id") != project["id"]:
        raise ValueError("参考来源的工程归属不匹配")
    receipt = source["receipt"]
    path = core.within(base, core.ROOT / receipt["path"])
    with path.open("rb") as handle:
        content = handle.read(MAX_REFERENCE_BYTES + 1)
    if len(content) > MAX_REFERENCE_BYTES or len(content) != receipt.get("bytes") or hashlib.sha256(content).hexdigest() != receipt["sha256"]:
        raise ValueError("参考来源校验失败或超过8MiB")
    with Image.open(io.BytesIO(content)) as image:
        if image.format not in {"JPEG", "PNG", "WEBP"} or min(image.size) < 64 or max(image.size) > 4096:
            raise ValueError("系列参考图仅支持64–4096像素的PNG/JPEG/WebP")
        extension = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}[image.format]
        image.verify()
    return content, extension, receipt


def _immutable_reference(value, content, extension):
    folder = core.within(core.ROOT / "production/series-assets", value["asset_id"] + "/v%04d" % value["version"])
    folder.mkdir(parents=True, exist_ok=True)
    target = core.within(folder, "reference" + extension)
    digest = hashlib.sha256(content).hexdigest()
    fd, temporary = tempfile.mkstemp(prefix="reference-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)  # Atomic publication, never replaces an original.
        except FileExistsError:
            if not target.is_file() or core.sha256(target) != digest:
                raise ValueError("该资产版本已有不同文件，拒绝覆盖") from None
        directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return core.media_receipt(target)


def _plan(db, project, payload):
    if set(payload) - {"expected_revision", "asset_id", "expected_version", "kind", "entity_id"}:
        raise ValueError("未知资产计划字段")
    key = require_series(project)
    kind = payload.get("kind")
    if kind not in KINDS:
        raise ValueError("资产类型仅支持 character、scene、prop")
    entity_id = identifier(payload.get("entity_id"))
    document = bible(db, key)
    if not document:
        raise ValueError("请先建立正式的 Series Bible")
    field = "character_id" if kind == "character" else "entity_id"
    entity = next((row for row in document["document"][KINDS[kind]] if row[field] == entity_id), None)
    if not entity:
        raise ValueError("资产实体不在当前 Series Bible 中")
    expected = integer(payload.get("expected_version", 0), "expected_version")
    ident = identifier(payload["asset_id"]) if payload.get("asset_id") else "asset_" + uuid.uuid4().hex
    previous = db.execute("SELECT * FROM series_assets WHERE asset_id=? ORDER BY version DESC LIMIT 1", (ident,)).fetchone()
    if (previous["version"] if previous else 0) != expected:
        raise ValueError("资产最新版本已变化，请刷新")
    if previous and (previous["series_key"], previous["kind"], previous["entity_id"]) != (key, kind, entity_id):
        raise ValueError("稳定资产 ID 不能改换系列、类型或身份")
    data = {"entity": entity, "bible_version": document["version"], "history": [
        {"to": "planned", "actor": "producer", "at": time.time()}]}
    version = expected + 1
    db.execute("INSERT INTO series_assets VALUES(?,?,?,?,?,?,?,?,?)",
               (ident, version, key, kind, entity_id, "planned", 1, store.encoded(data), time.time()))
    return asset(db, ident, version)


def mutate_asset(pid, action, payload):
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        project = project_cas(db, pid, payload.get("expected_revision"))
        if action == "plan":
            result = _plan(db, project, payload)
        else:
            allowed = {"expected_revision", "asset_id", "version", "expected_asset_revision"}
            allowed |= {"source_key"} if action == "register" else {"confirmed"} if action == "approve" else set()
            if set(payload) - allowed:
                raise ValueError("未知资产状态字段")
            value = asset_cas(db, project, payload)
            if action == "register":
                if value["status"] != "planned":
                    raise ValueError("换图必须创建新版本，不能覆盖已有参考")
                content, extension, source = _snapshot_source(project, payload.get("source_key"))
                value["data"].update(receipt=_immutable_reference(value, content, extension),
                                     source_project_id=pid, source_receipt=source)
                result = save_asset(db, value, "generated")
            elif action == "review":
                if value["status"] not in {"generated", "reviewed"}:
                    raise ValueError("先登记实际参考图，再进行验证")
                path = reference_path(value, require_approved=False)
                value["data"]["review"] = {"technical": inspect_image(path), "reviewed_at": time.time()}
                result = save_asset(db, value, "reviewed")
            else:
                if payload.get("confirmed") is not True:
                    raise ValueError("正式复用需要出品人明确确认")
                if value["status"] != "reviewed" or value["data"].get("review", {}).get("technical", {}).get("status") != "pass":
                    raise ValueError("验证未通过，不能批准为正式参考")
                reference_path(value, require_approved=False)
                value["data"].update(approved_at=time.time(), approved_by="producer")
                result = save_asset(db, value, "approved")
        store._event(db, pid, "系列资产状态已记录，已批准版本和原素材保持不变。", "series_asset",
                     {key: result[key] for key in ("asset_id", "version", "status", "revision")})
        return result
