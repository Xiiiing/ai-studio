"""Gateway trust and installation guards use temporary files, never production state."""
import hashlib
import json
from pathlib import Path
import ssl
import subprocess

import pytest

from scripts import wkp_live_gateway as gateway


@pytest.mark.parametrize("host", ["0.0.0.0", "8.8.8.8", "127.0.0.1", "100.82.71.126", "::1", "example.com", "172.20.114.10", "172.20.12.202\nrespond 200"])
def test_gateway_rejects_non_lan_and_wkp_bind(host):
    with pytest.raises(ValueError):
        gateway.endpoint(host, 8789)


@pytest.mark.parametrize("port", [0, 443, 65536, True, "8789"])
def test_gateway_rejects_invalid_port(port):
    with pytest.raises(ValueError):
        gateway.endpoint("172.20.12.202", port)


def test_server_certificate_pin_rejects_before_any_trust_material_is_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway, "AREA", tmp_path / "private")
    monkeypatch.setattr(gateway, "fetch_server_certificate", lambda: gateway.verified_server_certificate(b"untrusted DER"))
    with pytest.raises(RuntimeError, match="handoff fingerprint"):
        gateway.prepare("172.20.12.202", 8789)
    assert not list((tmp_path / "private").iterdir())


def test_certificate_pin_accepts_only_recorded_digest(monkeypatch):
    der = b"public certificate bytes"
    monkeypatch.setattr(gateway, "WKP_FINGERPRINT", hashlib.sha256(der).hexdigest())
    assert gateway.verified_server_certificate(der) == ssl.DER_cert_to_PEM_cert(der)


def test_prepare_never_follows_private_directory_symlinks(tmp_path, monkeypatch):
    target = tmp_path / "other"
    target.mkdir()
    area = tmp_path / "private"
    area.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(gateway, "AREA", area)
    with pytest.raises(RuntimeError, match="Symlink"):
        gateway.prepare("172.20.12.202", 8789)
    assert not list(target.iterdir())


def test_existing_credentials_preserve_identity_and_are_not_printed(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway, "AREA", tmp_path)
    first = gateway.read_credentials()
    second = gateway.read_credentials()
    assert first == second
    assert first["username"] == "studio"
    assert len(first["password"]) >= 24
    assert (tmp_path / "credentials.json").stat().st_mode & 0o777 == 0o600


def test_credentials_symlink_cannot_read_or_overwrite_unrelated_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway, "AREA", tmp_path)
    unrelated = tmp_path / "private-config"
    unrelated.write_text("do not expose this")
    (tmp_path / "credentials.json").symlink_to(unrelated)
    with pytest.raises(RuntimeError, match="Symlink"):
        gateway.read_credentials()
    assert unrelated.read_text() == "do not expose this"


def test_malformed_credentials_are_not_silently_rotated(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway, "AREA", tmp_path)
    path = tmp_path / "credentials.json"
    initial = json.dumps({"username": "other", "password": "old"})
    path.write_text(initial)
    path.chmod(0o600)
    with pytest.raises(RuntimeError, match="Malformed"):
        gateway.read_credentials()
    assert path.read_text() == initial


def test_world_readable_credentials_are_not_loaded(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway, "AREA", tmp_path)
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({"username": "studio", "password": "a" * 24}))
    path.chmod(0o644)
    with pytest.raises(RuntimeError, match="0600"):
        gateway.read_credentials()


def test_foreign_service_is_rejected_before_network_or_private_material(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / ".config/systemd/user" / gateway.UNIT
    path.parent.mkdir(parents=True)
    path.write_text("[Service]\nExecStart=/some/other/service\n")
    monkeypatch.setattr(gateway, "prepare", lambda *args: pytest.fail("prepare must not run"))
    with pytest.raises(RuntimeError, match="unrelated service"):
        gateway.start("172.20.12.202", 8789)
    assert "ExecStart=/some/other/service" in path.read_text()


def test_unhealthy_remote_does_not_change_installed_unit_or_config(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(gateway, "AREA", tmp_path / "private")
    gateway.AREA.mkdir()
    path = tmp_path / ".config/systemd/user" / gateway.UNIT
    path.parent.mkdir(parents=True)
    original = f"# AI-STUDIO-WKP-LIVE:{gateway.AREA}\nhealthy service"
    path.write_text(original)
    config = gateway.AREA / "Caddyfile"
    config.write_text("healthy config")
    monkeypatch.setattr(gateway, "prepare", lambda *args: None)
    def unhealthy():
        raise RuntimeError("remote offline")
    monkeypatch.setattr(gateway, "upstream_health", unhealthy)
    with pytest.raises(RuntimeError, match="remote offline"):
        gateway.start("172.20.12.202", 8789)
    assert path.read_text() == original
    assert config.read_text() == "healthy config"


def test_failed_service_restart_restores_previous_enabled_active_service(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway, "ROOT", tmp_path)
    (tmp_path / "ui/factory/dist").mkdir(parents=True)
    (tmp_path / "ui/factory/dist/index.html").write_text("built frontend")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(gateway, "AREA", tmp_path / "private")
    gateway.AREA.mkdir()
    binary = tmp_path / "caddy"
    binary.touch()
    monkeypatch.setattr(gateway.shutil, "which", lambda _: str(binary))
    path = tmp_path / ".config/systemd/user" / gateway.UNIT
    path.parent.mkdir(parents=True)
    old_unit = f"# AI-STUDIO-WKP-LIVE:{gateway.AREA}\nprevious valid service"
    path.write_text(old_unit)
    config = gateway.AREA / "Caddyfile"
    config.write_text("previous valid config")
    monkeypatch.setattr(gateway, "prepare", lambda *args: None)
    monkeypatch.setattr(gateway, "upstream_health", lambda: {"ok": True})
    monkeypatch.setattr(gateway, "read_credentials", lambda: {"password": "secret"})
    monkeypatch.setattr(gateway.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0))
    calls = []
    failed = False
    def fake_run(*args, **kwargs):
        nonlocal failed
        calls.append(args)
        if args == ("systemctl", "--user", "restart", gateway.UNIT) and not failed:
            failed = True
            raise subprocess.CalledProcessError(1, args)
        return subprocess.CompletedProcess(args, 0, stdout="$2a$hash", stderr="")
    monkeypatch.setattr(gateway, "run", fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        gateway.start("172.20.12.202", 8789)
    assert path.read_text() == old_unit
    assert config.read_text() == "previous valid config"
    assert calls.count(("systemctl", "--user", "restart", gateway.UNIT)) == 2
    assert not (gateway.AREA / "deployed.json").exists()


def test_missing_frontend_does_not_replace_running_gateway(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway, "ROOT", tmp_path)
    monkeypatch.setattr(gateway, "unit_path", lambda: tmp_path / "gateway.service")
    monkeypatch.setattr(gateway, "prepare", lambda *args: None)
    monkeypatch.setattr(gateway, "upstream_health", lambda: {"status": "ready"})
    monkeypatch.setattr(gateway, "run", lambda *args, **kwargs: pytest.fail("must not modify service"))
    with pytest.raises(RuntimeError, match="Build the current frontend"):
        gateway.start("172.20.12.202", 8789)
