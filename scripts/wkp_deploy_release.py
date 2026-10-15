#!/usr/bin/env python3
"""Upgrade an idle Wkp checkout to an exact merged commit, preserving private state."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tarfile
import time
import urllib.request

UNITS = ["drama-factory-web.service", "drama-factory-worker.service"]


def run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True, timeout=60)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def active_jobs(root):
    with sqlite3.connect(f"file:{root / 'runtime/factory/studio.sqlite3'}?mode=ro", uri=True) as db:
        return db.execute("SELECT id,project_id,status FROM jobs WHERE status NOT IN ('completed','failed','cancelled')").fetchall()


def unpack(bundle, destination):
    with tarfile.open(bundle) as archive:
        members = archive.getmembers()
        if not members or len(members) > 1000 or sum(m.size for m in members) > 30 * 1024 * 1024:
            raise ValueError("Invalid UI bundle size")
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "dist":
                raise ValueError("UI bundle must contain only dist/")
            if not (member.isfile() or member.isdir()):
                raise ValueError("Links and special files are forbidden in UI bundle")
        archive.extractall(destination, filter="data")
    if not (destination / "dist/index.html").is_file():
        raise ValueError("UI bundle has no index.html")


def deploy(root, commit, bundle, checksum):
    root, bundle = root.resolve(), bundle.resolve()
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("Use exact Git and SHA256 identities")
    if bundle.stat().st_size > 30 * 1024 * 1024 or digest(bundle) != checksum:
        raise ValueError("UI bundle checksum mismatch")
    if run("git", "status", "--porcelain", cwd=root).stdout.strip():
        raise RuntimeError("Production checkout has local changes; preserve them before deploying")
    run("git", "merge-base", "--is-ancestor", commit, "origin/main", cwd=root)
    if active_jobs(root):
        raise RuntimeError("Queued, running or deferred jobs exist; wait for a safe boundary")
    previous = run("git", "rev-parse", "HEAD", cwd=root).stdout.strip()
    directories = run("systemctl", "show", "--property=WorkingDirectory", "--value", *UNITS).stdout.splitlines()
    directories = [value.strip() for value in directories if value.strip()]
    if directories != [str(root)] * len(UNITS):
        raise RuntimeError("Factory services belong to another checkout; no services changed")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = root / "runtime/deployments" / (stamp + "-" + commit[:8])
    backup.mkdir(parents=True, mode=0o700)
    backup.chmod(0o700)
    unpack(bundle, backup / "incoming")
    config = root / "config/factory.json"
    config_hash = digest(config)
    # Production credentials remain on Wkp; backup never leaves this host.
    private = backup / "config"
    private.mkdir(mode=0o700)
    for name in ("factory.json", "runtime.env"):
        source = root / "config" / name
        if source.exists():
            shutil.copy2(source, private / name)
            (private / name).chmod(0o600)
    tag = "deploy/wkp-before-" + stamp
    run("git", "tag", tag, previous, cwd=root)
    # Freeze HTTP submissions before stopping the idle worker. A project that
    # arrived during preflight must not be killed by an upgrade shutdown.
    worker_stop_attempted = False
    try:
        run("systemctl", "stop", UNITS[0])
        if active_jobs(root):
            raise RuntimeError("A task arrived during preflight; Web restored, worker left running")
        worker_stop_attempted = True
        run("systemctl", "stop", UNITS[1])
    except BaseException:
        # Failed reads and stop timeouts must not strand the operator's Web.
        # A busy queue is observed before attempting to stop the worker.
        if worker_stop_attempted:
            try:
                run("systemctl", "start", UNITS[1])
            finally:
                run("systemctl", "start", UNITS[0])
        else:
            run("systemctl", "start", UNITS[0])
        raise
    dist = root / "ui/factory/dist"
    try:
        if active_jobs(root):
            raise RuntimeError("A task arrived before shutdown; aborting without upgrading")
        for name in ("studio.sqlite3", "checkpoints.sqlite3"):
            source = root / "runtime/factory" / name
            if source.exists():
                with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src, sqlite3.connect(backup / name) as dst:
                    src.backup(dst)
                (backup / name).chmod(0o600)
        if dist.exists():
            shutil.copytree(dist, backup / "previous-dist")
        # The clean previous commit is pinned by a local deployment tag. No
        # configuration, database, checkpoint or production asset is reset.
        run("git", "reset", "--hard", commit, cwd=root)
        if dist.exists():
            shutil.rmtree(dist)
        shutil.move(str(backup / "incoming/dist"), str(dist))
        if digest(config) != config_hash:
            raise RuntimeError("Private configuration changed during deployment")
        run("systemctl", "start", *UNITS)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        port = json.loads(config.read_text())["web"]["port"]
        deadline = time.monotonic() + 20
        while True:
            try:
                with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=2) as response:
                    observed = json.load(response)
                if observed.get("root") != str(root):
                    raise RuntimeError("Health response belongs to another checkout")
                with opener.open(f"http://127.0.0.1:{port}/", timeout=2) as response:
                    if response.status == 200 and b"/factory/static/" in response.read():
                        break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Upgraded Web health/static UI check failed")
            time.sleep(.25)
        run("systemctl", "is-active", *UNITS)
    except BaseException:
        run("systemctl", "stop", *UNITS)
        run("git", "reset", "--hard", previous, cwd=root)
        if (backup / "previous-dist").exists():
            if dist.exists():
                shutil.rmtree(dist)
            shutil.copytree(backup / "previous-dist", dist)
        run("systemctl", "start", *UNITS)
        raise
    result = {"previous_commit": previous, "commit": commit, "rollback_tag": tag,
              "backup": str(backup), "ui_sha256": checksum, "private_config_preserved": True,
              "health": observed, "services": UNITS}
    (backup / "deployment.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/root/jialin/drama_studio"))
    parser.add_argument("--commit", required=True)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(deploy(args.root, args.commit, args.bundle, args.sha256), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
