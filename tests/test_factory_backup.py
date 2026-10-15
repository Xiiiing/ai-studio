"""Temporary SQLite/opaque checkpoint/media tests; no production restore experiments."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from factory import store
from studio.core import sha256

PID = "012345abcdef"


@pytest.fixture
def instance(tmp_path, monkeypatch):
    from langgraph.checkpoint.sqlite import SqliteSaver
    root = tmp_path / "source"; root.mkdir()
    data = root / "runtime/factory"; data.mkdir(parents=True)
    monkeypatch.setattr(store, "DATA", data); monkeypatch.setattr(store, "DATABASE", data / "studio.sqlite3")
    store.initialize()
    folder = root / ("production/factory-" + PID); folder.mkdir(parents=True)
    media = folder / "clip.mp4"; media.write_bytes(b"synthetic opaque media for backup integrity only")
    receipt = {"path": media.relative_to(root).as_posix(), "sha256": sha256(media), "bytes": media.stat().st_size}
    project = {"id": PID, "slug": "factory-" + PID, "revision": 4, "production_revision": 2,
               "status": "paused", "shots": [{"id": "S01", "video": receipt}], "assets": {}}
    (folder / "factory_owner.json").write_text(json.dumps({"project_id": PID}))
    with store.connection() as db:
        db.execute("INSERT INTO projects VALUES(?,?,?,?)", (PID, 1, 4, json.dumps(project)))
        db.execute("INSERT INTO jobs(id,project_id,status,created,phase,lease) VALUES(?,?,?,?,?,?)", ("b" * 32, PID, "queued", 1, "remote_poll", 2))
        db.execute("INSERT INTO cloud_tasks(intent_id,project_id,shot_id,attempt,take,provider,model,request_signature,task_id,file_id,status,currency,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("a" * 64, PID, "S01", 1, "take", "minimax", "recorded-model", "c" * 64, "task-existing", "file-existing", "unknown", "CNY", 1, 1))
    with sqlite3.connect(data / "checkpoints.sqlite3") as db:
        SqliteSaver(db).setup()
        db.execute("INSERT INTO checkpoints(thread_id,checkpoint_id,type,checkpoint,metadata) VALUES(?,?,?,?,?)", (PID, "cp-one", "msgpack", b"opaque-do-not-deserialize", b"metadata"))
    (data / "worker.lock").touch()
    config = root / "config"; config.mkdir()
    (config / "factory.json").write_text('{"private_marker":"must-not-be-packed"}')
    (config / "runtime.env").write_text('SYNTHETIC_SECRET=never-read-this-test-marker\n')
    return root


def test_maintenance_confirmation_required_before_any_output(instance, tmp_path):
    from factory.backup import create, BackupError
    target = tmp_path / "backup"
    with pytest.raises(BackupError, match="maintenance"):
        create(instance, target)
    assert not target.exists()


def test_backup_restore_is_isolated_preserves_tasks_and_excludes_config(instance, tmp_path, monkeypatch):
    from factory import backup
    from factory import remote_tasks
    def forbidden(*args, **kwargs):
        raise AssertionError("Backup/restore must never recover, claim or execute a task")
    monkeypatch.setattr(remote_tasks, "recover_jobs", forbidden)
    monkeypatch.setattr(remote_tasks, "claim_job", forbidden)
    target = tmp_path / "backup"
    report = backup.create(instance, target, maintenance_confirmed=True)
    assert report["scope"] == "complete_instance"
    assert report["projects"][0]["id"] == PID
    assert report["unresolved_cloud_tasks"][0]["task_id"] == "task-existing"
    assert not (target / "config").exists()
    assert b"must-not-be-packed" not in (target / "backup-manifest.json").read_bytes()
    assert backup.verify(target)["verified"] is True
    destination = tmp_path / "restored"
    restored = backup.restore(target, destination)
    assert restored["generation_started"] is False
    assert backup.audit(destination)["verified"] is True
    for name in ("studio.sqlite3", "checkpoints.sqlite3"):
        with sqlite3.connect(target / "runtime/factory" / name) as before, sqlite3.connect(destination / "runtime/factory" / name) as after:
            assert list(before.iterdump()) == list(after.iterdump())
    with sqlite3.connect(destination / "runtime/factory/studio.sqlite3") as db:
        assert db.execute("SELECT task_id,file_id,status,request_signature FROM cloud_tasks").fetchone() == ("task-existing", "file-existing", "unknown", "c" * 64)
    assert not (destination / "config").exists()
    with pytest.raises(backup.BackupError, match="exists"):
        backup.restore(target, destination)


def test_wal_pages_are_in_the_snapshot_and_source_rows_unchanged(instance, tmp_path):
    from factory import backup
    path = instance / "runtime/factory/studio.sqlite3"
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("UPDATE cloud_tasks SET task_id='persisted-in-wal'"); writer.commit()
    try:
        assert Path(str(path) + "-wal").exists()
        before = list(writer.iterdump())
        backup.create(instance, tmp_path / "backup", maintenance_confirmed=True)
        assert list(writer.iterdump()) == before
        with sqlite3.connect(tmp_path / "backup/runtime/factory/studio.sqlite3") as db:
            assert db.execute("SELECT task_id FROM cloud_tasks").fetchone()[0] == "persisted-in-wal"
    finally:
        writer.close()


@pytest.mark.parametrize("defect", ["missing_media", "wrong_sha", "linked_media", "linked_directory", "checkpoint_other_project", "unknown_schema"])
def test_preexisting_inconsistency_prevents_completed_backup(instance, tmp_path, defect):
    from factory import backup
    media = instance / ("production/factory-" + PID + "/clip.mp4")
    if defect == "missing_media": media.unlink()
    elif defect == "wrong_sha": media.write_bytes(b"changed")
    elif defect == "linked_media":
        replacement = tmp_path / "other.mp4"; replacement.write_bytes(media.read_bytes())
        media.unlink(); media.symlink_to(replacement)
    elif defect == "linked_directory": (media.parent / "linked").symlink_to(tmp_path, target_is_directory=True)
    elif defect == "checkpoint_other_project":
        with sqlite3.connect(instance / "runtime/factory/checkpoints.sqlite3") as db: db.execute("UPDATE checkpoints SET thread_id='abcdef012345'")
    else:
        with sqlite3.connect(instance / "runtime/factory/studio.sqlite3") as db: db.execute("PRAGMA user_version=999")
    destination = tmp_path / "backup"
    with pytest.raises(backup.BackupError): backup.create(instance, destination, maintenance_confirmed=True)
    assert not (destination / "backup-manifest.json").exists()


def test_worker_lock_and_database_writer_are_not_overridden(instance, tmp_path):
    import fcntl
    from factory import backup
    with (instance / "runtime/factory/worker.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(backup.BackupError, match="worker"):
            backup.create(instance, tmp_path / "locked", maintenance_confirmed=True)
    with sqlite3.connect(instance / "runtime/factory/checkpoints.sqlite3") as writer:
        writer.execute("BEGIN IMMEDIATE")
        with pytest.raises(backup.BackupError, match="busy"):
            backup.create(instance, tmp_path / "busy", maintenance_confirmed=True)
    assert not (tmp_path / "locked").exists() and not (tmp_path / "busy").exists()


@pytest.mark.parametrize("defect", ["missing", "tampered", "unknown_version", "wrong_pair", "path_escape"])
def test_restore_preflight_fails_before_creating_target(instance, tmp_path, defect):
    from factory import backup
    directory = tmp_path / "backup"; backup.create(instance, directory, maintenance_confirmed=True)
    path = directory / "backup-manifest.json"; manifest = json.loads(path.read_text())
    if defect == "missing": (directory / manifest["files"][0]["path"]).unlink()
    elif defect == "tampered": (directory / manifest["files"][0]["path"]).write_bytes(b"wrong-hash")
    elif defect == "unknown_version": manifest["version"] = 999
    elif defect == "wrong_pair": manifest["databases"][0]["snapshot_id"] = "foreign-snapshot"
    else: manifest["files"][0]["path"] = "../private.txt"
    path.write_text(json.dumps(manifest))
    target = tmp_path / "restore"
    with pytest.raises(backup.BackupError): backup.restore(directory, target)
    assert not target.exists()


def test_approved_series_reference_is_included_and_checked(instance, tmp_path):
    from factory import backup
    folder = instance / "production/series-assets/asset_lead/v0001"; folder.mkdir(parents=True)
    reference = folder / "reference.png"; reference.write_bytes(b"synthetic shared approved reference")
    receipt = {"path": reference.relative_to(instance).as_posix(), "sha256": sha256(reference), "bytes": reference.stat().st_size}
    with sqlite3.connect(instance / "runtime/factory/studio.sqlite3") as db:
        db.execute("INSERT INTO series_assets VALUES(?,?,?,?,?,?,?,?,?)", ("asset_lead", 1, "series", "character", "lead", "approved", 1, json.dumps({"receipt": receipt}), 1))
        db.execute("INSERT INTO series_dependencies VALUES(?,?,?,?,?,?)", (PID, "S01", "asset_lead", 1, receipt["sha256"], "lead"))
    backup.create(instance, tmp_path / "backup", maintenance_confirmed=True)
    backup.restore(tmp_path / "backup", tmp_path / "restore")
    assert sha256(tmp_path / "restore" / receipt["path"]) == receipt["sha256"]


def test_copy_interruption_does_not_publish_a_restorable_manifest(instance, tmp_path, monkeypatch):
    from factory import backup, backup_files
    def interrupted(*args, **kwargs):
        raise backup.BackupError("Synthetic copy interruption")
    monkeypatch.setattr(backup_files, "copy_verified", interrupted)
    destination = tmp_path / "backup"
    with pytest.raises(backup.BackupError, match="interruption"):
        backup.create(instance, destination, maintenance_confirmed=True)
    assert not (destination / "backup-manifest.json").exists()
    with pytest.raises(backup.BackupError): backup.verify(destination)


def test_existing_or_source_nested_backup_target_is_preserved(instance, tmp_path):
    from factory import backup
    existing = tmp_path / "existing"; existing.mkdir(); (existing / "keep").write_text("preserved")
    for path in (existing, instance / "backup"):
        with pytest.raises(backup.BackupError): backup.create(instance, path, maintenance_confirmed=True)
    assert (existing / "keep").read_text() == "preserved"
