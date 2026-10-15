"""Local TLS fixtures: no Wkp access, production keys, or model inference."""
from __future__ import annotations

import json
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

import pytest

from factory import config, providers

TLS_NAMES = {"tls_ca_file_env": "FIXTURE_CA_FILE", "tls_client_cert_env": "FIXTURE_CLIENT_CERT",
             "tls_client_key_env": "FIXTURE_CLIENT_KEY"}
PREFIX = "/inference/models/primary"


def profile(**changes):
    value = {"provider": "llama_cpp", "base_url": "https://127.0.0.1:8790" + PREFIX + "/v1",
             "model": "fixture-primary", "context_tokens": 4096, "vision": True,
             "enabled": True, "network_mode": "loopback", **TLS_NAMES}
    value.update(changes)
    return value


@pytest.mark.parametrize("field", TLS_NAMES)
@pytest.mark.parametrize("value", ["/tmp/tls.pem", "../identity", "TLS CERT", "TLS_CERT\n", 123])
def test_tls_profiles_accept_only_environment_names(field, value):
    with pytest.raises(ValueError, match="environment variable name"):
        providers.normalize_profile(profile(**{field: value}))


@pytest.mark.parametrize("omitted", ["tls_client_cert_env", "tls_client_key_env"])
def test_tls_client_identity_requires_both_files(omitted):
    value = profile()
    value.pop(omitted)
    with pytest.raises(ValueError, match="configured together"):
        providers.normalize_profile(value)


def test_tls_configuration_requires_https_and_empty_fields_remain_compatible():
    with pytest.raises(ValueError, match="require HTTPS"):
        providers.normalize_profile(profile(base_url="http://127.0.0.1:8790" + PREFIX + "/v1"))
    plain = providers.normalize_profile(profile(**{name: "" for name in TLS_NAMES},
                                              base_url="http://127.0.0.1:8095/v1"))
    assert not set(TLS_NAMES).intersection(plain)


def test_tls_env_is_resolved_only_for_selected_profile_and_errors_are_safe(tmp_path, monkeypatch):
    for name in TLS_NAMES.values():
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(providers.ProviderError) as missing:
        providers._tls_context(profile())
    assert missing.value.code == "missing_tls_configuration"
    private_path = str(tmp_path / "not-a-real-private-key.pem")
    monkeypatch.setenv("FIXTURE_CA_FILE", private_path)
    with pytest.raises(providers.ProviderError) as invalid:
        providers._tls_context(profile())
    assert invalid.value.code == "invalid_tls_configuration"
    assert private_path not in str(invalid.value)
    monkeypatch.setenv("FIXTURE_CA_FILE", str(tmp_path))
    with pytest.raises(providers.ProviderError, match="regular file"):
        providers._tls_context(profile())
    context = Mock()
    monkeypatch.setattr(ssl, "create_default_context", Mock(return_value=context))
    # Ambient identity variables must never authenticate unrelated providers/CDNs.
    providers._tls_context()
    context.load_cert_chain.assert_not_called()
    ssl.create_default_context.assert_called_once_with(cafile=None)


@pytest.fixture(scope="module")
def certificates(tmp_path_factory):
    if not shutil.which("openssl"):
        pytest.skip("OpenSSL CLI is required for isolated TLS certificate fixtures")
    root = tmp_path_factory.mktemp("provider-tls")
    def run(*args):
        subprocess.run(["openssl", *map(str, args)], check=True, timeout=15,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    ca, ca_key = root / "ca.pem", root / "ca-key.pem"
    run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=Provider Test CA",
        "-keyout", ca_key, "-out", ca)
    result = {"ca": ca}
    for name, purpose in (("server", "serverAuth"), ("client", "clientAuth")):
        key, csr, cert, extensions = [root / (name + suffix) for suffix in ("-key.pem", ".csr", ".pem", ".ext")]
        extensions.write_text("basicConstraints=CA:FALSE\nextendedKeyUsage=" + purpose +
                              ("\nsubjectAltName=IP:127.0.0.1" if name == "server" else "") + "\n")
        run("req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=Provider Test " + name,
            "-keyout", key, "-out", csr)
        run("x509", "-req", "-in", csr, "-CA", ca, "-CAkey", ca_key, "-CAcreateserial",
            "-days", "1", "-out", cert, "-extfile", extensions)
        result[name], result[name + "_key"] = cert, key
    return result


@pytest.fixture
def mtls_server(certificates, monkeypatch):
    requests = []
    response_status = [200]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def respond(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length)) if length else None
            requests.append((self.command, self.path, body, self.headers.get("Authorization")))
            if self.path == PREFIX + "/props":
                value = {"default_generation_settings": {"n_ctx": 4096}}
            elif self.path == PREFIX + "/apply-template":
                value = {"prompt": "rendered fixture"}
            elif self.path == PREFIX + "/tokenize":
                value = {"tokens": [1, 2, 3]}
            else:
                value = {"data": [{"id": "fixture-primary"}]}
            data = json.dumps(value).encode()
            self.send_response(response_status[0])
            if response_status[0] == 302:
                self.send_header("Location", "https://example.invalid/stolen-identity")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        do_GET = respond
        do_POST = respond
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificates["server"], certificates["server_key"])
    context.load_verify_locations(cafile=str(certificates["ca"]))
    context.verify_mode = ssl.CERT_REQUIRED
    server.socket = context.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    worker.start()
    for env, path in (("FIXTURE_CA_FILE", "ca"), ("FIXTURE_CLIENT_CERT", "client"),
                      ("FIXTURE_CLIENT_KEY", "client_key")):
        monkeypatch.setenv(env, str(certificates[path]))
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    value = providers.normalize_profile(profile(base_url=f"https://127.0.0.1:{server.server_port}" + PREFIX + "/v1"))
    try:
        yield value, requests, response_status
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_real_mtls_preserves_llama_proxy_prefix_without_bearer_credentials(mtls_server):
    value, requests, _ = mtls_server
    result = providers.list_models(value, timeout=2)
    assert result["data"][0]["id"] == "fixture-primary"
    budget = providers.context_budget(value, [{"role": "user", "content": "fixture"}], 128)
    assert budget["method"] == "llama_tokenize" and budget["prompt_tokens"] == 3 and budget["fits"]
    assert [row[1] for row in requests] == [PREFIX + "/v1/models", PREFIX + "/props",
                                            PREFIX + "/apply-template", PREFIX + "/tokenize"]
    assert all(row[3] is None for row in requests)
    assert requests[2][2]["chat_template_kwargs"] == {"enable_thinking": False}
    assert requests[3][2]["content"] == "rendered fixture"


@pytest.mark.parametrize("omitted", ["client", "ca"])
def test_real_mtls_fails_closed_without_client_identity_or_server_trust(mtls_server, omitted):
    value, requests, _ = mtls_server
    if omitted == "client":
        value.pop("tls_client_cert_env")
        value.pop("tls_client_key_env")
    else:
        value.pop("tls_ca_file_env")
    with pytest.raises(providers.ProviderError) as error:
        providers.request_json(value["base_url"], "/models", profile=value, timeout=2)
    assert error.value.code == "transport_failed"
    assert requests == []


def test_real_mtls_still_verifies_hostname_and_never_follows_redirects(mtls_server):
    value, requests, status = mtls_server
    wrong_host = {**value, "base_url": value["base_url"].replace("127.0.0.1", "localhost")}
    with pytest.raises(providers.ProviderError) as mismatch:
        providers.request_json(wrong_host["base_url"], "/models", profile=wrong_host, timeout=2)
    assert mismatch.value.code == "transport_failed" and not requests
    status[0] = 302
    with pytest.raises(providers.ProviderError) as redirect:
        providers.request_json(value["base_url"], "/models", profile=value, timeout=2)
    assert redirect.value.code == "http_302" and len(requests) == 1


def test_registry_roundtrip_preserves_only_tls_environment_names(tmp_path, monkeypatch):
    primary = profile(base_url="https://172.20.114.10:8790" + PREFIX + "/v1", network_mode="lan",
                      allowed_hosts=["172.20.114.10"])
    critic = {**primary, "base_url": primary["base_url"].replace("/primary/", "/critic/"), "model": "fixture-critic"}
    roles = {role: "critic" if role in config.TEXT_CRITIC_ROLES else "primary" for role in config.ROLE_LABELS}
    cfg = {"schema_version": 1, "llm_profiles": {"primary": primary, "critic": critic}, "llm_roles": roles,
           "video_profiles": {"local_wan": {"provider": "comfyui_wan", "model": "Wan2.2-TI2V-5B", "enabled": True}},
           "video_roles": {"video_generator": "local_wan"}, "private_runtime_policy": {"preserve": True}}
    path = tmp_path / "factory.json"
    path.write_text(json.dumps(cfg))
    # Registry reads/saves are configuration-only, without opening any TLS files.
    monkeypatch.setattr(providers, "_tls_file_path", Mock(side_effect=AssertionError("No TLS file reads during registry save")))
    public = providers.public_inventory(cfg)
    assert public["profiles"][0]["transport_auth"] == "mtls"
    assert public["profiles"][0]["tls_client_configured"] is True
    assert public["profiles"][0]["auth_configured"] is False
    result = providers.save_registry(public["editable_profiles"], public["role_assignments"], public["revision"], config_path=path)
    assert all(result["editable_profiles"]["primary"][field] == env for field, env in TLS_NAMES.items())
    saved = json.loads(path.read_text())
    assert saved["private_runtime_policy"] == {"preserve": True}
    assert not any("BEGIN" in str(value) or "/runtime/" in str(value) for value in saved["llm_profiles"]["primary"].values())
    assert saved["llm_profiles"]["primary"]["tls_client_key_env"] == "FIXTURE_CLIENT_KEY"


def test_ssl_cert_file_supplies_ca_trust_but_cannot_supply_client_identity(mtls_server, certificates, monkeypatch):
    value, requests, _ = mtls_server
    monkeypatch.setenv("SSL_CERT_FILE", str(certificates["ca"]))
    value.pop("tls_ca_file_env")
    assert providers.list_models(value, timeout=2)["data"][0]["id"] == "fixture-primary"
    value.pop("tls_client_cert_env")
    value.pop("tls_client_key_env")
    with pytest.raises(providers.ProviderError) as error:
        providers.request_json(value["base_url"], "/models", profile=value, timeout=2)
    assert error.value.code == "transport_failed"
    assert len(requests) == 1
