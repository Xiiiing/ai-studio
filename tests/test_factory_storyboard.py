"""CPU-only contracts for the bounded screenwriter-to-director handoff."""
import copy
import json
from unittest.mock import patch

import pytest
from jsonschema import ValidationError, validate

from factory import creative, llm, schemas, storyboard
from tests import test_factory_creative as creative_fixtures


@pytest.fixture(autouse=True)
def forbid_network_and_subprocess(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("This suite must not call a model, network, subprocess or GPU")
    monkeypatch.setattr("socket.getaddrinfo", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)


@pytest.fixture
def draft(tmp_path, monkeypatch):
    monkeypatch.setattr(schemas, "ROOT", tmp_path)
    project = {"id": "abc123abc123", "slug": "factory-abc123abc123",
               "title": "分镜交接验证", "format": "motion_comic", "duration_seconds": 60,
               "creation_mode": "original", "script_generation": 1}
    episode = creative._canonical_episode(creative_fixtures.fixture(schemas.episode_schema()), project)
    for index, shot in enumerate(episode["shots"], 1):
        shot["action"] = "The adult woman watches the man beside the closed door, beat %d." % index
        shot["dialogue"] = [{"id": shot["id"] + "_L1", "role": "female",
                            "text": "请等一下", "start": .2, "emotion": "平静"}]
        shot["qa"] = {"score": 45, "sentinel": "OLD_REVIEW_MUST_NOT_GUIDE_DIRECTOR"}
        shot["voice"] = {"path": "unchanged-voice.wav", "sha256": str(index) * 64}
        shot["render_style_prompt"] = "ink wash, cool blue palette, soft light"
        shot["render_style_source"] = {"artifact": "keep-existing-style-receipt.json"}
    episode["preserve_extension"] = {"sentinel": "unknown metadata is preserved"}
    config = {"llm": {"model": "writer-fixture"},
              "llm_roles": {"storyboard_director": "director"},
              "llm_profiles": {"director": {
                  "base_url": "http://127.0.0.1:8090/v1", "model": "director-fixture",
                  "context_tokens": 32768, "vision": True,
                  "temperature_override": .35, "system_prompt_mode": "system"}}}
    story = {"logline": "保留已批准的故事和结局", "ending": "人物完成同一晚的归还"}
    return project, config, episode, story


def direction_response(payload):
    return {"shots": {sid: {
        "prompt": "A restrained two-person medium shot with the woman by the closed door and the man opposite.",
        "motion_prompt": "A slow camera pan left across the still composition.",
        "camera_motion": "pan_left", "reference_group": "two",
    } for sid in payload["editable_shot_ids"]}, "blocked_shots": {}}


def call_direction(draft, **kwargs):
    project, config, episode, story = draft
    return storyboard.direct_storyboard(project, config, episode, approved_story=story, **kwargs)


def assert_story_unchanged(before, after, editable_ids):
    assert len(after["shots"]) == 15
    assert [s["id"] for s in after["shots"]] == [s["id"] for s in before["shots"]]
    assert {k: v for k, v in after.items() if k != "shots"} == {
        k: v for k, v in before.items() if k != "shots"}
    for old, new in zip(before["shots"], after["shots"]):
        if old["id"] not in editable_ids:
            assert new == old
        else:
            assert {k: v for k, v in new.items() if k not in storyboard.DIRECTOR_FIELDS} == {
                k: v for k, v in old.items() if k not in storyboard.DIRECTOR_FIELDS}


def test_complete_handoff_preserves_story_and_uses_independent_cached_role(draft, monkeypatch):
    calls = []
    def reply(**request):
        calls.append(copy.deepcopy(request))
        return direction_response(request["payload"])
    monkeypatch.setattr(llm, "json_call", reply)
    before = copy.deepcopy(draft[2])
    first, second = call_direction(draft), call_direction(draft)
    assert len(calls) == 1 and calls[0]["role"] == "storyboard_director"
    assert first == second and draft[2] == before
    assert_story_unchanged(before, first["episode"], {s["id"] for s in before["shots"]})
    assert all(s["camera_motion"] == "pan_left" and s["reference_group"] == "two"
               for s in first["episode"]["shots"])
    assert first["receipt"]["model_identity"]["profile"] == "director"
    assert first["receipt"]["status"] == "directed_pending_review"
    assert len(first["receipt"]["applied_shot_ids"]) == 15
    saved = json.loads((schemas.ROOT / first["artifact"]).read_text())
    assert saved["input_episode_signature"] == schemas.fingerprint(before)
    assert saved["output_episode_signature"] == schemas.fingerprint(first["episode"])
    payload = calls[0]["payload"]
    assert payload["approved_story"] == draft[3]
    assert len(payload["current_episode"]["shots"]) == 15
    assert "OLD_REVIEW_MUST_NOT_GUIDE_DIRECTOR" not in json.dumps(payload)
    assert payload["model_capabilities"]["synchronized_lip_motion"] is False
    assert "No diffusion video" in payload["model_capabilities"]["video_engine"]
    # A different director profile parameter must not reuse the old role response.
    changed = list(draft)
    changed[1] = copy.deepcopy(draft[1])
    changed[1]["llm_profiles"]["director"]["temperature_override"] = .25
    third = call_direction(changed)
    assert len(calls) == 2
    assert third["receipt"]["source_signature"] != first["receipt"]["source_signature"]


def test_partial_handoff_changes_only_requested_production_fields(draft, monkeypatch):
    calls = []
    def reply(**request):
        calls.append(copy.deepcopy(request))
        return direction_response(request["payload"])
    monkeypatch.setattr(llm, "json_call", reply)
    before = copy.deepcopy(draft[2])
    result = call_direction(draft, shot_ids=["S13", "S08"])
    assert result["receipt"]["requested_shot_ids"] == ["S08", "S13"]
    assert len(calls[0]["payload"]["locked_shot_ids"]) == 13
    assert set(calls[0]["schema"]["properties"]["shots"]["properties"]) == {"S08", "S13"}
    assert_story_unchanged(before, result["episode"], {"S08", "S13"})
    assert draft[2] == before




def test_feedback_and_repair_hints_reach_director_invalidate_cache_without_expanding_scope(draft, monkeypatch):
    calls = []
    def reply(**request):
        calls.append(copy.deepcopy(request))
        return direction_response(request["payload"])
    monkeypatch.setattr(llm, "json_call", reply)
    before = copy.deepcopy(draft[2])
    feedback = {"priority_order": ["revision_feedback"], "instructions": [
        {"source": "revision_feedback", "content": "修正 S08 的可见道具归属，保留其他镜头。"}]}
    issue = {"severity": "major", "code": "prop_continuity",
             "description": "请核查本镜是否有缺乏依据的物品",
             "evidence": "S08.prompt: a second parcel", "suggestion": "按当前动作核实物品来源",
             "shot_ids": ["S01", "S08"], "scores": {"causality": 1},
             "status": "fail", "summary": "An old verdict must not guide the director."}
    original_issue = copy.deepcopy(issue)
    args = {"shot_ids": ["S08"], "producer_feedback": feedback, "repair_issues": [issue]}
    first = call_direction(draft, **args)
    repeat = call_direction(draft, **args)
    assert len(calls) == 1 and repeat == first
    payload = calls[0]["payload"]
    assert payload["producer_feedback"] == feedback
    assert payload["repair_issues"] == [
        {key: issue[key] for key in storyboard.REPAIR_FIELDS}]
    assert not {"scores", "status", "summary"}.intersection(payload["repair_issues"][0])
    assert payload["editable_shot_ids"] == ["S08"] and "S01" in payload["locked_shot_ids"]
    assert set(calls[0]["schema"]["properties"]["shots"]["properties"]) == {"S08"}
    assert_story_unchanged(before, first["episode"], {"S08"})
    assert draft[2] == before and issue == original_issue

    changed_feedback = copy.deepcopy(feedback)
    changed_feedback["instructions"][0]["content"] = "S08 只描绘当前动作明确拥有的物品。"
    second = call_direction(draft, **{**args, "producer_feedback": changed_feedback})
    assert len(calls) == 2
    assert second["receipt"]["source_signature"] != first["receipt"]["source_signature"]
    changed_issue = {**issue, "evidence": "S08.continuity: the woman keeps the parcel"}
    third = call_direction(draft, **{**args, "repair_issues": [changed_issue]})
    assert len(calls) == 3
    assert third["receipt"]["source_signature"] != first["receipt"]["source_signature"]
    for result in (second, third):
        assert_story_unchanged(before, result["episode"], {"S08"})


@pytest.mark.parametrize("invalid_issues", [
    {"scores": {"causality": 1}, "issues": []}, ["an unstructured old verdict"]])
def test_full_review_or_unstructured_hints_fail_before_director_call(draft, monkeypatch, invalid_issues):
    monkeypatch.setattr(llm, "json_call", lambda **_: pytest.fail("Invalid hints must fail before a model call"))
    with pytest.raises(schemas.CreativeReviewError):
        call_direction(draft, repair_issues=invalid_issues)


def test_repeated_direction_keeps_motion_suffix_once_and_locked_shots_verbatim(draft, monkeypatch):
    draft[2]["shots"][7]["motion_prompt"] = "A restrained slow camera push across the still scene."
    calls = []
    def reply(**request):
        calls.append(copy.deepcopy(request))
        payload = request["payload"]
        selected = set(payload["editable_shot_ids"])
        return {"shots": {shot["id"]: {
            key: shot[key] + ("  " if key == "motion_prompt" else "")
            for key in storyboard.DIRECTOR_FIELDS}
            for shot in payload["current_episode"]["shots"] if shot["id"] in selected},
            "blocked_shots": {}}
    monkeypatch.setattr(llm, "json_call", reply)
    first = call_direction(draft, shot_ids=["S08"])
    next_draft = list(draft)
    next_draft[2] = first["episode"]
    second = call_direction(next_draft, shot_ids=["S08"])
    assert len(calls) == 2 and second["episode"] == first["episode"]
    suffix = "Keep all faces, bodies and closed mouths still; voice-over only."
    assert second["episode"]["shots"][7]["motion_prompt"].count(suffix) == 1
    assert_story_unchanged(draft[2], second["episode"], {"S08"})


@pytest.mark.parametrize("error", ["missing_id", "extra_id", "action_edit", "cast_edit", "locked_blocker"])
def test_invalid_model_output_never_updates_episode_or_enters_cache(draft, monkeypatch, error):
    before = copy.deepcopy(draft[2])
    def reply(**request):
        value = direction_response(request["payload"])
        if error == "missing_id":
            value["shots"].pop("S08")
        elif error == "extra_id":
            value["shots"]["S01"] = copy.deepcopy(value["shots"]["S08"])
        elif error == "action_edit":
            value["shots"]["S08"]["action"] = "A replacement plot action."
        elif error == "cast_edit":
            value["cast"] = {"female": "Replace the approved woman."}
        else:
            value["blocked_shots"]["S01"] = {
                "reason": "锁定镜头不能成为导演修改范围",
                "suggestion": "不应允许更改指定范围之外的镜头"}
        return value
    monkeypatch.setattr(llm, "json_call", reply)
    with pytest.raises(ValidationError):
        call_direction(draft, shot_ids=["S08"])
    assert draft[2] == before
    assert not list(schemas.planning_dir(draft[0]).glob("cache/storyboard-direction-*.json"))


@pytest.mark.parametrize("scope", [[], ["S08", "S08"], ["S99"]])
def test_invalid_scope_is_rejected_before_model_call(draft, monkeypatch, scope):
    monkeypatch.setattr(llm, "json_call", lambda **_: pytest.fail("Scope must fail before a model call"))
    with pytest.raises(schemas.CreativeReviewError):
        call_direction(draft, shot_ids=scope)


def test_unrepresentable_action_retains_writer_fields_and_produces_blocking_evidence(draft, monkeypatch):
    before = copy.deepcopy(draft[2])
    def reply(**request):
        result = direction_response(request["payload"])
        result["blocked_shots"]["S03"] = {
            "reason": "该动作依赖连续递交过程，静帧运镜不能表现其必需的动态变化",
            "suggestion": "反馈编剧用原有镜头槽位明确动作完成后的可见状态"}
        return result
    monkeypatch.setattr(llm, "json_call", reply)
    result = call_direction(draft)
    assert result["episode"]["shots"][2] == before["shots"][2]
    assert_story_unchanged(before, result["episode"], {s["id"] for s in before["shots"]} - {"S03"})
    issue = result["issues"][0]
    validate(issue, schemas.ISSUE_SCHEMA)
    assert issue["severity"] == "blocking" and issue["code"] == "storyboard_feasibility"
    assert issue["evidence"] == "S03.action: " + before["shots"][2]["action"]
    assert result["receipt"]["status"] == "feasibility_blocked"


@pytest.mark.parametrize("always_block", [False, True])
def test_creative_handoff_runs_between_writer_and_critic_with_shared_round_limit(always_block):
    case = creative_fixtures.CreativeContractTests(
        "test_approved_compact_target_reused_only_with_same_sources_policy_and_body")
    case.setUp()
    try:
        case.config["creative"] = {"separate_storyboard": True, "max_review_rounds": 2}
        director_calls = []
        def reply(**request):
            if request["role"] != "storyboard_director":
                return case.reply(**request)
            case.calls.append(copy.deepcopy(request))
            director_calls.append(copy.deepcopy(request))
            result = direction_response(request["payload"])
            if always_block or len(director_calls) == 1:
                result["blocked_shots"]["S03"] = {
                    "reason": "当前静帧媒介无法完成此镜头必要的连续动作",
                    "suggestion": "请编剧在现有剧情和时长约束下提供可见结果"}
            return result
        case.model.side_effect = reply
        project = case.approved_project()
        project["story_feedback"] = "以当晚物品归还为完整闭环，删除额外支线。"
        result = creative.create_script_and_styles(project, case.config, None)
        roles = [call["role"] for call in case.calls]
        triples = [role for role in roles if role in (
            "screenwriter", "storyboard_director", "independent_critic")]
        assert triples == ["screenwriter", "storyboard_director", "independent_critic"] * 2
        assert len(director_calls) == 2
        assert result["script_review"]["passed"] is (not always_block)
        assert (result["needs_decision"] is not None) is always_block
        writers = [c for c in case.calls if c["role"] == "screenwriter"]
        reviewers = [c for c in case.calls if c["role"] == "independent_critic"]
        assert "编剧兼分镜导演" not in writers[0]["system"]
        assert writers[1]["payload"]["independent_feedback"]["issues"][0]["code"] == "storyboard_feasibility"
        assert director_calls[0]["payload"]["repair_issues"] == []
        expected_feedback = creative._producer_feedback(project)
        assert expected_feedback["instructions"][0]["content"] == project["story_feedback"]
        for request in director_calls:
            assert request["payload"]["producer_feedback"] == expected_feedback
        previous_issues = writers[1]["payload"]["independent_feedback"]["issues"]
        assert director_calls[1]["payload"]["repair_issues"] == previous_issues
        assert "scores" not in director_calls[1]["payload"]
        assert director_calls[1]["payload"]["repair_issues"][0]["code"] == "storyboard_feasibility"
        assert reviewers[0]["payload"]["episode"]["shots"][0]["camera_motion"] == "pan_left"
        assert reviewers[0]["payload"]["technical_findings"][0]["code"] == "storyboard_feasibility"
        assert len(result["script_review"]["rounds"]) == 2
        assert all("storyboard" in item for item in result["script_review"]["rounds"])
        assert "storyboard_direction" in result["planning_artifacts"]
    finally:
        case.tearDown()
        case.doCleanups()


def test_unconfigured_director_preserves_legacy_writer_path():
    case = creative_fixtures.CreativeContractTests(
        "test_approved_compact_target_reused_only_with_same_sources_policy_and_body")
    case.setUp()
    try:
        result = creative.create_script_and_styles(case.approved_project(), case.config, None)
        assert "storyboard_director" not in [call["role"] for call in case.calls]
        assert result["script_review"]["passed"] is True
        assert "storyboard_direction" not in result["planning_artifacts"]
        writer = next(call for call in case.calls if call["role"] == "screenwriter")
        assert "编剧兼分镜导演" in writer["system"]
    finally:
        case.tearDown()
        case.doCleanups()
