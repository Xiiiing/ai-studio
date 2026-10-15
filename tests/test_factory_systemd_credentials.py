"""Systemd credential authentication stays isolated from profiles and responses."""
import json
import os
from pathlib import Path

import pytest

from factory.providers import (_credential_value, _credential_available, normalize_profile,
                               redact_secrets, ProviderError, public_inventory)


def profile():
    return {"provider": "llama_cpp", "base_url": "http://127.0.0.1:8095/v1",
            "model": "example-primary", "vision": False, "context_tokens": 16384,
            "api_key_credential": "qwen38_primary"}


def test_mounted_named_credential_redaction_and_inventory(tmp_path, monkeypatch):
    folder = tmp_path / "credentials"
    folder.mkdir()
    key = "FAKE_TEST_CREDENTIAL_NOT_REAL_012345"
    (folder / "qwen38_primary").write_text(key + "\n")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(folder))
    value = normalize_profile(profile())
    assert _credential_available(value)
    assert _credential_value(value, required=True) == key
    assert key not in json.dumps(redact_secrets({"message": "prefix-" + key}, value))
    inventory = public_inventory({"schema_version": 1,
        "llm_profiles": {"primary": profile()},
        "llm_roles": {"screenwriter": "primary"},
        "video_profiles": {"local_wan": {"provider": "comfyui_wan", "model": "Wan2.2-TI2V-5B", "enabled": True}},
        "video_roles": {"video_generator": "local_wan"}})
    assert inventory["profiles"][0]["auth_configured"] is True
    assert key not in json.dumps(inventory)
    assert inventory["editable_profiles"]["primary"]["api_key_credential"] == "qwen38_primary"


@pytest.mark.parametrize("invalid", ["/tmp/secret", "../key", "bad/name", "", "a" * 70,
                                    "key\nheader", "key space"])
def test_credential_identifier_rejects_untrusted_paths(invalid):
    value = profile()
    value["api_key_credential"] = invalid
    if invalid == "":
        assert "api_key_credential" not in normalize_profile(value)
    else:
        with pytest.raises(ValueError):
            normalize_profile(value)


def test_missing_or_symlink_credentials_fail_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    value = normalize_profile(profile())
    assert not _credential_available(value)
    with pytest.raises(ProviderError, match="not mounted"):
        _credential_value(value, required=True)
    folder = tmp_path / "creds"
    folder.mkdir()
    external = tmp_path / "outside"
    external.write_text("FAKE_SECRET")
    (folder / "qwen38_primary").symlink_to(external)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(folder))
    assert not _credential_available(value)
    with pytest.raises(ProviderError, match="cannot be read"):
        _credential_value(value, required=True)


@pytest.mark.parametrize("contents", [b"", b"A" * 520, b"ABC\nDEF", b"ABC\x00Z"])
def test_invalid_token_is_rejected(tmp_path, monkeypatch, contents):
    folder = tmp_path / "creds"
    folder.mkdir()
    (folder / "qwen38_primary").write_bytes(contents)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(folder))
    with pytest.raises(ProviderError):
        _credential_value(normalize_profile(profile()), required=True)


def test_environment_auth_and_systemd_auth_are_mutually_exclusive():
    value = profile()
    value["api_key_env"] = "TEST_AUTH_ENV"
    with pytest.raises(ValueError, match="exactly one"):
        normalize_profile(value)


def test_http_auth_header_uses_mounted_credential_without_leaking(tmp_path, monkeypatch):
    from factory import providers
    directory = tmp_path / "creds"
    directory.mkdir()
    secret = "FAKE_TEST_TRANSPORT_TOKEN"
    (directory / "qwen38_primary").write_text(secret + "\n")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(directory))
    captured = {}

    class Response:
        status = 200
        def __init__(self):
            self.once = False
        def getheader(self, *args, **kwargs):
            return "identity"
        def read1(self, limit):
            if self.once:
                return b""
            self.once = True
            return json.dumps({"note": secret, "data": [{"id": "fixture"}]}).encode()

    class Connection:
        sock = None
        def connect(self):
            return None
        def request(self, method, path, body=None, headers=None):
            captured.update(method=method, path=path, auth=headers.get("Authorization"))
        def getresponse(self):
            return Response()
        def close(self):
            return None

    monkeypatch.setattr(providers, "_resolve_addresses", lambda *a, **k: [])
    monkeypatch.setattr(providers, "_connection", lambda *a, **k: Connection())
    cfg = normalize_profile(profile())
    value = providers.request_json(cfg["base_url"], "/models", timeout=3, profile=cfg)
    assert captured["auth"] == "Bearer " + secret
    assert value["note"] == "[REDACTED]"
    assert secret not in json.dumps(value)


def test_nonloopback_credential_requires_https():
    value = profile()
    value.update(base_url="http://172.20.114.10:8095/v1", network_mode="lan",
                 enabled=True, allowed_hosts=["172.20.114.10"])
    with pytest.raises(ValueError, match="HTTPS"):
        normalize_profile(value)
