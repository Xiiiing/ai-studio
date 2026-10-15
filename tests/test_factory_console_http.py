"""Real HTTP/SQLite recovery contracts; the worker and providers never run."""

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from factory import config, store, web


@pytest.fixture
def endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(store, "DATABASE", tmp_path / "studio.sqlite3")
    monkeypatch.setattr(config, "load_config", lambda: {"llm_roles": {}})
    monkeypatch.setattr(web, "snapshot", lambda: {"worker": {"available": False}})
    store.initialize()

    class Quiet(web.Handler):
        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    server.daemon_threads = True
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()

    def request(method, path, body=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        try:
            connection.request(
                method,
                "/api/factory" + path,
                body=None if body is None else json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        yield request
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def create(endpoint):
    code, result = endpoint(
        "POST", "/projects", {"brief": "成年邻居共同寻找钥匙，完成温暖反转。"}
    )
    assert code == 202
    return result["project"]


@pytest.mark.parametrize(
    "command,inputs",
    [
        (
            "provide_authorized_source",
            {"rights": "owned", "text": "作者本人允许改编的原创邻里故事。"},
        ),
        ("switch_to_original", {}),
    ],
)
def test_source_inbox_descriptor_reaches_real_recovery_transaction(
    endpoint, command, inputs
):
    project = create(endpoint)
    assert project["workflow_version"] == 2 and project["team_mode"] == "delegated"
    project = store.update(
        project["id"],
        {
            "status": "blocked",
            "stage": "research",
            "creation_mode": "adaptation",
            "needs_decision": {
                "kind": "source_rights",
                "type": "review",
                "message": "原文未授权或无法读取",
            },
        },
    )
    code, dashboard = endpoint("GET", "/console")
    assert code == 200 and dashboard["metrics"]["attention"] == 1
    attention = dashboard["projects"][0]["attention"]
    assert command in {action["id"] for action in attention["actions"]}
    route = "/projects/" + project["id"] + "/action"
    code, result = endpoint(
        "POST",
        route,
        {"action": command, "expected_revision": project["revision"], **inputs},
    )
    assert code == 202
    after = result["project"]
    assert after["status"] == "queued" and after["workflow_restart_from"] == "research"
    assert after["needs_decision"] is None and after["budget"] == project["budget"]
    code, _ = endpoint(
        "POST",
        route,
        {"action": command, "expected_revision": project["revision"], **inputs},
    )
    assert code == 409 and store.get(project["id"], False) == after


def test_http_old_gate_is_rejected_even_with_current_revision(endpoint):
    project = create(endpoint)
    store.update(
        project["id"],
        {
            "proposals": [{"id": "A", "eligible": True}],
            "proposals_complete_generation": 1,
            "creation_review": {"passed": True},
        },
    )
    gate = store.gate(project["id"], "story", [{"id": "A"}], "选择故事", 1)
    current = store.update(project["id"], {"proposal_generation": 2})
    code, error = endpoint(
        "POST",
        "/projects/" + project["id"] + "/decision",
        {
            "type": "story",
            "choice": "A",
            "decision_id": gate["id"],
            "expected_revision": current["revision"],
        },
    )
    assert code == 409 and "旧版本" in error["error"]
    assert store.get(project["id"], False) == current


def test_http_visual_rejection_is_bound_to_current_gate_and_feedback(endpoint):
    project = create(endpoint)
    store.update(
        project["id"],
        {
            "styles": [{"id": "ink"}],
            "style_previews_generation": 1,
            "script_complete_generation": 1,
            "script_review": {"passed": True},
        },
    )
    gate = store.gate(project["id"], "style", [{"id": "ink"}], "选择画风", 1)
    before = store.get(project["id"], False)
    payload = {
        "type": "style",
        "choice": "__restyle__",
        "decision_id": gate["id"],
        "expected_revision": before["revision"],
    }
    route = "/projects/" + project["id"] + "/decision"
    assert endpoint("POST", route, payload)[0] == 409
    payload["feedback"] = "保留人物身份，改为低饱和水彩和自然光。"
    code, result = endpoint("POST", route, payload)
    assert (
        code == 202
        and result["project"]["answers"][gate["id"]]["feedback"] == payload["feedback"]
    )
    assert endpoint("POST", route, payload) == (202, result)


@pytest.mark.parametrize(
    "query", ["offset=-1", "limit=abc", "offset=0&offset=1", "unknown=1"]
)
def test_console_rejects_ambiguous_pagination(endpoint, query):
    assert endpoint("GET", "/console?" + query)[0] == 400
