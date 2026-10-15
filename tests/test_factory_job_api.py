"""Targeted job controls never become a second queue or a cloud cancel/repost."""
from __future__ import annotations

import pytest

from factory import store
from studio.http_security import HTTPError


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(store, "DATABASE", tmp_path / "studio.sqlite3")
    store.initialize()
    project = store.create({"brief": "A wholly synthetic offline test project."})
    with store.connection() as db:
        row = dict(db.execute("SELECT * FROM jobs WHERE project_id=?", (project["id"],)).fetchone())
    return project, row


def intent(project):
    with store.connection() as db:
        db.execute("""INSERT INTO cloud_tasks(intent_id,project_id,shot_id,attempt,take,provider,model,
                   request_signature,task_id,file_id,status,currency,created,updated)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                   "f" * 64, project["id"], "S01", 1, "offline", "minimax", "test-model", "a" * 64,
                   "existing-task", None, "unknown", "CNY", 0, 0))


def test_job_snapshot_preserves_unknown_external_outcome(job):
    from factory import job_api
    project, row = job
    intent(project)
    before = store.get(project["id"])
    value = job_api.snapshot(row["id"])
    assert value["queue_state"] == "queued" and value["state"] == "queued"
    assert value["project_id"] == project["id"] and value["project_revision"] == project["revision"]
    assert value["remote_tasks"][0]["status"] == "unknown"
    assert value["remote_tasks"][0]["task_id"] == "existing-task"
    assert value["can_cancel"] is False
    assert store.get(project["id"]) == before


def test_pause_exact_job_keeps_task_and_ledger(job):
    from factory import job_api
    project, row = job
    intent(project)
    with store.connection() as db:
        before = [dict(v) for v in db.execute("SELECT * FROM cloud_tasks")]
    result, status = job_api.control(row["id"], "pause", {
        "expected_revision": project["revision"], "expected_lease": row["lease"]})
    assert status == 202 and result["dispatched"] is True
    assert result["job"]["state"] == "paused" and result["job"]["queue_state"] == "cancelled"
    assert result["external_cancelled"] is False
    with store.connection() as db:
        assert [dict(v) for v in db.execute("SELECT * FROM cloud_tasks")] == before
    assert store.claim_job() is None


def test_running_pause_is_a_request_not_completion(job):
    from factory import job_api
    project, _ = job
    row = store.claim_job()
    result, status = job_api.control(row["id"], "pause", {
        "expected_revision": project["revision"], "expected_lease": row["lease"]})
    assert status == 202 and result["job"]["state"] == "pause_requested"
    assert result["job"]["queue_state"] == "running"
    assert result["job"]["project_state"] != "paused"


def test_stale_lease_or_revision_cannot_pause_new_work(job):
    from factory import job_api
    project, row = job
    running = store.claim_job()
    for expected in [{"expected_revision": project["revision"], "expected_lease": row["lease"]},
                     {"expected_revision": project["revision"] - 1, "expected_lease": running["lease"]}]:
        with pytest.raises(HTTPError) as error:
            job_api.control(row["id"], "pause", expected)
        assert error.value.status == 409
    assert not store.get(project["id"])["pause_requested"]


def test_cancel_is_explicitly_unsupported_not_pause(job):
    from factory import job_api
    project, row = job
    before = store.get(project["id"])
    result, status = job_api.control(row["id"], "cancel", {
        "expected_revision": project["revision"], "expected_lease": row["lease"]})
    assert status == 409 and result["code"] == "cancel_not_supported"
    assert result["dispatched"] is False and result["external_cancelled"] is False
    assert store.get(project["id"]) == before


@pytest.mark.parametrize("state", ["completed", "failed", "cancelled"])
def test_terminal_job_is_a_noop_not_a_project_pause(job, state):
    from factory import job_api
    project, row = job
    with store.connection() as db:
        db.execute("UPDATE jobs SET status=? WHERE id=?", (state, row["id"]))
    before = store.get(project["id"])
    result, status = job_api.control(row["id"], "cancel", {
        "expected_revision": project["revision"], "expected_lease": row["lease"]})
    assert status == 200 and result["dispatched"] is False
    assert result["job"]["queue_state"] == state
    assert store.get(project["id"]) == before


def test_unknown_job_is_not_a_success(job):
    from factory import job_api
    with pytest.raises(HTTPError) as error:
        job_api.snapshot("0" * 32)
    assert error.value.status == 404 and error.value.code == "job_not_found"


@pytest.mark.parametrize("identifier", [None, [], {}, "ABC", "../secret", "0" * 33])
def test_invalid_ids_fail_before_database(monkeypatch, identifier):
    from factory import job_api
    def forbidden():
        raise AssertionError("Invalid ID reached the database")
    monkeypatch.setattr(store, "connection", forbidden)
    with pytest.raises(HTTPError) as error:
        job_api.snapshot(identifier)
    assert error.value.status == 400


def test_second_project_and_completed_claim_remain_untouched(job):
    from factory import job_api
    first, row = job
    second = store.create({"brief": "An independent synthetic project for task isolation."})
    job_api.control(row["id"], "pause", {"expected_revision": first["revision"], "expected_lease": 0})
    assert store.get(second["id"], include_events=False) == second
    assert store.claim_job()["project_id"] == second["id"]


def test_unknown_queue_state_is_not_completed(job):
    from factory import job_api
    _, row = job
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='future-state' WHERE id=?", (row["id"],))
    result = job_api.snapshot(row["id"])
    assert result["state"] == "unknown" and result["queue_state"] == "future-state"
    assert not result["can_pause"] and not result["can_cancel"]
