"""Frozen project evidence through real HTTP; no model or production side effects."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import uuid

import pytest

from factory import quality_baselines as q, store
from factory.creative_evidence_io import digest, read_json
from studio.http_security import HTTPError
from test_factory_team_ledger import project, http_client, database_dump


def body(p, **changes):
    return {"request_id": uuid.uuid4().hex, "label": "优化前", "expected_revision": p["revision"], **changes}


def call(root, p, *, status="succeeded", elapsed=2.5):
    folder = root / "production" / p["slug"] / "planning/llm/calls/offline-call-1"
    folder.mkdir(parents=True)
    request = {"model": "historical-model", "messages": [{"role": "system", "content": "PRIVATE-ROLE-INSTRUCTIONS"},
                {"role": "user", "content": "PRIVATE-PROJECT-INPUT"}]}
    receipt = {"call_id": folder.name, "role": "screenwriter", "configured_model": "historical-model",
        "response_model": "historical-model" if status == "succeeded" else None,
        "model_identity": {"endpoint": "PRIVATE-CONNECTION", "model": "historical-model"},
        "config_revision": "old-config", "parameters": {"temperature": .5},
        "status": status, "attempts": [{}, {}], "elapsed_seconds": elapsed,
        "execution_context": {"project_id": p["id"], "stage": "screenplay"},
        "prompt": {"status": "registered", "version": "1.0.0", "input_sha256": digest(request)}}
    for name, value in (("receipt.json", receipt), ("request-01.json", request)):
        (folder / name).write_text(json.dumps(value))
    return folder


def freeze(p, root, **changes):
    return q.create(p["id"], body(p, **changes), root=root)


def test_http_freeze_and_read_have_no_project_queue_or_model_side_effects(project, http_client, monkeypatch):
    from factory import llm, config
    monkeypatch.setattr(llm, "_request", lambda *a, **k: pytest.fail("No model may run"))
    monkeypatch.setattr(config, "load_config", lambda: pytest.fail("No current config may fill history gaps"))
    before = database_dump()
    url = f"/api/factory/projects/{project['id']}/quality-baselines"
    assert http_client("GET", url)[1]["baselines"] == []
    assert not q._folder(project["id"]).exists()
    status, saved = http_client("POST", url, body(project))
    assert status == 201
    assert http_client("GET", url)[1]["baselines"][0]["id"] == saved["id"]
    detail = http_client("GET", url + "/" + saved["id"])[1]
    assert detail["metrics"]["elapsed_seconds_sum"] is None
    assert detail["metrics"]["schema_retries"] is None
    assert detail["quality_conclusion"] == "not_established" and detail["reviews"] == []
    assert database_dump() == before


def test_private_inputs_frozen_but_public_projection_never_returns_raw_request(project, tmp_path):
    folder = call(tmp_path, project)
    (folder / "response-01.json").write_text('{"choices":[{"message":{"content":"PRIVATE-FAILED-OUTPUT"}}]}')
    saved = freeze(project, tmp_path)
    value = q.detail(project["id"], saved["id"])
    assert value["records"][0]["configured_model"] == "historical-model"
    assert value["metrics"]["schema_retries"] == 1
    assert value["metrics"]["elapsed_seconds_sum"] == 2.5
    public = json.dumps(value)
    assert "PRIVATE-" not in public and "endpoint" not in public
    frozen = q._folder(project["id"]) / saved["id"]
    assert "PRIVATE-ROLE-INSTRUCTIONS" in (frozen / "call-000-request-01.json").read_text()
    assert "PRIVATE-FAILED-OUTPUT" in (frozen / "call-000-response-01.json").read_text()
    folder.joinpath("request-01.json").write_text('{"new":true}')
    store.update(project["id"], {"brief": "A changed requirement after capture."})
    assert q.detail(project["id"], saved["id"]) == value
    assert frozen.stat().st_mode & 0o777 == 0o700
    assert (frozen / "record.json").stat().st_mode & 0o777 == 0o600


def test_request_retry_is_idempotent_after_project_revision_moves(project, tmp_path):
    payload = body(project)
    first = q.create(project["id"], payload, root=tmp_path)
    store.update(project["id"], {"progress": 25})
    assert q.create(project["id"], payload, root=tmp_path) == first
    with pytest.raises(ValueError, match="相同请求"):
        q.create(project["id"], {**payload, "label": "别的版本"}, root=tmp_path)
    with pytest.raises(HTTPError) as exc:
        q.create(project["id"], body(project), root=tmp_path)
    assert exc.value.status == 409


def test_concurrent_duplicate_clicks_publish_exactly_one_complete_snapshot(project, tmp_path):
    payload = body(project)
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: q.create(project["id"], payload, root=tmp_path), range(2)))
    assert values[0] == values[1]
    assert len(q.inventory(project["id"])["baselines"]) == 1


def test_freeze_race_rejects_and_removes_pending_files(project, tmp_path, monkeypatch):
    original = q._capture_calls
    def changed(*args):
        result = original(*args)
        store.update(project["id"], {"progress": 30})
        return result
    monkeypatch.setattr(q, "_capture_calls", changed)
    with pytest.raises(HTTPError):
        freeze(project, tmp_path)
    assert q.inventory(project["id"])["baselines"] == []
    assert not list(q._folder(project["id"]).glob(".pending-*"))


def test_changed_call_evidence_during_capture_never_publishes(project, tmp_path, monkeypatch):
    folder = call(tmp_path, project)
    original = q._capture_calls
    def changed(*args):
        result = original(*args)
        (folder / "request-01.json").write_text('{"changed":true}')
        return result
    monkeypatch.setattr(q, "_capture_calls", changed)
    with pytest.raises(HTTPError):
        freeze(project, tmp_path)
    assert q.inventory(project["id"])["baselines"] == []


def test_tamper_or_symlink_is_visible_and_never_replaced_with_current_data(project, tmp_path):
    saved = freeze(project, tmp_path)
    folder = q._folder(project["id"]) / saved["id"]
    record = folder / "project.json"
    record.write_text('{"modified":true}')
    with pytest.raises(ValueError, match="校验失败"):
        q.detail(project["id"], saved["id"])
    listing = q.inventory(project["id"])
    assert listing["baselines"] == [] and listing["unreadable_baselines"] == 1
    record.unlink()
    record.symlink_to(tmp_path / "elsewhere")
    assert q.inventory(project["id"])["unreadable_baselines"] == 1


def test_comparison_preserves_failures_and_refuses_changed_goal_effect_claim(project, tmp_path):
    p = store.update(project["id"], {"script_review": {"passed": False,
        "issues": [{"code": "motivation", "severity": "major", "evidence": "S01", "description": "缺少行动理由"}],
        "rounds": [{}, {}]}})
    call(tmp_path, p, status="failed", elapsed=None)
    a = freeze(p, tmp_path)
    p = store.update(p["id"], {"script_review": {"passed": True, "rounds": [{}, {}, {}]}})
    b = freeze(p, tmp_path, label="优化后")
    result = q.compare(p["id"], a["id"], b["id"])
    assert result["same_inputs"] is True and result["quality_conclusion"] == "not_established"
    assert result["review_changes"][0]["before"]["status"] == "fail"
    assert result["baseline"]["metrics"]["failed_operations"] == 1
    assert result["candidate"]["metrics"]["review_repairs"]["script_review"] == 2
    p = store.update(p["id"], {"script_feedback": "Change the central conflict."})
    c = freeze(p, tmp_path)
    assert q.compare(p["id"], a["id"], c["id"])["same_inputs"] is False
    assert q.compare(p["id"], a["id"], a["id"])["same_inputs"] is False


def test_missing_currency_or_unknown_cost_is_not_reported_as_free():
    metrics = q._metrics({"evaluation_cloud_tasks": [
        {"currency": "CNY", "estimated_cost_micros": 2000000, "confirmed_cost_micros": None},
        {"currency": "USD", "estimated_cost_micros": 300, "confirmed_cost_micros": 0},
        {"currency": None, "estimated_cost_micros": 900},
    ]}, [])
    assert metrics["unconfirmed_cost_count"] == 2
    assert metrics["costs"][0]["confirmed_count"] == 0
    assert metrics["costs"][1]["confirmed_count"] == 1
    assert len(metrics["costs"]) == 2 and metrics["elapsed_seconds_sum"] is None


def test_source_symlink_is_a_gap_not_a_read_outside_project(project, tmp_path):
    folder = call(tmp_path, project)
    secret = tmp_path / "private.json"
    secret.write_text('{"PRIVATE-SECRET":"never read"}')
    (folder / "request-01.json").unlink()
    (folder / "request-01.json").symlink_to(secret)
    saved = freeze(project, tmp_path)
    assert saved["gaps"]
    assert q.detail(project["id"], saved["id"])["records"][0]["role"] == "screenwriter"
    assert "PRIVATE-SECRET" not in "".join(p.read_text() for p in (q._folder(project["id"]) / saved["id"]).glob("*.json"))


@pytest.mark.parametrize("query", ["?candidate=a", "?baseline=a&candidate=b&candidate=c", "?baseline=a&candidate=b&other=c"])
def test_http_compare_rejects_ambiguous_or_unknown_query(project, http_client, query):
    assert http_client("GET", f"/api/factory/projects/{project['id']}/quality-baselines/compare" + query)[0] == 400


def test_http_unknown_project_and_stale_version_and_cross_project_isolation(project, http_client, tmp_path):
    url = f"/api/factory/projects/{project['id']}/quality-baselines"
    assert http_client("POST", url, body(project, expected_revision=0))[0] == 409
    saved = freeze(project, tmp_path)
    second = store.create({"brief": "A separate offline project without access to the first project's evidence."})
    assert http_client("GET", f"/api/factory/projects/{second['id']}/quality-baselines/{saved['id']}")[0] == 400
    assert http_client("GET", "/api/factory/projects/ffffffffffff/quality-baselines")[0] == 404
    assert http_client("POST", url, {**body(project), "root": "/private"})[0] == 400
