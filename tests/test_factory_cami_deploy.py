"""Cami service operations use only temporary files and a fake user service manager."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import deploy_cami as deploy

EXAMPLE = Path(__file__).resolve().parents[1] / "config/factory.example.json"


class Services:
    def __init__(self, directory):
        self.directory = directory
        self.states = {name: {"active": False, "enabled": "disabled", "pid": 0} for name in deploy.UNITS}
        self.calls = []
        self.fail = None
        self.foreign = {}

    def __call__(self, *args, check=True):
        self.calls.append(args)
        operation = args[0]
        if operation == "show":
            name = args[1]
            state = self.states[name]
            path = self.directory / name
            fragment = self.foreign.get(name, str(path) if path.exists() else "")
            loaded = "loaded" if fragment else "not-found"
            output = {"LoadState": loaded, "FragmentPath": fragment,
                      "ActiveState": "active" if state["active"] else "inactive",
                      "UnitFileState": state["enabled"], "MainPID": state["pid"]}
            return SimpleNamespace(returncode=0, stdout="\n".join(key + "=" + str(value) for key, value in output.items()))
        names = [value for value in args[1:] if value in deploy.UNITS]
        for name in names:
            if operation == "start":
                self.states[name].update(active=True, pid=5000 + deploy.UNITS.index(name))
            elif operation == "stop":
                self.states[name].update(active=False, pid=0)
            elif operation == "enable":
                self.states[name]["enabled"] = "enabled-runtime" if "--runtime" in args else "enabled"
            elif operation == "disable":
                self.states[name]["enabled"] = "disabled"
        # Simulate partial success too: failed service-manager commands can change state.
        if self.fail == args:
            self.fail = None
            raise deploy.DeployError("Synthetic service activation failed")
        return SimpleNamespace(returncode=0, stdout="")


@pytest.fixture
def installation(tmp_path, monkeypatch):
    root = tmp_path / "cami"
    root.mkdir()
    (root / "config").mkdir()
    (root / "config/factory.example.json").write_text(EXAMPLE.read_text())
    # Existing sources must never become a new configuration or data source.
    (root / "config/factory.json").write_text('{"private_sentinel":"OLD_PRIVATE_CONFIG"}')
    (root / "runtime/factory").mkdir(parents=True)
    (root / "runtime/factory/studio.sqlite3").write_bytes(b"OLD_DATABASE_UNTOUCHED")
    (root / "runtime/cami-preview/data").mkdir(parents=True)
    (root / "runtime/cami-preview/data/studio.sqlite3").write_bytes(b"OLD_PREVIEW_UNTOUCHED")
    (root / ".venv/bin").mkdir(parents=True)
    python = root / ".venv/bin/python"
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o700)
    (root / "ui/factory/dist").mkdir(parents=True)
    (root / "ui/factory/dist/index.html").write_text("<!doctype html><html>built fixture</html>")
    area = root / "runtime/wkp-live-gateway"
    area.mkdir(mode=0o700)
    for filename in deploy.TLS_ENV.values():
        path = area / filename
        path.write_text("Synthetic local TLS fixture, not production identity")
        path.chmod(0o600)
    units = tmp_path / "user-units"
    units.mkdir()
    monkeypatch.setattr(deploy, "ROOT", root)
    monkeypatch.setattr(deploy, "WORKSPACE", root / "runtime/cami-studio")
    monkeypatch.setattr(deploy, "TLS_AREA", area)
    monkeypatch.setattr(deploy, "unit_directory", lambda: units)
    context = Mock()
    monkeypatch.setattr(deploy.ssl, "create_default_context", Mock(return_value=context))
    services = Services(units)
    monkeypatch.setattr(deploy, "command", services)
    monkeypatch.setattr(deploy, "check_web_port", Mock())
    monkeypatch.setattr(deploy, "wait_ready", Mock())
    return SimpleNamespace(root=root, units=units, services=services, context=context)


def prepared_config():
    return json.loads((deploy.WORKSPACE / "config.json").read_text())


def database(status="running"):
    path = deploy.WORKSPACE / "data/studio.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE jobs(id TEXT,status TEXT)")
        db.execute("INSERT INTO jobs VALUES('fixture-job',?)", (status,))
    path.chmod(0o600)
    return path


def existing_units(installation):
    for name in deploy.UNITS:
        path = installation.units / name
        path.write_text(deploy.marker() + "\n# Prior owned service\n")
        path.chmod(0o640)


def test_prepare_builds_empty_isolated_control_plane_without_migrating_any_old_data(installation):
    result = deploy.prepare()
    current = prepared_config()
    assert result["old_data_migrated"] is False
    assert list((deploy.WORKSPACE / "data").iterdir()) == []
    assert installation.services.calls == []
    assert (installation.root / "runtime/factory/studio.sqlite3").read_bytes() == b"OLD_DATABASE_UNTOUCHED"
    assert (installation.root / "runtime/cami-preview/data/studio.sqlite3").read_bytes() == b"OLD_PREVIEW_UNTOUCHED"
    assert "OLD_PRIVATE_CONFIG" not in (deploy.WORKSPACE / "config.json").read_text()
    assert current["web"] == {"host": "127.0.0.1", "port": 8787}
    assert current["production"]["gpu_cards"] == [4, 5, 6, 7]
    assert current["production"]["gpu_service"]["base_url"] == "https://172.20.114.10:8790/inference"
    source = json.loads(EXAMPLE.read_text())
    for key in ("image_width", "image_height", "output_width", "output_height"):
        assert current["production"][key] == source["production"][key]
    assert current["production"]["video_profile"] == "h3_production"
    assert (current["production"]["video_width"], current["production"]["video_height"]) == (768, 1344)
    assert set(current["video_profiles"]) == {"wkp_h3"}
    assert current["video_roles"] == {"video_generator": "wkp_h3"}
    assert current["video_profiles"]["wkp_h3"]["provider"] == "comfyui_h3"
    assert current["llm_roles"]["executive_producer"] == "primary"
    assert current["llm_roles"]["independent_critic"] == current["llm_roles"]["story_critic"] == "critic"
    assert current["llm_roles"]["visual_critic"] == "vision"
    for name, model, tokens in (("primary", "qwen3.8-27b-q8-opt-2dflash", 32768),
                               ("critic", "ai-studio-qwen38-23", 16384),
                               ("vision", "ai-studio-qwen38-vision", 16384)):
        profile = current["llm_profiles"][name]
        assert profile["base_url"] == deploy.WKP + "/models/" + name + "/v1"
        assert profile["model"] == model and profile["context_tokens"] == tokens
        assert profile["network_mode"] == "lan" and profile["allowed_hosts"] == ["172.20.114.10"]
        assert profile["response_format"] == "llama_json_schema" and profile["context_validation"] == "llama_tokenize"
        assert not {"api_key_env", "api_key_credential"}.intersection(profile)
    assert deploy.WORKSPACE.stat().st_mode & 0o777 == 0o700
    assert (deploy.WORKSPACE / "data").stat().st_mode & 0o777 == 0o700
    assert (deploy.WORKSPACE / "service.env").stat().st_mode & 0o777 == 0o600
    assert (deploy.WORKSPACE / "config.json").stat().st_mode & 0o777 == 0o600
    env = (deploy.WORKSPACE / "service.env").read_text()
    assert "DRAMA_MEDIA_PYTHON=" + str(installation.root / ".venv/bin/python") in env
    assert "CAMI_WKP_TLS_KEY=" + str(deploy.TLS_AREA / "client-private-key.pem") in env
    assert "BEGIN PRIVATE KEY" not in env and "MINIMAX_API_KEY" not in env


def test_new_configuration_passes_real_provider_registry_validation(installation):
    from factory import providers
    deploy.prepare()
    current = prepared_config()
    inventory = providers.public_inventory(current)
    assert all(profile["transport_auth"] == "mtls" for profile in inventory["profiles"])
    validated = providers.validate_registry(inventory["editable_profiles"], inventory["role_assignments"], current)
    assert validated["llm_profiles"]["vision"]["vision"] is True


def test_existing_model_edits_and_extra_private_env_variables_are_preserved(installation):
    deploy.prepare()
    path = deploy.WORKSPACE / "config.json"
    current = prepared_config()
    current["llm_profiles"]["primary"]["temperature_override"] = .42
    current["llm_profiles"]["primary"]["model_aliases"] = ["operator-model-alias"]
    original = json.dumps(current, ensure_ascii=False, separators=(",", ":"))
    path.write_text(original)
    env_path = deploy.WORKSPACE / "service.env"
    with env_path.open("a") as stream:
        stream.write("OPERATOR_SETTING=SYNTHETIC_PRESERVE_ME\n")
    original_env = env_path.read_text()
    deploy.prepare()
    assert path.read_text() == original
    assert env_path.read_text() == original_env


@pytest.mark.parametrize("kind", ["unit", "config", "env", "data", "key"])
def test_symlinks_never_redirect_owned_unit_config_data_or_tls_access(installation, kind):
    secret = installation.root / "unrelated-private-file"
    secret.write_text("PRIVATE_SENTINEL")
    if kind in ("config", "env", "data"):
        deploy.WORKSPACE.mkdir(mode=0o700)
    targets = {"unit": installation.units / deploy.UNITS[0], "config": deploy.WORKSPACE / "config.json",
               "env": deploy.WORKSPACE / "service.env", "data": deploy.WORKSPACE / "data",
               "key": deploy.TLS_AREA / "client-private-key.pem"}
    target = targets[kind]
    target.unlink(missing_ok=True)
    target.symlink_to(secret)
    with pytest.raises(deploy.DeployError, match="symbolic link"):
        deploy.prepare()
    assert secret.read_text() == "PRIVATE_SENTINEL"
    assert installation.services.calls == []


@pytest.mark.parametrize("failure", ["missing-ui", "missing-python", "missing-key", "public-key-permissions", "invalid-pem"])
def test_preflight_failure_never_starts_or_replaces_services(installation, failure, monkeypatch):
    if failure == "missing-ui":
        (installation.root / "ui/factory/dist/index.html").unlink()
    elif failure == "missing-python":
        (installation.root / ".venv/bin/python").unlink()
    elif failure == "missing-key":
        (deploy.TLS_AREA / "client-private-key.pem").unlink()
    elif failure == "public-key-permissions":
        (deploy.TLS_AREA / "client-private-key.pem").chmod(0o644)
    else:
        installation.context.load_cert_chain.side_effect = ssl_error = OSError("SYNTHETIC_PRIVATE_VALUE")
    with pytest.raises(deploy.DeployError) as error:
        deploy.start()
    assert "SYNTHETIC_PRIVATE_VALUE" not in str(error.value)
    assert installation.services.calls == []
    assert not deploy.WORKSPACE.exists()


def test_foreign_unit_content_or_global_fragment_is_not_adopted(installation):
    path = installation.units / deploy.UNITS[0]
    path.write_text("[Service]\nExecStart=/unrelated/service\n")
    with pytest.raises(deploy.DeployError, match="unrelated"):
        deploy.start()
    assert installation.services.calls == []
    assert path.read_text().endswith("/unrelated/service\n")
    path.unlink()
    installation.services.foreign[deploy.UNITS[0]] = "/etc/systemd/user/ai-studio-web.service"
    with pytest.raises(deploy.DeployError, match="differently installed"):
        deploy.start()
    assert not any(call[0] in ("start", "stop", "enable", "daemon-reload") for call in installation.services.calls)


@pytest.mark.parametrize("job_state", ["queued", "running", "waiting_remote"])
def test_active_jobs_block_restart_and_stop_without_touching_services(installation, job_state):
    deploy.prepare()
    path = database(job_state)
    before = path.read_bytes()
    with pytest.raises(deploy.DeployError, match="unfinished jobs"):
        deploy.start()
    with pytest.raises(deploy.DeployError, match="active cami jobs"):
        deploy.stop()
    assert path.read_bytes() == before
    assert not any(call[0] != "show" for call in installation.services.calls)


def test_success_installs_only_two_local_control_units_and_does_not_seed_or_authorize(installation):
    result = deploy.start()
    assert result["status"] == "active" and result["worker"] == "local"
    for name, module in zip(deploy.UNITS, ("factory.web", "factory.worker")):
        content = (installation.units / name).read_text()
        assert content.startswith(deploy.marker() + "\n")
        assert "WorkingDirectory=" + str(installation.root) in content
        assert "ExecStart=" + str(installation.root / ".venv/bin/python") + " -u -m " + module in content
        assert "EnvironmentFile=" + str(deploy.WORKSPACE / "service.env") in content
        assert "UMask=0077" in content and "SendSIGKILL=no" in content
        assert "8788" not in content and "gateway" not in name
        assert installation.services.states[name]["active"] is True
        assert installation.services.states[name]["enabled"] == "enabled"
    deploy.wait_ready.assert_called_once()
    assert list((deploy.WORKSPACE / "data").iterdir()) == []
    assert set(path.name for path in installation.units.iterdir()) == set(deploy.UNITS)


def test_failed_initial_start_removes_units_and_restores_inactive_disabled_state(installation):
    installation.services.fail = ("start", deploy.UNITS[1])
    with pytest.raises(deploy.DeployError, match="Synthetic service activation failed"):
        deploy.start()
    assert not any(installation.units.iterdir())
    assert all(not state["active"] and state["enabled"] == "disabled" for state in installation.services.states.values())
    assert list((deploy.WORKSPACE / "data").iterdir()) == []


def test_failed_upgrade_restores_prior_unit_bytes_modes_enablement_and_liveness(installation):
    deploy.prepare()
    existing_units(installation)
    installation.services.states[deploy.UNITS[0]].update(active=True, pid=111, enabled="enabled-runtime")
    installation.services.states[deploy.UNITS[1]].update(active=True, pid=222, enabled="disabled")
    before = {name: (installation.units / name).read_bytes() for name in deploy.UNITS}
    config_before = (deploy.WORKSPACE / "config.json").read_bytes()
    env_before = (deploy.WORKSPACE / "service.env").read_bytes()
    installation.services.fail = ("start", deploy.UNITS[1])
    with pytest.raises(deploy.DeployError, match="Synthetic service activation failed"):
        deploy.start()
    for name in deploy.UNITS:
        assert (installation.units / name).read_bytes() == before[name]
        assert (installation.units / name).stat().st_mode & 0o777 == 0o640
        assert installation.services.states[name]["active"] is True
    assert installation.services.states[deploy.UNITS[0]]["enabled"] == "enabled-runtime"
    assert installation.services.states[deploy.UNITS[1]]["enabled"] == "disabled"
    assert (deploy.WORKSPACE / "config.json").read_bytes() == config_before
    assert (deploy.WORKSPACE / "service.env").read_bytes() == env_before


def test_job_arriving_after_web_admission_stops_does_not_interrupt_the_old_worker(installation, monkeypatch):
    deploy.prepare()
    existing_units(installation)
    for state in installation.services.states.values():
        state.update(active=True, pid=123)
    monkeypatch.setattr(deploy, "active_jobs", Mock(side_effect=[0, 1]))
    with pytest.raises(deploy.DeployError, match="New cami jobs appeared"):
        deploy.start()
    assert ("stop", deploy.UNITS[1]) not in installation.services.calls
    assert all(state["active"] for state in installation.services.states.values())


def test_stop_preserves_config_and_data_and_status_never_creates_a_database(installation):
    deploy.start()
    before = (deploy.WORKSPACE / "config.json").read_bytes()
    result = deploy.stop()
    assert result["data_preserved"] is True
    assert all(not state["active"] for state in installation.services.states.values())
    assert (deploy.WORKSPACE / "config.json").read_bytes() == before
    assert deploy.status()["active_jobs"] == 0
    assert not (deploy.WORKSPACE / "data/studio.sqlite3").exists()


def test_readiness_requires_the_current_systemd_worker_pid_not_a_fresh_manual_worker(installation, monkeypatch):
    deploy.prepare()
    existing_units(installation)
    for index, state in enumerate(installation.services.states.values()):
        state.update(active=True, pid=100 + index)
    heartbeat = deploy.WORKSPACE / "data/worker.json"
    heartbeat.write_text(json.dumps({"status": "running", "heartbeat": 200, "pid": 999}))
    heartbeat.chmod(0o600)
    monkeypatch.setattr(deploy, "health", lambda: True)
    monkeypatch.setattr(deploy, "wait_ready", original_wait_ready)
    monkeypatch.setattr(deploy.time, "sleep", lambda _: None)
    ticks = iter((0, 0, 2))
    monkeypatch.setattr(deploy.time, "monotonic", lambda: next(ticks))
    with pytest.raises(deploy.DeployError, match="did not become ready"):
        deploy.wait_ready(150, timeout=1)
    heartbeat.write_text(json.dumps({"status": "running", "heartbeat": 200, "pid": 101}))
    ticks = iter((0, 0))
    monkeypatch.setattr(deploy.time, "monotonic", lambda: next(ticks))
    deploy.wait_ready(150, timeout=1)


original_wait_ready = deploy.wait_ready
