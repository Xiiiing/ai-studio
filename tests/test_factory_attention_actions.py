"""Producer recovery crosses the real SQLite/worker boundary, never providers."""

import copy
import json
import time

import pytest

from factory import (
    store,
    worker,
    operator_console as console,
    attention_actions as actions,
)
from factory.schemas import CreativeReviewError


@pytest.fixture
def project(tmp_path, monkeypatch):
    from factory import config

    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(store, "DATABASE", tmp_path / "studio.sqlite3")
    monkeypatch.setattr(config, "load_config", lambda: {"llm_roles": {}})
    store.initialize()
    return store.create({"brief": "两位成年人共同解决误会的原创悬疑故事"})


def update(project, **patch):
    return store.update(project["id"], patch)


def action(project, command, **fields):
    current = store.get(project["id"], False)
    return store.action(
        project["id"],
        {"action": command, "expected_revision": current["revision"], **fields},
    )


def story_gate(project):
    p = update(
        project,
        proposals=[
            {"id": "A", "title": "方向一", "eligible": True},
            {"id": "B", "eligible": False},
        ],
        proposals_complete_generation=1,
        creation_review={"passed": True},
    )
    return store.gate(p["id"], "story", [{"id": "A"}, {"id": "B"}], "选择故事方向", 1)


def blocked(project, **patch):
    return update(
        project,
        status="blocked",
        stage="script_review",
        needs_decision={"type": "review", "kind": "script_review"},
        **patch,
    )


def test_new_defaults_keep_delegated_request_and_explain_manual_fallback(project):
    assert project["workflow_version"] == 2 and project["team_mode"] == "delegated"
    assert project["delegation"]["mode"] == "human_fallback"
    assert project["delegation"]["reason_code"] == "supervisor_not_configured"
    story_gate(project)
    attention = console.attention(store.get(project["id"], False))
    assert attention["delegation"]["mode"] == "human_fallback"
    assert attention["can_choose"]


def test_old_snapshot_defaults_to_workflow_one_without_migrating_its_state(project):
    with store.connection() as db:
        p = copy.deepcopy(project)
        p.pop("workflow_version")
        db.execute(
            "UPDATE projects SET snapshot=? WHERE id=?", (store.encoded(p), p["id"])
        )
    assert store.get(project["id"], False)["workflow_version"] == 1


@pytest.mark.parametrize("revision", [None, True, -1])
def test_recovery_rejects_missing_or_stale_revision_without_writes(project, revision):
    blocked(project)
    before = store.get(project["id"], False)
    with pytest.raises(ValueError):
        store.action(
            project["id"],
            {
                "action": "provide_feedback",
                "feedback": "补齐人物动机",
                "expected_revision": revision,
            },
        )
    assert store.get(project["id"], False) == before


def test_current_revision_cannot_authorize_an_old_gate(project):
    gate = story_gate(project)
    p = update(project, proposal_generation=2)
    before = copy.deepcopy(p)
    assert console.current_gate(p) is None
    with pytest.raises(ValueError, match="旧版本"):
        store.decide(
            p["id"],
            {
                "type": "story",
                "choice": "A",
                "decision_id": gate["id"],
                "expected_revision": p["revision"],
            },
        )
    assert store.get(p["id"], False) == before


@pytest.mark.parametrize(
    "patch",
    [
        {"proposals": []},
        {"proposals": [{"id": "A", "eligible": False}]},
        {"proposals": [{"id": "A"}]},
        {"proposals_complete_generation": 0},
        {"creation_review": {"passed": False}},
    ],
)
def test_v2_eligibility_and_review_are_checked_at_commit(project, patch):
    gate = story_gate(project)
    p = update(project, **patch)
    with pytest.raises(ValueError):
        store.decide(
            p["id"],
            {
                "type": "story",
                "choice": "A",
                "decision_id": gate["id"],
                "expected_revision": p["revision"],
            },
        )
    assert not store.get(p["id"], False)["answers"]


def test_exact_answer_replay_remains_idempotent_after_revision_moves(project):
    gate = story_gate(project)
    payload = {
        "type": "story",
        "choice": "A",
        "decision_id": gate["id"],
        "expected_revision": store.get(project["id"])["revision"],
    }
    first = store.decide(project["id"], payload)
    second = store.decide(project["id"], payload)
    assert first["revision"] == second["revision"]


def test_creative_exception_reaches_the_inbox_with_actual_evidence(
    project, monkeypatch, capsys
):
    issue = {
        "severity": "blocking",
        "description": "S03 的归还道具凭空出现",
        "suggestion": "补充取回道具的原因",
        "shot_ids": ["S03"],
    }
    update(project, selected_story="A", stage="story_resolution_review")

    def fail(_):
        raise CreativeReviewError("故事目标存在因果问题", [issue])

    monkeypatch.setattr(worker, "advance", fail)
    job = store.claim_job(project["id"])
    worker.handle(job)
    p = store.get(project["id"], False)
    assert p["needs_decision"]["type"] == "review" and p["needs_decision"][
        "issues"
    ] == [issue]
    attention = console.attention(p)
    assert (
        attention["kind"] == "creative"
        and attention["issues"][0]["description"] == issue["description"]
    )
    assert "provide_feedback" in {a["id"] for a in attention["actions"]}
    assert "Traceback" not in capsys.readouterr().err
    recovered = action(p, "provide_feedback", feedback="补齐归还前取回道具的因果")
    assert recovered["script_feedback"] == "补齐归还前取回道具的因果"
    assert recovered["script_generation"] == p["script_generation"] + 1
    assert recovered["workflow_restart_from"] == "story_resolution"


def test_revision_creative_error_accepts_new_feedback_at_the_right_stage(
    project, monkeypatch
):
    update(
        project,
        selected_story="A",
        selected_style="ink",
        revision_request={"choice": "revise", "feedback": "旧要求"},
    )

    def fail(_):
        raise CreativeReviewError("修订目标未通过", [{"description": "新目标没有落实"}])

    monkeypatch.setattr(worker, "advance", fail)
    worker.handle(store.claim_job(project["id"]))
    p = store.get(project["id"], False)
    assert p["needs_decision"]["type"] == "edit_feedback"
    result = action(p, "provide_feedback", feedback="新要求：保留未修改镜头，补齐动机")
    assert result["revision_request"]["feedback"].startswith("新要求")
    assert result["workflow_restart_from"] == "revision"


@pytest.mark.parametrize(
    "command,fields",
    [
        (
            "provide_authorized_source",
            {
                "title": "自有故事",
                "text": "两名成年人在车站寻找失主，最终归还遗失的手表。",
                "rights": "owned",
            },
        ),
        ("switch_to_original", {}),
    ],
)
def test_rights_gate_has_executable_recovery_without_replacing_spend(
    project, command, fields
):
    p = update(
        project,
        creation_mode="adaptation",
        status="blocked",
        needs_decision={"type": "review", "kind": "source_rights"},
        research_complete=True,
        proposals_complete_generation=1,
        script_complete_generation=1,
        budget={"spent_gpu_minutes": 3.25},
        production={"gpu_seconds": 195},
    )
    descriptor = next(a for a in console.attention(p)["actions"] if a["id"] == command)
    assert descriptor["enabled"] and descriptor["impact"]
    result = action(p, command, **fields)
    assert (
        result["workflow_restart_from"] == "research" and result["status"] == "queued"
    )
    assert (
        "research_complete" not in result and "script_complete_generation" not in result
    )
    assert (
        result["proposal_generation"] == 2
        and result["budget"]["spent_gpu_minutes"] == 3.25
    )
    assert result["production"]["gpu_seconds"] == 195
    if command == "provide_authorized_source":
        assert result["submitted_sources"][0]["rights"] == "owned"
    else:
        assert result["creation_mode"] == "original"


def test_source_validation_failure_has_no_partial_mutation(project):
    p = update(
        project,
        status="blocked",
        needs_decision={"kind": "source_rights", "type": "review"},
    )
    with pytest.raises(ValueError, match="至少填写"):
        action(p, "provide_authorized_source", rights="licensed")
    assert store.get(p["id"], False) == p


def test_new_brief_invalidates_downstream_versions_and_pending_gate(project):
    gate = story_gate(project)
    result = action(
        project,
        "revise_brief",
        brief="新的创作方向：两名成年人在修表店共同解决失物谜题",
    )
    assert result["brief"].startswith("新的创作方向")
    assert result["proposal_generation"] == 2 and result["script_generation"] == 2
    assert (
        result["decisions"][0]["id"] == gate["id"]
        and result["decisions"][0]["status"] == "superseded"
    )
    assert result["workflow_restart_revision"] == result["revision"]


def test_visual_rejection_is_a_bounded_action_even_when_no_candidates_exist(project):
    update(
        project,
        selected_story="A",
        styles=[],
        script_complete_generation=1,
        script_review={"passed": True},
    )
    gate = store.gate(project["id"], "style", [], "选择视觉方案", 1)
    assert {o["id"] for o in gate["options"]} == {"__restyle__"}
    attention = console.attention(store.get(project["id"], False))
    assert not attention["can_choose"] and "regenerate" in {
        a["id"] for a in attention["actions"]
    }
    with pytest.raises(ValueError, match="画风"):
        action(project, "regenerate")
    result = action(project, "regenerate", feedback="请改成柔和水彩，保留成年人设定")
    assert result["answers"][gate["id"]]["choice"] == "__restyle__"


def test_running_producer_takeover_waits_for_the_safe_boundary(project):
    store.claim_job(project["id"])
    result = action(project, "take_control")
    assert result["team_mode"] == "guided" and result["pause_requested"]
    assert result["control_change_reason"]
    assert result["status"] != "paused"


def test_delegation_never_skips_final_review_or_invent_a_supervisor(project):
    p = update(project, team_mode="guided", status="awaiting_final")
    with pytest.raises(ValueError, match="终审"):
        action(p, "delegate_control")
    p = update(project, status="queued")
    with pytest.raises(ValueError, match="尚未可用"):
        action(p, "delegate_control")


def test_takeover_fences_a_supervisor_result_that_arrives_after_the_user(
    project, monkeypatch
):
    from factory import supervision as supervisor

    gate = story_gate(project)
    monkeypatch.setattr(
        supervisor, "load_config", lambda: {"llm_roles": {"executive_producer": "boss"}}
    )
    monkeypatch.setattr(
        supervisor,
        "resolve_llm",
        lambda *_: {
            "profile": "boss",
            "model": "fixture-manager",
            "configured": True,
            "enabled": True,
        },
    )

    def late_result(*args, **kwargs):
        action(project, "take_control")
        return {
            "action": "select",
            "choice": "A",
            "rationale": "符合已审核目标",
            "risks": [],
        }

    monkeypatch.setattr(supervisor, "cached_json_call", late_result)
    assert supervisor.supervise_gate(project["id"], gate) is None
    saved = store.get(project["id"], False)
    assert saved["team_mode"] == "guided" and not saved["answers"]
    assert saved["supervision"]["story"]["status"] == "superseded"
    assert saved["decisions"][0]["status"] == "pending"


def test_delegate_control_preserves_the_gate_and_reopens_only_manual_supervision(
    project, monkeypatch
):
    from factory import config

    story_gate(project)
    p = update(
        project,
        team_mode="guided",
        supervision={
            "story": {
                "gate_id": project["id"] + ":story:1",
                "status": "manual",
                "run_id": "previous-manual",
            }
        },
    )
    monkeypatch.setattr(
        config, "load_config", lambda: {"llm_roles": {"executive_producer": "boss"}}
    )
    monkeypatch.setattr(config, "resolve_llm", lambda *_: {"enabled": True})
    result = action(p, "delegate_control")
    assert result["team_mode"] == "delegated" and result["status"] == "queued"
    assert result["decisions"][0]["status"] == "pending" and not result["answers"]
    assert "story" not in result["supervision"]
    assert any(
        e["kind"] == "supervision_redelegated" for e in store.get(p["id"])["events"]
    )


def test_unknown_supervisor_request_cannot_be_redelegated(project, monkeypatch):
    from factory import config

    story_gate(project)
    p = update(
        project, team_mode="guided", supervision={"story": {"status": "requested"}}
    )
    monkeypatch.setattr(
        config, "load_config", lambda: {"llm_roles": {"executive_producer": "boss"}}
    )
    monkeypatch.setattr(config, "resolve_llm", lambda *_: {"enabled": True})
    descriptor = next(
        a for a in actions.descriptors(p) if a["id"] == "delegate_control"
    )
    assert not descriptor["enabled"] and descriptor["disabled_reason"]
    with pytest.raises(ValueError, match="尚未确认"):
        action(p, "delegate_control")
    assert store.get(p["id"], False) == p


def test_uncertain_cloud_charge_blocks_redelegation(project, monkeypatch):
    from factory import config

    p = update(
        project,
        team_mode="guided",
        status="blocked",
        needs_decision={"type": "production", "code": "cloud_submission_unknown"},
    )
    monkeypatch.setattr(
        config, "load_config", lambda: {"llm_roles": {"executive_producer": "boss"}}
    )
    monkeypatch.setattr(config, "resolve_llm", lambda *_: {"enabled": True})
    with pytest.raises(ValueError, match="费用"):
        action(p, "delegate_control")
    assert store.get(p["id"], False) == p


def test_workspace_exposes_control_without_requiring_a_pending_decision(
    project, monkeypatch, tmp_path
):
    from factory import team_flow

    monkeypatch.setattr(team_flow, "ROOT", tmp_path)
    work = console.workspace(project["id"])
    assert work["operator"]["attention"] is None
    assert "take_control" in {a["id"] for a in work["operator"]["actions"]}
    assert work["flow"]["project_id"] == project["id"] and work["task_board"]["tasks"]
    assert work["operator"]["delegation"]["mode"] == "human_fallback"


def test_dashboard_aggregates_every_project_but_limits_recent_cards(project):
    with store.connection() as db:
        db.execute("DELETE FROM jobs")
        for index in range(105):
            p = copy.deepcopy(project)
            p.update(
                id=f"{index + 1:012x}",
                status="approved" if index < 5 else "queued",
                title=f"项目{index}",
            )
            p["budget"]["spent_gpu_minutes"] = 2
            db.execute(
                "INSERT INTO projects VALUES(?,?,?,?)",
                (p["id"], time.time() + index, 1, json.dumps(p)),
            )
    result = console.dashboard()
    assert (
        result["metrics"]["projects"] == 106
        and len(result["projects"]) == 100
        and result["bounded"]
    )
    assert (
        result["metrics"]["delivered"] == 5 and result["metrics"]["gpu_minutes"] == 210
    )


def test_old_pending_project_remains_reachable_outside_the_recent_page(project):
    blocked(project)
    with store.connection() as db:
        db.execute("UPDATE projects SET updated=0 WHERE id=?", (project["id"],))
        for index in range(105):
            p = copy.deepcopy(project)
            p.update(id=f"{index + 1:012x}", status="approved", title=f"已完成{index}")
            db.execute(
                "INSERT INTO projects VALUES(?,?,?,?)",
                (p["id"], time.time() + index, 1, json.dumps(p)),
            )
    first = console.dashboard()
    assert project["id"] not in {p["id"] for p in first["projects"]}
    assert project["id"] in {p["id"] for p in first["attention_projects"]}
    assert first["pagination"]["next_offset"] == 100
    second = console.dashboard(offset=100)
    assert len(second["projects"]) == 6 and not second["pagination"]["has_more"]
    assert project["id"] in {p["id"] for p in second["projects"]}


def test_attention_list_has_independent_pagination(project):
    with store.connection() as db:
        db.execute("DELETE FROM jobs")
        for index in range(5):
            p = copy.deepcopy(project)
            p.update(
                id=f"{index + 1:012x}",
                status="blocked",
                needs_decision={"type": "runtime", "message": "模型待恢复"},
            )
            db.execute(
                "INSERT INTO projects VALUES(?,?,?,?)",
                (p["id"], time.time() + index, 1, json.dumps(p)),
            )
    first = console.dashboard(limit=1, attention_limit=2)
    second = console.dashboard(limit=1, attention_offset=2, attention_limit=2)
    assert first["metrics"]["attention"] == 5 and len(first["attention_projects"]) == 2
    assert (
        first["attention_pagination"]["next_offset"] == 2
        and second["attention_pagination"]["next_offset"] == 4
    )
    assert not (
        {p["id"] for p in first["attention_projects"]}
        & {p["id"] for p in second["attention_projects"]}
    )


def test_queue_and_resource_waits_are_distinct_from_a_running_worker(project):
    update(project, status="waiting_remote", production={"phase": "remote_poll"})
    result = console.dashboard()
    assert (
        result["metrics"]["waiting_remote"] == 1 and result["metrics"]["running"] == 0
    )
    update(project, status="producing", production={"phase": "waiting_resources"})
    result = console.dashboard()
    assert (
        result["metrics"]["waiting_resources"] == 1
        and result["metrics"]["waiting_remote"] == 0
    )


def test_review_shortcut_is_plain_feedback_and_uses_existing_revision_boundary(project):
    p = update(project, status="blocked", stage="script_review", selected_story="A",
               needs_decision={"type": "script_review", "issues": [
                   {"description": "镜头之间缺少行动因果", "suggestion": "补齐取回道具动作", "shot_ids": ["S03"]}]})
    attention = console.attention(p)
    shortcut = attention["feedback_choices"][0]
    before = copy.deepcopy(p)
    with pytest.raises(ValueError, match="已更新"):
        store.action(p["id"], {"action": "provide_feedback", "expected_revision": p["revision"] - 1,
                               "feedback": shortcut["feedback"]})
    assert store.get(p["id"], False) == before
    result = store.action(p["id"], {"action": "provide_feedback", "expected_revision": p["revision"],
                                   "feedback": shortcut["feedback"]})
    assert result["script_feedback"] == shortcut["feedback"]
    assert result["script_generation"] == p["script_generation"] + 1
    assert result["workflow_restart_from"] == "story_resolution"
    assert result["selected_story"] == "A"
    assert result["status"] == "queued"
