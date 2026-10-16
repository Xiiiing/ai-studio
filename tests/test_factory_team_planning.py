"""Hierarchical arrangements use actual controllers and offline model responses."""
from __future__ import annotations

import copy
import json

import pytest

from factory import config, store, team_control, team_ledger
from test_factory_role_routing import routing_config
from test_factory_shot_workbench import project as shot_project
from test_factory_team_control import intervention, action
from test_factory_team_ledger import database_dump, task


@pytest.fixture
def project(shot_project):
    episode = copy.deepcopy(shot_project["episode"])
    episode["shots"][0]["dialogue"] = [{"id": "S01_D01", "role": "lead", "text": "This is your book."}]
    return store.update(shot_project["id"], {"episode": episode, "script": copy.deepcopy(episode)})


@pytest.fixture
def leader_config(monkeypatch):
    value = routing_config()
    for leader in ("executive_producer", "story_editor", "storyboard_director", "visual_designer", "dialogue_editor", "edit_director"):
        value["llm_roles"][leader] = "qwen"
    monkeypatch.setattr(config, "load_config", lambda: copy.deepcopy(value))
    return value


def command(project, ids=("video:S02",), **changes):
    ids = list(ids)
    value = intervention(project, task_id=ids[0], target_type="leader", target_id="visual_designer",
                         operation="replan", task_ids=ids,
                         task_generations={ident: task(project["id"], ident)["generation"] for ident in ids})
    value.update(changes)
    return value


def operation(name, ident, *, priority=0, owner="", requirements=""):
    return {"action": name, "task_id": ident, "priority": priority, "owner": owner, "requirements": requirements}


def model_plan(*operations, delegations=()):
    return {"summary": "Arrange the explicitly selected offline production tasks.", "operations": list(operations)}


def root_plan(*delegations):
    return {"summary": "Delegate each selected task to its department.", "delegations": list(delegations)}


def mock_planner(monkeypatch, response):
    """Bypass model output validation deliberately; validate real prompt inputs."""
    from factory import team_planning
    from factory.prompt_registry import PromptText, prompt_receipt
    calls = []

    def reply(project, config, role, stage, system, payload, schema, **kwargs):
        assert isinstance(system, PromptText)
        receipt = prompt_receipt(system, role, payload, schema)
        assert receipt["status"] == "registered"
        calls.append({"role": role, "stage": stage, "payload": copy.deepcopy(payload), "receipt": receipt})
        return response(role, payload) if callable(response) else copy.deepcopy(response)

    monkeypatch.setattr(team_planning, "cached_json_call", reply)
    return calls


def test_department_priority_changes_actual_queue_and_prerequisite_effective_priority(project, leader_config, monkeypatch):
    response = model_plan(operation("prioritize", "video:S02", priority=60))
    calls = mock_planner(monkeypatch, response)
    pid = project["id"]
    submitted = team_control.submit(pid, command(project))
    assert team_control.process_pending(pid)["status"] == "applied"
    selected = task(pid, "video:S02")
    assert selected["priority"] == 60
    assert task(pid, "image:S02")["effective_priority"] == 60
    assert task(pid, "image:S01")["priority"] == 0
    assert team_control.ordered(pid, "image", ["S01", "S02", "S03"]) == ["S02", "S01", "S03"]
    assert [row["role"] for row in calls] == ["visual_designer"]
    assert calls[0]["receipt"]["version"] == "2.0.0"
    saved = next(row for row in team_control.instructions(pid)["instructions"] if row["id"] == submitted["instruction"]["id"])
    assert saved["plan"]["management_tasks"]
    assert all(task(pid, ident)["kind"] == "management" for ident in saved["plan"]["management_tasks"])


def test_total_producer_delegates_selected_scopes_to_real_department_leads(project, leader_config, monkeypatch):
    def response(role, payload):
        if role == "executive_producer":
            return root_plan(
                {"department": "visual", "task_ids": ["video:S02"], "requirements": "Finish this selected shot first."},
                {"department": "sound", "task_ids": ["dialogue:S01"], "requirements": "Check this selected dialogue timing."},
            )
        if role == "visual_designer":
            return model_plan(operation("prioritize", "video:S02", priority=50))
        if role == "dialogue_editor":
            return model_plan(operation("pause", "dialogue:S01"))
        pytest.fail("Unexpected department call: " + role)

    calls = mock_planner(monkeypatch, response)
    pid = project["id"]
    store.update(pid, {"status": "awaiting_final", "pause_requested": False})
    team_control.submit(pid, command(project, ("video:S02", "dialogue:S01"), target_id="executive_producer"))
    from factory import worker
    job = store.claim_job(pid)
    assert job["kind"] == "team_instruction"
    worker.handle(job)
    assert [row["role"] for row in calls] == ["executive_producer", "visual_designer", "dialogue_editor"]
    assert all(row["receipt"]["version"] == "2.0.0" for row in calls)
    assert task(pid, "video:S02")["priority"] == 50
    assert task(pid, "dialogue:S01")["paused"] is True
    saved = team_control.instructions(pid)["instructions"][0]
    assert {row["department"] for row in saved["plan"]["delegations"]} == {"visual", "sound"}
    assert len(saved["plan"]["management_tasks"]) == 3
    before = database_dump()
    worker.handle(job)
    assert database_dump() == before and len(calls) == 3


@pytest.mark.parametrize("bad_operations", [
    [operation("prioritize", "video:S01", priority=80)],
    [operation("approve", "video:S02")],
    [operation("prioritize", "video:S99", priority=80)],
    [operation("pause", "video:S02"), operation("resume", "video:S02")],
    [operation("assign", "video:S02", owner="visual_critic")],
    [operation("assign", "video:S02", owner="screenwriter")],
    [operation("prioritize", "video:S02", priority=101)],
])
def test_invalid_manager_operations_cannot_partially_change_tasks_or_human_authority(project, leader_config, monkeypatch, bad_operations):
    calls = mock_planner(monkeypatch, model_plan(operation("prioritize", "video:S02", priority=20), *bad_operations))
    pid = project["id"]
    protected = copy.deepcopy(store.get(pid, False))
    generations = {ident: task(pid, ident)["generation"] for ident in ("video:S01", "video:S02")}
    team_control.submit(pid, command(project))
    result = team_control.process_pending(pid)
    assert result["status"] == "needs_decision"
    assert calls and team_control.instructions(pid)["instructions"][0]["reason"]
    assert task(pid, "video:S02")["priority"] == 0 and task(pid, "video:S01")["priority"] == 0
    assert {ident: task(pid, ident)["generation"] for ident in generations} == generations
    current = store.get(pid, False)
    assert current["episode"] == protected["episode"] and current["budget"] == protected["budget"]
    assert current.get("delivery") == protected.get("delivery") and current["decisions"] == protected["decisions"]
    assert current["status"] != "approved"


def test_department_cannot_delegate_its_selected_work_to_another_department(project, leader_config, monkeypatch):
    calls = mock_planner(monkeypatch, model_plan(operation("prioritize", "dialogue:S01", priority=70)))
    team_control.submit(project["id"], command(project))
    result = team_control.process_pending(project["id"])
    assert result["status"] == "needs_decision" and calls
    assert task(project["id"], "dialogue:S01")["priority"] == 0
    assert task(project["id"], "video:S02")["priority"] == 0


def test_engine_task_without_another_compatible_member_has_clear_decision(project, leader_config, monkeypatch):
    from factory import team_planning

    assert team_planning.allowed_owners(task(project["id"], "image:S02")) == ["image_generator"]
    mock_planner(monkeypatch, model_plan(operation("assign", "image:S02", owner="video_generator")))
    team_control.submit(project["id"], command(project, ("image:S02",)))
    result = team_control.process_pending(project["id"])
    assert result["status"] == "needs_decision"
    reason = team_control.instructions(project["id"])["instructions"][0]["reason"]
    assert reason and any(word in reason for word in ("能力", "成员", "转派", "兼容"))
    assert task(project["id"], "image:S02")["owner"] == "image_generator"


def test_writer_cannot_be_assigned_to_independent_text_review(project, leader_config, monkeypatch):
    from factory import team_planning

    eligible = team_planning.allowed_owners(task(project["id"], "stage:script_review"))
    assert "independent_critic" in eligible and not {"screenwriter", "creator", "story_editor"} & set(eligible)
    mock_planner(monkeypatch, model_plan(operation("assign", "stage:script_review", owner="screenwriter")))
    team_control.submit(project["id"], command(project, ("stage:script_review",), target_id="independent_critic"))
    assert team_control.process_pending(project["id"])["status"] == "needs_decision"
    assert task(project["id"], "stage:script_review")["owner"] == "independent_critic"


def test_assignment_changes_actual_text_model_binding_without_mutating_original_configuration(project, leader_config, monkeypatch):
    from factory import team_planning
    from factory.execution_context import current_execution

    leader_config["llm_roles"]["creator"] = "critic"
    mock_planner(monkeypatch, model_plan(operation("assign", "stage:screenplay", owner="creator")))
    pid = project["id"]
    team_control.submit(pid, command(project, ("stage:screenplay",), target_id="story_editor"))
    assert team_control.process_pending(pid)["status"] == "applied"
    assert task(pid, "stage:screenplay")["owner"] == "creator"
    before = copy.deepcopy(leader_config)
    context = current_execution.set({"project_id": pid, "stage": "screenplay"})
    try:
        routed = team_planning.task_config(leader_config, "screenwriter")
    finally:
        current_execution.reset(context)
    assert config.resolve_llm(routed, "screenwriter")["model"] == "fixture-critic"
    assert config.resolve_llm(leader_config, "screenwriter")["model"] == "fixture-qwen"
    assert leader_config == before
    assert routed["_team_actual_members"]["screenwriter"] == "creator"
    assert config.resolve_llm(routed, "independent_critic")["model"] == "fixture-critic"


def test_stale_plan_after_new_directive_keeps_new_requirement_and_does_not_apply_old_priority(project, leader_config, monkeypatch):
    pid = project["id"]
    newer = []

    def respond(role, payload):
        if not newer:
            newer.append(team_control.submit(pid, intervention(project, task_id="video:S02",
                request_id="new-command-during-leader-planning", operation="retry", instruction="Use this newer requirement instead.")))
        return model_plan(operation("prioritize", "video:S02", priority=90))

    calls = mock_planner(monkeypatch, respond)
    first = team_control.submit(pid, command(project))
    result = team_control.process_pending(pid)
    assert result["status"] == "needs_decision" and len(calls) == 1
    assert task(pid, "video:S02")["priority"] == 0
    history = team_control.instructions(pid)["instructions"]
    assert next(row for row in history if row["id"] == first["instruction"]["id"])["status"] == "needs_decision"
    assert next(row for row in history if row["id"] == newer[0]["instruction"]["id"])["instruction"] == "Use this newer requirement instead."
    assert store.get(pid, False)["needs_decision"]["type"] == "team"
    assert any(row["kind"] == "management" and row["attempts"] for row in team_ledger.task_page(pid, limit=100)["tasks"])


def test_planner_transport_failure_retains_history_and_original_work(project, leader_config, monkeypatch):
    def fail(role, payload):
        raise OSError("Synthetic unavailable management transport")

    calls = mock_planner(monkeypatch, fail)
    pid = project["id"]
    before = copy.deepcopy(store.get(pid, False)["episode"])
    submitted = team_control.submit(pid, command(project))
    result = team_control.process_pending(pid)
    assert result["status"] == "needs_decision" and calls
    assert store.get(pid, False)["episode"] == before
    assert task(pid, "video:S02")["priority"] == 0
    saved = team_control.instructions(pid)["instructions"][0]
    assert saved["id"] == submitted["instruction"]["id"] and saved["instruction"] == submitted["instruction"]["instruction"]
    assert saved["reason"] and any(row["kind"] == "management" for row in team_ledger.task_page(pid, limit=100)["tasks"])


def test_missing_explicit_leader_binding_never_falls_back_to_default_writer(project, leader_config, monkeypatch):
    leader_config["llm_roles"].pop("executive_producer")
    calls = mock_planner(monkeypatch, root_plan({"department": "visual", "task_ids": ["video:S02"], "requirements": "Prioritize this shot."}))
    team_control.submit(project["id"], command(project, target_id="executive_producer"))
    assert team_control.process_pending(project["id"])["status"] == "needs_decision"
    assert calls == [] and task(project["id"], "video:S02")["priority"] == 0


@pytest.mark.parametrize("protection", ["lock", "preserve"])
def test_retry_dependency_conflict_rolls_back_all_priority_and_media_changes(project, leader_config, monkeypatch, protection):
    from studio import core
    from test_factory_series import pattern

    pid = project["id"]
    receipt = pattern(core.ROOT / "production" / project["slug"] / "assets/shots/S03/take_001/image.png", 1)
    store.update(pid, {"shots": [{"id": "S03", "image": receipt}]})
    token = team_ledger.begin_unit(pid, "image", "S03", input_version="protected-frame")
    team_ledger.finish_unit(token, artifact=receipt)
    if protection == "lock":
        team_control.task_action(pid, "image:S03", action(project, "image:S03", "lock"))
    preserve = ["image:S03"] if protection == "preserve" else []
    calls = mock_planner(monkeypatch, model_plan(
        operation("prioritize", "video:S02", priority=70), operation("retry", "image:S01", requirements="Retry this root frame.")))
    episode = copy.deepcopy(store.get(pid, False)["episode"])
    generation = task(pid, "image:S01")["generation"]
    team_control.submit(pid, command(project, ("video:S02", "image:S01"), preserve=preserve))
    result = team_control.process_pending(pid)
    assert calls and result["status"] == "needs_decision"
    assert task(pid, "video:S02")["priority"] == 0
    assert task(pid, "image:S01")["generation"] == generation
    assert task(pid, "image:S03")["input_version"] == "protected-frame"
    assert store.get(pid, False)["episode"] == episode


@pytest.mark.parametrize("delegation", [
    {"department": "sound", "task_ids": ["video:S02"], "requirements": "Wrong department."},
    {"department": "visual", "task_ids": ["video:S01"], "requirements": "Unselected task."},
    {"department": "new_department", "task_ids": ["video:S02"], "requirements": "Invented department."},
])
def test_producer_cannot_expand_scope_or_invent_departments(project, leader_config, monkeypatch, delegation):
    calls = mock_planner(monkeypatch, root_plan(delegation))
    team_control.submit(project["id"], command(project, target_id="executive_producer"))
    assert team_control.process_pending(project["id"])["status"] == "needs_decision"
    assert [row["role"] for row in calls] == ["executive_producer"]
    assert task(project["id"], "video:S02")["priority"] == 0


@pytest.mark.parametrize("intent", ["retry", "revise"])
def test_manager_cannot_silently_replace_requested_rework_with_priority_only(project, leader_config, monkeypatch, intent):
    mock_planner(monkeypatch, model_plan(operation("prioritize", "video:S02", priority=30)))
    team_control.submit(project["id"], command(project, operation=intent))
    result = team_control.process_pending(project["id"])
    assert result["status"] == "needs_decision"
    assert task(project["id"], "video:S02")["priority"] == 0
    assert "不能仅调整队列" in result["reason"]


def test_illegal_action_reports_field_and_constraint_without_dumping_model_output(project, leader_config, monkeypatch):
    bad = operation("PRIVATE_UNSUPPORTED_ACTION_VALUE", "video:S02")
    mock_planner(monkeypatch, model_plan(bad))
    accepted = team_control.submit(project["id"], command(project))
    result = team_control.process_pending(project["id"])
    assert result["status"] == "needs_decision"
    assert "operations.0.action" in result["reason"] and "enum" in result["reason"]
    assert "PRIVATE_UNSUPPORTED_ACTION_VALUE" not in result["reason"]
    assert team_control.instructions(project["id"])["instructions"][0]["instruction"] == accepted["instruction"]["instruction"]


def test_management_record_metadata_cannot_be_forged_after_model_validation(project, leader_config, monkeypatch):
    from factory import team_planning

    mock_planner(monkeypatch, model_plan(operation("prioritize", "video:S02", priority=20)))
    pid = project["id"]
    team_control.submit(pid, command(project))
    assert team_control.process_pending(pid)["status"] == "applied"
    saved = team_control.instructions(pid)["instructions"][0]
    tasks = {row["id"]: row for row in team_ledger.task_page(pid, limit=100)["tasks"]}
    forged = {**saved["plan"], "management_tasks": ["management:invented-claim"]}
    before = database_dump()
    with pytest.raises(ValueError, match="真实负责人"):
        team_planning.validate_plan(store.get(pid, False), saved, tasks, forged, leader_config)
    assert database_dump() == before


def test_restart_after_cached_plan_before_application_reuses_model_and_applies_once(project, leader_config, monkeypatch):
    from factory import team_planning
    from test_factory_role_routing import mock_transport

    def response(request):
        return {"model": request["model"], "choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(model_plan(operation("prioritize", "video:S02", priority=40)))}}]}

    calls, _ = mock_transport(monkeypatch, response)
    pid = project["id"]
    team_control.submit(pid, command(project))
    apply = team_control._apply_direct

    def power_interruption(*args, **kwargs):
        raise SystemExit("Synthetic crash after the durable model answer")

    monkeypatch.setattr(team_control, "_apply_direct", power_interruption)
    with pytest.raises(SystemExit):
        team_control.process_pending(pid)
    assert len(calls) == 1 and task(pid, "video:S02")["priority"] == 0
    assert team_control.instructions(pid)["instructions"][0]["status"] == "planning"
    monkeypatch.setattr(team_control, "_apply_direct", apply)
    store.initialize()
    assert team_control.process_pending(pid)["status"] == "applied"
    assert len(calls) == 1 and task(pid, "video:S02")["priority"] == 40
    before = database_dump()
    assert team_control.process_pending(pid) is None and database_dump() == before and len(calls) == 1
    management = [row for row in team_ledger.task_page(pid, limit=100)["tasks"] if row["kind"] == "management"]
    assert len(management) == 1 and len(management[0]["attempts"]) == 2
    assert management[0]["model"] == "fixture-qwen"
