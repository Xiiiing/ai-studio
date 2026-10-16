"""Persistent team execution and real loopback HTTP contracts, without model calls."""
from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from factory import store, web


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path / "data")
    monkeypatch.setattr(store, "DATABASE", tmp_path / "data/studio.sqlite3")
    monkeypatch.setattr(web, "ROOT", tmp_path)
    store.initialize()
    return store.create({"brief": "Two fictional adults resolve a misunderstanding in an offline fixture."})


@pytest.fixture
def http_client(project):
    class Quiet(web.Handler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()

    def request(method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            conn.request(method, path, body=json.dumps(body) if body is not None else None,
                         headers={"Content-Type": "application/json", **(headers or {})})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    try:
        yield request
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def database_dump():
    """Compare all durable rows, including history that project revisions cannot reveal."""
    with store.connection() as db:
        return tuple(db.iterdump())


def task(pid, identifier):
    from factory import team_ledger
    return next(row for row in team_ledger.task_page(pid, limit=100)["tasks"] if row["id"] == identifier)


def test_new_v2_projects_enable_control_without_inventing_execution(project):
    from factory import team_ledger

    assert project["team_control_version"] == 1
    value = team_ledger.team_snapshot(project["id"])
    assert value["enabled"] is True
    assert len(value["departments"]) == 7
    assert type(value["control_revision"]) is int and value["control_revision"] >= 0
    assert value["instructions"] == [] and value["locks"] == []
    assert value["members"]
    assert not any(row.get("attempts") for row in team_ledger.task_page(project["id"])["tasks"])


def test_reads_are_durable_database_noops_even_for_legacy_projects(project, http_client):
    from factory import team_ledger

    legacy = store.create({"brief": "An isolated old workflow should retain its historical behavior.",
                           "workflow_version": 1})
    # Existing v2 projects have no new-control marker until explicitly enabled.
    old_v2 = store.create({"brief": "An existing v2 project with no execution evidence.",
                           "workflow_version": 1})
    with store.connection() as db:
        p = store._read(db, old_v2["id"])
        p["workflow_version"] = 2
        p.pop("team_control_version", None)
        db.execute("UPDATE projects SET snapshot=? WHERE id=?", (store.encoded(p), p["id"]))

    before = database_dump()
    for item, enabled in ((project, True), (legacy, False), (old_v2, False)):
        pid = item["id"]
        assert team_ledger.team_snapshot(pid)["enabled"] is enabled
        team_ledger.task_page(pid)
        assert http_client("GET", f"/api/factory/projects/{pid}/team")[0] == 200
        assert http_client("GET", f"/api/factory/projects/{pid}/tasks")[0] == 200
    assert database_dump() == before


@pytest.mark.parametrize("query", [
    "offset=-1", "offset=1.5", "offset=one", "offset=0&offset=1",
    "limit=0", "limit=101", "limit=true", "limit=10&limit=20",
    "unknown=value", "department=x&department=y", "status=x&status=y",
    "shot_id=S01&shot_id=S02",
])
def test_http_task_queries_reject_malformed_or_unbounded_inputs(project, http_client, query):
    before = database_dump()
    status, body = http_client("GET", f"/api/factory/projects/{project['id']}/tasks?{query}")
    assert status == 400 and body["error"]
    assert database_dump() == before


@pytest.mark.parametrize("route", ["team", "tasks", "tasks/image:S01"])
@pytest.mark.parametrize("headers", [{"Host": "rebind.example"}, {"Origin": "https://cross-site.example"}])
def test_team_http_reads_preserve_origin_boundary(project, http_client, route, headers):
    before = database_dump()
    status, body = http_client("GET", f"/api/factory/projects/{project['id']}/{route}", headers=headers)
    assert status == 403 and body["error"]
    assert database_dump() == before


def test_real_execution_keeps_review_separate_and_model_provenance_across_restart(project, http_client):
    from factory import team_ledger

    pid = project["id"]
    token = team_ledger.begin_unit(pid, "image", "S01", input_version="script-v1",
                                   inputs=[{"label": "人物参考", "version": 2}])
    current = task(pid, "image:S01")
    assert current["status"] == "running" and current["attempt"] == 1
    artifact = {"path": f"production/factory-{pid}/shots/S01/frame.png", "sha256": "a" * 64}
    assert team_ledger.finish_unit(token, artifact=artifact, model="recorded-model-v1")
    assert not team_ledger.finish_unit(token, artifact="replacement.png", model="replacement-model")

    # Migration and opening a fresh connection must retain original receipts.
    store.initialize()
    status, value = http_client("GET", f"/api/factory/projects/{pid}/tasks/image:S01")
    assert status == 200
    saved = value["task"]
    assert saved["status"] == "completed"
    assert saved["review_status"] == "pending" and saved["adoption_status"] == "unselected"
    assert saved["model"] == "recorded-model-v1"
    assert saved["input_version"] == "script-v1"
    assert saved["inputs"] == [{"label": "人物参考", "version": 2}]
    assert saved["artifacts"][0]["artifact_url"] == "/media/" + artifact["path"]
    assert saved["artifacts"][0]["sha256"] == "a" * 64
    assert len(saved["attempts"]) == 1


def test_late_generation_is_fenced_but_external_receipt_is_preserved(project):
    from factory import team_ledger

    pid = project["id"]
    old = team_ledger.begin_unit(pid, "video", "S01", input_version="video-v1")
    new = team_ledger.begin_unit(pid, "video", "S01", input_version="video-v2")
    assert old["task_id"] == new["task_id"] == "video:S01"
    assert new["generation"] > old["generation"]
    assert not team_ledger.is_current(old) and team_ledger.is_current(new)
    late = {"path": f"production/factory-{pid}/shots/S01/old.mp4"}
    assert not team_ledger.finish_unit(old, artifact=late, model="old-model",
                                       details={"remote_task_id": "external-task-1", "cost": 2.5})
    assert task(pid, "video:S01")["status"] == "running"
    assert team_ledger.finish_unit(new, artifact=f"production/factory-{pid}/shots/S01/current.mp4",
                                   review_status="pending", model="current-model")
    current = task(pid, "video:S01")
    assert current["model"] == "current-model" and current["input_version"] == "video-v2"
    assert current["artifacts"][0]["path"].endswith("/current.mp4")
    historical = next(row for row in current["attempts"] if row["run_id"] == old["run_id"])
    assert historical["status"] == "superseded"
    assert historical["artifact"]["path"] == late["path"]
    assert historical["details"]["remote_task_id"] == "external-task-1"
    assert historical["details"]["cost"] == 2.5


def test_reused_artifact_is_not_claimed_as_a_new_model_execution(project):
    from factory import team_ledger

    pid = project["id"]
    team_ledger.mark_reused(pid, "speech", "S01_D01", input_version="line-v1",
                            artifact=f"production/factory-{pid}/audio/line.wav")
    saved = task(pid, "speech:S01_D01")
    assert saved["status"] == "reused" and saved["attempts"] == [] and saved["model"] is None
    before = database_dump()
    assert team_ledger.begin_unit("0" * 12, "image", "S01") is None
    assert database_dump() == before


def test_task_filters_paginate_without_cross_project_leakage(project, http_client):
    from factory import team_ledger

    pid = project["id"]
    for kind, scope in (("image", "S01"), ("video", "S01"), ("image", "S02"), ("speech", "S01_D01")):
        token = team_ledger.begin_unit(pid, kind, scope, input_version="v1")
        assert team_ledger.finish_unit(token)
    other = store.create({"brief": "A different fictional project must keep its own tasks."})
    token = team_ledger.begin_unit(other["id"], "image", "S01", input_version="foreign-version")
    assert team_ledger.finish_unit(token)

    path = f"/api/factory/projects/{pid}/tasks?department=visual&status=completed&offset=1&limit=1"
    status, value = http_client("GET", path)
    assert status == 200 and value["total"] == 3
    assert value["offset"] == 1 and value["limit"] == 1 and len(value["tasks"]) == 1
    status, value = http_client("GET", f"/api/factory/projects/{pid}/tasks?shot_id=S01&status=completed")
    assert status == 200 and {row["id"] for row in value["tasks"]} == {"image:S01", "video:S01", "speech:S01_D01"}
    assert all(row["input_version"] == "v1" for row in value["tasks"])
    assert http_client("GET", f"/api/factory/projects/{pid}/tasks/image:S99")[0] == 404
    assert http_client("GET", "/api/factory/projects/000000000000/team")[0] == 404


def test_script_definitions_expose_real_dependencies_without_completion_evidence(project):
    from factory import team_ledger

    store.update(project["id"], {"script": {
        "cast": {"A": {"description": "A fictional adult in a green coat."}},
        "shots": [
            {"id": "S01", "cast": ["A"], "prompt": "An adult enters.",
             "dialogue": [{"id": "S01_D01", "role": "A", "text": "Hello."}]},
            {"id": "S02", "cast": ["A"], "prompt": "The adult leaves.", "reference_shots": ["S01"], "dialogue": []},
        ],
    }})
    before = database_dump()
    first = task(project["id"], "image:S01")
    second = task(project["id"], "image:S02")
    assert "character:A" in first["dependencies"]
    assert "image:S01" in second["dependencies"]
    assert task(project["id"], "video:S01")["dependencies"] == ["image_review:S01"]
    assert "speech:S01_D01" in task(project["id"], "dialogue:S01")["dependencies"]
    assert "video_review:S02" in task(project["id"], "edit:delivery")["dependencies"]
    assert all(row["status"] == "pending" and row["attempts"] == []
               for row in team_ledger.task_page(project["id"], limit=100)["tasks"])
    assert database_dump() == before


def test_paused_attempt_remains_resumable_without_becoming_approved(project):
    from factory import team_ledger
    from factory.production import ProductionPaused

    pid = project["id"]
    with pytest.raises(ProductionPaused):
        with team_ledger.unit(pid, "image", "S01", input_version="same-input"):
            raise ProductionPaused("Synthetic operator pause")
    paused = task(pid, "image:S01")
    assert paused["status"] == "paused" and paused["review_status"] == "pending"
    assert paused["attempts"][0]["details"] == {"error_type": "ProductionPaused"}
    store.initialize()
    resumed = team_ledger.begin_unit(pid, "image", "S01", input_version="same-input")
    assert resumed["generation"] == paused["generation"] and resumed["attempt"] == 2
    assert team_ledger.finish_unit(resumed)
    assert {row["status"] for row in task(pid, "image:S01")["attempts"]} == {"paused", "completed"}
    assert task(pid, "image:S01")["review_status"] == "pending"
