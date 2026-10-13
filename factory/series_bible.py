"""Versioned series facts on the project's existing SQLite connection."""
from __future__ import annotations

import copy
import json
import re
import time

from . import store
from studio.core import fingerprint

ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"
KINDS = {"character": "characters", "scene": "scenes", "prop": "props"}


def initialize(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS series_bibles(
      series_key TEXT NOT NULL, version INTEGER NOT NULL, document TEXT NOT NULL,
      sha256 TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(series_key,version));
    CREATE TABLE IF NOT EXISTS series_assets(
      asset_id TEXT NOT NULL, version INTEGER NOT NULL, series_key TEXT NOT NULL,
      kind TEXT NOT NULL, entity_id TEXT NOT NULL, status TEXT NOT NULL,
      revision INTEGER NOT NULL, data TEXT NOT NULL, created REAL NOT NULL,
      PRIMARY KEY(asset_id,version));
    CREATE INDEX IF NOT EXISTS series_assets_series ON series_assets(series_key,created);
    CREATE TABLE IF NOT EXISTS series_dependencies(
      project_id TEXT NOT NULL REFERENCES projects(id), shot_id TEXT NOT NULL,
      asset_id TEXT NOT NULL, version INTEGER NOT NULL, sha256 TEXT NOT NULL, role TEXT NOT NULL,
      PRIMARY KEY(project_id,shot_id,asset_id),
      FOREIGN KEY(asset_id,version) REFERENCES series_assets(asset_id,version));
    CREATE INDEX IF NOT EXISTS series_dependency_asset ON series_dependencies(asset_id,version);
    """)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(ID_PATTERN, value):
        raise ValueError("系列实体 ID 应为字母开头的 1–64 位字母、数字、下划线或连字符")
    return value


def integer(value, label, minimum=0):
    if type(value) is not int or not minimum <= value <= 1000000000:
        raise ValueError(label + " 必须是有效整数")
    return value


def project_cas(db, pid, expected):
    integer(expected, "expected_revision", 1)
    project = store._read(db, pid)
    if project["revision"] != expected:
        raise ValueError("项目已更新，请刷新后提交")
    return project


def require_series(project):
    key = project.get("series_key")
    if not isinstance(key, str) or not key.strip() or len(key) > 100:
        raise ValueError("请先为项目填写 series_key")
    return key


def validate_document(document):
    from jsonschema import Draft202012Validator
    from .schemas import series_bible_schema
    errors = list(Draft202012Validator(series_bible_schema()).iter_errors(document))
    if errors:
        error = errors[0]
        raise ValueError("Series Bible 字段不符合约束：" + ".".join(map(str, error.absolute_path)))
    for plural in KINDS.values():
        key = "character_id" if plural == "characters" else "entity_id"
        ids = [row[key] for row in document[plural]]
        if len(ids) != len(set(ids)):
            raise ValueError("Series Bible 包含重复实体 ID")
    return copy.deepcopy(document)


def bible(db, series_key, version=None):
    if version is None:
        row = db.execute("SELECT * FROM series_bibles WHERE series_key=? ORDER BY version DESC LIMIT 1",
                         (series_key,)).fetchone()
    else:
        integer(version, "Bible version", 1)
        row = db.execute("SELECT * FROM series_bibles WHERE series_key=? AND version=?", (series_key, version)).fetchone()
    return {**dict(row), "document": json.loads(row["document"])} if row else None


def save_bible(pid, payload):
    if set(payload) - {"expected_revision", "expected_version", "document"}:
        raise ValueError("未知 Bible 字段")
    expected = integer(payload.get("expected_version"), "expected_version")
    document = validate_document(payload.get("document"))
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        project = project_cas(db, pid, payload.get("expected_revision"))
        key = require_series(project)
        prior = bible(db, key)
        if (prior["version"] if prior else 0) != expected:
            raise ValueError("系列设定已更新，请刷新版本")
        version = expected + 1
        value = {"series_key": key, "version": version, "document": document,
                 "sha256": fingerprint(document), "created": time.time()}
        db.execute("INSERT INTO series_bibles VALUES(?,?,?,?,?)",
                   (key, version, store.encoded(document), value["sha256"], value["created"]))
        # A published series revision never silently changes an existing script.
        if not (project.get("episode") or project.get("script")):
            project["series_bible"] = value
            store._write(db, project)
        store._event(db, pid, "已追加系列设定版本；已有剧集的固定引用不自动变更。", "series_bible",
                     {"series_key": key, "version": version, "sha256": value["sha256"]})
        return value


def asset(db, asset_id, version):
    identifier(asset_id)
    integer(version, "asset version", 1)
    row = db.execute("SELECT * FROM series_assets WHERE asset_id=? AND version=?", (asset_id, version)).fetchone()
    if not row:
        raise KeyError("系列资产版本不存在")
    return {**dict(row), "data": json.loads(row["data"])}


def asset_cas(db, project, payload):
    value = asset(db, payload.get("asset_id"), payload.get("version"))
    if value["series_key"] != require_series(project):
        raise ValueError("不能修改其他系列的资产")
    if value["revision"] != integer(payload.get("expected_asset_revision"), "expected_asset_revision", 1):
        raise ValueError("资产状态已更新，请刷新后提交")
    if value["status"] == "approved":
        raise ValueError("已批准的资产版本不可覆盖；请新建版本")
    return value


def save_asset(db, value, status):
    value["data"].setdefault("history", []).append({"from": value["status"], "to": status,
                                                  "actor": "producer", "at": time.time()})
    value.update(status=status, revision=value["revision"] + 1)
    db.execute("UPDATE series_assets SET status=?,revision=?,data=? WHERE asset_id=? AND version=?",
               (status, value["revision"], store.encoded(value["data"]), value["asset_id"], value["version"]))
    return value


def summary(pid):
    from .series_assets import source_inventory
    from .series_bindings import affected_shots
    with store.connection() as db:
        project = store._read(db, pid)
        key = project.get("series_key")
        values = []
        if key:
            rows = db.execute("SELECT asset_id,version FROM series_assets WHERE series_key=? ORDER BY created DESC LIMIT 257", (key,)).fetchall()
            values = [asset(db, row["asset_id"], row["version"]) for row in rows[:256]]
            for value in values:
                value["affected_shots"] = affected_shots(db, value["asset_id"], value["version"])
        return {"project_id": pid, "revision": project["revision"], "series_key": key,
                "bible": bible(db, key) if key else None, "pinned_bible": project.get("series_bible"),
                "assets": values, "truncated": bool(key and len(rows) > 256),
                "sources": source_inventory(project), "bindings": project.get("series_references", {})}


def mutate(pid, payload):
    action = payload.get("action")
    arguments = {key: value for key, value in payload.items() if key != "action"}
    if action == "save_bible":
        return save_bible(pid, arguments)
    if action == "bind":
        from .series_bindings import bind_asset
        return bind_asset(pid, arguments)
    if action in {"plan", "register", "review", "approve"}:
        from .series_assets import mutate_asset
        return mutate_asset(pid, action, arguments)
    raise ValueError("不支持的系列资产操作")
