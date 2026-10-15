"""Failure, concurrency and manifest-boundary tests for offline data-only restore."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from test_factory_backup import instance, PID


@pytest.mark.parametrize("name", ["studio.sqlite3-wal", "checkpoints.sqlite3-shm", "studio.sqlite3-journal"])
def test_completed_backup_rejects_unmanifested_database_sidecars(instance, tmp_path, name):
    from factory import backup
    target = tmp_path / "backup"; backup.create(instance, target, maintenance_confirmed=True)
    (target / "runtime/factory" / name).write_bytes(b"")
    with pytest.raises(backup.BackupError, match="sidecar"):
        backup.verify(target)


@pytest.mark.parametrize("key,value", [("source_root", []), ("source_data_dir", None), ("consistency", {}), ("databases", [None, {}])])
def test_malformed_metadata_is_a_structured_preflight_failure(instance, tmp_path, key, value):
    from factory import backup
    target = tmp_path / "backup"; backup.create(instance, target, maintenance_confirmed=True)
    path = target / "backup-manifest.json"; data = json.loads(path.read_text()); data[key] = value
    path.write_text(json.dumps(data))
    with pytest.raises(backup.BackupError): backup.preflight(target, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()


def test_filesystem_change_during_backup_never_publishes_complete_manifest(instance, tmp_path, monkeypatch):
    from factory import backup, backup_files
    original = backup_files.copy_verified
    def changed(source, target, item):
        original(source, target, item)
        if item["path"].endswith("clip.mp4"):
            (Path(source) / item["path"]).write_bytes(b"changed after copying")
    monkeypatch.setattr(backup_files, "copy_verified", changed)
    target = tmp_path / "backup"
    with pytest.raises(backup.BackupError, match="changed"):
        backup.create(instance, target, maintenance_confirmed=True)
    assert not (target / "backup-manifest.json").exists()


def test_both_databases_block_other_writers_through_media_copy(instance, tmp_path, monkeypatch):
    from factory import backup, backup_files
    original = backup_files.copy_verified
    checked = set()
    def check(source, target, item):
        for name in ("studio.sqlite3", "checkpoints.sqlite3"):
            with sqlite3.connect(instance / "runtime/factory" / name, timeout=.01) as writer:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    writer.execute("BEGIN IMMEDIATE")
            checked.add(name)
        original(source, target, item)
    monkeypatch.setattr(backup_files, "copy_verified", check)
    backup.create(instance, tmp_path / "backup", maintenance_confirmed=True)
    assert len(checked) == 2


def test_checkpoint_wal_pending_writes_remain_opaque_and_identical(instance, tmp_path):
    from factory import backup
    source = sqlite3.connect(instance / "runtime/factory/checkpoints.sqlite3")
    source.execute("PRAGMA journal_mode=WAL")
    source.execute("INSERT INTO writes(thread_id,checkpoint_id,task_id,idx,channel,type,value) VALUES(?,?,?,?,?,?,?)",
                   (PID, "cp-one", "pending-task", 0, "channel", "msgpack", b"not-a-deserializer-input")); source.commit()
    try:
        backup.create(instance, tmp_path / "backup", maintenance_confirmed=True)
        backup.restore(tmp_path / "backup", tmp_path / "restored")
        with sqlite3.connect(tmp_path / "restored/runtime/factory/checkpoints.sqlite3") as db:
            assert db.execute("SELECT value FROM writes").fetchone()[0] == b"not-a-deserializer-input"
    finally: source.close()


def test_external_data_path_is_relocated_without_editing_task_payloads(instance, tmp_path):
    from factory import backup
    source = instance / "runtime/factory"; external = tmp_path / "external-data"; source.rename(external)
    backup.create(instance, tmp_path / "backup", maintenance_confirmed=True, data_dir=external)
    backup.restore(tmp_path / "backup", tmp_path / "relocated")
    assert backup.audit(tmp_path / "relocated")["verified"]
    assert not source.exists()


def test_private_files_not_packed_or_values_reported(instance, tmp_path):
    from factory import backup
    secret = instance / ("production/factory-" + PID + "/.env")
    secret.write_text("SYNTHETIC_PRIVATE_VALUE=do-not-export-this")
    result = backup.create(instance, tmp_path / "backup", maintenance_confirmed=True)
    assert not (tmp_path / "backup" / secret.relative_to(instance)).exists()
    assert secret.relative_to(instance).as_posix() in result["excluded"]["private_production_paths"]
    assert "do-not-export-this" not in json.dumps(result)


def test_legacy_database_copy_is_not_a_versioned_snapshot(instance):
    from factory import backup
    with pytest.raises(backup.BackupError): backup.verify(instance)


def test_cli_roundtrip_remains_offline_and_preserves_existing_data(instance, tmp_path):
    prefix = [sys.executable, "-m", "factory.backup"]
    backup = tmp_path / "backup"; restore = tmp_path / "restore"
    commands = [
        ["create", "--root", str(instance), "--output", str(backup), "--maintenance-window-confirmed"],
        ["verify", str(backup)], ["preflight", str(backup), "--destination", str(restore)],
        ["restore", str(backup), "--destination", str(restore)], ["audit", str(restore)],
    ]
    for args in commands:
        result = subprocess.run(prefix + args, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr + result.stdout
        assert isinstance(json.loads(result.stdout), dict)
    result = subprocess.run(prefix + commands[3], capture_output=True, text=True, timeout=30)
    assert result.returncode == 2 and json.loads(result.stdout)["status"] == "blocked"


def test_real_langgraph_interrupt_checkpoint_survives_data_only_restore(instance, tmp_path):
    from langgraph.graph import StateGraph, START, END
    from langgraph.types import interrupt
    from langgraph.checkpoint.sqlite import SqliteSaver
    from factory import backup
    def first(state):
        return {**state, "completed_step": True}
    def review(state):
        interrupt({"decision": "human_review_required"})
        return state
    def graph(saver):
        builder = StateGraph(dict); builder.add_node("first", first); builder.add_node("review", review)
        builder.add_edge(START, "first"); builder.add_edge("first", "review"); builder.add_edge("review", END)
        return builder.compile(checkpointer=saver)
    settings = {"configurable": {"thread_id": PID}}
    with sqlite3.connect(instance / "runtime/factory/checkpoints.sqlite3", check_same_thread=False) as db:
        db.execute("DELETE FROM checkpoints"); db.commit()
        app = graph(SqliteSaver(db))
        app.invoke({"project_id": PID}, settings)
        before = app.get_state(settings)
        assert before.values["completed_step"] and before.tasks[0].interrupts
    backup.create(instance, tmp_path / "backup", maintenance_confirmed=True)
    backup.restore(tmp_path / "backup", tmp_path / "restore")
    with sqlite3.connect(tmp_path / "restore/runtime/factory/checkpoints.sqlite3", check_same_thread=False) as db:
        after = graph(SqliteSaver(db)).get_state(settings)  # Test-owned payload only; no graph invocation.
        assert after.values == before.values and after.next == before.next
        assert after.tasks[0].interrupts[0].value == {"decision": "human_review_required"}


@pytest.mark.parametrize("operation", ["backup", "restore"])
def test_unreferenced_historical_file_copy_is_verified_before_completion(instance, tmp_path, monkeypatch, operation):
    from factory import backup, backup_files
    name = "production/factory-" + PID + "/historical-cache.txt"
    (instance / name).write_text("Historical but unselected data still needs an exact copy")
    source = instance
    if operation == "restore":
        source = tmp_path / "backup"
        backup.create(instance, source, maintenance_confirmed=True)
    original = backup_files.copy_verified
    def corrupt(source, destination, item):
        original(source, destination, item)
        if item["path"] == name: (Path(destination) / name).write_text("corrupted copied file")
    monkeypatch.setattr(backup_files, "copy_verified", corrupt)
    target = tmp_path / "target"
    with pytest.raises(backup.BackupError, match="copied|Copied"):
        if operation == "backup": backup.create(instance, target, maintenance_confirmed=True)
        else: backup.restore(source, target)
    assert not (target / "backup-manifest.json").exists()


def test_every_ledger_column_is_preserved_not_only_task_status(instance, tmp_path, monkeypatch):
    from factory import backup, backup_sqlite
    original = backup_sqlite.copy_database
    def corrupt(source, destination):
        original(source, destination)
        if destination.name == "studio.sqlite3":
            with sqlite3.connect(destination) as db:
                db.execute("UPDATE cloud_tasks SET confirmed_cost_micros=999")
    monkeypatch.setattr(backup_sqlite, "copy_database", corrupt)
    with pytest.raises(backup.BackupError, match="content|Content"):
        backup.create(instance, tmp_path / "backup", maintenance_confirmed=True)
    assert not (tmp_path / "backup/backup-manifest.json").exists()
