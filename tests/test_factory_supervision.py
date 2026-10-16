"""Delegated gates use mock LLM output, temporary data and real checkpoint recovery."""
import copy
import hashlib

import pytest

from factory import store, workflows as wf, supervision as sv, schemas
from factory.config import ROLE_LABELS, list_roles
from factory.providers import validate_registry


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path / "runtime")
    monkeypatch.setattr(store, "DATABASE", tmp_path / "runtime/studio.sqlite3")
    monkeypatch.setattr(wf, "DATA", tmp_path / "runtime")
    monkeypatch.setattr(wf, "CHECKPOINTS", tmp_path / "runtime/checkpoints.sqlite3")
    for module in (wf, sv, schemas):
        monkeypatch.setattr(module, "ROOT", tmp_path)
    store.initialize()
    config = {"llm_roles": {sv.ROLE: "boss"}}
    monkeypatch.setattr(sv, "load_config", lambda: config)
    monkeypatch.setattr(sv, "resolve_llm", lambda *_: {"profile": "boss", "model": "manager-fixture",
        "configured": True, "enabled": True})
    def no_call(*args, **kwargs):
        raise AssertionError("Unexpected model request")
    monkeypatch.setattr(sv, "cached_json_call", no_call)
    return tmp_path, config


def project(**extra):
    return store.create({"brief": "两个成年人解决误会的原创悬疑短剧", "team_mode": "delegated", "workflow_version": 1, **extra})


def proposal(choice="B", action="select"):
    return {"action": action, "choice": choice, "rationale": "符合出品要求，保留因果和明确结局。", "risks": ["终片仍须人工审片。"]}


def story_gate(p):
    choices = [{"id": k, "title": "故事" + k, "synopsis": "成年人共同解决误会", "eligible": k != "A"} for k in "ABC"]
    store.update(p["id"], {"proposals": choices, "creation_review": {"passed": True},
        "proposals_complete_generation": 1})
    return store.gate(p["id"], "story", [{"id": k} for k in "ABC"], "选择故事", 1)


def style_gate(p, root):
    choices = []
    for ident in ("ink", "cinema", "anime"):
        raw = f"production/{p['slug']}/style_previews/{ident}/approved.png"
        path = root / raw
        path.parent.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        Image.new("RGB", (32, 32)).save(path)
        choices.append({"id": ident, "name": ident, "prompt": "Original visual design",
                        "preview": {"path": raw, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}})
    store.update(p["id"], {"styles": choices, "script_complete_generation": 1,
        "style_previews_generation": 1, "script_review": {"passed": True}})
    return store.gate(p["id"], "style", [{"id": v["id"]} for v in choices], "选择视觉", 1)


def final_gate(p):
    store.update(p["id"], {"delivery": {"video_url": "/media/fixture-only.mp4"}, "delivery_complete_revision": 1})
    return store.gate(p["id"], "final", [{"id": "approve"}, {"id": "revise"}], "终片人工审阅", 1)


@pytest.mark.parametrize("mode", [None, "auto", "", True, {}, []])
def test_mode_is_strict(env, mode):
    with pytest.raises(ValueError, match="团队模式"):
        project(team_mode=mode)


def test_explicit_guided_does_not_request_model(env, monkeypatch):
    p = store.create({"brief": "保留人工关卡的原创成年人故事", "team_mode": "guided"})
    gate = story_gate(p)
    called = []
    monkeypatch.setattr(sv, "cached_json_call", lambda *a, **k: called.append(True))
    assert sv.supervise_gate(p["id"], gate) is None
    assert not called and p["team_mode"] == "guided"


def test_authorized_story_choice_is_auditable_and_budget_unchanged(env, monkeypatch):
    p = project()
    gate = story_gate(p)
    store.update(p["id"], {"story_feedback": "保留双方主动解决误会"})
    from factory.execution_context import current_execution
    contexts = []
    def respond(*args, **kwargs):
        contexts.append(dict(current_execution.get()))
        return proposal()
    monkeypatch.setattr(sv, "cached_json_call", respond)
    answer = sv.supervise_gate(p["id"], gate)
    assert answer["choice"] == "B" and answer["actor"] == sv.ROLE
    assert answer["feedback"] == "保留双方主动解决误会"
    saved = store.get(p["id"])
    record = saved["supervision"]["story"]
    assert contexts == [{"project_id": p["id"], "stage": "story_decision", "run_id": record["run_id"]}]
    assert current_execution.get() is None
    assert record["status"] == "selected" and record["basis"] == sv.BASIS
    assert record["requested_model"] == "manager-fixture" and record["model_source"] == "request"
    assert saved["budget"] == p["budget"] and not saved.get("accepted_by")
    assert saved["answers"][gate["id"]] == answer
    assert sv.supervise_gate(p["id"], gate) is None


@pytest.mark.parametrize("choice", ["A", "unknown", "__repropose__", "approve"])
def test_unapproved_or_unknown_choice_cannot_advance(env, monkeypatch, choice):
    p = project()
    gate = story_gate(p)
    monkeypatch.setattr(sv, "cached_json_call", lambda *a, **k: proposal(choice))
    assert sv.supervise_gate(p["id"], gate) is None
    saved = store.get(p["id"])
    assert saved["status"] == "awaiting_story" and not saved["answers"]
    assert saved["supervision"]["story"]["reason_code"] == "invalid_selection"


@pytest.mark.parametrize("patch", [{"creation_review": {"passed": False}}, {"proposals_complete_generation": 0},
                                  {"proposal_generation": 2}])
def test_current_independent_review_is_required(env, monkeypatch, patch):
    p = project()
    gate = story_gate(p)
    store.update(p["id"], patch)
    called = []
    monkeypatch.setattr(sv, "cached_json_call", lambda *a, **k: called.append(True))
    assert sv.supervise_gate(p["id"], gate) is None
    assert not called and not store.get(p["id"])["answers"]


def test_missing_explicit_supervisor_binding_falls_back_to_human(env, monkeypatch):
    p = project()
    gate = story_gate(p)
    called = []
    monkeypatch.setattr(sv, "cached_json_call", lambda *a, **k: called.append(True))
    assert sv.supervise_gate(p["id"], gate, config={"llm": {"model": "legacy"}}) is None
    assert not called
    assert store.get(p["id"])["supervision"]["story"]["reason_code"] == "supervisor_not_configured"


def test_human_answer_during_model_request_wins(env, monkeypatch):
    p = project()
    gate = story_gate(p)
    def concurrent(*a, **k):
        store.decide(p["id"], {"type": "story", "choice": "B", "decision_id": gate["id"]})
        return proposal("C")
    monkeypatch.setattr(sv, "cached_json_call", concurrent)
    assert sv.supervise_gate(p["id"], gate) is None
    saved = store.get(p["id"])
    assert saved["answers"][gate["id"]]["choice"] == "B"
    assert saved["answers"][gate["id"]]["actor"] == "producer"
    assert saved["supervision"]["story"]["status"] == "superseded"


def test_pause_during_model_request_is_preserved(env, monkeypatch):
    p = project()
    gate = story_gate(p)
    def concurrent(*a, **k):
        store.action(p["id"], {"action": "pause"})
        return proposal()
    monkeypatch.setattr(sv, "cached_json_call", concurrent)
    assert sv.supervise_gate(p["id"], gate) is None
    saved = store.get(p["id"])
    assert saved["pause_requested"] and saved["status"] == "paused" and not saved["answers"]


def test_model_failure_has_no_secret_text_and_is_not_retried(env, monkeypatch):
    p = project()
    gate, calls = story_gate(p), []
    def fail(*a, **k):
        calls.append(True)
        raise RuntimeError("SECRET_MUST_NOT_ENTER_AUDIT")
    monkeypatch.setattr(sv, "cached_json_call", fail)
    assert sv.supervise_gate(p["id"], gate) is None
    assert sv.supervise_gate(p["id"], gate) is None
    saved = store.get(p["id"])
    assert len(calls) == 1 and "SECRET_MUST_NOT_ENTER_AUDIT" not in str(saved["supervision"])
    assert saved["supervision"]["story"]["error_type"] == "RuntimeError"


def test_extra_model_fields_cannot_grant_permissions(env, monkeypatch):
    p = project()
    gate = story_gate(p)
    monkeypatch.setattr(sv, "cached_json_call", lambda *a, **k: {**proposal(), "budget": {"gpu_minutes": 1440}})
    assert sv.supervise_gate(p["id"], gate) is None
    assert store.get(p["id"])["budget"] == p["budget"]


@pytest.mark.parametrize("action,choice,status", [("escalate", "", "advisory"), ("select", "approve", "manual")])
def test_final_is_always_human_only(env, monkeypatch, action, choice, status):
    p = project()
    gate = final_gate(p)
    monkeypatch.setattr(sv, "cached_json_call", lambda *a, **k: proposal(choice, action))
    assert sv.supervise_gate(p["id"], gate) is None
    saved = store.get(p["id"])
    assert saved["status"] == "awaiting_final" and not saved["answers"] and not saved.get("accepted_by")
    assert saved["supervision"]["final"]["status"] == status


def test_preview_must_exist_and_match_its_receipt(env, monkeypatch):
    root, _ = env
    p = project()
    gate = style_gate(p, root)
    styles = store.get(p["id"])["styles"]
    (root / styles[0]["preview"]["path"]).write_bytes(b"changed")
    monkeypatch.setattr(sv, "cached_json_call", lambda *a, **k: proposal("ink"))
    assert sv.supervise_gate(p["id"], gate) is None
    assert not store.get(p["id"])["answers"]


def test_preview_changed_while_model_runs_is_superseded(env, monkeypatch):
    root, _ = env
    p = project()
    gate = style_gate(p, root)
    path = root / store.get(p["id"])["styles"][0]["preview"]["path"]
    def change(*a, **k):
        path.write_bytes(b"changed")
        return proposal("ink")
    monkeypatch.setattr(sv, "cached_json_call", change)
    assert sv.supervise_gate(p["id"], gate) is None
    assert store.get(p["id"])["supervision"]["style"]["status"] == "superseded"


def test_cross_project_and_symlink_previews_are_rejected(env):
    root, _ = env
    p = project()
    style_gate(p, root)
    style = store.get(p["id"])["styles"][0]
    path = root / style["preview"]["path"]
    foreign = root / "foreign.png"
    foreign.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(foreign)
    assert not sv._preview_valid(p, style)
    style["preview"]["path"] = "../foreign.png"
    assert not sv._preview_valid(p, style)


def test_optional_role_does_not_break_all_preexisting_explicit_bindings():
    profile = {"provider": "openai_compatible", "base_url": "http://127.0.0.1:9000", "model": "fixture",
               "vision": True, "context_tokens": 32768}
    roles = {role: "all" for role in ROLE_LABELS if role != sv.ROLE}
    config = validate_registry({"all": profile}, roles, {"schema_version": 1})
    leader = next(row for row in list_roles(config) if row["role"] == sv.ROLE)
    assert not leader["enabled"] and leader["model"] is None and leader["optional"]


@pytest.mark.parametrize("crash_gate", [None, "story", "style"])
def test_delegated_real_graph_reaches_final_and_still_needs_producer(env, monkeypatch, crash_gate):
    root, _ = env
    p = project()
    calls = []
    def mock_llm(project, config, role, stage, system, payload, schema, **kwargs):
        kind = payload["gate"]["type"]
        calls.append(kind)
        return proposal("B") if kind == "story" else proposal("ink") if kind == "style" else proposal("", "escalate")
    monkeypatch.setattr(sv, "cached_json_call", mock_llm)
    def proposals(state):
        gate = story_gate(store.get(state["project_id"], False))
        return {"route": "continue", "story_generation": 1}
    def screenplay(state):
        current = store.get(state["project_id"], False)
        style_gate(current, root)
        store.update(current["id"], {"script": {"title": "已选故事", "cast": {}, "shots": []}})
        return {"route": "continue", "style_generation": 1}
    def production(state):
        final_gate(store.get(state["project_id"], False))
        return {"route": "continue", "final_generation": 1}
    for name, fn in [("research", lambda _: {"route": "continue"}), ("proposals", proposals),
                     ("screenplay", screenplay), ("visual_development", lambda _: {"route": "continue"}),
                     ("production", production)]:
        monkeypatch.setattr(wf, name, fn)
    if crash_gate:
        original = sv.supervise_gate
        crashed = []
        def save_then_crash(pid, gate, config=None):
            answer = original(pid, gate, config)
            if answer and gate["type"] == crash_gate and not crashed:
                crashed.append(True)
                raise RuntimeError("simulated loss after decision receipt before graph checkpoint")
            return answer
        monkeypatch.setattr(sv, "supervise_gate", save_then_crash)
        with pytest.raises(RuntimeError, match="simulated loss"):
            wf.advance(p["id"])
    wf.advance(p["id"])
    saved = store.get(p["id"])
    assert saved["status"] == "awaiting_final"
    assert saved["selected_story"] == "B" and saved["selected_style"] == "ink"
    gate = saved["decisions"][-1]
    store.decide(p["id"], {"type": "final", "choice": "approve", "decision_id": gate["id"]})
    wf.advance(p["id"])
    assert store.get(p["id"])["accepted_by"] == "producer"
    assert calls == ["story", "style", "final"]


def test_duplicate_candidate_ids_fail_closed_before_model_request(env, monkeypatch):
    p = project()
    gate = story_gate(p)
    saved = store.get(p["id"])
    saved["proposals"].insert(0, {"id": "B", "title": "歧义候选", "eligible": False})
    store.update(p["id"], {"proposals": saved["proposals"]})
    called = []
    monkeypatch.setattr(sv, "cached_json_call", lambda *a, **k: called.append(True))
    assert sv.supervise_gate(p["id"], gate) is None
    assert not called and not store.get(p["id"])["answers"]


def test_public_snapshot_is_bounded_and_read_only(env):
    p = project()
    original = copy.deepcopy(p)
    result = sv.snapshot(p)
    assert p == original and not result["records"]
    assert result["human_required"] == ["budget", "final", "platform_release"]
