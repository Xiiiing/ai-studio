"""HTTP contracts for the workflow and model UI; no live models or projects."""
from __future__ import annotations

import copy
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from factory import providers, web, workflow_spec

PROJECT_ID = "012345abcdef"


@pytest.fixture
def endpoint(monkeypatch):
    calls = []
    inventory = {
        "revision": "revision-a",
        "profiles": [{"id": "writer", "model": "writer-model", "vision": True}],
        "editable_profiles": {"writer": {
            "provider": "llama_cpp", "base_url": "http://127.0.0.1:8090/v1",
            "model": "writer-model", "context_tokens": 32768, "vision": True,
            "api_key_env": "MODEL_KEY", "chat_template_kwargs": {"enable_thinking": False},
        }},
        "role_assignments": {"screenwriter": "writer", "visual_critic": "writer"},
        "roles": [{"role": "screenwriter", "profile": "writer"}],
        "providers": [], "capabilities": {},
    }
    project = {"id": PROJECT_ID, "status": "awaiting_final", "revision": 9,
               "needs_decision": {"type": "final", "id": PROJECT_ID + ":final:1"}}
    monkeypatch.setattr(providers, "public_inventory", lambda: copy.deepcopy(inventory))
    def probe(profile_id, *, timeout=8):
        calls.append(("probe", profile_id, timeout))
        return {"profile": profile_id, "status": "ready", "model_match": True,
                "configured_model": "writer-model", "models": ["writer-model"],
                "generation": False, "elapsed_ms": 3}
    monkeypatch.setattr(providers, "probe_profile", probe)
    def save(profiles, roles, expected_revision):
        if expected_revision != inventory["revision"]:
            raise providers.ConfigConflict("Configuration changed; reload before saving")
        calls.append(("save", copy.deepcopy(profiles), copy.deepcopy(roles), expected_revision))
        result = copy.deepcopy(inventory)
        result["revision"] = "revision-b"
        return result
    monkeypatch.setattr(providers, "save_registry", save)
    monkeypatch.setattr(workflow_spec, "export_spec", lambda: {
        "orchestrator": "LangGraph", "nodes": [{"id": "research"}],
        "edges": [{"source": "__start__", "target": "research"}],
    })
    monkeypatch.setattr(web.store, "list_projects", lambda: [project])
    monkeypatch.setattr(web.store, "queue_summary", lambda: {"running": 0})
    monkeypatch.setattr(web, "snapshot", lambda: {"worker": {"available": True}})
    monkeypatch.setattr(web.store, "get", lambda pid: copy.deepcopy(project))
    monkeypatch.setattr(web.store, "decide", lambda pid, payload: calls.append(("decision", pid, payload)) or copy.deepcopy(project))
    monkeypatch.setattr(web.store, "action", lambda pid, payload: calls.append(("action", pid, payload)) or copy.deepcopy(project))
    monkeypatch.setattr(web.store, "create", lambda payload: calls.append(("create", payload)) or copy.deepcopy(project))

    class QuietHandler(web.Handler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()

    class Client:
        def request(self, method, path, payload=None, *, headers=None):
            body = None if payload is None else json.dumps(payload).encode()
            headers = {"Host": f"127.0.0.1:{server.server_port}", **(headers or {})}
            if body is not None:
                headers.setdefault("Content-Type", "application/json")
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                connection.request(method, path, body=body, headers=headers)
                response = connection.getresponse()
                raw = response.read()
                content_type = response.getheader("Content-Type", "")
                result = json.loads(raw) if content_type.startswith("application/json") else raw
                return response.status, dict(response.getheaders()), result
            finally:
                connection.close()
        port = server.server_port

    try:
        yield Client(), calls, inventory, project
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_inventory_and_real_topology_are_read_only(endpoint):
    client, calls, inventory, _ = endpoint
    code, headers, data = client.request("GET", "/api/factory/models")
    assert code == 200 and data == inventory
    assert headers["Cache-Control"] == "no-store"
    assert data["editable_profiles"]["writer"]["api_key_env"] == "MODEL_KEY"
    code, _, data = client.request("GET", "/api/factory/workflow")
    assert code == 200 and data["nodes"] == [{"id": "research"}]
    assert calls == []


def test_saved_profile_probe_has_no_generation(endpoint):
    client, calls, _, _ = endpoint
    code, _, data = client.request("POST", "/api/factory/models/test", {"profile_id": "writer"})
    assert code == 200 and data["model_match"] is True and data["generation"] is False
    assert calls == [("probe", "writer", 8)]


@pytest.mark.parametrize("payload", [{"profile_id": "writer", "base_url": "https://example.test/v1"},
                                      {"profile_id": 123}, {"profile": "writer"}])
def test_probe_only_accepts_a_saved_profile_identifier(endpoint, payload):
    client, calls, _, _ = endpoint
    code, _, data = client.request("POST", "/api/factory/models/test", payload)
    assert code == 409 and data["error"]
    assert calls == []


def test_model_save_preserves_advanced_parameters_and_revision(endpoint):
    client, calls, inventory, _ = endpoint
    payload = {"profiles": inventory["editable_profiles"], "roles": inventory["role_assignments"],
               "expected_revision": inventory["revision"]}
    code, _, data = client.request("POST", "/api/factory/models", payload,
                                  headers={"Content-Type": "Application/JSON; charset=utf-8"})
    assert code == 200 and data["revision"] == "revision-b"
    assert calls == [("save", payload["profiles"], payload["roles"], "revision-a")]
    assert calls[0][1]["writer"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_stale_model_save_fails_without_writing(endpoint):
    client, calls, inventory, _ = endpoint
    code, _, data = client.request("POST", "/api/factory/models", {
        "profiles": inventory["editable_profiles"], "roles": inventory["role_assignments"],
        "expected_revision": "older",
    })
    assert code == 409 and "changed" in data["error"]
    assert calls == [] and inventory["revision"] == "revision-a"


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("headers", [
    {"Host": "untrusted.example"}, {"Origin": "https://untrusted.example"},
    {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"},
])
def test_model_registry_rejects_other_origins_before_any_operation(endpoint, method, headers):
    client, calls, _, _ = endpoint
    code, _, data = client.request(method, "/api/factory/models", {} if method == "POST" else None,
                                  headers=headers)
    assert code == 403 and data["error"]
    assert calls == []


def test_actual_listening_port_and_localhost_origin_are_supported(endpoint):
    client, calls, _, _ = endpoint
    host = f"localhost:{client.port}"
    code, _, _ = client.request("POST", "/api/factory/models/test", {"profile_id": "writer"},
                               headers={"Host": host, "Origin": "http://" + host})
    assert code == 200 and len(calls) == 1


def test_console_serves_built_assets_only(endpoint, tmp_path, monkeypatch):
    client, calls, _, _ = endpoint
    monkeypatch.setattr(web, "ROOT", tmp_path)
    dist = tmp_path / "ui/factory/dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<html><div id="root"></div><script type="module" src="/factory/static/assets/app-hash.js"></script></html>')
    (dist / "assets/app-hash.js").write_text('document.title = "AI Studio";')
    (dist / "assets/app-hash.css").write_text('body { color: #172033; }')
    code, headers, html = client.request("GET", "/")
    assert code == 200 and headers["X-Content-Type-Options"] == "nosniff"
    assert b'id="root"' in html and b'/factory/static/assets/app-hash.js' in html
    for name in ("assets/app-hash.js", "assets/app-hash.css"):
        code, _, body = client.request("GET", "/factory/static/" + name)
        assert code == 200 and body
    assert client.request("GET", "/factory/static/src/main.tsx")[0] == 404
    assert client.request("GET", "/factory/static/assets/app-hash.js.map")[0] == 404
    assert calls == []


def test_missing_ui_build_is_actionable_without_starting_work(endpoint, tmp_path, monkeypatch):
    client, calls, _, _ = endpoint
    monkeypatch.setattr(web, "ROOT", tmp_path)
    code, _, body = client.request("GET", "/")
    assert code == 503 and body["code"] == "ui_build_missing"
    assert "npm run build" in body["error"]
    assert calls == []


def test_existing_project_read_and_decision_paths_are_preserved(endpoint):
    client, calls, _, project = endpoint
    code, _, data = client.request("GET", "/api/factory/projects/" + PROJECT_ID)
    assert code == 200 and data == project and calls == []
    payload = {"type": "final", "choice": "revise", "feedback": "测试意见",
               "decision_id": PROJECT_ID + ":final:1"}
    code, _, data = client.request("POST", "/api/factory/projects/" + PROJECT_ID + "/decision", payload)
    assert code == 202 and data["project"] == project
    assert calls == [("decision", PROJECT_ID, payload)]


def test_existing_project_create_and_pause_contracts_are_preserved(endpoint):
    client, calls, _, project = endpoint
    payload = {"brief": "仅模拟请求，不建立生产项目", "request_id": "http-only-test"}
    code, _, data = client.request("POST", "/api/factory/projects", payload)
    assert code == 202 and data["project"] == project
    code, _, _ = client.request("POST", "/api/factory/projects/" + PROJECT_ID + "/action", {"action": "pause"})
    assert code == 202
    assert calls == [("create", payload), ("action", PROJECT_ID, {"action": "pause"})]


def test_template_import_export_and_separate_body_limit(endpoint, monkeypatch, tmp_path):
    from factory import workflow_templates as wt
    from studio.http_routes import FACTORY_ROUTES
    monkeypatch.setattr(wt, 'DATA', tmp_path)
    client, _, _, _ = endpoint
    value = {'name': 'API graph', 'kind': 'image',
             'graph': {'1': {'class_type': 'Prompt', 'inputs': {'text': ''}}},
             'mappings': [{'node': '1', 'input': 'text', 'source': 'prompt'}],
             'output': {'node': '1', 'field': 'images', 'index': 0}}
    code, _, saved = client.request('POST', '/api/factory/workflow-templates', value)
    assert code == 200
    code, _, exported = client.request('GET', '/api/factory/workflow-templates/' + saved['id'])
    assert code == 200 and exported == value
    assert next(r for r in FACTORY_ROUTES if r.name == 'workflow_template_import').body_limit == 1048576
    assert next(r for r in FACTORY_ROUTES if r.name == 'model_save').body_limit == 65536
