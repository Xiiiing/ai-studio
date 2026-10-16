"""No artwork or character motion is certified by a filename or UI format."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from factory import llm
from factory.media_contract import motion_provenance, preview_contract_valid, video_motion
from factory.production import _Run, generate_project, generate_style_previews
from factory.scheduler import ProductionNeedsDecision, Scheduler
from studio.core import media_receipt
from tests import test_factory_production as fixtures


def contract(style="illustrated"):
    return {"schema_version": 1, "visual_style": style, "story_mode": "standalone",
            "pacing": "short_drama", "motion": "still_camera" if style == "illustrated" else "generated_video"}


@pytest.fixture
def case():
    value = fixtures.FactorySafetyTests("runTest")
    value.setUp()
    value.project["creative_contract"] = contract()
    value.config.update(semantic_enabled=True, semantic_review_revision="contract-test-v1")
    try:
        yield value
    finally:
        value.tearDown()
        value.doCleanups()


def passing_review(*args):
    assert not fixtures.FixtureWorker.active
    return {"status": "pass", "score": 95, "issues": [],
            "style_alignment": {"status": "match", "evidence": "符合明确指定的画风和角色视觉设计"}}


def test_contract_previews_require_independent_review_and_reuse_success(case):
    calls = []
    def reviewer(project, shot, path, references):
        calls.append(copy.deepcopy(shot))
        return passing_review()
    with patch.object(Scheduler, "worker", fixtures.fake_worker):
        first = generate_style_previews(case.project, case.config, case.save, semantic_evaluator=reviewer)
        second = generate_style_previews(case.project, case.config, case.save, semantic_evaluator=reviewer)
    assert len(fixtures.FixtureWorker.calls) == 3 and len(calls) == 3
    assert all(value["review_purpose"] == "visual_direction_preview" for value in calls)
    assert all(value["visual_direction"]["id"] in {"a", "b", "c"} for value in calls)
    assert all(value["preview"]["qa"]["semantic_passed"] for value in first)
    assert all(preview_contract_valid(case.project, value) for value in first)
    assert first[0]["preview"]["sha256"] == second[0]["preview"]["sha256"]


def test_missing_preview_reviewer_stops_before_generation(case):
    with pytest.raises(ProductionNeedsDecision) as caught:
        generate_style_previews(case.project, case.config, case.save)
    assert caught.value.reason == "semantic_unavailable"
    assert not fixtures.FixtureWorker.calls


def test_approved_style_changes_invalidate_review_even_when_media_inputs_match(case):
    case.project["selected_style"] = "a"
    first = _Run(case.project, case.config, case.save, None, passing_review)
    target1 = first._target("S01", case.folder / "renders/S01", "Unchanged media prompt", kind="video")
    case.project["styles"][0]["prompt"] = "Revised approved palette and lighting"
    second = _Run(case.project, case.config, case.save, None, passing_review)
    target2 = second._target("S01", case.folder / "renders/S01", "Unchanged media prompt", kind="video")
    assert target1["signature"] == target2["signature"]
    assert target1["review_signature"] != target2["review_signature"]
    case.project["format"] = "animation"
    case.project["creative_contract"] = contract("anime_2d")
    third = _Run(case.project, case.config, case.save, None, passing_review)
    target3 = third._target("S01", case.folder / "renders/S01", "Unchanged media prompt", kind="video")
    assert target3["signature"] != target2["signature"]


@pytest.mark.parametrize("record", [{}, {"motion_method": "generated_video"},
    {"origin": "procedural_camera_motion", "generation_kind": "local_diffusion_video", "diffusion_video": True}])
def test_animation_cannot_deliver_camera_only_or_unknown_provenance(case, record):
    case.project.update(format="animation", creative_contract=contract("anime_2d"))
    run = _Run(case.project, case.config, case.save, None, passing_review)
    for shot in run.shots:
        shot["video"] = record
    with pytest.raises(ProductionNeedsDecision) as caught:
        run._assert_semantic_gate()
    assert caught.value.reason == "generated_motion_required"


def test_animation_routes_to_real_video_generation_and_not_camera(case):
    case.project.update(format="animation", creative_contract=contract("anime_2d"))
    run = _Run(case.project, case.config, case.save, None, passing_review)
    image = case.folder / "reference.png"
    Image.new("RGB", (64, 64), "white").save(image)
    for shot in run.shots:
        shot["image"] = media_receipt(image)
    with patch.object(run, "_camera_clip", side_effect=AssertionError("No camera substitute")), \
         patch.object(run, "_render_targets") as render:
        run._video()
    targets, phase = render.call_args.args
    assert phase == "diffusion_video" and all(value["kind"] == "video" for value in targets)
    assert all("Character movement must be visible" in value["prompt"] for value in targets)
    assert all("2D anime" in value["prompt"] for value in targets)


def test_export_records_contract_and_actual_still_motion(case):
    with patch.object(Scheduler, "worker", fixtures.fake_worker):
        delivered = generate_project(case.project, case.config, case.save, semantic_evaluator=passing_review)
    assert delivered["creative_contract"] == contract()
    assert delivered["motion_provenance"] == {
        "requested": "still_camera", "generated_video_shots": 0, "still_camera_shots": 2,
        "unknown_shots": 0, "lip_sync": False}
    assert delivered["semantic_status"] == "pass"


@pytest.mark.parametrize("style,status,expected", [
    ("anime_2d", "match", "pass"), ("stylized_3d", "mismatch", "fail"),
    ("photorealistic", "uncertain", "unavailable"),
])
def test_independent_style_check_receives_contract_and_can_override_high_score(tmp_path, style, status, expected):
    sample = tmp_path / "candidate.png"
    sample.write_bytes(b"fixture")
    project = {"slug": "factory-contract-test", "format": "live_action" if style == "photorealistic" else "animation",
               "creative_contract": contract(style), "selected_style": "a",
               "styles": [{"id": "a", "name": "批准画风", "prompt": "Approved palette", "preview": {"qa": "OLD_VERDICT"}}]}
    fresh = {"status": "pass", "score": 99, "summary": "画面清晰", "issues": [],
             "style_alignment": {"status": status, "evidence": "根据当前候选观察具体媒介和材质"}}
    seen = {}
    def wire(*args, **kwargs):
        seen.update(context=json.loads(kwargs["content"][0]["text"]), schema=args[4])
        return copy.deepcopy(fresh)
    with patch.object(llm, "_image_url", return_value="data:image/png;base64,bW9jaw=="), \
         patch.object(llm, "_request", side_effect=wire):
        result = llm.review_media(project, {"id": "S01"}, sample, [],
                                  {"llm": {"model": "fixture"}}, output_dir=tmp_path / "review")
    assert seen["context"]["creative_contract"] == project["creative_contract"]
    assert seen["context"]["approved_visual_style"] == {"id": "a", "name": "批准画风", "prompt": "Approved palette"}
    assert "OLD_VERDICT" not in json.dumps(seen["context"])
    assert "style_alignment" in seen["schema"]["required"] and "motion_alignment" not in seen["schema"]["required"]
    assert result["status"] == expected


@pytest.mark.parametrize("observation,expected", [("character_motion", "pass"), ("object_motion", "pass"), ("camera_only", "fail"), ("uncertain", "unavailable")])
def test_video_quality_requires_observed_character_motion_not_only_frame_change(tmp_path, observation, expected):
    project = {"slug": "factory-contract-video", "format": "animation", "creative_contract": contract("anime_2d")}
    answer = {"status": "pass", "score": 99, "summary": "画面清晰", "issues": [],
              "style_alignment": {"status": "match", "evidence": "全部抽帧保持指定二维动画画风"},
              "motion_alignment": {"status": observation, "evidence": "0秒至1秒之间对照可见人物的姿态变化"}}
    samples = [{"image": tmp_path / "sample.png"}] * 2
    with patch.object(llm, "_image_url", return_value="data:image/png;base64,bW9jaw=="), \
         patch("factory.evaluation_sampling.sample_video", return_value=(samples, {"policy": "fixture", "timestamps": [0, 1]})), \
         patch.object(llm, "_request", return_value=answer):
        result = llm.review_media(project, {"id": "S01"}, tmp_path / "candidate.mp4", [],
                                  {"llm": {"model": "fixture"}}, output_dir=tmp_path / "review")
    assert result["status"] == expected and result["sampled_frames"] == 2
    if observation == "camera_only":
        assert any(value["code"] == "action" and value["severity"] == "blocking" for value in result["issues"])


def test_provenance_counts_mixed_and_unknown_without_trusting_desired_method():
    shots = [{"video": {"origin": "procedural_camera_motion"}},
             {"video": {"generation_kind": "local_diffusion_video"}},
             {"video": {"motion_method": "generated_video"}}]
    summary = motion_provenance(shots, "generated_video")
    assert summary["generated_video_shots"] == summary["still_camera_shots"] == summary["unknown_shots"] == 1
    assert video_motion(shots[2]["video"]) == "unknown"


def test_preview_gate_rejects_old_technical_only_or_changed_art(case):
    from factory.attention_actions import validate_gate
    from studio.core import fingerprint
    style = case.project["styles"][0]
    case.project.update(workflow_version=1, selected_style=None, style_generation=1)
    gate = {"id": case.ident + ":style:1", "type": "style", "status": "pending", "options": [{"id": "a"}]}
    style["preview"] = {"qa": {"eligible_for_selection": True, "technical": {"status": "pass"}}}
    with pytest.raises(ValueError, match="独立审核"):
        validate_gate(case.project, gate, "a")
    from factory.media_contract import visual_direction
    style["preview"].update(creative_contract_signature=fingerprint(contract()),
                            visual_direction_signature=fingerprint(visual_direction(style)))
    style["preview"]["qa"]["semantic"] = passing_review()
    validate_gate(case.project, gate, "a")
    style["prompt"] = "An unreviewed replacement art direction"
    with pytest.raises(ValueError, match="独立审核"):
        validate_gate(case.project, gate, "a")


@pytest.mark.parametrize("preview", [False, True])
def test_keyframes_do_not_need_to_complete_future_video_action(tmp_path, preview):
    captured = {}
    project = {"slug": "factory-first-frame", "format": "animation", "creative_contract": contract("anime_2d")}
    shot = {"id": "S01", "action": "Raise the amulet from waist to chest.",
            "motion_prompt": "Raise right arm over time.", "prompt": "Right hand holding an amulet at the waist."}
    if preview:
        shot["review_purpose"] = "visual_direction_preview"
    def wire(*args, **kwargs):
        captured.update(json.loads(kwargs["content"][0]["text"]))
        return {"status": "pass", "score": 95, "summary": "起始画面符合要求", "issues": [],
                "style_alignment": {"status": "match", "evidence": "二维角色画风与当前静帧要求一致"}}
    with patch.object(llm, "_image_url", return_value="data:image/png;base64,bW9jaw=="), \
         patch.object(llm, "_request", side_effect=wire):
        result = llm.review_media(project, shot, tmp_path / "frame.png", [],
                                  {"llm": {"model": "fixture"}}, output_dir=tmp_path / "review")
    assert "action" not in captured["shot"] and "motion_prompt" not in captured["shot"]
    assert captured["shot"]["prompt"] == shot["prompt"] and result["status"] == "pass"


@pytest.mark.parametrize("motion", ["character_motion", "object_motion"])
def test_generated_file_and_generic_visual_pass_do_not_certify_motion(case, motion):
    case.project.update(format="animation", creative_contract=contract("anime_2d"))
    run = _Run(case.project, case.config, case.save, None, passing_review)
    for shot in run.shots:
        shot["image"] = {"qa": {"semantic": passing_review()}}
        shot["video"] = {"generation_kind": "local_diffusion_video", "qa": {"semantic": passing_review()}}
    with pytest.raises(ProductionNeedsDecision) as caught:
        run._assert_semantic_gate()
    assert caught.value.reason == "semantic_unavailable"
    for shot in run.shots:
        shot["video"]["qa"]["semantic"]["motion_alignment"] = {
            "status": motion, "evidence": "0到1秒可见批准主体动作，与相机运动独立"}
    run._assert_semantic_gate()


@pytest.mark.parametrize("mode,fmt", [("system", "llama_json_schema"), ("user_prefix", "prompt")])
def test_temporal_review_reserves_reference_and_output_tokens(mode, fmt):
    profile = {"context_tokens": 32768, "image_token_budget": 4096,
               "system_prompt_mode": mode, "response_format": fmt}
    content = [{"type": "text", "text": "Current frame expectations " * 60},
               {"type": "image_url", "image_url": {"url": "data:image/png;base64,reference"}}]
    count = llm._review_sample_limit(profile, content, llm._contract_review_schema(True), 8, 2200)
    assert 4 <= count < 8
    assert len(content) == 2 and content[-1]["image_url"]["url"].endswith("reference")


def test_small_vision_context_preserves_media_without_a_model_call(tmp_path):
    project = {"slug": "factory-small-context", "format": "animation", "creative_contract": contract("anime_2d")}
    profile = {"context_tokens": 8192, "image_token_budget": 4096}
    with patch.object(llm, "resolve_llm", return_value=profile), \
         patch.object(llm, "_request", side_effect=AssertionError("Must not submit oversized request")), \
         patch("factory.evaluation_sampling.sample_video", side_effect=AssertionError("No pointless decoding")):
        result = llm.review_media(project, {"id": "S01"}, tmp_path / "existing.mp4", [],
                                  {"quality": {"semantic_enabled": True}}, output_dir=tmp_path / "review")
    assert result["status"] == "unavailable" and result["reason"] == "visual_context_too_small"


def test_context_limited_sampling_is_explicit_and_keeps_uniform_endpoints(tmp_path):
    project = {"slug": "factory-limited-context", "format": "animation", "creative_contract": contract("anime_2d")}
    profile = {"context_tokens": 32768, "image_token_budget": 4096, "model": "fixture", "profile": "vision"}
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"fixture reference")
    counts = []
    def sample(path, count):
        counts.append(count)
        timestamps = [3 * index / (count - 1) for index in range(count)]
        return ([{"image": reference} for _ in timestamps],
                {"policy": "fixture_uniform_endpoints", "requested_samples": count, "timestamps": timestamps})
    answer = {"status": "pass", "score": 95, "summary": "动作符合批准分镜", "issues": [],
              "style_alignment": {"status": "match", "evidence": "所有抽样保持明确的二维动画画风"},
              "motion_alignment": {"status": "character_motion", "evidence": "0到3秒之间可见手臂抬起和玉佩上移"}}
    with patch.object(llm, "resolve_llm", return_value=profile), \
         patch.object(llm, "_image_url", return_value="data:image/png;base64,bW9jaw=="), \
         patch("factory.evaluation_sampling.sample_video", side_effect=sample), \
         patch.object(llm, "_request", return_value=answer):
        result = llm.review_media(project, {"id": "S01"}, tmp_path / "existing.mp4", [reference],
                                  {"quality": {"semantic_enabled": True}}, output_dir=tmp_path / "review")
    assert 4 <= counts[0] < 8
    assert result["sampling"]["configured_samples"] == 8 and result["sampling"]["context_limited"] is True
    assert result["sampling"]["timestamps"][0] == 0 and result["sampling"]["timestamps"][-1] == 3
    assert result["reference_count"] == 1 and result["status"] == "pass"
