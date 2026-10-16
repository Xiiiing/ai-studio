"""Read-only team projections preserve assignment and availability truth."""
from __future__ import annotations

from factory import store, team_blueprint, team_flow, team_ledger
from test_factory_team_planning import project, leader_config, shot_project
from test_factory_team_ledger import database_dump, task


def test_department_availability_does_not_depend_on_root_binding(project, leader_config):
    leader_config["llm_roles"].pop("executive_producer")
    before = database_dump()
    value = team_ledger.team_snapshot(project["id"])
    root = next(member for member in value["members"] if member["id"] == "executive_producer")
    department = next(group for group in value["departments"] if group["id"] == "visual")
    assert root["leader_available"] is False and root["leader_unavailable_reason"]
    assert department["management_available"] is True
    assert department["management_unavailable_reason"] is None
    assert next(action for action in value["actions"] if action["id"] == "replan")["enabled"] is False
    assert database_dump() == before


def test_task_capabilities_and_effective_priority_are_read_only(project, leader_config):
    pid = project["id"]
    with store.connection() as db:
        db.execute("UPDATE team_work_items SET priority=60 WHERE project_id=? AND id='video:S02'", (pid,))
    before = database_dump()
    image = task(pid, "image:S02")
    assert image["priority"] == 0 and image["effective_priority"] == 60
    assert image["allowed_owners"] == ["image_generator"]
    assert image["default_owner"] == "image_generator"
    assert image["assignment_reason"]
    screenplay = task(pid, "stage:screenplay")
    assert "creator" in screenplay["allowed_owners"]
    assert screenplay["default_owner"] == "screenwriter"
    assert screenplay["assignment_reason"] is None
    assert database_dump() == before


def test_workflow_owner_and_active_role_match_stage_assignment(project):
    value = {**store.get(project["id"], False), "stage": "screenplay", "status": "scripting",
             "team_assignments": {"stage:screenplay": "creator", "stage:revision": "screenwriter"}}
    before = database_dump()
    node = next(node for node in team_blueprint.project_graph(value)["nodes"] if node["id"] == "screenplay")
    assert node["owner"] == "creator"
    assert "creator" in node["members"] and "screenwriter" not in node["members"]
    assert node["reviewer"] == "independent_critic"
    assert team_flow._active_role(value) == "creator"
    assert team_flow._active_role({**value, "stage": "script_creation"}) == "creator"
    assert team_flow._active_role({**value, "stage": "revision", "revision_request": {"choice": "rewrite"}}) == "screenwriter"
    assert team_flow._active_role({**value, "stage": "revision", "revision_request": {"choice": "reedit"}}) == "edit_director"
    assert team_flow._active_role({**value, "stage": "story_review"}) == "independent_critic"
    assert team_flow._active_role({**value, "status": "awaiting_final"}) == "producer"
    assert team_flow._active_role({**value, "workflow_version": 1}) == "screenwriter"
    assert database_dump() == before
