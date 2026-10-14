"""Explicit maintenance-window snapshots and data-only restore into a new directory."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import sqlite3
import subprocess
import uuid

from . import backup_files as files
from . import backup_sqlite as sql
from .backup_audit import inspect, inspect_copies
from .backup_files import BackupError
from .backup_manifest import MANIFEST, SCHEMA, VERSION, verify


def now():
    return datetime.now(timezone.utc).isoformat()


def new_destination(value, forbidden=()):
    target = files.checked_root(value)
    if target.exists(): raise BackupError("Destination already exists; it will not be overwritten")
    if not target.parent.is_dir(): raise BackupError("Destination parent must already exist")
    for root in forbidden:
        root = files.checked_root(root)
        if target.is_relative_to(root) or root.is_relative_to(target):
            raise BackupError("Destination must be outside the source/production/backup tree")
    return target


def source_version(root):
    if not (Path(root) / ".git").exists():
        return {"git_commit": "unknown", "git_dirty": "unknown"}
    try:
        def git(*args):
            return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True, timeout=5).stdout.strip()
        return {"git_commit": git("rev-parse", "HEAD"), "git_dirty": bool(git("status", "--porcelain"))}
    except (OSError, subprocess.SubprocessError):
        return {"git_commit": "unknown", "git_dirty": "unknown"}


def create(root, destination, *, maintenance_confirmed=False, data_dir=None):
    """The operator must quiesce ALL web/worker/CLI/filesystem writers first.

    This function does not stop them. SQLite reservations block further SQL
    writers, but cannot atomically snapshot a filesystem or finish an old node.
    """
    if maintenance_confirmed is not True:
        raise BackupError("Explicit maintenance window confirmation is required; no services will be stopped")
    root = files.checked_root(root)
    if not root.is_dir(): raise BackupError("Source root does not exist")
    data = files.checked_root(data_dir if data_dir is not None else root / sql.DATA_PATH)
    destination = new_destination(destination, (root, data))
    snapshot_id = uuid.uuid4().hex
    captured_at = now()
    with sql.fenced(data) as readers:
        media, excluded = files.inventory(root)
        before = inspect(root, readers, media)
        version = source_version(root)
        # The snapshot destination is created only after admission/preflight.
        destination.mkdir(mode=0o700)
        for name in sql.NAMES:
            sql.copy_database(readers[name], destination / sql.DATA_PATH / name)
        for item in media:
            files.copy_verified(root, destination, item)
        final_media, final_excluded = files.inventory(root)
        if media != final_media or excluded != final_excluded:
            raise BackupError("Production files changed during maintenance-window backup")
        copied_media, _ = files.inventory(destination, exclude_private=False)
        if copied_media != media:
            raise BackupError("Copied production files failed destination read-back verification")
        copied = inspect_copies(destination, media)
        for key in ("projects", "checkpoint_threads", "unresolved_cloud_tasks", "checked_references", "queued_or_running_jobs"):
            if copied[key] != before[key]: raise BackupError("Copied database evidence differs: " + key)
        for name in sql.NAMES:
            if copied["schemas"][name]["schema_sha256"] != before["schemas"][name]["schema_sha256"]:
                raise BackupError("SQLite backup changed database structure")
            if copied["schemas"][name]["content_sha256"] != before["schemas"][name]["content_sha256"]:
                raise BackupError("SQLite backup content differs from its fenced source")
        database_files = [files.describe(destination, sql.DATA_PATH + "/" + name) for name in sql.NAMES]
        all_files = sorted(media + database_files, key=lambda item: item["path"])
        if len(all_files) > files.MAX_FILES or sum(v["bytes"] for v in all_files) > files.MAX_BYTES:
            raise BackupError("Complete snapshot exceeds backup limits")
    # Both reservations have been checked and released. These copies are now
    # self-contained; restarting a source writer cannot change the saved bytes.
    databases = [{"path": item["path"], "snapshot_id": snapshot_id, "sha256": item["sha256"],
                  "schema": copied["schemas"][Path(item["path"]).name]} for item in database_files]
    manifest = {"schema": SCHEMA, "version": VERSION, "snapshot_id": snapshot_id, "scope": "complete_instance",
                "created_at": captured_at, "completed_at": now(), "source_root": str(root),
                "source_data_dir": str(data), "restored_data_dir": sql.DATA_PATH,
                "source_version": version, "backup_tool_version": source_version(Path(__file__).resolve().parents[1]),
                "databases": databases, "files": all_files,
                **{key: copied[key] for key in ("projects", "checkpoint_threads", "unresolved_cloud_tasks", "checked_references", "queued_or_running_jobs")},
                "sqlite_runtime_version": sqlite3.sqlite_version,
                "checkpoint_reader_version": importlib.metadata.version("langgraph-checkpoint-sqlite"),
                "consistency": {"operator_confirmed_quiescence": True, "both_database_writer_fences": True,
                                "sqlite_backup_api": True, "filesystem_atomic_snapshot": False,
                                "source_file_set_rehashed": True, "checkpoint_deserialization": "not_run"},
                "excluded": {"private_production_paths": excluded,
                             "categories": ["config/", "environment variables and credentials", "models/", "envs/", ".git/", "runtime process locks/heartbeats"],
                             "configuration_action": "Reconfigure paths and credential variables separately; no values were collected"},
                "restore_policy": "data_only_new_directory_no_worker_no_network_no_paid_submission"}
    if len(json.dumps(manifest, ensure_ascii=False, indent=2).encode()) + 1 > files.MAX_JSON:
        raise BackupError("Snapshot manifest exceeds the supported read/restore size bound")
    files.write_json_new(destination / MANIFEST, manifest)
    files.sync_directory(destination.parent)
    return manifest


def preflight(directory, destination):
    checked = verify(directory)
    manifest = checked["manifest"]
    target = new_destination(destination, (directory, manifest["source_root"], manifest["source_data_dir"]))
    return {**checked, "destination": str(target)}


def restore(directory, destination):
    checked = preflight(directory, destination)
    manifest = checked["manifest"]
    source = files.checked_root(directory)
    target = Path(checked["destination"])
    target.mkdir(mode=0o700)
    for item in manifest["files"]:
        files.copy_verified(source, target, item)
    for item in manifest["files"]:
        if files.describe(target, item["path"]) != item:
            raise BackupError("Copied restore file failed destination read-back verification")
    media, excluded = files.inventory(target, exclude_private=False)
    copied = inspect_copies(target, media)
    for key in ("projects", "checkpoint_threads", "unresolved_cloud_tasks", "checked_references"):
        if copied[key] != manifest[key]: raise BackupError("Restore audit differs from the original snapshot: " + key)
    report = {"schema": "ai-studio.restore.v1", "snapshot_id": manifest["snapshot_id"], "restored_at": now(),
              "verified": True, "generation_started": False, "automatic_resume": False,
              "checkpoint_payloads": "preserved_opaque_not_executed", "queued_or_running_jobs": manifest["queued_or_running_jobs"],
              "unresolved_cloud_tasks": manifest["unresolved_cloud_tasks"],
              "required_operator_actions": ["Validate path/configuration/model compatibility outside this tool",
                                            "Reconcile existing and unknown cloud tasks without resubmitting",
                                            "Do not connect a restored copy and its source to concurrent production workers"]}
    files.write_json_new(target / MANIFEST, manifest)
    files.write_json_new(target / "restore-report.json", report)
    files.sync_directory(target.parent)
    return report


def audit(root):
    result = verify(root)
    report = files.read_json(root, "restore-report.json")
    if report.get("schema") != "ai-studio.restore.v1" or report.get("snapshot_id") != result["manifest"]["snapshot_id"]:
        raise BackupError("Restore lacks a matching completion receipt")
    return {"verified": True, "snapshot_id": report["snapshot_id"], "generation_started": False, **result["audit"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create_parser = commands.add_parser("create", help="Requires an operator-arranged maintenance window")
    create_parser.add_argument("--root", type=Path, required=True)
    create_parser.add_argument("--data-dir", type=Path)
    create_parser.add_argument("--output", type=Path, required=True)
    create_parser.add_argument("--maintenance-window-confirmed", action="store_true")
    for command in ("verify", "preflight", "restore", "audit"):
        sub = commands.add_parser(command)
        sub.add_argument("directory", type=Path)
        if command in {"preflight", "restore"}: sub.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "create":
            result = create(args.root, args.output, maintenance_confirmed=args.maintenance_window_confirmed, data_dir=args.data_dir)
            result = {"status": "created", "snapshot_id": result["snapshot_id"], "files": len(result["files"]), "output": str(args.output)}
        elif args.command == "verify":
            verified = verify(args.directory)
            result = {"verified": True, "snapshot_id": verified["manifest"]["snapshot_id"]}
        elif args.command == "preflight":
            result = preflight(args.directory, args.destination)
            result = {"verified": result["verified"], "destination": result["destination"], "generation_started": False}
        elif args.command == "restore": result = restore(args.directory, args.destination)
        else: result = audit(args.directory)
    except (BackupError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "generation_started": False}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
