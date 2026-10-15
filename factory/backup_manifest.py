"""Versioned complete-instance manifest validation and read-only preflight."""
from __future__ import annotations

from pathlib import Path
import re

from . import backup_files as files
from . import backup_sqlite as sql
from .backup_audit import inspect_copies
from .backup_files import BackupError

SCHEMA = "ai-studio.consistent-backup"
VERSION = 1
MANIFEST = "backup-manifest.json"


def verify(directory):
    root = files.checked_root(directory)
    manifest = files.read_json(root, MANIFEST)
    if (not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA or
        type(manifest.get("version")) is not int or manifest["version"] != VERSION or
        manifest.get("scope") != "complete_instance"):
        raise BackupError("Unsupported backup format/version; legacy file copies are not trusted snapshots")
    for key in ("source_root", "source_data_dir"):
        value = manifest.get(key)
        if not isinstance(value, str) or not value or len(value) > 4096 or not Path(value).is_absolute():
            raise BackupError("Invalid original root metadata: " + key)
    boundary = manifest.get("consistency")
    if (not isinstance(boundary, dict) or any(boundary.get(key) is not True for key in (
            "operator_confirmed_quiescence", "both_database_writer_fences", "sqlite_backup_api", "source_file_set_rehashed")) or
            boundary.get("filesystem_atomic_snapshot") is not False or boundary.get("checkpoint_deserialization") != "not_run"):
        raise BackupError("Backup lacks its explicit consistency/maintenance boundary")
    if manifest.get("restored_data_dir") != sql.DATA_PATH or manifest.get("restore_policy") != "data_only_new_directory_no_worker_no_network_no_paid_submission":
        raise BackupError("Unsupported restore policy or database layout")
    # A completed snapshot is a self-contained DELETE-journal database. A stray
    # WAL could otherwise override hashed main-file pages during SQL inspection.
    for name in sql.NAMES:
        for suffix in ("-wal", "-shm", "-journal"):
            path = root / sql.DATA_PATH / (name + suffix)
            if path.exists() or path.is_symlink():
                raise BackupError("Completed snapshots must not contain SQLite sidecar files")
    snapshot = manifest.get("snapshot_id")
    if not isinstance(snapshot, str) or not re.fullmatch(r"[a-f0-9]{32}", snapshot): raise BackupError("Invalid snapshot ID")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not 2 <= len(entries) <= files.MAX_FILES:
        raise BackupError("Invalid bounded snapshot file list")
    db_paths = {sql.DATA_PATH + "/" + name for name in sql.NAMES}
    catalog, size = {}, 0
    for entry in entries:
        if not isinstance(entry, dict): raise BackupError("Invalid file receipt")
        name = files.relative(entry.get("path"))
        if (name not in db_paths and not name.startswith("production/")) or files.private_name(name):
            raise BackupError("Unexpected file category in backup")
        if name in catalog or type(entry.get("bytes")) is not int or entry["bytes"] < 0:
            raise BackupError("Duplicate or invalid backup file receipt")
        if not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]):
            raise BackupError("Invalid backup file checksum")
        size += entry["bytes"]
        if size > files.MAX_BYTES: raise BackupError("Backup exceeds total byte bound")
        if files.describe(root, name) != entry: raise BackupError("Backup checksum/size mismatch: " + name)
        catalog[name] = entry
    if not db_paths <= catalog.keys(): raise BackupError("Both database snapshots are mandatory")
    databases = manifest.get("databases")
    if (not isinstance(databases, list) or len(databases) != 2 or
        {v.get("path") for v in databases if isinstance(v, dict)} != db_paths or
        any(v.get("snapshot_id") != snapshot or v.get("sha256") != catalog[v["path"]]["sha256"] for v in databases)):
        raise BackupError("Database snapshots do not belong to the same backup/version pair")
    media, _ = files.inventory(root, exclude_private=False)
    if {v["path"] for v in media} != set(catalog) - db_paths:
        raise BackupError("Production file set differs from the snapshot manifest")
    result = inspect_copies(root, media)
    for value in databases:
        if value.get("schema") != result["schemas"][Path(value["path"]).name]:
            raise BackupError("Database schema version differs from its recorded snapshot")
    for key in ("projects", "checkpoint_threads", "unresolved_cloud_tasks", "checked_references"):
        if result[key] != manifest.get(key): raise BackupError("Database/project evidence differs from backup manifest: " + key)
    return {"verified": True, "manifest": manifest, "audit": result, "generation_started": False}
