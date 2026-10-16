"""Provider contracts: isolated CPU fixtures; no real model/GPU or external API calls."""
import copy
import io
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import Mock

import pytest

from factory import config as settings, llm, providers
from planner.client import local_base


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_absolute_http_deadline_interrupts_slow_drip(phase):
    class SlowHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            try:
                if phase == "headers":
                    self.wfile.write(b"HTTP/1.0 200 OK\r\n")
                    for _ in range(8):
                        self.wfile.write(b"X-Test: a\r\n")
                        self.wfile.flush()
                        time.sleep(.2)
                    self.wfile.write(b"Content-Length: 2\r\n\r\n{}")
                else:
                    self.wfile.write(b"HTTP/1.0 200 OK\r\nContent-Length: 8\r\n\r\n")
                    for character in b'{"a":12}':
                        self.wfile.write(bytes([character]))
                        self.wfile.flush()
                        time.sleep(.2)
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = HTTPServer(("127.0.0.1", 0), SlowHandler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    worker.start()
    started = time.monotonic()
    try:
        with pytest.raises(providers.ProviderError):
            providers.request_json("http://127.0.0.1:" + str(server.server_port) + "/v1", "/models", timeout=1)
        elapsed = time.monotonic() - started
        assert .8 <= elapsed < 1.3, elapsed
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_tls_handshake_uses_remaining_absolute_deadline(monkeypatch):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    release = threading.Event()
    def sink():
        try:
            connection, _ = listener.accept()
            with connection:
                release.wait(3)
        finally:
            listener.close()
    worker = threading.Thread(target=sink, daemon=True)
    worker.start()
    original = providers._connect_socket
    def delayed_tcp_return(addresses, timeout):
        connection = original(addresses, timeout)
        time.sleep(.45)
        return connection
    monkeypatch.setattr(providers, "_connect_socket", delayed_tcp_return)
    started = time.monotonic()
    try:
        with pytest.raises(providers.ProviderError) as error:
            providers.request_json("https://127.0.0.1:" + str(port) + "/v1", "/models", timeout=1)
        elapsed = time.monotonic() - started
        assert error.value.code == "transport_failed"
        assert .8 <= elapsed < 1.3, elapsed
    finally:
        release.set()
        worker.join(timeout=2)


def test_dns_timeout_is_bounded_and_does_not_start_an_http_request(monkeypatch):
    release = threading.Event()
    entered = threading.Event()
    def stalled_dns(*args, **kwargs):
        entered.set()
        release.wait(2)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    monkeypatch.setattr(socket, "getaddrinfo", stalled_dns)
    started = time.monotonic()
    try:
        with pytest.raises(providers.ProviderError) as error:
            providers._resolve_addresses("slow.example", 443, "remote", timeout=.05)
        assert error.value.code == "dns_timeout" and entered.is_set()
        assert time.monotonic() - started < .25
    finally:
        release.set()


SCHEMA = {"type": "object", "additionalProperties": False,
          "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def profile(**changes):
    return {"base_url": "http://127.0.0.1:8090/v1", "model": "fixture-model",
            "context_tokens": 32768, "vision": True, **changes}


def configuration(**changes):
    result = {"schema_version": 1, "llm": profile(),
              "llm_profiles": {"fixture": profile()},
              "llm_roles": {role: "fixture" for role in settings.ROLE_LABELS},
              "production": {"max_parallel": 1}, "private_runtime_policy": {"preserve": True}}
    result.update(changes)
    return result


@pytest.fixture(autouse=True)
def no_external_dns(monkeypatch):
    def guarded(host, port, *args, **kwargs):
        if host != "127.0.0.1":
            raise AssertionError("Provider tests may only contact their own loopback fixture")
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (host, port))]
    monkeypatch.setattr(socket, "getaddrinfo", guarded)
    monkeypatch.setattr(socket, "getfqdn", lambda host="": host)


@pytest.mark.parametrize("backend,fmt,method", [
    ("llama_cpp", "llama_json_schema", "llama_tokenize"),
    ("ollama", "openai_json_schema", "conservative_estimate"),
    ("vllm", "openai_json_schema", "conservative_estimate"),
    ("openai_compatible", "json_object", "conservative_estimate"),
])
def test_provider_defaults_are_explicit(backend, fmt, method):
    result = providers.normalize_profile(profile(provider=backend))
    assert result["provider"] == backend
    assert result["response_format"] == fmt and result["context_validation"] == method
    assert result["network_mode"] == "loopback" and result["enabled"] is True
    assert result["max_schema_attempts"] == 2
    assert result["chat_template_kwargs"] == ({"enable_thinking": False} if backend == "llama_cpp" else {})


@pytest.mark.parametrize("changes", [
    {"base_url": "https://api.example.com/v1"},
    {"base_url": "https://api.example.com/v1", "network_mode": "remote", "allowed_hosts": ["api.example.com"]},
    {"base_url": "https://api.example.com/v1", "network_mode": "remote", "enabled": True},
    {"base_url": "https://api.example.com/v1", "network_mode": "remote", "enabled": True,
     "allowed_hosts": ["*.example.com"]},
    {"base_url": "http://api.example.com/v1", "network_mode": "remote", "enabled": True,
     "allowed_hosts": ["api.example.com"]},
    {"base_url": "http://127.0.0.1:8090/v1?api_key=PRIVATE_FIXTURE"},
    {"base_url": "http://user:PRIVATE_FIXTURE@127.0.0.1:8090/v1"},
    {"base_url": "http://127.0.0.1:8090/v1#PRIVATE_FIXTURE"},
    {"base_url": "http://169.254.169.254/v1", "network_mode": "lan", "enabled": True,
     "allowed_hosts": ["169.254.169.254"]},
    {"base_url": "https://10.0.0.5/v1", "network_mode": "remote", "enabled": True,
     "allowed_hosts": ["10.0.0.5"]},
    {"base_url": "https://8.8.8.8/v1", "network_mode": "lan", "enabled": True,
     "allowed_hosts": ["8.8.8.8"]},
    {"base_url": "http://192.168.1.5/v1", "network_mode": "lan", "enabled": True,
     "allowed_hosts": ["192.168.1.5"], "api_key_env": "MY_KEY"},
    {"api_key": "PRIVATE_FIXTURE"},
    {"headers": {"Authorization": "Bearer PRIVATE_FIXTURE"}},
    {"chat_template_kwargs": {"authorization": "PRIVATE_FIXTURE"}},
    {"start_command": "echo PRIVATE_FIXTURE"},
    {"api_key_env": "KEY=PRIVATE_FIXTURE"},
    {"context_tokens": True},
    {"max_schema_attempts": 3},
    {"provider": "ollama", "context_validation": "llama_tokenize"},
])
def test_unsafe_profiles_fail_closed_without_echoing_values(changes):
    with pytest.raises(ValueError) as error:
        providers.normalize_profile(profile(**changes))
    assert "PRIVATE_FIXTURE" not in str(error.value)


def test_remote_and_lan_profiles_require_explicit_scope_but_no_dns_during_edit():
    remote = providers.normalize_profile(profile(
        provider="openai_compatible", base_url="https://api.example.com/v1",
        network_mode="remote", enabled=True, allowed_hosts=["api.example.com"], api_key_env="MY_API_KEY"))
    assert remote["network_mode"] == "remote"
    lan = providers.normalize_profile(profile(base_url="http://192.168.1.5:8090/v1",
        network_mode="lan", enabled=True, allowed_hosts=["192.168.1.5"]))
    assert lan["base_url"].startswith("http://192.168.1.5")
    # The independent legacy planner remains loopback-only.
    with pytest.raises(ValueError):
        local_base(remote["base_url"])
    with pytest.raises(ValueError):
        local_base(lan["base_url"])


@pytest.mark.parametrize("address,mode", [
    ("169.254.169.254", "remote"), ("10.0.0.2", "remote"), ("127.0.0.1", "remote"),
    ("::1", "remote"), ("fd00::5", "remote"), ("224.0.0.1", "remote"),
    ("8.8.8.8", "lan"), ("169.254.169.254", "lan"), ("192.168.0.2", "loopback"),
])
def test_resolved_addresses_cannot_escape_network_scope(monkeypatch, address, mode):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (family, socket.SOCK_STREAM, 6, "", (address, 443))])
    with pytest.raises(providers.ProviderError) as error:
        providers._resolve_addresses("host.example", 443, mode)
    assert error.value.code == "address_denied"


def test_mixed_dns_answers_fail_and_validated_ip_is_pinned(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(providers.ProviderError, match="outside"):
        providers._resolve_addresses("host.example", 443, "remote")
    fake_socket = Mock()
    monkeypatch.setattr(socket, "socket", lambda *args: fake_socket)
    addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, ("8.8.8.8", 443))]
    connection = providers._connection("host.example", 443, True, addresses, 4)
    assert connection.host == "host.example" and connection._context.check_hostname
    returned = connection._create_connection(("untrusted.second.lookup", 443), 4)
    assert returned is fake_socket
    fake_socket.connect.assert_called_once_with(("8.8.8.8", 443))


@pytest.fixture
def http_fixture():
    state = {"requests": [], "status": 200, "payload": {"data": [{"id": "fixture-model"}]},
             "raw": None, "headers": {}}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.reply()
        def do_POST(self):
            self.reply()
        def reply(self):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else None
            state["requests"].append({"method": self.command, "path": self.path,
                "auth": self.headers.get("Authorization"), "body": json.loads(raw) if raw else None})
            body = state["raw"] if state["raw"] is not None else json.dumps(state["payload"]).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for key, value in state["headers"].items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)
    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    worker.start()
    yield state, "http://127.0.0.1:" + str(server.server_port) + "/v1"
    server.shutdown()
    server.server_close()
    worker.join(timeout=2)


def test_health_lists_models_without_generation_or_secret_output(http_fixture, monkeypatch):
    state, base = http_fixture
    monkeypatch.setenv("FIXTURE_API_KEY", "PRIVATE_AUTH_VALUE")
    cfg = configuration()
    cfg["llm_profiles"]["fixture"] = profile(base_url=base, api_key_env="FIXTURE_API_KEY")
    result = providers.probe_profile("fixture", cfg)
    assert result["status"] == "ready" and result["models"] == ["fixture-model"]
    assert result["generation"] is False
    assert [(call["method"], call["path"]) for call in state["requests"]] == [("GET", "/v1/models")]
    assert state["requests"][0]["auth"] == "Bearer PRIVATE_AUTH_VALUE"
    inventory = providers.public_inventory(cfg)
    assert inventory["profiles"][0]["auth_configured"] is True
    assert inventory["editable_profiles"]["fixture"]["api_key_env"] == "FIXTURE_API_KEY"
    assert "PRIVATE_AUTH_VALUE" not in json.dumps([result, inventory])
    assert len(state["requests"]) == 1


def test_model_mismatch_health_retains_visible_choices_without_a_generation(http_fixture):
    state, base = http_fixture
    state["payload"] = {"data": [{"id": "another-model"}]}
    cfg = configuration(llm_profiles={"fixture": profile(base_url=base)})
    result = providers.probe_profile("fixture", cfg)
    assert result["status"] == "error" and result["error"]["code"] == "model_missing"
    assert result["models"] == ["another-model"] and result["model_match"] is False
    assert len(state["requests"]) == 1 and state["requests"][0]["method"] == "GET"


@pytest.mark.parametrize("status,code", [(301, "http_301"), (401, "http_401"), (429, "http_429"), (500, "http_500")])
def test_http_errors_and_redirects_are_not_retried_or_echoed(http_fixture, monkeypatch, status, code):
    state, base = http_fixture
    monkeypatch.setenv("FIXTURE_API_KEY", "PRIVATE_AUTH_VALUE")
    state.update(status=status, raw=b"PRIVATE_AUTH_VALUE secret upstream details",
                 headers={"Location": "http://169.254.169.254/metadata"})
    value = providers.normalize_profile(profile(base_url=base, api_key_env="FIXTURE_API_KEY"))
    with pytest.raises(providers.ProviderError) as error:
        providers.request_json(base, "/models", profile=value)
    assert error.value.code == code and "PRIVATE" not in str(error.value)
    assert len(state["requests"]) == 1


@pytest.mark.parametrize("body,code", [(b"not json", "invalid_json"), (b"[]", "invalid_response"),
                                      (b'{"x":NaN}', "invalid_json")])
def test_invalid_http_responses_are_safe(http_fixture, body, code):
    state, base = http_fixture
    state["raw"] = body
    with pytest.raises(providers.ProviderError) as error:
        providers.request_json(base, "/models")
    assert error.value.code == code


def test_request_response_bounds_and_known_secret_echo_scrubbing(http_fixture, monkeypatch):
    state, base = http_fixture
    monkeypatch.setenv("FIXTURE_API_KEY", "PRIVATE_AUTH_VALUE")
    value = providers.normalize_profile(profile(base_url=base, api_key_env="FIXTURE_API_KEY"))
    state["payload"] = {"text": "echo PRIVATE_AUTH_VALUE", "authorization": "some-other-secret"}
    result = providers.request_json(base, "/models", profile=value)
    assert result == {"text": "echo [REDACTED]", "authorization": "[REDACTED]"}
    monkeypatch.setattr(providers, "MAX_RESPONSE_BYTES", 30)
    state["raw"] = b"x" * 31
    with pytest.raises(providers.ProviderError) as error:
        providers.request_json(base, "/models", profile=value)
    assert error.value.code == "response_too_large"
    before = len(state["requests"])
    monkeypatch.setattr(providers, "MAX_REQUEST_BYTES", 30)
    with pytest.raises(providers.ProviderError) as error:
        providers.request_json(base, "/chat/completions", {"text": "x" * 31}, profile=value)
    assert error.value.code == "request_too_large" and len(state["requests"]) == before


def test_disabled_and_missing_auth_profiles_do_not_connect(monkeypatch):
    send = Mock(side_effect=AssertionError("No connection is allowed"))
    monkeypatch.setattr(providers, "_resolve_addresses", send)
    cfg = configuration(llm_profiles={"fixture": profile(enabled=False)})
    assert providers.probe_profile("fixture", cfg)["status"] == "disabled"
    cfg["llm_profiles"]["fixture"] = profile(api_key_env="NEVER_CONFIGURED_FIXTURE_KEY")
    monkeypatch.delenv("NEVER_CONFIGURED_FIXTURE_KEY", raising=False)
    result = providers.probe_profile("fixture", cfg)
    assert result["error"]["code"] == "missing_auth"
    assert send.call_count == 0


def test_llama_exact_budget_uses_server_slot_and_never_falls_back(monkeypatch):
    calls = []
    def endpoint(base, route, payload=None, **kwargs):
        calls.append((base, route))
        if route == "/props":
            return {"default_generation_settings": {"n_ctx": 2048}}
        if route == "/apply-template":
            return {"prompt": "rendered fixture"}
        return {"tokens": list(range(1000))}
    monkeypatch.setattr(providers, "request_json", endpoint)
    value = providers.normalize_profile(profile())
    result = providers.context_budget(value, [{"role": "user", "content": "fixture"}], 1100)
    assert result["context_tokens"] == 2048 and result["fits"] is False
    assert result["method"] == "llama_tokenize" and result["prompt_tokens"] == 1000
    assert [route for _, route in calls] == ["/props", "/apply-template", "/tokenize"]
    monkeypatch.setattr(providers, "request_json", lambda *a, **k: {})
    with pytest.raises(providers.ProviderError):
        providers.context_budget(value, [{"role": "user", "content": "fixture"}], 128)


@pytest.mark.parametrize("backend", ["ollama", "vllm", "openai_compatible"])
def test_other_backends_estimate_text_and_images_without_llama_endpoints(monkeypatch, backend):
    monkeypatch.setattr(providers, "request_json", Mock(side_effect=AssertionError("No token API call")))
    value = providers.normalize_profile(profile(provider=backend))
    content = [{"type": "text", "text": "中文剧情"},
               {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 20000}}]
    result = providers.context_budget(value, [{"role": "user", "content": content}], 512)
    assert result["method"] == "conservative_utf8_estimate" and result["image_count"] == 1
    assert result["reserved_image_tokens"] == 4096 and result["prompt_tokens"] < 100
    assert result["fits"] is True
    value["context_tokens"] = 2048
    assert providers.context_budget(value, [{"role": "user", "content": content}], 512)["fits"] is False


def test_registry_round_trip_cas_preserves_other_config_and_rejects_bad_save(tmp_path):
    path = tmp_path / "factory.json"
    cfg = configuration()
    path.write_text(json.dumps(cfg), encoding="utf-8")
    inventory = providers.public_inventory(cfg)
    edits = copy.deepcopy(inventory["editable_profiles"])
    edits["fixture"]["model"] = "updated-fixture"
    updated = providers.save_registry(edits, inventory["role_assignments"], inventory["revision"], config_path=path)
    saved = json.loads(path.read_text())
    assert saved["private_runtime_policy"] == cfg["private_runtime_policy"]
    assert saved["production"] == cfg["production"] and saved["llm"] == cfg["llm"]
    assert updated["revision"] != inventory["revision"]
    assert saved["llm_profiles"]["fixture"]["model"] == "updated-fixture"
    with pytest.raises(providers.ConfigConflict):
        providers.save_registry(edits, inventory["role_assignments"], inventory["revision"], config_path=path)
    before = path.read_bytes()
    edits["fixture"]["api_key"] = "PRIVATE_AUTH_VALUE"
    with pytest.raises(ValueError, match="credentials"):
        providers.save_registry(edits, inventory["role_assignments"], updated["revision"], config_path=path)
    assert path.read_bytes() == before


def test_concurrent_registry_writers_cannot_overwrite_each_other(tmp_path):
    path = tmp_path / "factory.json"
    cfg = configuration()
    path.write_text(json.dumps(cfg))
    revision = providers.config_revision(cfg)
    def save(model):
        profiles = copy.deepcopy(cfg["llm_profiles"])
        profiles["fixture"]["model"] = model
        try:
            providers.save_registry(profiles, cfg["llm_roles"], revision, config_path=path)
            return "saved", model
        except providers.ConfigConflict:
            return "conflict", model
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ("writer-a", "writer-b")))
    assert sorted(status for status, _ in results) == ["conflict", "saved"]
    winner = next(model for status, model in results if status == "saved")
    assert json.loads(path.read_text())["llm_profiles"]["fixture"]["model"] == winner


@pytest.mark.parametrize("change", ["unknown_role", "unknown_profile", "vision", "disabled"])
def test_registry_rejects_invalid_routes(change):
    cfg = configuration()
    if change == "unknown_role":
        cfg["llm_roles"]["arbitrary_shell"] = "fixture"
    elif change == "unknown_profile":
        cfg["llm_roles"]["screenwriter"] = "missing"
    elif change == "vision":
        cfg["llm_profiles"]["fixture"]["vision"] = False
    else:
        cfg["llm_profiles"]["fixture"]["enabled"] = False
    with pytest.raises(ValueError):
        providers.validate_registry(cfg["llm_profiles"], cfg["llm_roles"], cfg)


def test_public_example_fallback_first_save_and_explicit_missing_path(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ROOT", tmp_path)
    monkeypatch.delenv("DRAMA_FACTORY_CONFIG", raising=False)
    (tmp_path / "config").mkdir()
    example = tmp_path / "config/factory.example.json"
    cfg = configuration()
    example.write_text(json.dumps(cfg))
    assert settings.load_config() == cfg
    inventory = providers.public_inventory()
    providers.save_registry(inventory["editable_profiles"], inventory["role_assignments"], inventory["revision"])
    assert (tmp_path / "config/factory.json").is_file()
    assert json.loads(example.read_text()) == cfg
    monkeypatch.setenv("DRAMA_FACTORY_CONFIG", str(tmp_path / "missing.json"))
    with pytest.raises(FileNotFoundError):
        settings.load_config()


def test_snapshot_ignores_mid_batch_config_edits_and_restores_on_exception(tmp_path, monkeypatch):
    path = tmp_path / "factory.json"
    monkeypatch.setenv("DRAMA_FACTORY_CONFIG", str(path))
    cfg = configuration()
    path.write_text(json.dumps(cfg))
    with pytest.raises(RuntimeError, match="fixture"):
        with settings.config_snapshot() as snapshot:
            snapshot["llm_profiles"]["fixture"]["model"] = "caller-mutated"
            changed = configuration()
            changed["llm_profiles"]["fixture"]["model"] = "next-batch"
            path.write_text(json.dumps(changed))
            assert settings.load_config()["llm_profiles"]["fixture"]["model"] == "fixture-model"
            again = settings.load_config()
            again["llm_profiles"]["fixture"]["model"] = "also-mutated"
            assert settings.load_config()["llm_profiles"]["fixture"]["model"] == "fixture-model"
            with settings.config_snapshot():
                assert settings.load_config()["llm_profiles"]["fixture"]["model"] == "fixture-model"
            raise RuntimeError("fixture")
    assert settings.load_config()["llm_profiles"]["fixture"]["model"] == "next-batch"


def mock_role_transport(monkeypatch, responder):
    calls = []
    monkeypatch.setattr(llm, "check_model", lambda model: {"data": [{"id": model["model"]}]})
    monkeypatch.setattr(llm, "context_budget", lambda *args: {"fits": True})
    def send(base, route, request, **kwargs):
        calls.append(copy.deepcopy(request))
        return responder(request, len(calls))
    monkeypatch.setattr(llm, "request_json", send)
    return calls


def completion(request, raw='{"ok":true}', actual=None):
    return {"model": actual or request["model"], "choices": [
        {"finish_reason": "stop", "message": {"content": raw}}]}


@pytest.mark.parametrize("fmt", ["llama_json_schema", "openai_json_schema", "json_object", "prompt"])
def test_schema_request_modes_keep_local_validation_and_explicit_policy(tmp_path, monkeypatch, fmt):
    cfg = configuration(llm_profiles={"fixture": profile(
        provider="openai_compatible", response_format=fmt, model_aliases=["served-alias"],
        max_tokens_field="max_completion_tokens", send_sampling_parameters=False)})
    calls = mock_role_transport(monkeypatch, lambda request, _: completion(request, actual="served-alias"))
    result = llm.json_call(cfg, "screenwriter", "Write", {}, SCHEMA, tmp_path, max_tokens=128)
    assert result == {"ok": True} and len(calls) == 1
    request = calls[0]
    assert request["max_completion_tokens"] == 128
    assert "max_tokens" not in request and "temperature" not in request and "chat_template_kwargs" not in request
    if fmt == "prompt":
        assert "response_format" not in request
    elif fmt == "json_object":
        assert request["response_format"] == {"type": "json_object"}
    elif fmt == "openai_json_schema":
        assert request["response_format"]["json_schema"]["schema"] == SCHEMA
    else:
        assert request["response_format"]["schema"] == SCHEMA
    if fmt in ("json_object", "prompt"):
        assert json.dumps(SCHEMA, ensure_ascii=False) in request["messages"][0]["content"]
    receipt = json.loads(next(tmp_path.glob("calls/*/receipt.json")).read_text())
    assert receipt["response_model"] == "served-alias"
    assert receipt["config_revision"] == providers.config_revision(cfg)


def test_schema_repair_is_bounded_and_model_or_transport_failure_never_switches_provider(tmp_path, monkeypatch):
    cfg = configuration()
    calls = mock_role_transport(monkeypatch, lambda request, count: completion(request, '{"ok":"wrong"}'))
    with pytest.raises(ValueError, match="合约"):
        llm.json_call(cfg, "screenwriter", "Write", {}, SCHEMA, tmp_path, max_tokens=128)
    assert len(calls) == 2 and all(call["model"] == "fixture-model" for call in calls)
    calls = mock_role_transport(monkeypatch, lambda request, _: completion(request, actual="unconfigured-other"))
    with pytest.raises(ValueError, match="does not match"):
        llm.json_call(cfg, "screenwriter", "Write", {}, SCHEMA, tmp_path, max_tokens=128)
    assert len(calls) == 1


def test_role_receipts_responses_and_exceptions_scrub_active_credential(tmp_path, monkeypatch):
    monkeypatch.setenv("FIXTURE_API_KEY", "PRIVATE_AUTH_VALUE")
    cfg = configuration(llm_profiles={"fixture": profile(api_key_env="FIXTURE_API_KEY")})
    def responder(request, number):
        result = completion(request)
        result["extra"] = "echo PRIVATE_AUTH_VALUE"
        result["debug"] = {"PRIVATE_AUTH_VALUE": {"nested-PRIVATE_AUTH_VALUE": "echo"}}
        result["usage"] = {"PRIVATE_AUTH_VALUE": 1}
        return result
    mock_role_transport(monkeypatch, responder)
    assert llm.json_call(cfg, "screenwriter", "Write", {"PRIVATE_AUTH_VALUE": "task"},
                         SCHEMA, tmp_path, max_tokens=128) == {"ok": True}
    assert all("PRIVATE_AUTH_VALUE" not in path.read_text() for path in tmp_path.rglob("*.json"))
    monkeypatch.setattr(llm, "check_model", Mock(side_effect=RuntimeError("PRIVATE_AUTH_VALUE upstream")))
    with pytest.raises(RuntimeError) as error:
        llm.json_call(cfg, "screenwriter", "Write", {}, SCHEMA, tmp_path, max_tokens=128)
    assert "PRIVATE_AUTH_VALUE" not in str(error.value)
    assert all("PRIVATE_AUTH_VALUE" not in path.read_text() for path in tmp_path.rglob("*.json"))


def test_model_receipt_preserves_execution_context_without_changing_wire_request(tmp_path, monkeypatch):
    from factory.execution_context import current_execution
    cfg = configuration()
    calls = mock_role_transport(monkeypatch, lambda request, _: completion(request))
    identity = {'project_id': '012345abcdef', 'stage': 'screenplay', 'run_id': 'fixture-run'}
    token = current_execution.set(identity)
    try:
        llm.json_call(cfg, 'screenwriter', 'Write', {}, SCHEMA, tmp_path, max_tokens=128)
    finally:
        current_execution.reset(token)
    receipt = json.loads(next(tmp_path.glob('calls/*/receipt.json')).read_text())
    assert receipt['execution_context'] == identity
    assert 'execution_context' not in calls[0]
