#!/usr/bin/env python3
"""Deploy an isolated, persistent UI preview with a real API and no worker."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "runtime/cami-preview"
UNIT = "ai-studio-preview.service"


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs)


def checked_path(path):
    if any(c.isspace() or c in '%"\\' for c in str(path)):
        raise ValueError("Preview paths must not contain whitespace or systemd escapes")
    return path


def health(port):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=2) as response:
        return json.load(response)


def configuration(port):
    path = WORKSPACE / "config.json"
    if path.exists():
        value = json.loads(path.read_text())
    else:
        value = json.loads((ROOT / "config/factory.example.json").read_text())
        # Example endpoints belong to other installations. Never probe them here.
        for profile in [value["llm"], *value.get("llm_profiles", {}).values(),
                        *value.get("video_profiles", {}).values()]:
            profile["enabled"] = False
        value["production"]["gpu_cards"] = []
    value["web"] = {"host": "127.0.0.1", "port": port}
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)
    return path


def deploy(port):
    checked_path(ROOT)
    python = checked_path(ROOT / ".venv/bin/python")
    if not python.is_file():
        raise RuntimeError("Install the project's .venv before starting the preview")
    run("git", "diff", "--quiet", "HEAD", cwd=ROOT)
    if not (ROOT / "ui/factory/dist/index.html").is_file():
        raise RuntimeError("Build the UI with npm run build first")
    commit = run("git", "rev-parse", "HEAD", cwd=ROOT).stdout.strip()
    WORKSPACE.mkdir(parents=True, exist_ok=True, mode=0o700)
    WORKSPACE.chmod(0o700)
    unit_path = Path.home() / ".config/systemd/user" / UNIT
    previous_unit = unit_path.read_text() if unit_path.exists() else None
    if previous_unit and f"{WORKSPACE}" not in previous_unit:
        raise RuntimeError("An unrelated service owns the preview unit name")
    previous_path = WORKSPACE / "deployed.json"
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    if previous.get("port") != port:
        # Fail without changing any running service if another app owns the port.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    release = WORKSPACE / "releases" / commit
    if not release.exists():
        release.mkdir(parents=True)
        try:
            with tempfile.TemporaryDirectory(prefix="studio-release-") as temporary:
                archive = Path(temporary) / "release.tar"
                run("git", "archive", "--format=tar", "-o", str(archive), commit, cwd=ROOT)
                with tarfile.open(archive) as package:
                    package.extractall(release, filter="data")
            shutil.copytree(ROOT / "ui/factory/dist", release / "ui/factory/dist")
            if previous.get("release"):
                prior = Path(previous["release"]).resolve()
                if not prior.is_relative_to((WORKSPACE / "releases").resolve()):
                    raise RuntimeError("Invalid previous preview release")
                if (prior / "production").exists():
                    shutil.copytree(prior / "production", release / "production")
        except BaseException:
            shutil.rmtree(release)
            raise
    config_path = WORKSPACE / "config.json"
    previous_config = config_path.read_text() if config_path.exists() else None
    configuration(port)
    environment = {**os.environ, "DRAMA_FACTORY_CONFIG": str(config_path),
                   "DRAMA_FACTORY_DATA": str(WORKSPACE / "data")}
    seeded = run(str(python), str(release / "scripts/seed_preview.py"),
                 "--workspace", str(WORKSPACE), env=environment, cwd=release)
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(f"""[Unit]
Description=AI Studio isolated preview ({WORKSPACE})
After=network.target

[Service]
Type=simple
WorkingDirectory={release}
ExecStart={python} -u -m factory.web
Environment=DRAMA_FACTORY_CONFIG={config_path}
Environment=DRAMA_FACTORY_DATA={WORKSPACE / 'data'}
Environment=DRAMA_COMFY_HOME={WORKSPACE / 'no-comfy'}
Environment=DRAMA_GPU_LOCK_DIR={WORKSPACE / 'gpu-locks'}
Environment=LANGSMITH_TRACING=false
Environment=PYTHONUNBUFFERED=1
UMask=0077
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
""")
    try:
        run("systemctl", "--user", "daemon-reload")
        run("systemctl", "--user", "enable", UNIT)
        run("systemctl", "--user", "restart", UNIT)
        deadline = time.monotonic() + 15
        while True:
            try:
                observed = health(port)
                if observed.get("root") == str(release):
                    break
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("Preview health check failed; inspect journalctl --user -u " + UNIT)
            time.sleep(.2)
    except BaseException:
        if previous_unit:
            if previous_config is not None:
                config_path.write_text(previous_config)
            unit_path.write_text(previous_unit)
            run("systemctl", "--user", "daemon-reload")
            run("systemctl", "--user", "restart", UNIT)
        else:
            run("systemctl", "--user", "disable", "--now", UNIT)
            unit_path.unlink(missing_ok=True)
            run("systemctl", "--user", "daemon-reload")
        raise
    result = {"commit": commit, "release": str(release), "port": port,
              "url": f"http://127.0.0.1:{port}/", "worker": "disabled",
              "samples": json.loads(seeded.stdout)}
    previous_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "status", "stop"])
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be within 1024..65535")
    if args.action == "start":
        result = deploy(args.port)
    elif args.action == "stop":
        run("systemctl", "--user", "disable", "--now", UNIT)
        result = {"service": UNIT, "status": "stopped", "data": str(WORKSPACE)}
    else:
        state = subprocess.run(["systemctl", "--user", "is-active", UNIT],
                               capture_output=True, text=True)
        path = WORKSPACE / "deployed.json"
        result = json.loads(path.read_text()) if path.exists() else {}
        result["status"] = state.stdout.strip()
        if result["status"] == "active":
            result["health"] = health(result["port"])
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
