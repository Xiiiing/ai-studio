#!/usr/bin/env python3
"""Prepare and control cami's isolated, complete studio; Wkp supplies inference APIs."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import fcntl
import json
import os
from pathlib import Path
import socket
import sqlite3
import ssl
import stat
import subprocess
import tempfile
import time
import urllib.request
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORKSPACE = ROOT / "runtime/cami-studio"
TLS_AREA = ROOT / "runtime/wkp-live-gateway"
UNITS = ("ai-studio-web.service", "ai-studio-worker.service")
WKP = "https://172.20.114.10:8790/inference"
WEB_PORT = 8787
TLS_ENV = {"CAMI_WKP_TLS_CA": "wkp-server-certificate.pem", "CAMI_WKP_TLS_CERT": "client-certificate.pem",
           "CAMI_WKP_TLS_KEY": "client-private-key.pem"}


class DeployError(RuntimeError):
    pass


def unit_directory():
    return Path.home() / ".config/systemd/user"


def marker():
    return f"# AI-STUDIO-CAMI-CONTROL:{ROOT}:{WORKSPACE}"


def guarded(path, *, private=False, directory=False):
    """Reject links or foreign owners before reading/writing deployment-owned paths."""
    path = Path(path)
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise DeployError("A deployment path contains a symbolic link")
    if path.exists():
        details = path.stat()
        kind = stat.S_ISDIR(details.st_mode) if directory else stat.S_ISREG(details.st_mode)
        if not kind or details.st_uid != os.getuid():
            raise DeployError("A deployment path has an unexpected owner or file type")
        if private and details.st_mode & 0o077:
            raise DeployError("Private cami files/directories require owner-only permissions")
    return path


def checked_root():
    if not ROOT.is_absolute() or any(ch.isspace() or ch in '%"\\\n\r' for ch in str(ROOT)):
        raise DeployError("The project path is not suitable for systemd environment files")
    guarded(ROOT, directory=True)
    guarded(ROOT / "runtime", directory=True)


def private_directory(path):
    guarded(path, private=True, directory=True)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    guarded(path, private=True, directory=True)


def atomic_write(path, content, mode=0o600):
    guarded(path)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        guarded(path)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def check_units():
    guarded(unit_directory(), directory=True)
    paths = {name: guarded(unit_directory() / name) for name in UNITS}
    for path in paths.values():
        if path.exists() and not path.read_text().startswith(marker() + "\n"):
            raise DeployError("An unrelated service owns a cami unit name; it has been preserved")
    return paths


def command(*args, check=True):
    try:
        result = subprocess.run(["systemctl", "--user", *args], text=True, capture_output=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired):
        raise DeployError("The user service manager is unavailable or timed out") from None
    if check and result.returncode:
        raise DeployError("The user service manager could not complete " + args[0])
    return result


def service_state(name):
    result = command("show", name, "--property=LoadState,FragmentPath,ActiveState,UnitFileState,MainPID", check=False)
    if result.returncode or not result.stdout.strip():
        raise DeployError("Cannot inspect user services; check the current user's systemd session")
    state = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    fragment = state.get("FragmentPath", "")
    if fragment and Path(fragment) != unit_directory() / name:
        raise DeployError("A differently installed service owns a cami unit name")
    if state.get("LoadState") != "not-found" and not (unit_directory() / name).is_file():
        raise DeployError("An existing cami-named service has no verifiable owned unit file")
    return {"pid": int(state.get("MainPID") or 0), "active": state.get("ActiveState") == "active", "active_state": state.get("ActiveState", "unknown"),
            "enabled": state.get("UnitFileState", "disabled"), "load_state": state.get("LoadState", "not-found")}


def tls_paths():
    guarded(TLS_AREA, private=True, directory=True)
    result = {env: guarded(TLS_AREA / filename, private=env.endswith("_KEY")) for env, filename in TLS_ENV.items()}
    for path in result.values():
        if not path.is_file() or not 1 <= path.stat().st_size <= 1024 * 1024:
            raise DeployError("Prepare the existing cami TLS identity before starting the control plane")
    try:
        context = ssl.create_default_context(cafile=str(result["CAMI_WKP_TLS_CA"]))
        context.load_cert_chain(result["CAMI_WKP_TLS_CERT"], result["CAMI_WKP_TLS_KEY"], password=lambda: "")
    except (OSError, ValueError):
        raise DeployError("The existing cami TLS CA or client certificate/key is invalid") from None
    return result


def preflight():
    checked_root()
    check_units()
    python = ROOT / ".venv/bin/python"
    # uv/venv Python executables are intentionally allowed to be symlinks.
    if not python.is_file() or not os.access(python, os.X_OK):
        raise DeployError("Install the project's .venv before starting the control plane")
    index = guarded(ROOT / "ui/factory/dist/index.html")
    if not index.is_file():
        raise DeployError("Build the production UI with npm run build first")
    return tls_paths()


def new_configuration(paths):
    """Only the public example is a source. No old private configuration or data is read."""
    value = json.loads((ROOT / "config/factory.example.json").read_text())
    profiles = {}
    models = {"primary": ("qwen3.8-27b-q8-opt-2dflash", 32768, False),
              "critic": ("ai-studio-qwen38-23", 16384, False),
              "vision": ("ai-studio-qwen38-vision", 16384, True)}
    for name, (model, context, vision) in models.items():
        template = value["llm_profiles"]["critic_local" if name == "critic" else "writer_local"]
        profile = copy.deepcopy(template)
        for key in ("api_key_env", "api_key_credential"):
            profile.pop(key, None)
        profile.update(provider="llama_cpp", enabled=True, network_mode="lan", allowed_hosts=["172.20.114.10"],
                       base_url=WKP + "/models/" + name + "/v1", model=model, context_tokens=context, vision=vision,
                       response_format="llama_json_schema", context_validation="llama_tokenize",
                       tls_ca_file_env="CAMI_WKP_TLS_CA", tls_client_cert_env="CAMI_WKP_TLS_CERT",
                       tls_client_key_env="CAMI_WKP_TLS_KEY")
        profiles[name] = profile
    value["llm_profiles"] = profiles
    value["llm"] = copy.deepcopy(profiles["primary"])
    value["llm_roles"] = {role: "critic" if role in {"independent_critic", "story_critic"} else "primary"
                          for role in value["llm_roles"]}
    value["llm_roles"].update(executive_producer="primary", visual_critic="vision")
    value["web"] = {"host": "127.0.0.1", "port": WEB_PORT}
    value["production"]["gpu_cards"] = [4, 5, 6, 7]
    value["production"]["gpu_service"] = {"base_url": WKP, "ca_file": str(paths["CAMI_WKP_TLS_CA"]),
                                          "client_cert": str(paths["CAMI_WKP_TLS_CERT"]),
                                          "client_key": str(paths["CAMI_WKP_TLS_KEY"])}
    # The original Wkp H3 chain is a ComfyUI graph, not the MiniMax cloud API.
    from factory.video_providers import H3_PRODUCTION_PROFILE
    value["video_profiles"] = {"wkp_h3": copy.deepcopy(H3_PRODUCTION_PROFILE)}
    value["video_roles"] = {"video_generator": "wkp_h3"}
    value["production"].update(video_profile="h3_production", video_width=768, video_height=1344, h3_reserve_vram_gib=12)
    return value


def environment(paths):
    return {"DRAMA_FACTORY_CONFIG": str(WORKSPACE / "config.json"), "DRAMA_FACTORY_DATA": str(WORKSPACE / "data"),
            "DRAMA_MEDIA_PYTHON": str(ROOT / ".venv/bin/python"),
            **{name: str(path) for name, path in paths.items()}, "LANGSMITH_TRACING": "false", "PYTHONUNBUFFERED": "1"}


def prepare_files(paths):
    private_directory(WORKSPACE / "data")
    config_path = guarded(WORKSPACE / "config.json", private=True)
    if config_path.exists():
        current = json.loads(config_path.read_text())
        production = current.get("production", {})
        expected_gpu = {"base_url": WKP, "ca_file": str(paths["CAMI_WKP_TLS_CA"]),
                        "client_cert": str(paths["CAMI_WKP_TLS_CERT"]), "client_key": str(paths["CAMI_WKP_TLS_KEY"])}
        cards = production.get("gpu_cards", [])
        if (current.get("schema_version") != 1 or current.get("web") != {"host": "127.0.0.1", "port": WEB_PORT}
                or production.get("gpu_service") != expected_gpu or not isinstance(cards, list) or not cards
                or any(type(card) is not int or card not in (4, 5, 6, 7) for card in cards)):
            raise DeployError("Existing cami configuration has incompatible control-plane/GPU settings; it was not changed")
    else:
        atomic_write(config_path, json.dumps(new_configuration(paths), ensure_ascii=False, indent=2) + "\n")
    env_path = guarded(WORKSPACE / "service.env", private=True)
    expected = environment(paths)
    if env_path.exists():
        seen = {}
        for line in env_path.read_text().splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            if key in expected:
                if key in seen:
                    raise DeployError("Existing cami service environment contains duplicate managed variables")
                seen[key] = value.strip().strip('"').strip("'")
        if any(seen.get(key) != value for key, value in expected.items()):
            raise DeployError("Existing cami environment has incompatible paths/settings; it was not changed")
    else:
        atomic_write(env_path, "".join(name + "=" + value + "\n" for name, value in expected.items()))
    return {"workspace": str(WORKSPACE), "config": str(config_path), "data": str(WORKSPACE / "data"),
            "web_url": f"http://127.0.0.1:{WEB_PORT}/", "services": list(UNITS), "old_data_migrated": False}


@contextmanager
def deployment_lock():
    checked_root()
    check_units()
    private_directory(WORKSPACE)
    path = guarded(WORKSPACE / ".deploy.lock", private=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DeployError("Another cami service operation is in progress") from None
        yield
    finally:
        os.close(descriptor)


def prepare():
    paths = preflight()
    with deployment_lock():
        return prepare_files(paths)


def active_jobs():
    """Read this cami database only, without initializing or opening any old database."""
    guarded(WORKSPACE, private=True, directory=True)
    guarded(WORKSPACE / "data", private=True, directory=True)
    path = guarded(WORKSPACE / "data/studio.sqlite3", private=True)
    if not path.exists():
        return 0
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1) as database:
            return database.execute("SELECT count(*) FROM jobs WHERE status NOT IN ('completed','failed','cancelled')").fetchone()[0]
    except sqlite3.Error:
        raise DeployError("Cannot verify cami job activity; existing data was not changed") from None


def unit_text(name):
    module = "factory.web" if name == UNITS[0] else "factory.worker"
    return f"""{marker()}
[Unit]
Description=AI Studio cami {'web control plane' if module == 'factory.web' else 'workflow worker'}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={ROOT}
ExecStart={ROOT / '.venv/bin/python'} -u -m {module}
EnvironmentFile={WORKSPACE / 'service.env'}
UMask=0077
Restart=on-failure
RestartSec=3
TimeoutStopSec=600
SendSIGKILL=no
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
"""


def health():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{WEB_PORT}/api/health", timeout=2) as response:
        value = json.loads(response.read(65537))
    return value.get("factory") is True and value.get("root") == str(ROOT)


def wait_ready(started, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            state = {name: service_state(name) for name in UNITS}
            heartbeat = guarded(WORKSPACE / "data/worker.json", private=True)
            observed = json.loads(heartbeat.read_text()) if heartbeat.exists() else {}
            if all(row["active"] for row in state.values()) and health() and observed.get("status") == "running" and observed.get("heartbeat", 0) >= started and observed.get("pid") == state[UNITS[1]]["pid"] and state[UNITS[1]]["pid"] > 0:
                return
        except (OSError, ValueError, DeployError):
            pass
        time.sleep(.2)
    raise DeployError("Cami web/worker did not become ready; inspect their user-service journals")


def restore_enablement(name, state):
    if state["enabled"] == "enabled-runtime":
        command("enable", "--runtime", name)
    else:
        command("enable" if state["enabled"] == "enabled" else "disable", name)


def check_web_port():
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", WEB_PORT))
    except OSError:
        raise DeployError("The cami web port is occupied by another process") from None


def start():
    paths = preflight()
    with deployment_lock():
        targets = check_units()
        previous = {name: service_state(name) for name in UNITS}
        if active_jobs():
            raise DeployError("Cami has unfinished jobs; pause/finish them before restarting services")
        # A new install must not displace a different process occupying the loopback port.
        if not previous[UNITS[0]]["active"]:
            check_web_port()
        result = prepare_files(paths)
        backup = {name: (path.read_text(), path.stat().st_mode & 0o777) if path.exists() else None
                  for name, path in targets.items()}
        changed = False
        touched = set()
        installed = set()
        try:
            # Stop admission before rechecking the queue, then stop the worker at its safe boundary.
            if previous[UNITS[0]]["active"]:
                changed = True
                touched.add(UNITS[0])
                command("stop", UNITS[0])
            if active_jobs():
                raise DeployError("New cami jobs appeared during restart; the previous services will be restored")
            if previous[UNITS[1]]["active"]:
                changed = True
                touched.add(UNITS[1])
                command("stop", UNITS[1])
            targets[UNITS[0]].parent.mkdir(parents=True, exist_ok=True)
            for name, path in targets.items():
                atomic_write(path, unit_text(name), 0o644)
                installed.add(name)
                changed = True
            command("daemon-reload")
            command("enable", *UNITS)
            started = time.time()
            touched.add(UNITS[0])
            command("start", UNITS[0])
            touched.add(UNITS[1])
            command("start", UNITS[1])
            wait_ready(started)
        except BaseException as original:
            if changed:
                failures = []
                # Stop this attempt before restoring files; preserve previous enablement and liveness.
                for name in reversed(UNITS):
                    try:
                        if name in touched:
                            command("stop", name)
                        if name in installed and previous[name]["enabled"] not in ("enabled", "enabled-runtime"):
                            command("disable", name)
                    except DeployError:
                        failures.append(name)
                for name, path in targets.items():
                    try:
                        if backup[name] is None:
                            guarded(path)
                            path.unlink(missing_ok=True)
                        else:
                            atomic_write(path, *backup[name])
                    except (OSError, DeployError):
                        failures.append(name)
                try:
                    command("daemon-reload")
                    for name in UNITS:
                        if backup[name] is not None:
                            restore_enablement(name, previous[name])
                            if name in touched:
                                command("start" if previous[name]["active"] else "stop", name)
                except DeployError:
                    failures.append("service-state")
                if failures:
                    raise DeployError("Cami startup failed and rollback needs attention; inspect the user-service journals") from None
            raise original
        return {**result, "status": "active", "worker": "local"}


def status():
    checked_root()
    check_units()
    return {"workspace": str(WORKSPACE), "web_url": f"http://127.0.0.1:{WEB_PORT}/",
            "services": {name: service_state(name) for name in UNITS}, "active_jobs": active_jobs()}


def stop():
    checked_root()
    with deployment_lock():
        check_units()
        states = {name: service_state(name) for name in UNITS}
        if active_jobs():
            raise DeployError("Pause/finish active cami jobs before stopping; the current services were preserved")
        for name in reversed(UNITS):
            if states[name]["load_state"] != "not-found":
                command("stop", name)
        return {"status": "stopped", "data": str(WORKSPACE / "data"), "data_preserved": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "start", "status", "stop"])
    args = parser.parse_args()
    try:
        result = {"prepare": prepare, "start": start, "status": status, "stop": stop}[args.action]()
    except DeployError as exc:
        # DeployError messages are fixed diagnostics; never include environment contents or stderr.
        parser.exit(1, str(exc) + "\n")
    except (ValueError, OSError):
        parser.exit(1, "Cami private configuration could not be read; inspect its format and ownership.\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
