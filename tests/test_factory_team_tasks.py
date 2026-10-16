"""Graph-step audit contracts: temporary SQLite and mock functions only."""
import copy

import pytest

from factory import store, workflows as wf
from factory import team_tasks as tasks


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path / "runtime")
    monkeypatch.setattr(store, "DATABASE", tmp_path / "runtime/studio.sqlite3")
    store.initialize()
    return store.create({"brief": "两位成年人解决误会的原创短剧"})


def test_contracts_have_owner_reviewer_and_acyclic_handoffs():
    contract = tasks.contracts()
    seen = set()
    for row in contract:
        assert row["owner"] and row["reviewer"] != row["owner"]
        assert row["inputs"] and row["outputs"]
        assert set(row["depends_on"]) <= seen
        seen.add(row["id"])
    assert seen == set(tasks.TASKS)
    contract[0]["owner"] = "tampered"
    assert tasks.contracts()[0]["owner"] != "tampered"


def test_actual_execution_and_bounded_snapshot_record(project):
    def execute(state):
        assert store.get(state["project_id"], False)["team_tasks"]["research"]["status"] == "running"
        store.update(project["id"], {"research_complete": True})
        return {"route": "continue"}
    assert tasks.tracked_node("research", execute)({"project_id": project["id"]}) == {"route": "continue"}
    saved = store.get(project["id"])
    row = saved["team_tasks"]["research"]
    assert row["status"] == "succeeded" and row["attempt"] == 1 and row["finished_at"]
    assert row["completion_semantics"] == "graph_node_returned_not_quality_approval"
    assert row["model"] is None
    assert len([e for e in saved["events"] if e["kind"] == "team_task"]) == 2


@pytest.mark.parametrize("exception,state", [(wf.ReviewBlocked("private"), "blocked"),
    (wf.ProjectPaused("private"), "paused"), (ValueError("SECRET_NOT_FOR_AUDIT"), "failed")])
def test_errors_remain_errors_without_leaking_exception_text(project, exception, state):
    def fail(_):
        raise exception
    with pytest.raises(type(exception)):
        tasks.tracked_node("screenplay", fail)({"project_id": project["id"]})
    saved = store.get(project["id"])
    assert saved["team_tasks"]["screenplay"]["status"] == state
    assert "SECRET_NOT_FOR_AUDIT" not in str(saved["team_tasks"])


def test_deferred_production_and_revision_handoff_are_not_success(project):
    from factory.remote_tasks import ProductionDeferred
    def deferred(_):
        raise ProductionDeferred(12345, "remote_poll")
    with pytest.raises(ProductionDeferred):
        tasks.tracked_node("production", deferred)({"project_id": project["id"]})
    assert store.get(project["id"])["team_tasks"]["production"]["status"] == "waiting"
    tasks.tracked_node("production", lambda _: {"route": "revision"})({"project_id": project["id"]})
    assert store.get(project["id"])["team_tasks"]["production"]["status"] == "waiting"


def test_stale_attempt_cannot_complete_a_new_attempt(project):
    first, second = tasks.begin(project["id"], "proposals"), tasks.begin(project["id"], "proposals")
    assert second["attempt"] == 2
    assert not tasks.finish(project["id"], "proposals", first["run_id"], "succeeded")
    assert store.get(project["id"])["team_tasks"]["proposals"]["status"] == "running"
    assert tasks.finish(project["id"], "proposals", second["run_id"], "succeeded")
    assert not tasks.finish(project["id"], "proposals", second["run_id"], "failed")


def test_previous_generation_is_not_current_work(project):
    token = tasks.begin(project["id"], "screenplay")
    tasks.finish(project["id"], "screenplay", token["run_id"], "succeeded")
    store.update(project["id"], {"script_generation": 2})
    row = next(r for r in tasks.snapshot(store.get(project["id"], False))["tasks"] if r["id"] == "screenplay")
    assert row["status"] == "pending" and row["previous_run"]["status"] == "succeeded"
    assert not row["current"]


def test_source_change_while_running_supersedes_completion(project):
    token = tasks.begin(project["id"], "research")
    store.update(project["id"], {"brief": "完全不同的成人悬疑故事"})
    assert not tasks.finish(project["id"], "research", token["run_id"], "succeeded")
    assert store.get(project["id"])["team_tasks"]["research"]["status"] == "superseded"


def test_snapshot_is_read_only_and_does_not_invent_history(project):
    before = copy.deepcopy(project)
    result = tasks.snapshot(project)
    assert project == before
    assert all(row["status"] == "pending" and row["record"] is None for row in result["tasks"])
    assert result["authority"] == "observational_not_a_scheduler"


def test_paused_project_cannot_start_new_task(project):
    store.action(project["id"], {"action": "pause"})
    called = []
    with pytest.raises(wf.ProjectPaused):
        tasks.tracked_node("research", lambda _: called.append(True))({"project_id": project["id"]})
    assert not called


def test_real_graph_uses_tracking_wrapper(project, monkeypatch):
    captured, original = [], tasks.tracked_node
    def track(name, fn):
        captured.append(name)
        return original(name, fn)
    monkeypatch.setattr(tasks, "tracked_node", track)
    wf.build_graph(None)
    assert set(captured) == set(tasks.TASKS)


@pytest.mark.parametrize('fails', [False, True])
def test_execution_context_is_scoped_to_actual_run_and_resets(project, fails):
    from factory.execution_context import current_execution
    captured = []
    def invoke(state):
        captured.append(dict(current_execution.get()))
        if fails:
            raise ValueError('fixture failure')
        return {}
    wrapper = tasks.tracked_node('screenplay', invoke)
    if fails:
        with pytest.raises(ValueError):
            wrapper({'project_id': project['id']})
    else:
        wrapper({'project_id': project['id']})
    saved = store.get(project['id'], False)['team_tasks']['screenplay']
    assert captured == [{'project_id': project['id'], 'stage': 'screenplay', 'run_id': saved['run_id']}]
    assert current_execution.get() is None
