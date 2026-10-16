"""Real creative boundaries with offline model responses and durable receipts."""
import copy

import pytest

from factory import creative, creative_runtime, review_policy
from tests import test_factory_creative as fixtures
from tests.test_factory_storyboard import direction_response


@pytest.fixture
def case():
    value = fixtures.CreativeContractTests("runTest")
    value.setUp()
    try:
        yield value
    finally:
        value.tearDown()
        value.doCleanups()


@pytest.mark.parametrize("invalid", [True, False, 0, 7, "4", 2.0, None])
def test_round_limit_rejects_non_integer_and_out_of_bounds(invalid):
    with pytest.raises(ValueError, match="integer from 1 to 6"):
        review_policy.max_review_rounds({"creative": {"max_review_rounds": invalid}})


def test_default_and_explicit_round_limits():
    assert review_policy.max_review_rounds({}) == 4
    assert review_policy.max_review_rounds({"creative": {"max_review_rounds": 2}}) == 2
    assert review_policy.max_review_rounds({"creative": {"max_review_rounds": 6}}) == 6


def configure(case, passing_round):
    case.config["creative"] = {"separate_storyboard": True}

    def reply(**request):
        role = request["role"]
        if role == "storyboard_director":
            case.calls.append(copy.deepcopy(request))
            return direction_response(request["payload"])
        value = case.reply(**request)
        if role == "screenwriter":
            n = sum(row["role"] == "screenwriter" for row in case.calls)
            value["title"] = "第%d轮完整稿" % n
        elif role == "independent_critic" and "episode" in request["payload"]:
            n = sum(row["role"] == "independent_critic" and "episode" in row["payload"] for row in case.calls)
            value["issues"] = [] if n >= passing_round else [
                {**fixtures.major_issue(), "evidence": "第%d轮真实审核证据" % n}]
        return value

    case.model.side_effect = reply
    # Include a resolved target: this formerly discarded the complete old draft.
    return {**case.approved_project(), "workflow_version": 2,
            "story_feedback": "保留同一晚归还钥匙的闭环，删除宠物支线。"}


def assert_repair_inputs(case, rounds):
    writers = [row for row in case.calls if row["role"] == "screenwriter"]
    directors = [row for row in case.calls if row["role"] == "storyboard_director"]
    assert len(writers) == len(directors) == rounds
    for index in range(1, rounds):
        writer = writers[index]["payload"]
        director = directors[index]["payload"]
        for payload in (writer, director):
            assert payload["previous_attempt"]["title"] == "第%d轮完整稿" % index
            assert len(payload["previous_attempt"]["shots"]) == 15
        assert writer["independent_feedback"]["issues"][0]["evidence"] == "第%d轮真实审核证据" % index
        assert director["repair_issues"][0]["evidence"] == "第%d轮真实审核证据" % index


@pytest.mark.parametrize("boundary,status", [
    ("write_screenplay", "writing"), ("direct_screenplay", "writing"),
    ("review_screenplay", "reviewing"),
])
def test_current_round_is_visible_before_slow_model_returns(case, boundary, status):
    project = configure(case, 99)
    project.update(creative_runtime.resolve_target(project, case.config, None))
    project.update(creative_runtime.write_screenplay(project, case.config, None))
    project.update(creative_runtime.direct_screenplay(project, case.config, None))
    project.update(creative_runtime.review_screenplay(project, case.config, None))
    assert project["creative_round"] == 2
    if boundary != "write_screenplay":
        project.update(creative_runtime.write_screenplay(project, case.config, None))
    if boundary == "review_screenplay":
        project.update(creative_runtime.direct_screenplay(project, case.config, None))
    observed = {}

    def interrupted(**request):
        assert observed["review_automation"]["round"] == 2
        assert observed["review_automation"]["status"] == status
        raise KeyboardInterrupt("model has not returned")

    case.model.side_effect = interrupted
    with pytest.raises(KeyboardInterrupt):
        getattr(creative_runtime, boundary)(project, case.config, observed.update)
    assert project["script_review_complete_round"] == [1, 1]


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("passing_round", [3, 4])
def test_third_or_fourth_review_passes_without_human_intervention(case, version, passing_round):
    project = configure(case, passing_round)
    if version == 1:
        result = creative.create_script_and_styles(project, case.config, None)
    else:
        project.update(creative_runtime.resolve_target(project, case.config, None))
        for _ in range(4):
            project.update(creative_runtime.write_screenplay(project, case.config, None))
            project.update(creative_runtime.direct_screenplay(project, case.config, None))
            project.update(creative_runtime.review_screenplay(project, case.config, None))
            if project["script_review"]["passed"] or project.get("needs_decision"):
                break
        result = project
    assert result["script_review"]["passed"] is True
    assert len(result["script_review"]["rounds"]) == passing_round
    assert result["needs_decision"] is None
    assert result["review_automation"]["status"] == "passed"
    assert result["review_automation"]["automatic_repairs"] == passing_round - 1
    assert_repair_inputs(case, passing_round)


@pytest.mark.parametrize("limit", [1, 2, 4, 6])
def test_exhausted_rounds_preserve_blockers_and_do_not_design_visuals(case, limit):
    project = configure(case, 99)
    case.config["creative"]["max_review_rounds"] = limit
    result = creative.create_script_and_styles(project, case.config, None)
    assert result["review_automation"]["status"] == "blocked"
    assert len(result["script_review"]["rounds"]) == limit
    assert result["needs_decision"]["issues"][0]["evidence"] == "第%d轮真实审核证据" % limit
    assert "自动返修 %d 次" % (limit - 1) in result["needs_decision"]["message"]
    assert not any(row["role"] == "visual_designer" for row in case.calls)
    assert_repair_inputs(case, limit)


def test_legacy_restart_reuses_completed_two_rounds_and_feedback(case):
    project = configure(case, 4)

    class Paused(RuntimeError):
        pass

    def pause(patch):
        if patch.get("review_automation", {}).get("round") == 3:
            raise Paused()

    with pytest.raises(Paused):
        creative.create_script_and_styles(project, case.config, pause)
    assert len([row for row in case.calls if row["role"] == "screenwriter"]) == 2
    result = creative.create_script_and_styles(project, case.config, None)
    assert result["script_review"]["passed"] is True
    assert_repair_inputs(case, 4)
    before = case.model.call_count
    creative.create_script_and_styles(project, case.config, None)
    assert case.model.call_count == before


@pytest.mark.parametrize("passing_round", [3, 4])
def test_proposals_automatically_repair_until_first_eligible_candidate(case, passing_round):
    reviews = 0

    def reply(**request):
        nonlocal reviews
        value = case.reply(**request)
        if "reviews" in request["schema"]["properties"]:
            reviews += 1
            for row in value["reviews"]:
                row["issues"] = [] if reviews >= passing_round and row["proposal_id"] == "A" else [fixtures.major_issue()]
        return value

    case.model.side_effect = reply
    result = creative.create_proposals(case.project, case.config, None)
    assert result["creation_review"]["eligible_count"] == 1
    assert len(result["creation_review"]["rounds"]) == passing_round
    assert result["review_automation"]["status"] == "passed"
    creators = [row for row in case.calls if row["role"] == "creator"]
    assert len(creators) == passing_round
    assert creators[-1]["payload"]["previous_proposals"]
    assert creators[-1]["payload"]["independent_feedback"]["reviews"][0]["issues"] == [fixtures.major_issue()]
    before = case.model.call_count
    creative.create_proposals(case.project, case.config, None)
    assert case.model.call_count == before


def goal_project(case):
    return {**case.approved_project(), "story_feedback": "删除豆豆项圈，只保留当晚归还钥匙的闭环。"}


def test_compact_goal_passes_third_review_with_grounded_feedback(case):
    case.reject_brief_reviews = 2
    result = creative.resolve_story_brief(goal_project(case), case.config)
    assert result["resolution_revision"] == 3
    assert result["resolution_review"]["maximum_target_versions"] == 4
    assert [row["revision"] for row in result["resolution_review"]["rounds"]] == [1, 2, 3]
    editors = [row for row in case.calls if row["role"] == "story_editor"]
    critics = [row for row in case.calls if row["role"] == "story_critic"]
    assert len(editors) == len(critics) == 3
    repair = editors[2]["payload"]
    assert repair["current_resolved_target"]["resolution_revision"] == 2
    assert repair["current_resolved_target"]["source_mapping"]["story_feedback"] == "project.story_feedback"
    assert repair["independent_feedback"]["checks"]["causal_chain"]["status"] == "fail"
    assert repair["independent_feedback"]["issues"][0]["evidence"]
    assert repair["current_resolved_target"]["requirements"][0]["source_quote"] == goal_project(case)["story_feedback"]
    lookup = {row["id"]: row["text"] for row in critics[-1]["payload"]["evidence_catalog"]}
    for check in result["resolution_review"]["rounds"][-1]["review"]["checks"].values():
        assert check["evidence_quotes"] == [lookup[ident] for ident in check["evidence_ids"]]
    before = case.model.call_count
    creative.resolve_story_brief(goal_project(case), case.config)
    assert case.model.call_count == before


@pytest.mark.parametrize("limit", [1, 2, 4, 6])
def test_compact_goal_stops_at_shared_limit_and_keeps_failed_evidence(case, limit):
    import json
    from factory import schemas
    case.config["creative"] = {"max_review_rounds": limit}
    case.reject_brief_reviews = 99
    project = goal_project(case)
    with pytest.raises(schemas.CreativeReviewError) as error:
        creative.resolve_story_brief(project, case.config)
    assert "自动返修 %d 次" % (limit - 1) in str(error.value)
    assert error.value.issues[0]["evidence"]
    folder = schemas.planning_dir(project)
    saved = json.loads((folder / "resolved-story-brief.json").read_text())
    assert saved["resolution_revision"] == limit
    assert len(saved["resolution_review"]["rounds"]) == limit
    assert saved["resolution_review"]["maximum_target_versions"] == limit
    assert len(list(folder.glob("resolved-story-brief-v1-r*-*.json"))) == limit
    count = case.model.call_count
    with pytest.raises(schemas.CreativeReviewError):
        creative.resolve_story_brief(project, case.config)
    assert case.model.call_count == count


def test_old_compact_goal_two_round_checkpoint_extends_without_replaying(case):
    from factory import schemas
    case.config["creative"] = {"max_review_rounds": 2}
    case.reject_brief_reviews = 3
    project = goal_project(case)
    with pytest.raises(schemas.CreativeReviewError):
        creative.resolve_story_brief(project, case.config)
    folder = schemas.planning_dir(project)
    snapshots = {path: path.read_bytes() for path in folder.glob("resolved-story-brief-v1-r*-*.json")}
    assert len(snapshots) == 2
    old_count = len(case.calls)
    case.config["creative"]["max_review_rounds"] = 4
    result = creative.resolve_story_brief(project, case.config)
    assert result["resolution_revision"] == 4
    assert [row["revision"] for row in result["resolution_review"]["rounds"]] == [1, 2, 3, 4]
    assert [row["role"] for row in case.calls[old_count:]] == ["story_editor", "story_critic", "story_editor", "story_critic"]
    assert all(path.read_bytes() == value for path, value in snapshots.items())


def test_compact_goal_restart_after_second_review_resumes_next_revision_only(case):
    case.reject_brief_reviews = 2
    class Paused(RuntimeError):
        pass
    def pause(patch):
        state = patch.get("review_automation", {})
        if state.get("phase") == "story_resolution" and state.get("round") == 2 and state.get("status") == "repairing":
            raise Paused()
    project = goal_project(case)
    with pytest.raises(Paused):
        creative.resolve_story_brief(project, case.config, pause)
    count = len(case.calls)
    result = creative.resolve_story_brief(project, case.config)
    assert result["resolution_revision"] == 3
    assert [row["role"] for row in case.calls[count:]] == ["story_editor", "story_critic"]
    assert len(result["resolution_review"]["rounds"]) == 3


def test_compact_goal_changed_policy_reviews_stored_second_target_before_repair(case):
    import json
    from factory import schemas
    case.config["creative"] = {"max_review_rounds": 2}
    case.reject_brief_reviews = 99
    project = goal_project(case)
    with pytest.raises(schemas.CreativeReviewError):
        creative.resolve_story_brief(project, case.config)
    path = schemas.planning_dir(project) / "resolved-story-brief.json"
    saved = json.loads(path.read_text())
    saved["review_policy_signature"] = "old-evidence-policy"
    path.write_text(json.dumps(saved))
    case.config["llm"]["model"] = "new-critic-policy-fixture"
    case.config["creative"]["max_review_rounds"] = 4
    case.reject_brief_reviews = 3
    before = len(case.calls)
    result = creative.resolve_story_brief(project, case.config)
    assert result["resolution_revision"] == 3
    assert [row["role"] for row in case.calls[before:]] == ["story_critic", "story_editor", "story_critic"]
    assert result["resolution_review"]["rounds"][0]["policy_reassessment"] is True
    assert result["resolution_review"]["rounds"][-1]["policy_reassessment"] is False
    assert result["resolution_review"]["previous_policy_record"]


@pytest.mark.parametrize("defect", ["source_quote", "evidence_id"])
def test_third_goal_round_cannot_bypass_source_or_grounded_evidence_contract(case, defect):
    from factory import schemas
    from jsonschema import ValidationError
    case.reject_brief_reviews = 2
    editors = critics = 0
    def reply(**request):
        nonlocal editors, critics
        value = case.reply(**request)
        if request["role"] == "story_editor":
            editors += 1
            if editors == 3 and defect == "source_quote":
                value["requirements"][0]["source_quote"] = "并不存在的用户授权或要求"
        elif request["role"] == "story_critic":
            critics += 1
            if critics == 3 and defect == "evidence_id":
                value["checks"]["causal_chain"]["evidence_ids"] = ["INVENTED_EVIDENCE"]
        return value
    case.model.side_effect = reply
    expected = schemas.CreativeReviewError if defect == "source_quote" else ValidationError
    with pytest.raises(expected):
        creative.resolve_story_brief(goal_project(case), case.config)
    assert editors == 3
    assert critics == (2 if defect == "source_quote" else 3)


@pytest.mark.parametrize("operation", ["write_screenplay", "direct_screenplay", "review_screenplay"])
def test_lower_round_limit_blocks_each_runtime_boundary_without_model_calls(case, operation):
    project = {**case.approved_project(), "creative_round": 3,
        "screenplay_draft": {"title": "第3轮已有稿", "shots": []},
        "previous_screenplay_draft": {"title": "第2轮原稿", "shots": []},
        "creative_review_rounds": [{"round": 1}, {"round": 2}],
        "script_review_complete_round": [1, 2],
        "draft_review": {"issues": [fixtures.major_issue()],
                         "scores": {key: 8 for key in fixtures.schemas.SCORE_KEYS}}}
    before = copy.deepcopy(project)
    case.config["creative"] = {"max_review_rounds": 2}
    result = getattr(creative_runtime, operation)(project, case.config, None)
    assert result["needs_decision"]["issues"] == [fixtures.major_issue()]
    assert result["review_automation"]["status"] == "blocked"
    assert result["review_automation"]["round"] == result["review_automation"]["max_rounds"] == 2
    assert result["review_automation"]["blocked_round"] == 3
    assert project == before
    case.model.assert_not_called()
