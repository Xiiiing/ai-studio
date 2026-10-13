"""Protocol adapters exercised through offline transports and a loopback HTTP fixture."""
import copy
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import Mock

import pytest

from factory import config, llm, providers


SCHEMA = {"type": "object", "additionalProperties": False,
          "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def model_profile(**changes):
    return {"provider": "ollama_native", "base_url": "http://127.0.0.1:11434",
            "model": "qwen-fixture", "context_tokens": 32768, "vision": True, **changes}


def configuration(profile):
    return {"schema_version": 1, "llm_profiles": {"selected": profile},
            "llm_roles": {"screenwriter": "selected", "visual_critic": "selected"}}


def native_response(raw='{"ok":true}', **changes):
    return {"model": "qwen-fixture:latest", "done": True, "done_reason": "stop",
            "message": {"role": "assistant", "content": raw},
            "prompt_eval_count": 41, "eval_count": 9, "eval_duration": 123456, **changes}


@pytest.fixture
def native_server(monkeypatch):
    state = {"requests": [], "responses": [], "prefix": ""}
    def no_external_dns(host, *args, **kwargs):
        raise AssertionError("This test must use its literal loopback address")
    monkeypatch.setattr(socket, "getaddrinfo", no_external_dns)
    monkeypatch.setattr(socket, "getfqdn", lambda *args: "localhost")
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            state["requests"].append(("GET", self.path, None))
            assert self.path == state["prefix"] + "/api/tags"
            self.reply({"models": [{"name": "qwen-fixture:latest", "model": "qwen-fixture:latest"}]})
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append(("POST", self.path, body))
            assert self.path == state["prefix"] + "/api/chat"
            self.reply(state["responses"].pop(0) if state["responses"] else native_response())
        def reply(self, value):
            data = json.dumps(value).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    worker.start()
    try:
        yield state, "http://127.0.0.1:" + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.parametrize("suffix,prefix", [("", ""), ("/api", ""), ("/gateway", "/gateway"),
                                           ("/gateway/api", "/gateway")])
def test_native_health_and_schema_repair_use_the_configured_prefix(native_server, tmp_path, suffix, prefix):
    state, base = native_server
    state["prefix"] = prefix
    state["responses"] = [native_response('{"ok":"wrong"}'), native_response()]
    profile = model_profile(base_url=base + suffix, thinking=False)
    cfg = configuration(profile)
    health = providers.probe_profile("selected", cfg)
    assert health["status"] == "ready" and health["model_match"] is True
    assert health["models"] == ["qwen-fixture:latest"] and health["generation"] is False
    assert len(state["requests"]) == 1 and state["requests"][0][0] == "GET"
    assert llm.json_call(cfg, "screenwriter", "Write", {"brief": "test"}, SCHEMA,
                         tmp_path, max_tokens=256) == {"ok": True}
    posts = [body for method, _, body in state["requests"] if method == "POST"]
    assert len(posts) == 2
    assert posts[0]["options"] == {"num_predict": 256, "num_ctx": 32768, "temperature": .65, "top_p": .9}
    assert posts[0]["format"] == SCHEMA and posts[0]["think"] is False
    assert posts[0]["stream"] is False and posts[0]["model"] == "qwen-fixture"
    assert "response_format" not in posts[0] and "max_tokens" not in posts[0]
    assert [message["role"] for message in posts[1]["messages"]] == ["system", "user", "assistant", "user"]
    receipt = json.loads(next(tmp_path.glob("calls/*/receipt.json")).read_text())
    assert receipt["configured_model"] == "qwen-fixture" and receipt["response_model"] == "qwen-fixture:latest"
    assert receipt["model_discovery"] == "verified" and receipt["status"] == "succeeded"
    assert receipt["parameters"]["max_tokens_field"] == "options.num_predict"
    assert receipt["attempts"][0]["usage"] == {"prompt_tokens": 41, "completion_tokens": 9, "total_tokens": 50}
    assert receipt["attempts"][0]["timings"] == {"eval_duration": 123456}
    native = json.loads(next(tmp_path.glob("calls/*/response-01.json")).read_text())
    assert native["done"] is True and native["message"]["content"] == '{"ok":"wrong"}'


def test_native_vision_translates_inline_images_and_keeps_role_prefix(native_server, tmp_path):
    state, base = native_server
    cfg = configuration(model_profile(base_url=base, system_prompt_mode="user_prefix"))
    content = [{"type": "text", "text": "Review this frame"},
               {"type": "image_url", "image_url": {"url": "data:image/png;base64,eA=="}}]
    assert llm._request(cfg, "visual_critic", "Review", None, SCHEMA, tmp_path,
                        content=content, max_tokens=128) == {"ok": True}
    body = next(body for method, _, body in state["requests"] if method == "POST")
    assert len(body["messages"]) == 1
    message = body["messages"][0]
    assert message["role"] == "user" and message["images"] == ["eA=="]
    assert "Review this frame" in message["content"] and "任务资料开始" in message["content"]
    assert "data:image" not in message["content"] and "think" not in body


@pytest.mark.parametrize("change,error", [
    ({"model": "another-model"}, "does not match"),
    ({"done_reason": "length"}, "length"),
    ({"done": False}, "incomplete"),
    ({"message": None}, "invalid completion"),
    ({"message": {"role": "assistant", "content": None}}, "no JSON text"),
])
def test_native_failure_never_falls_back_or_repairs_identity_or_incomplete_output(
        native_server, tmp_path, change, error):
    state, base = native_server
    state["responses"] = [native_response(**change)]
    with pytest.raises(ValueError, match=error):
        llm.json_call(configuration(model_profile(base_url=base)), "screenwriter", "Write", {},
                      SCHEMA, tmp_path, max_tokens=128)
    assert len([r for r in state["requests"] if r[0] == "POST"]) == 1
    receipt = json.loads(next(tmp_path.glob("calls/*/receipt.json")).read_text())
    assert receipt["status"] == "failed" and receipt["attempts"][0]["usage"]["completion_tokens"] == 9


@pytest.mark.parametrize("mode,expected", [("json_object", "json"), ("prompt", None),
                                          ("openai_json_schema", SCHEMA), ("llama_json_schema", SCHEMA)])
def test_native_json_modes_and_sampling_omission(mode, expected):
    profile = providers.normalize_profile(model_profile(response_format=mode))
    parameters = {"max_tokens": 128, "max_tokens_field": "max_tokens",
                  "send_sampling_parameters": False, "chat_template_kwargs": {}}
    route, body = providers.chat_request(profile, [{"role": "user", "content": "JSON"}],
                                         SCHEMA, "screenwriter", parameters)
    assert route == "/api/chat" and body.get("format") == expected
    assert body["options"] == {"num_predict": 128, "num_ctx": 32768}
    assert "temperature" not in body


@pytest.mark.parametrize("url", ["https://example.com/image.png", "file:///private/image.png",
                                 "data:image/png;base64,eA=", "data:text/plain;base64,eA=="])
def test_native_images_do_not_fetch_urls_or_read_paths(url):
    with pytest.raises(ValueError, match="image"):
        providers._ollama_messages([{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": url}}]}])


@pytest.mark.parametrize("provider", ["ollama_native", "ollama"])
def test_ollama_model_tag_equivalence_does_not_change_namespaces_or_versions(provider):
    profile = model_profile(provider=provider, model="team/qwen", model_aliases=["explicit-alias:7b"])
    assert providers.model_matches(profile, "team/qwen:latest")
    assert providers.model_matches(profile, "team/qwen")
    assert providers.model_matches(profile, "explicit-alias:7b")
    for value in ("qwen:latest", "other/qwen:latest", "team/qwen:7b", "explicit-alias:latest", "", None):
        assert not providers.model_matches(profile, value)
    assert not providers.model_matches(model_profile(provider="deepseek"), "qwen-fixture:latest")


@pytest.mark.parametrize("provider,suffix", [("deepseek", ""), ("lm_studio", "/v1"),
                                            ("dashscope", "/compatible-mode/v1"),
                                            ("openai_compatible", "/proxy/custom-prefix")])
def test_compatible_endpoints_preserve_prefix_and_model_identity(tmp_path, monkeypatch, provider, suffix):
    calls = []
    profile = model_profile(provider=provider, base_url="http://127.0.0.1:8090" + suffix,
                            model="served-model", model_discovery="skip")
    def request(base, route, body, **kwargs):
        calls.append((base, route, copy.deepcopy(body)))
        return {"model": body["model"], "choices": [
            {"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]}
    monkeypatch.setattr(llm, "request_json", request)
    assert llm.json_call(configuration(profile), "screenwriter", "Write", {}, SCHEMA,
                         tmp_path, max_tokens=128) == {"ok": True}
    base, route, body = calls[0]
    assert base == profile["base_url"] and route == "/chat/completions" and body["model"] == "served-model"
    if provider == "dashscope":
        assert body["enable_thinking"] is False and "chat_template_kwargs" not in body
    receipt = json.loads(next(tmp_path.glob("calls/*/receipt.json")).read_text())
    assert receipt["model_discovery"] == "skipped" and receipt["response_model"] == "served-model"


def test_skipped_discovery_does_not_claim_health_or_skip_completion_identity(tmp_path, monkeypatch):
    profile = model_profile(provider="dashscope", base_url="http://127.0.0.1/compatible-mode/v1")
    cfg = configuration(profile)
    no_network = Mock(side_effect=AssertionError("A configuration-only probe must not connect"))
    monkeypatch.setattr(providers, "request_json", no_network)
    health = providers.probe_profile("selected", cfg)
    assert health["status"] == "unverified" and health["model_match"] is False and health["models"] == []
    assert health["model_discovery"] == "skip" and health["generation"] is False
    assert no_network.call_count == 0
    calls = []
    def wrong_identity(*args, **kwargs):
        calls.append(args)
        return {"model": "unconfigured-model", "choices": [
            {"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]}
    monkeypatch.setattr(llm, "request_json", wrong_identity)
    with pytest.raises(ValueError, match="does not match"):
        llm.json_call(cfg, "screenwriter", "Write", {}, SCHEMA, tmp_path, max_tokens=128)
    assert len(calls) == 1


@pytest.mark.parametrize("changes", [
    {"thinking": "false"}, {"provider": "deepseek", "thinking": False},
    {"chat_template_kwargs": {"enable_thinking": False}},
    {"base_url": "http://127.0.0.1:11434/v1"}, {"base_url": "http://127.0.0.1:11434/api/chat"},
    {"model_discovery": "skip"}, {"model_discovery": "auto"},
])
def test_native_unsupported_policy_is_rejected_before_execution(changes):
    with pytest.raises(ValueError):
        providers.normalize_profile(model_profile(**changes))


@pytest.mark.parametrize("route", ["/models", "/chat/completions", "/api/pull", "/api/delete", "/props"])
def test_native_route_allowlist_cannot_start_model_management_or_other_protocols(monkeypatch, route):
    no_network = Mock(side_effect=AssertionError("Invalid route must fail before DNS"))
    monkeypatch.setattr(providers, "_resolve_addresses", no_network)
    profile = providers.normalize_profile(model_profile())
    with pytest.raises(ValueError, match="Unsupported model API route"):
        providers.request_json(profile["base_url"], route, profile=profile)
    assert no_network.call_count == 0


def test_thinking_and_discovery_are_part_of_cached_model_identity():
    cfg = configuration(model_profile(thinking=False))
    original = config.llm_identity(cfg, "screenwriter")
    cfg["llm_profiles"]["selected"]["thinking"] = True
    assert config.llm_identity(cfg, "screenwriter") != original
    cfg = configuration(model_profile(provider="openai_compatible", model_discovery="required"))
    original = config.llm_identity(cfg, "screenwriter")
    cfg["llm_profiles"]["selected"]["model_discovery"] = "skip"
    assert config.llm_identity(cfg, "screenwriter") != original
