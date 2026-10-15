#!/usr/bin/env python3
"""Install only the Wkp GPU API, reusing existing server-side model credentials."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import time
import urllib.request

ROOT = Path("/root/jialin/drama_studio")
UNIT = "ai-studio-inference.service"


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=60)


def credential_directives(unit_text, required):
    # Read mount references, never the credential files or literal environment.
    bindings = {}
    for line in unit_text.splitlines():
        if line.startswith("LoadCredential="):
            values = shlex.split(line.split("=", 1)[1])
            if not values:
                bindings.clear()
            for value in values:
                bindings[value.split(":", 1)[0]] = value
    if not set(required) <= bindings.keys():
        raise RuntimeError("Existing model service lacks required credential bindings")
    return "".join("LoadCredential=" + shlex.quote(bindings[key]) + "\n" for key in sorted(required))


def install():
    if os.geteuid() != 0 or Path.cwd().resolve() != ROOT:
        raise RuntimeError("Install through authorized Wkp management in its source checkout")
    config = json.loads((ROOT / "config/factory.json").read_text())
    required = {value["api_key_credential"] for value in config.get("llm_profiles", {}).values()
                if value.get("api_key_credential")}
    references = credential_directives(run("systemctl", "cat", "drama-factory-worker.service").stdout, required)
    path = Path("/etc/systemd/system") / UNIT
    previous = path.read_text() if path.exists() else None
    marker = "AI-STUDIO-INFERENCE:" + str(ROOT)
    if previous and marker not in previous:
        raise RuntimeError("Another installation owns the inference service unit")
    if not previous:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 8791))
    for job in (ROOT / "runtime/inference").glob("*/job.json"):
        if json.loads(job.read_text()).get("status") in ("queued", "running"):
            raise RuntimeError("Inference work is active; keep its process running")
    old_active = subprocess.run(["systemctl", "is-active", "--quiet", UNIT]).returncode == 0
    old_enabled = subprocess.run(["systemctl", "is-enabled", "--quiet", UNIT]).returncode == 0
    path.write_text(f"""# {marker}
[Unit]
Description=AI Studio model and GPU inference API only
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={ROOT}
ExecStart={ROOT}/envs/factory/bin/python -m factory.gpu_service --host 127.0.0.1 --port 8791
Environment=DRAMA_FACTORY_CONFIG={ROOT}/config/factory.json
{references}UMask=0077
NoNewPrivileges=true
PrivateTmp=true
KillMode=control-group
TimeoutStopSec=45
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
""")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", UNIT)
        run("systemctl", "restart", UNIT)
        deadline = time.monotonic() + 20
        while True:
            try:
                with opener.open("http://127.0.0.1:8791/health", timeout=2) as response:
                    observed = json.load(response)
                if observed.get("service") == "gpu_inference" and observed.get("workflow_execution") is False:
                    break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Inference API did not become healthy")
            time.sleep(.25)
        run("systemctl", "is-active", UNIT)
    except BaseException:
        if previous:
            path.write_text(previous)
        else:
            subprocess.run(["systemctl", "disable", "--now", UNIT], capture_output=True)
            path.unlink(missing_ok=True)
        run("systemctl", "daemon-reload")
        if previous:
            run("systemctl", "enable" if old_enabled else "disable", UNIT)
            run("systemctl", "restart" if old_active else "stop", UNIT)
        raise
    return {"unit": UNIT, "health": observed, "credential_ids": sorted(required),
            "credential_values_exported": False}


if __name__ == "__main__":
    print(json.dumps(install(), ensure_ascii=False, indent=2))
