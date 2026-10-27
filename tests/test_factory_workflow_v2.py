"""Persisted v2 boundaries and recovery with isolated, non-network adapters."""

import copy

import pytest

from factory import creative, creative_runtime, store, team_tasks, workflows as wf
from factory.runtime_observations import checkpoint
from factory.team_blueprint import project_graph


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path / "runtime")
    monkeypatch.setattr(store, "DATABASE", tmp_path / "runtime/studio.sqlite3")
    monkeypatch.setattr(wf, "DATA", tmp_path / "runtime")
    monkeypatch.setattr(wf, "CHECKPOINTS", tmp_path / "runtime/checkpoints.sqlite3")
    monkeypatch.setattr(wf, "ROOT", tmp_path)
    store.initialize()
    calls = []

    def research(state):
        calls.append("research")
        store.update(state["project_id"], {"research_complete": True})
        return {"route": "continue"}

    def proposals(state):
        calls.append("proposals")
        p = store.get(state["project_id"], False)
        store.update(
            p["id"],
            {
                "proposals": [{"id": "A", "title": "故事A", "eligible": True}],
                "proposals_complete_generation": p["proposal_generation"],
                "creation_review": {"passed": True},
            },
        )
        return {"route": "continue", "story_generation": p["proposal_generation"]}

    def resolve(project, config, callback):
        calls.append("story_resolution")
        return {
            "resolved_story_brief": None,
            "story_resolution_complete_generation": project["script_generation"],
            "creative_round": 1,
            "creative_review_rounds": [],
        }

    def write(project, config, callback):
        calls.append("screenplay")
        return {
            "screenplay_draft": {"title": "故事A", "shots": []},
            "screenplay_complete_round": [
                project["script_generation"],
                project.get("creative_round", 1),
            ],
        }

    def direct(project, config, callback):
        calls.append("storyboard")
        return {
            "storyboard_complete_round": [
                project["script_generation"],
                project.get("creative_round", 1),
            ]
        }

    def review(project, config, callback):
        calls.append("script_review")
        return {
            "script": project["screenplay_draft"],
            "episode": project["screenplay_draft"],
            "script_review": {"passed": True},
            "script_complete_generation": project["script_generation"],
        }

    def design(project, config, callback):
        calls.append(("visual_design", project.get("style_feedback", "")))
        version = project.get("style_generation", project["script_generation"])
        return {
            "styles": [
                {
                    "id": "style-1",
                    "name": "水墨",
                    "prompt": "ink illustration",
                    "preview_url": "/media/old.png",
                }
            ],
            "visual_design_complete_generation": version,
        }

    def visual(state):
        calls.append("visual_development")
        p = store.get(state["project_id"], False)
        store.update(
            p["id"],
            {
                "style_previews_generation": p.get(
                    "style_generation", p["script_generation"]
                )
            },
        )
        return {"route": "continue"}

    def production(state):
        calls.append("production")
        p = store.get(state["project_id"], False)
        store.update(
            p["id"],
            {
                "delivery": {"video_url": "/media/test.mp4"},
                "delivery_complete_revision": p["production_revision"],
            },
        )
        return {"route": "continue", "final_generation": p["production_revision"]}

    for name, value in [
        ("research", research),
        ("proposals", proposals),
        ("visual_development", visual),
        ("production", production),
    ]:
        monkeypatch.setattr(wf, name, value)
    for name, value in [
        ("resolve_target", resolve),
        ("write_screenplay", write),
        ("direct_screenplay", direct),
        ("review_screenplay", review),
        ("design_visuals", design),
    ]:
        monkeypatch.setattr(creative_runtime, name, value)
    p = store.create({"brief": "原创成年人解决误会的短剧", "team_mode": "guided"})
    assert p["workflow_version"] == 2
    return p, calls


def decide(pid, choice, feedback=""):
    gate = store.get(pid, False)["decisions"][-1]
    return store.decide(
        pid,
        {
            "type": gate["type"],
            "decision_id": gate["id"],
            "choice": choice,
            "feedback": feedback,
        },
    )


def to_style(runtime):
    p, calls = runtime
    wf.advance(p["id"])
    decide(p["id"], "A")
    wf.advance(p["id"])
    assert store.get(p["id"], False)["status"] == "awaiting_style"
    return p, calls


def test_new_graph_records_independent_boundaries_across_connection_reopens(runtime):
    p, calls = to_style(runtime)
    assert calls[:6] == [
        "research",
        "proposals",
        "story_resolution",
        "screenplay",
        "storyboard",
        "script_review",
    ]
    saved = store.get(p["id"], False)
    assert all(
        saved["team_tasks"][stage]["status"] == "succeeded"
        for stage in ("screenplay", "storyboard", "script_review", "visual_design")
    )
    assert saved["team_tasks"]["story_resolution"]["status"] == "skipped"
    count = len(calls)
    wf.advance(p["id"])
    assert len(calls) == count
    decide(p["id"], "style-1")
    wf.advance(p["id"])
    assert calls.count("production") == 1
    wf.advance(p["id"])
    assert calls.count("production") == 1
    decide(p["id"], "approve")
    wf.advance(p["id"])
    assert store.get(p["id"], False)["status"] == "approved"


def test_rejected_visuals_use_new_version_and_feedback_without_rewriting_script(
    runtime,
):
    p, calls = to_style(runtime)
    before = store.get(p["id"], False)
    old_styles = copy.deepcopy(before["styles"])
    with pytest.raises(ValueError):
        decide(p["id"], "__restyle__")
    decide(p["id"], "__restyle__", "更轻的线条与暖色，人物不要过度写实")
    wf.advance(p["id"])
    after = store.get(p["id"], False)
    assert after["style_generation"] == 2 and after["style_previews_generation"] == 2
    assert after["style_history"][0]["styles"] == old_styles
    assert after["script_generation"] == 1 and calls.count("screenplay") == 1
    assert ("visual_design", "更轻的线条与暖色，人物不要过度写实") in calls
    assert after["budget"] == before["budget"]
    assert after["decisions"][-1]["id"].endswith(":style:2")


def test_recovery_command_reroutes_away_from_superseded_interrupt(runtime):
    p, calls = to_style(runtime)
    current = store.get(p["id"], False)
    store.update(
        p["id"],
        {
            "script_generation": 2,
            "style_generation": 2,
            "status": "queued",
            "workflow_restart_from": "story_resolution",
            "workflow_restart_revision": current["revision"] + 1,
        },
    )
    wf.advance(p["id"])
    after = store.get(p["id"], False)
    assert calls.count("story_resolution") == 2 and calls.count("screenplay") == 2
    assert after["status"] == "awaiting_style" and "workflow_restart_from" not in after
    assert after["script_complete_generation"] == 2


def test_topology_and_contracts_follow_project_execution_version(runtime):
    p, _ = runtime
    for version in (1, 2):
        project = {**p, "workflow_version": version}
        graph = project_graph(project)
        actual = wf.build_graph(None, version).get_graph().to_json()
        ids = {n["id"] for n in actual["nodes"]} - {"__start__", "__end__"}
        assert {x for n in graph["nodes"] for x in n["execution_nodes"]} == ids
        assert ("storyboard" in ids) == (version == 2)
        assert all(n["status"] != "succeeded" for n in graph["nodes"])
    reviewer = next(n for n in project_graph(p)["nodes"] if n["id"] == "screenplay")
    assert (
        reviewer["reviewer"] == "independent_critic"
        and "independent_critic" in reviewer["members"]
    )


def test_shot_attempt_observations_require_receipts_and_keep_versions(runtime):
    p, _ = runtime
    patch = {"shots": [{"id": "S01", "image": {"path": "anything.png"}}]}
    checkpoint(p["id"], patch)
    assert not store.get(p["id"], False).get("production_tasks")
    patch["shots"][0]["attempts"] = [
        {
            "stage": "image",
            "take": "take_01",
            "state": "rendering",
            "started_at": 10,
            "production_revision": 1,
        }
    ]
    checkpoint(p["id"], patch)
    checkpoint(p["id"], patch)
    patch["shots"][0]["attempts"][0].update(state="passed", finished_at=12)
    checkpoint(p["id"], patch)
    saved = store.get(p["id"])
    task = saved["production_tasks"]["S01:image:v1:take_01"]
    assert task["status"] == "succeeded" and task["finished_at"] == 12
    assert len([e for e in saved["events"] if e["kind"] == "production_task"]) == 2
    store.update(p["id"], {"production_revision": 2})
    checkpoint(p["id"], patch)
    assert len(store.get(p["id"], False)["production_tasks"]) == 1
    assert (
        store.get(p["id"], False)["production_tasks"]["S01:image:v1:take_01"]["status"]
        == "reused"
    )


def test_visual_model_payload_contains_rejection_and_version(monkeypatch):
    captured = []
    monkeypatch.setattr(creative, "report", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        creative,
        "cached_json_call",
        lambda *args, **kwargs: captured.append(args[5]) or {"styles": []},
    )
    creative._styles(
        {
            "brief": "短剧",
            "style_generation": 3,
            "style_feedback": "改用冷色",
            "styles": [{"name": "旧暖色"}],
        },
        {},
        {"title": "故事", "synopsis": "概要", "cast": {}, "shots": []},
        lambda _: None,
    )
    revision = captured[0]["project"]["visual_direction_revision"]
    assert revision["producer_visual_feedback"] == "改用冷色"
    assert (
        revision["generation"] == 3
        and revision["previous_directions"][0]["name"] == "旧暖色"
    )


def test_pending_guided_gate_can_resume_with_delegated_supervision(
    runtime, monkeypatch
):
    from factory import supervision

    p, calls = runtime
    wf.advance(p["id"])
    assert store.get(p["id"], False)["status"] == "awaiting_story"
    store.update(p["id"], {"team_mode": "delegated", "status": "queued"})
    supervised = []

    def supervise(pid, gate):
        assert store.get(pid, False)["status"] == "awaiting_" + gate["type"]
        supervised.append(gate["type"])
        if gate["type"] != "story":
            return None
        store.decide(pid, {"type": "story", "decision_id": gate["id"], "choice": "A"})
        return store.answer_for(pid, gate["id"])

    monkeypatch.setattr(supervision, "supervise_gate", supervise)
    wf.advance(p["id"])
    assert supervised == ["story", "style"]
    assert calls.count("screenplay") == 1
    assert store.get(p["id"], False)["status"] == "awaiting_style"


def test_restart_before_first_checkpoint_and_replayed_command_are_safe(runtime):
    p, calls = runtime
    current = store.get(p["id"], False)
    store.update(
        p["id"],
        {
            "workflow_restart_from": "research",
            "workflow_restart_revision": current["revision"] + 1,
        },
    )
    wf.advance(p["id"])
    assert calls == ["research", "proposals"]
    assert store.get(p["id"], False)["status"] == "awaiting_story"
    assert "workflow_restart_from" not in store.get(p["id"], False)


def test_script_review_automatic_repair_is_a_separate_persisted_round(
    runtime, monkeypatch
):
    p, calls = runtime
    original = creative_runtime.review_screenplay

    def review(project, config, callback):
        if project.get("creative_round", 1) == 1:
            calls.append("script_review")
            return {
                "script_review": {"passed": False},
                "draft_review": {"issues": [{"code": "causality"}]},
                "creative_round": 2,
            }
        return original(project, config, callback)

    monkeypatch.setattr(creative_runtime, "review_screenplay", review)
    to_style(runtime)
    saved = store.get(p["id"], False)
    assert (
        calls.count("screenplay") == 2
        and calls.count("storyboard") == 2
        and calls.count("script_review") == 2
    )
    assert saved["team_tasks"]["screenplay"]["attempt"] == 2
    assert saved["team_tasks"]["storyboard"]["attempt"] == 2
    assert saved["script_review"]["passed"] is True


def test_postproduction_tasks_follow_explicit_phases_and_receipts(runtime):
    p, _ = runtime
    checkpoint(
        p["id"],
        {
            "production": {
                "phase": "voice_rendering",
                "voice_attempts": ["audio/attempt_01.json"],
            }
        },
    )
    checkpoint(
        p["id"],
        {
            "production": {"phase": "voice_complete"},
            "assets": {
                "audio": {
                    "lines": [
                        {
                            "id": "S01_L1",
                            "source_signature": "abc123",
                            "metadata_path": "audio/line.json",
                            "path": "audio/line.wav",
                        }
                    ]
                }
            },
        },
    )
    checkpoint(
        p["id"],
        {
            "production": {"phase": "dialogue_rewriting"},
            "shots": [
                {
                    "id": "S01",
                    "dialogue_repairs": [
                        {
                            "attempt": 1,
                            "state": "rewriting",
                            "started_at": 10,
                            "production_revision": 1,
                        }
                    ],
                }
            ],
        },
    )
    checkpoint(
        p["id"],
        {
            "production": {"phase": "dialogue_repaired"},
            "shots": [
                {
                    "id": "S01",
                    "dialogue_repairs": [
                        {
                            "attempt": 1,
                            "state": "applied",
                            "started_at": 10,
                            "finished_at": 12,
                            "production_revision": 1,
                        }
                    ],
                    "attempts": [
                        {
                            "stage": "image",
                            "take": "take_01",
                            "state": "passed",
                            "production_revision": 1,
                            "semantic_attempts": [
                                {
                                    "at": 10,
                                    "elapsed_seconds": 2,
                                    "assessment": {
                                        "status": "pass",
                                        "reason": "构图与目标一致",
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )
    for phase in ("sound_edit", "edit", "final_technical_review", "delivery_ready"):
        checkpoint(p["id"], {"production": {"phase": phase}})
    before = store.get(p["id"], False)
    checkpoint(p["id"], {"production": {"phase": "delivery_ready"}})
    after = store.get(p["id"], False)
    tasks = after["production_tasks"]
    assert tasks["production:speech:v1:1"]["status"] == "succeeded"
    assert (
        tasks["S01_L1:speech:vunknown:abc123"]["provider_receipt"] == "audio/line.json"
    )
    assert tasks["S01:dialogue:v1:1"]["status"] == "succeeded"
    assert tasks["S01:image_review:v1:take_01:1"]["assessment"]["status"] == "pass"
    assert tasks["production:edit:v1:1"]["status"] == "finished"
    assert tasks["production:technical_review:v1:1"]["status"] == "succeeded"
    assert tasks.keys() == before["production_tasks"].keys()


def test_actual_supervisor_claim_can_consume_existing_manual_interrupt(
    runtime, monkeypatch, tmp_path
):
    from factory import config, supervision as sv

    p, calls = runtime
    wf.advance(p["id"])
    configuration = {"llm_roles": {"executive_producer": "boss"}}
    profile = {
        "profile": "boss",
        "model": "offline-test",
        "configured": True,
        "enabled": True,
    }
    monkeypatch.setattr(config, "load_config", lambda: configuration)
    monkeypatch.setattr(config, "resolve_llm", lambda *a: profile)
    monkeypatch.setattr(sv, "load_config", lambda: configuration)
    monkeypatch.setattr(sv, "resolve_llm", lambda *a: profile)
    monkeypatch.setattr(sv, "ROOT", tmp_path)
    requests = []

    def model(*args, **kwargs):
        requests.append(args[5]["gate"]["type"])
        return {
            "action": "select",
            "choice": "A",
            "rationale": "通过当前版本独立审稿，故事完整并适合制作。",
            "risks": ["终片仍需出品人确认。"],
        }

    monkeypatch.setattr(sv, "cached_json_call", model)
    store.action(
        p["id"],
        {
            "action": "delegate_control",
            "expected_revision": store.get(p["id"], False)["revision"],
        },
    )
    wf.advance(p["id"])
    saved = store.get(p["id"], False)
    assert saved["supervision"]["story"]["status"] == "selected"
    assert saved["selected_story"] == "A" and calls.count("screenplay") == 1
    assert requests == ["story"]
    assert saved["status"] == "awaiting_style"


def test_review_reentry_after_store_commit_does_not_review_old_draft_as_new_round(
    runtime, monkeypatch
):
    p, _ = runtime
    # Historical persisted review has no versioned editorial evidence.
    store.update(p["id"], {"creative_quality_contract": None})
    store.update(
        p["id"],
        {
            "script_generation": 1,
            "creative_round": 1,
            "screenplay_complete_round": [1, 1],
            "screenplay_draft": {"title": "旧稿", "shots": []},
            "story_resolution_complete_generation": 1,
        },
    )
    reviewed = []

    def review(project, config, callback):
        reviewed.append(
            (project["creative_round"], project["screenplay_complete_round"])
        )
        return {
            "creative_round": 2,
            "script_review": {"passed": False},
            "script_review_complete_round": [1, 1],
            "script_review_route": "repair",
        }

    monkeypatch.setattr(creative_runtime, "review_screenplay", review)
    state = {"project_id": p["id"]}
    assert wf.script_review(state) == {"route": "repair"}
    assert wf.script_review(state) == {"route": "repair"}
    assert reviewed == [(1, [1, 1])]
    wf.screenplay_v2(state)
    assert store.get(p["id"], False)["screenplay_complete_round"] == [1, 2]


def test_old_attempt_without_recorded_execution_version_is_not_relabelled(runtime):
    p, _ = runtime
    store.update(p["id"], {"production_revision": 2})
    checkpoint(
        p["id"],
        {
            "shots": [
                {
                    "id": "S01",
                    "attempts": [
                        {
                            "stage": "image",
                            "take": "take_01",
                            "state": "passed",
                            "started_at": 100,
                            "finished_at": 110,
                        }
                    ],
                }
            ]
        },
    )
    task = store.get(p["id"], False)["production_tasks"]["S01:image:vunknown:take_01"]
    assert task["production_revision"] is None and task["referenced_by_revision"] == 2
    assert task["status"] == "unknown" and task["started_at"] == 100


def test_old_two_round_ceiling_resumes_third_round_without_replaying_calls(runtime, monkeypatch):
    p, calls = runtime
    # Historical persisted review has no versioned editorial evidence.
    store.update(p["id"], {"creative_quality_contract": None})
    configuration = {"creative": {"max_review_rounds": 2}}
    monkeypatch.setattr(wf, "load_config", lambda: configuration)
    reviewed = []

    def review(project, config, callback):
        from factory.review_policy import max_review_rounds
        n = project["creative_round"]
        reviewed.append(n)
        passed = n == 4
        record = {"passed": passed, "issues": [{"severity": "major", "code": "causality",
            "description": "动作缺少因果", "evidence": "第%d轮证据" % n,
            "suggestion": "补齐动作", "shot_ids": ["S01"]}], "scores": {}}
        history = project.get("creative_review_rounds", []) + [{"round": n, "review": record}]
        result = {"script_review": {"passed": passed, "rounds": history, "review": record},
                  "creative_review_rounds": history, "draft_review": record,
                  "script_review_complete_round": [1, n],
                  "script_review_route": "continue" if passed else "repair"}
        if passed:
            result.update(script_complete_generation=1, needs_decision=None)
        elif n < max_review_rounds(config):
            result.update(creative_round=n + 1, needs_decision=None)
        else:
            result["needs_decision"] = {"kind": "script_review", "message": "旧上限阻断", "issues": record["issues"]}
        return result

    monkeypatch.setattr(creative_runtime, "review_screenplay", review)
    wf.advance(p["id"])
    decide(p["id"], "A")
    with pytest.raises(wf.ReviewBlocked):
        wf.advance(p["id"])
    blocked = store.get(p["id"], False)
    assert blocked["creative_round"] == 2
    assert blocked["script_review_complete_round"] == [1, 2]
    assert reviewed == [1, 2]
    configuration["creative"]["max_review_rounds"] = 4
    # Reopen the same LangGraph SQLite checkpoint, as a restarted worker does.
    wf.advance(p["id"])
    saved = store.get(p["id"], False)
    assert reviewed == [1, 2, 3, 4]
    assert calls.count("screenplay") == calls.count("storyboard") == 4
    assert saved["status"] == "awaiting_style"
    assert saved["script_review"]["passed"] is True
    assert [row["round"] for row in saved["creative_review_rounds"]] == [1, 2, 3, 4]
    wf.advance(p["id"])
    assert reviewed == [1, 2, 3, 4]


def test_persisted_exhausted_review_blocks_again_without_infinite_repair(runtime, monkeypatch):
    p, _ = runtime
    # Historical persisted review has no versioned editorial evidence.
    store.update(p["id"], {"creative_quality_contract": None})
    monkeypatch.setattr(wf, "load_config", lambda: {"creative": {"max_review_rounds": 4}})
    store.update(p["id"], {"creative_round": 4,
        "screenplay_complete_round": [1, 4], "script_review_complete_round": [1, 4],
        "script_review_route": "repair", "draft_review": {"scores": {}, "issues": []}})
    for _ in range(2):
        with pytest.raises(wf.ReviewBlocked):
            wf.script_review({"project_id": p["id"]})
    saved = store.get(p["id"], False)
    assert saved["creative_round"] == 4
    assert saved["review_automation"]["status"] == "blocked"
    assert "自动返修 3 次" in saved["needs_decision"]["message"]


@pytest.mark.parametrize("boundary", ["screenplay", "storyboard", "script_review"])
def test_lowered_limit_after_restart_preserves_draft_and_stops_pending_round(runtime, monkeypatch, boundary):
    p, calls = runtime
    configuration = {"creative": {"max_review_rounds": 4}}
    monkeypatch.setattr(wf, "load_config", lambda: configuration)
    reviewed = []
    class Paused(RuntimeError):
        pass

    def review(project, config, callback):
        n = project["creative_round"]
        reviewed.append(n)
        record = {"passed": False, "scores": {key: 8 for key in creative.SCORE_KEYS},
            "issues": [{"severity": "major", "code": "causality", "description": "缺少因果",
                        "evidence": "第%d轮真实证据" % n, "suggestion": "补齐因果", "shot_ids": ["S01"]}]}
        return {"script_review": {"passed": False, "review": record}, "draft_review": record,
                "creative_review_rounds": project.get("creative_review_rounds", []) + [{"round": n, "review": record}],
                "creative_round": n + 1, "script_review_complete_round": [1, n], "script_review_route": "repair"}
    monkeypatch.setattr(creative_runtime, "review_screenplay", review)
    name = {"screenplay": "write_screenplay", "storyboard": "direct_screenplay",
            "script_review": "review_screenplay"}[boundary]
    original = getattr(creative_runtime, name)
    def pause_third(project, config, callback):
        if project["creative_round"] == 3:
            raise Paused()
        return original(project, config, callback)
    monkeypatch.setattr(creative_runtime, name, pause_third)
    wf.advance(p["id"])
    decide(p["id"], "A")
    with pytest.raises(Paused):
        wf.advance(p["id"])
    before = store.get(p["id"], False)
    assert before["creative_round"] == 3
    assert before["script_review_complete_round"] == [1, 2]
    call_count = len(calls)
    configuration["creative"]["max_review_rounds"] = 2
    with pytest.raises(wf.ReviewBlocked):
        wf.advance(p["id"])
    after = store.get(p["id"], False)
    assert len(calls) == call_count and reviewed == [1, 2]
    for key in ("screenplay_draft", "creative_review_rounds", "draft_review", "screenplay_complete_round",
                "script_review_complete_round"):
        assert after[key] == before[key]
    assert after["review_automation"]["status"] == "blocked"
    assert after["review_automation"]["round"] == 2
    assert after["review_automation"]["blocked_round"] == 3
    assert after["needs_decision"]["issues"][0]["evidence"] == "第2轮真实证据"
