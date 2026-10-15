"""Operator attention and graph views use isolated SQLite; no model generation."""
import copy
import json

import pytest

from factory import store, operator_console as console
from factory.team_blueprint import project_graph


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(store, "DATABASE", tmp_path / "db.sqlite3")
    store.initialize()
    return store.create({"title": "试用问题回归", "brief": "成年人合作解决误会的原创故事", "team_mode": "delegated"})


def gate(project, kind="story"):
    options = [{"id": "A", "label": "候选 A"}, {"id": "B", "label": "候选 B"}]
    store.update(project["id"], {"proposals": [{"id": "A", "title": "合格方向", "eligible": True}, {"id": "B", "title": "未合格", "eligible": False}, {"id": "C", "title": "不在关卡中", "eligible": True}]})
    return store.gate(project["id"], kind, options, "请确认创作方向", 1)


def test_live_delegated_gate_is_not_a_human_inbox_item(project):
    gate(project)
    p = store.get(project["id"], False)
    assert console.attention(p, job_running=True) is None
    human = console.attention(p, job_running=False)
    assert human["kind"] == "story" and human["can_choose"]
    assert [x["id"] for x in human["options"]] == ["A"]
    assert console.attention({**p, "team_mode": "guided"}, job_running=True)["kind"] == "story"


def test_known_old_generation_and_terminal_gates_are_not_reused(project):
    gate(project)
    p = store.get(project["id"], False)
    assert console.current_gate({**p, "proposal_generation": 2}) is None
    for status in ("approved", "cancelled"):
        assert console.attention({**p, "status": status}) is None


def test_empty_needs_decision_is_not_attention(project):
    for need in ({}, None):
        p = {**project, "status": "producing", "needs_decision": need}
        assert console.attention(p) is None
        assert console.project_summary(p)["pending_decisions"] == 0


def test_nested_creative_blocker_has_actionable_feedback_without_raw_dump(project):
    p = {**project, "status": "blocked", "selected_story": "A", "needs_decision": {"type": "review", "kind": "script_review", "message": "剧本审核未通过", "details": {"issues": [{"severity": "blocking", "description": "S03人物动机缺失", "suggestion": "在S03补齐救人的理由", "shot_ids": ["S03"], "evidence": "原分镜未说明", "private": "NOT_FOR_UI"}]}}}
    before = copy.deepcopy(p)
    a = console.attention(p)
    assert a["kind"] == "creative" and a["blocking"]
    assert a["issues"][0]["shot_ids"] == ["S03"]
    assert "补齐救人的理由" in a["suggested_feedback"]
    assert "NOT_FOR_UI" not in json.dumps(a)
    assert p == before


def test_blocked_project_uses_review_actions_even_with_dangling_gate(project):
    gate(project)
    p = store.get(project["id"], False)
    p.update(status="blocked", needs_decision={"kind": "script_review", "type": "review", "message": "重新审稿"})
    assert console.attention(p)["kind"] == "creative"


def test_preview_object_becomes_owned_media_url(project):
    store.update(project["id"], {"styles": [{"id": "A", "name": "素描", "preview": {"path": "production/" + project["slug"] + "/style_previews/A/preview.png"}}]})
    store.gate(project["id"], "style", [{"id": "A"}], "选视觉", 1)
    a = console.attention(store.get(project["id"], False))
    assert a["options"][0]["preview_url"].startswith("/media/production/" + project["slug"] + "/")


@pytest.mark.parametrize("url", ["https://other.invalid/img.png", "production/factory-abcdef012345/img.png", "production/factory-012345abcdef/../secret.json", "/etc/passwd", "javascript:alert(1)"])
def test_unowned_or_unsafe_preview_paths_have_no_link(project, url):
    assert console.media_url(project, url) is None


def test_current_snapshot_reads_job_claim_and_decision_together(project):
    gate(project)
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='running' WHERE project_id=?", (project["id"],))
    overview = console.dashboard()
    assert overview["metrics"]["attention"] == 0 and overview["metrics"]["running"] == 1
    work = console.workspace(project["id"])
    assert work["revision"] == work["project"]["revision"] == work["workflow"]["revision"]
    assert work["operator"]["attention"] is None
    selected = next(n for n in work["workflow"]["nodes"] if n["id"] == "story_decision")
    assert selected["status"] == "running" and selected["owner"] == "executive_producer"
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='completed' WHERE project_id=?", (project["id"],))
    assert console.dashboard()["metrics"]["attention"] == 1


def test_graph_covers_real_executable_nodes_without_fabricated_success(project):
    from factory.workflows import build_graph
    graph = project_graph(project)
    actual = {node["id"] for node in build_graph(None).get_graph().to_json()["nodes"] if node["id"] not in ("__start__", "__end__")}
    assert {x for node in graph["nodes"] for x in node["execution_nodes"]} == actual
    assert all(node["status"] != "succeeded" for node in graph["nodes"])
    assert any(edge["loop"] for edge in graph["edges"])


def test_optional_cas_rejects_stale_decision_but_accepts_exact_duplicate(project):
    d = gate(project)
    before = store.get(project["id"], False)
    payload = {"type": "story", "choice": "A", "decision_id": d["id"], "expected_revision": before["revision"]}
    store.update(project["id"], {"progress": 13})
    with pytest.raises(ValueError, match="已更新"):
        store.decide(project["id"], payload)
    assert not store.get(project["id"], False)["answers"]
    payload["expected_revision"] = store.get(project["id"], False)["revision"]
    first = store.decide(project["id"], payload)
    second = store.decide(project["id"], payload)
    assert first["revision"] == second["revision"]


def test_optional_cas_blocks_stale_pause_without_side_effect(project):
    with pytest.raises(ValueError, match="已更新"):
        store.action(project["id"], {"action": "pause", "expected_revision": project["revision"] - 1})
    assert store.get(project["id"], False)["pause_requested"] is False


def test_pending_safe_pause_is_not_a_resume_button_until_job_yields(project):
    a = console.attention({**project, "pause_requested": True}, job_running=True)
    assert a["kind"] == "paused" and not a["can_resume"]
