"""Director compilation contracts; fixture prompts are not model-quality evidence."""
import copy
import hashlib
import json
import socket
from pathlib import Path

import pytest

from factory import h3_prompting as prompting
from factory.prompt_registry import load_prompt
from factory.scheduler import ProductionNeedsDecision
from tests.test_factory_role_routing import mock_transport, routing_config


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("H3 compiler tests must not invoke external services")
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def plan():
    return {"status": "ready", "blocked_reason": "",
            "visual_description": "Medium portrait of the approved adult in the cafe, natural window light.",
            "initial_state": "The adult faces the window, hands resting beside the cup.",
            "timeline": [{"start_seconds": 0, "end_seconds": 2,
                          "action": "The adult naturally blinks and turns toward the camera."},
                         {"start_seconds": 2, "end_seconds": 5,
                          "action": "The expression gradually softens into a small smile."}],
            "end_state": "The adult looks toward the camera with a gentle smile.",
            "camera": "Locked medium portrait; fixed camera.",
            "continuity": "Hands and the white cup remain on the table; preserve all approved people and clothing.",
            "audio": "silent"}


@pytest.fixture
def inputs(tmp_path):
    cfg = routing_config()
    cfg["llm_roles"]["storyboard_director"] = "qwen"
    return {"project": {"id": "promptfixture"},
            "shot": {"id": "S01", "action": "Turn toward camera and smile.",
                     "continuity": "The cup stays on the table.", "camera_motion": "hold",
                     "motion_prompt": "Natural head turn followed by a soft smile."},
            "config": cfg, "references": [{"sha256": "a" * 64, "bytes": 100}],
            "style": "Photorealistic live action", "seconds": 5,
            "output_dir": tmp_path / "compiler"}


def transport(monkeypatch, plan):
    def responder(value):
        return {"model": value["model"], "choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(plan)}}], "usage": {"prompt_tokens": 80, "completion_tokens": 150}}
    return mock_transport(monkeypatch, responder)


def test_registered_compiler_renders_anchor_sections_and_actual_director_receipt(monkeypatch, inputs, plan):
    calls, _ = transport(monkeypatch, plan)
    result = prompting.compile_prompt(**inputs)
    assert result["prompt"].startswith("For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\n")
    assert all(section in result["prompt"] for section in
               ("integrated_multimodal_description: [Shot 1]", "overall_soundscape:", "non_diegetic_music:"))
    assert "0.00s-2.00s" in result["prompt"] and "2.00s-5.00s" in result["prompt"]
    assert "overall_soundscape: Silent scene." in result["prompt"]
    assert result["prompt"].endswith("non_diegetic_music: None.")
    receipt = result["receipt"]
    assert receipt["quality_status"] == "pending_actual_media_review"
    assert receipt["compiler"]["model_identity"]["profile"] == "qwen"
    assert receipt["prompt"]["status"] == "registered" and receipt["prompt"]["version"] == "3.0.0"
    assert receipt["first_frame_sha256"] == "a" * 64
    assert receipt["rendered_prompt_sha256"] == hashlib.sha256(result["prompt"].encode()).hexdigest()
    assert calls[0]["request"]["model"] == "fixture-qwen"
    assert inputs["shot"]["action"] == "Turn toward camera and smile."
    cache = next(inputs["output_dir"].glob("h3-prompt-*.json"))
    assert json.loads(cache.read_text())["plan"] == plan
    receipt_path = next(inputs["output_dir"].glob("llm/calls/*/receipt.json"))
    actual = json.loads(receipt_path.read_text())
    assert actual["response_model"] == "fixture-qwen" and actual["status"] == "succeeded"


def test_cache_reuses_exact_input_and_changes_with_model_action_frame_style_and_repair(monkeypatch, inputs, plan):
    calls, _ = transport(monkeypatch, plan)
    original = prompting.compile_prompt(**inputs)
    assert prompting.compile_prompt(**inputs) == original
    assert len(calls) == 1
    variants = []
    for name in ("action", "frame", "style", "repair", "model"):
        updated = copy.deepcopy(inputs)
        if name == "action":
            updated["shot"]["action"] = "Turn to camera more slowly and smile."
        elif name == "frame":
            updated["references"][0]["sha256"] = "b" * 64
        elif name == "style":
            updated["style"] = "Warm naturalistic portrait"
        elif name == "repair":
            updated["repair"] = {"diagnosis": {"primary_code": "action", "codes": ["action"]},
                                 "positive_constraints": [{"field": "action", "text": inputs["shot"]["action"]}]}
        else:
            updated["config"]["llm_profiles"]["qwen"]["revision"] = "new-model-revision"
        variants.append(prompting.compile_prompt(**updated)["receipt"]["source_signature"])
    assert len(set(variants + [original["receipt"]["source_signature"]])) == 6
    assert len(calls) == 6


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(status="blocked", blocked_reason="The approved handover cannot finish in this duration."),
    lambda p: p["timeline"][0].update(end_seconds=6),
    lambda p: p["timeline"][1].update(start_seconds=1),
    lambda p: p["timeline"][0].update(start_seconds=True),
    lambda p: p["timeline"][0].update(start_seconds=2, end_seconds=2),
    lambda p: p.update(timeline=[]),
    lambda p: p.update(camera=""),
    lambda p: p.update(quality_passed=True),
    lambda p: p.update(audio="generated_music"),
])
def test_invalid_or_blocked_plan_never_persists_success_cache(monkeypatch, inputs, plan, mutation):
    mutation(plan)
    monkeypatch.setattr(prompting, "json_call", lambda **kwargs: copy.deepcopy(plan))
    with pytest.raises(ProductionNeedsDecision) as caught:
        prompting.compile_prompt(**inputs)
    assert caught.value.reason == ("h3_prompt_infeasible" if plan["status"] == "blocked" else "h3_prompt_unavailable")
    assert not list(inputs["output_dir"].glob("h3-prompt-*.json"))


def test_provider_outage_preserves_call_evidence_and_has_no_fallback(monkeypatch, inputs):
    calls, _ = mock_transport(monkeypatch)
    monkeypatch.setattr("factory.llm.check_model", lambda *a: (_ for _ in ()).throw(ConnectionError("private endpoint")))
    with pytest.raises(ProductionNeedsDecision) as caught:
        prompting.compile_prompt(**inputs)
    assert caught.value.reason == "h3_prompt_unavailable"
    assert "private endpoint" not in str(caught.value) and "private endpoint" not in str(caught.value.details)
    assert not calls and not list(inputs["output_dir"].glob("h3-prompt-*.json"))
    assert list(inputs["output_dir"].glob("llm/calls/*/receipt.json"))


def test_wrong_returned_model_is_rejected_by_actual_transport(monkeypatch, inputs, plan):
    def responder(value):
        return {"model": "unexpected-model", "choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(plan)}}]}
    mock_transport(monkeypatch, responder)
    with pytest.raises(ProductionNeedsDecision) as caught:
        prompting.compile_prompt(**inputs)
    assert caught.value.reason == "h3_prompt_unavailable"
    assert not list(inputs["output_dir"].glob("h3-prompt-*.json"))


def test_corrupt_cached_plan_is_revalidated_and_recompiled(monkeypatch, inputs, plan):
    calls, _ = transport(monkeypatch, plan)
    original = prompting.compile_prompt(**inputs)
    cache = next(inputs["output_dir"].glob("h3-prompt-*.json"))
    saved = json.loads(cache.read_text())
    saved["plan"]["timeline"][0]["end_seconds"] = 10
    cache.write_text(json.dumps(saved))
    assert prompting.compile_prompt(**inputs) == original
    assert len(calls) == 2


def test_existing_producer_task_requirements_remain_within_registered_context(monkeypatch, inputs, plan):
    from factory.execution_context import current_execution
    seen = []
    def call(**kwargs):
        seen.append(kwargs["payload"])
        return plan
    monkeypatch.setattr(prompting, "json_call", call)
    inputs["project"]["team_stage_requirements"] = {"storyboard": "Keep the approved framing."}
    token = current_execution.set({"stage": "storyboard"})
    try:
        prompting.compile_prompt(**inputs)
    finally:
        current_execution.reset(token)
    assert seen[0]["producer_task_requirements"]["requirements"] == "Keep the approved framing."


def test_model_identity_uses_actual_task_config_and_disabled_model_fails(monkeypatch, inputs):
    effective = copy.deepcopy(inputs["config"])
    effective["llm_roles"]["storyboard_director"] = "critic"
    monkeypatch.setattr(prompting, "task_config", lambda config, role: effective)
    assert prompting.identity(inputs["config"])["model_identity"]["model"] == "fixture-critic"
    effective["llm_profiles"]["critic"]["enabled"] = False
    with pytest.raises(ProductionNeedsDecision, match="分镜导演模型"):
        prompting.identity(inputs["config"])


@pytest.mark.parametrize("config", [
    {"llm_roles": {"storyboard_director": "missing"}, "llm_profiles": {}},
    {"llm_roles": []},
    {"llm": {"model": "missing-endpoint"}},
    None,
])
def test_identity_invalid_configuration_is_fail_closed(config):
    with pytest.raises(ProductionNeedsDecision) as caught:
        prompting.identity(config)
    assert caught.value.reason == "h3_prompt_unavailable"


@pytest.mark.parametrize("mutation", [
    lambda s: s.update(lip_sync=True),
    lambda s: (s.pop("action"), s.pop("motion_prompt")),
    lambda s: s.update(action=" ", motion_prompt=""),
    lambda s: s.update(action=None, motion_prompt=[]),
])
def test_conflicting_lip_sync_or_missing_approved_motion_stops_before_model(monkeypatch, inputs, mutation):
    mutation(inputs["shot"])
    monkeypatch.setattr(prompting, "json_call", lambda **kw: pytest.fail("Infeasible shot reached a model"))
    with pytest.raises(ProductionNeedsDecision) as caught:
        prompting.compile_prompt(**inputs)
    assert caught.value.reason == "h3_prompt_infeasible"
    assert not inputs["output_dir"].exists()


def test_repair_discards_negative_diagnosis_and_preserves_approved_facts(monkeypatch, inputs, plan):
    payloads = []
    def call(**kwargs):
        payloads.append(copy.deepcopy(kwargs["payload"]))
        return plan
    monkeypatch.setattr(prompting, "json_call", call)
    inputs.update(repair={"diagnosis": {"primary_code": "identity", "severity": "major",
                    "issues": [{"detail": "WRONG_PERSON_INVENTED_SCENE"}]},
                    "positive_constraints": [{"field": "action", "text": inputs["shot"]["action"]}]},
                   previous_prompt="Old rejected prompt (untrusted).")
    inputs["references"][0]["qa"] = {"semantic": {"issues": [{"detail": "Existing image observation"}]}}
    prompting.compile_prompt(**inputs)
    assert "WRONG_PERSON_INVENTED_SCENE" not in json.dumps(payloads)
    assert payloads[0]["approved_shot"]["action"] == inputs["shot"]["action"]
    assert payloads[0]["previous_prompt_untrusted"] == inputs["previous_prompt"]
    assert "untrusted_existing_qa" in payloads[0]["first_frame_references"][0]


@pytest.mark.parametrize("seconds", [True, 0, 16, float("nan"), float("inf")])
def test_bad_duration_fails_before_model(monkeypatch, inputs, seconds):
    monkeypatch.setattr(prompting, "json_call", lambda **kw: pytest.fail("Invalid duration called model"))
    inputs["seconds"] = seconds
    with pytest.raises(ProductionNeedsDecision):
        prompting.compile_prompt(**inputs)


def test_history_and_default_storyboard_templates_are_unchanged():
    assert load_prompt("storyboard_director").spec.version == "1.1.0"
    frozen = json.loads((Path(__file__).parent / "fixtures/prompt-baseline-v1.json").read_text())
    assert hashlib.sha256(load_prompt("storyboard_director", "1.0.0").encode()).hexdigest() == frozen["prompts"]["storyboard_director"]
    assert load_prompt("storyboard_director", "2.0.0").spec.version == "2.0.0"
    assert load_prompt("storyboard_director", "3.0.0").spec.output_schema_refs == ("factory.h3_prompting:PROMPT_SCHEMA",)
