"""Two explicit SQLite writer fences; backup each database through the Backup API."""
from __future__ import annotations

from contextlib import ExitStack, closing, contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import sqlite3
import time

from .backup_files import BackupError, checked_root, digest, sync_directory

NAMES = ("studio.sqlite3", "checkpoints.sqlite3")
DATA_PATH = "runtime/factory"
STUDIO = {
    "projects": {"id", "updated", "revision", "snapshot"},
    "events": {"id", "project_id", "at", "kind", "message", "data"},
    "jobs": {"id", "project_id", "status", "created", "started", "finished", "error", "next_poll_at", "phase", "lease"},
    "memories": {"key", "project_id", "value", "updated"},
    "idempotency": {"key", "project_id", "request_hash"},
    "cloud_batches": {"id", "project_id", "production_revision", "source_signature", "scope", "created"},
    "cloud_authorizations": {"id", "batch_id", "project_id", "source_revision", "limits_json", "actor", "created"},
    "cloud_tasks": {"intent_id", "project_id", "batch_id", "authorization_id", "shot_id", "attempt", "take", "provider", "model", "request_signature", "task_id", "file_id", "status", "estimated_cost_micros", "confirmed_cost_micros", "currency", "created", "updated", "next_poll_at", "phase", "poll_failures"},
    "cloud_media_receipts": {"intent_id", "media_kind", "request_id", "usage_json"},
    "cloud_cost_events": {"id", "intent_id", "amount_micros", "currency", "evidence", "actor", "created"},
    "series_bibles": {"series_key", "version", "document", "sha256", "created"},
    "series_assets": {"asset_id", "version", "series_key", "kind", "entity_id", "status", "revision", "data", "created"},
    "series_dependencies": {"project_id", "shot_id", "asset_id", "version", "sha256", "role"},
    "material_assets": {"asset_id", "library_key", "kind", "latest_version", "archived"},
    "material_versions": {"asset_id", "version", "status", "data", "created"},
    "material_packages": {"library_key", "version", "data"},
    "material_dependencies": {"project_id", "shot_id", "asset_id", "version", "role"},
    "material_tasks": {"id", "library_key", "signature", "request_id", "status", "lease", "data", "result", "error", "created", "updated"},
    "material_submissions": {"intent_id", "task_id", "request_signature", "status", "response", "receipt", "estimated_cost_micros", "currency", "created", "updated"},
    "factory_migrations": {"name", "applied"},
    "team_control": {"project_id", "revision", "enabled"},
    "team_work_items": {"project_id", "id", "ordinal", "generation", "kind", "scope", "owner", "department", "status", "priority", "hold_after", "data"},
    "team_unit_attempts": {"run_id", "project_id", "task_id", "generation", "attempt", "status", "data"},
    "team_instructions": {"id", "project_id", "request_id", "request_hash", "status", "created_at", "data"},
    "team_locks": {"project_id", "task_id", "generation", "signature", "data", "created_at"},
    "team_control_requests": {"project_id", "request_id", "request_hash", "result"},
    "prompt_editions": {"id", "prompt_key", "document", "created"},
    "prompt_defaults": {"prompt_key", "edition_id", "revision"},
    "prompt_operations": {"id", "signature", "result"},
    "prompt_trials": {"id", "project_id", "state", "document", "created"},
    "sqlite_sequence": {"name", "seq"},
}
CHECKPOINTS = {
    "checkpoints": {"thread_id", "checkpoint_ns", "checkpoint_id", "parent_checkpoint_id", "type", "checkpoint", "metadata"},
    "writes": {"thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx", "channel", "type", "value"},
}


def connect(path, mode="ro"):
    path = checked_root(path)
    if not path.is_file(): raise BackupError("Both existing SQLite databases are required: " + path.name)
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(path) + suffix).is_symlink(): raise BackupError("Linked SQLite sidecar")
    db = sqlite3.connect(path.as_uri() + "?mode=" + mode, uri=True, timeout=.5)
    db.row_factory = sqlite3.Row
    return db


def content_signature(db):
    """Hash all logical rows, including opaque blobs and authoritative cost data.

    Statements are streamed into a digest, never saved, printed or executed.
    This detects copy corruption beyond the small human-readable audit summary.
    """
    value = hashlib.sha256()
    for statement in db.iterdump():
        encoded = statement.encode("utf-8")
        value.update(len(encoded).to_bytes(8, "big")); value.update(encoded)
    return value.hexdigest()


def schema(db, name):
    if db.execute("PRAGMA user_version").fetchone()[0] != 0:
        raise BackupError("Unsupported SQLite user schema version: " + name)
    expected = dict(STUDIO if name == "studio.sqlite3" else CHECKPOINTS)
    entries = list(db.execute("SELECT type,name,sql FROM sqlite_master WHERE type IN ('table','view','trigger') ORDER BY name"))
    if name == "studio.sqlite3" and "cloud_media_receipts" not in {r["name"] for r in entries}:
        expected.pop("cloud_media_receipts", None)  # Backups predating the additive media table.
    if name == "studio.sqlite3":
        team_tables = {"team_control", "team_work_items", "team_unit_attempts"}
        if not team_tables & {r["name"] for r in entries}:
            for table in team_tables:
                expected.pop(table)
        control_tables = {"team_instructions", "team_locks", "team_control_requests"}
        if not control_tables & {r["name"] for r in entries}:
            for table in control_tables:
                expected.pop(table)
        prompt_tables = {"prompt_editions", "prompt_defaults", "prompt_operations", "prompt_trials"}
        if not prompt_tables & {r["name"] for r in entries}:
            for table in prompt_tables:
                expected.pop(table)
        for table in list(expected):
            if table.startswith("material_") and table not in {r["name"] for r in entries}:
                expected.pop(table)  # Existing backups predate the optional material library.
    if any(r["type"] != "table" for r in entries) or {r["name"] for r in entries} != set(expected):
        raise BackupError("Unsupported database schema/table set: " + name)
    for table, columns in expected.items():
        actual = {r["name"] for r in db.execute('PRAGMA table_info("' + table + '")')}
        supported = [columns, columns | {"task_path"}] if table == "writes" else [columns]
        if table == "jobs":
            supported.append(columns | {"kind"})  # Additive advice jobs; old backups remain valid.
        if actual not in supported: raise BackupError("Unsupported schema columns: " + table)
    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
        raise BackupError("SQLite integrity/foreign key check failed: " + name)
    migrations = []
    if name == "studio.sqlite3":
        migrations = [r[0] for r in db.execute("SELECT name FROM factory_migrations ORDER BY name")]
        if migrations != ["remote_phases_v1"]: raise BackupError("Unsupported factory migrations")
    return {"user_version": 0, "schema_version": db.execute("PRAGMA schema_version").fetchone()[0],
            "schema_sha256": digest([dict(r) for r in entries]), "content_sha256": content_signature(db), "migrations": migrations}


@contextmanager
def fenced(data):
    """Caller must independently stop all filesystem writers before entering."""
    data = checked_root(data)
    identities = {}
    with ExitStack() as stack:
        guard = data / "worker.lock"
        if guard.exists() or guard.is_symlink():
            try:
                descriptor = os.open(guard, os.O_RDONLY | os.O_NOFOLLOW)
                stack.callback(os.close, descriptor)
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise BackupError("Existing worker is active or worker lock is unsafe; no service was stopped") from exc
        for name in NAMES:
            path = data / name
            writer = stack.enter_context(closing(connect(path, "rw")))
            try: writer.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc: raise BackupError("Database is busy; maintenance window is not quiescent") from exc
            stack.callback(writer.rollback)
            info = path.stat(); identities[name] = (info.st_dev, info.st_ino)
        # Only read after BOTH writer reservations have succeeded. Backup on a
        # separate read connection avoids SQLITE_LOCKED on our own write handle.
        readers = {}
        for name in NAMES:
            db = stack.enter_context(closing(connect(data / name)))
            db.execute("BEGIN"); readers[name] = db
        yield readers
        for name, identity in identities.items():
            info = (data / name).stat()
            if identity != (info.st_dev, info.st_ino): raise BackupError("Database file identity changed during backup")


def copy_database(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600); os.close(descriptor)
    started = time.monotonic()
    def progress(status, remaining, total):
        if status in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or time.monotonic() - started > 300:
            raise BackupError("SQLite Backup API is busy or exceeded 300 seconds")
    with closing(sqlite3.connect(destination)) as target:
        source.backup(target, pages=256, progress=progress, sleep=.01)
        target.execute("PRAGMA journal_mode=DELETE")
    with destination.open("rb") as handle: os.fsync(handle.fileno())
    sync_directory(destination.parent)
