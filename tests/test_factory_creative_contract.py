"""Offline creative intent, compatibility and role-cache boundaries."""
import copy

import pytest

from factory import creative, llm, schemas, storyboard, store, supervision
from factory.creative_contract import instructions, normalize, resolve, style_prompt
from factory.profile_creative import adapt_system
from factory.production_profiles import normalize as normalize_profile
from factory.prompt_registry import load_prompt
from tests.test_factory_creative import fixture


def contract(style="anime_2d", motion="generated_video"):
    return {"schema_version": 1, "visual_style": style, "story_mode": "standalone",
            "pacing": "short_drama", "motion": motion}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path / "runtime")
    monkeypatch.setattr(store, "DATABASE", tmp_path / "runtime/studio.sqlite3")
    monkeypatch.setattr(schemas, "ROOT", tmp_path)
    store.initialize()
    def forbidden(*args, **kwargs):
        raise AssertionError("Creative intent tests must not request a real model or GPU")
    monkeypatch.setattr("socket.getaddrinfo", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr(llm, "json_call", forbidden)


@pytest.mark.parametrize("mode,style,motion", [
    ("animation", "anime_2d", "generated_video"),
    ("animation", "stylized_3d", "generated_video"),
    ("live_action", "photorealistic", "generated_video"),
    ("motion_comic", "illustrated", "still_camera"),
])
def test_all_media_create_and_roundtrip_without_mutating_input(mode, style, motion):
    supplied = contract(style, motion)
    payload = {"brief": "主角在同一天完成复仇与自救，故事独立闭环", "format": mode,
               "creative_contract": supplied}
    original = copy.deepcopy(payload)
    project = store.create(payload)
    assert resolve(project) == supplied == store.get(project["id"])["creative_contract"]
    assert normalize(supplied, mode) is not supplied
    assert payload == original
    assert schemas.project_context(project)["creative_contract"] == supplied


@pytest.mark.parametrize("value", [
    None, [], {}, {**contract(), "unexpected": True},
    {k: v for k, v in contract().items() if k != "motion"},
    {**contract(), "schema_version": True}, {**contract(), "schema_version": 1.0},
    {**contract(), "schema_version": 2}, {**contract(), "story_mode": "serial"},
    {**contract(), "pacing": "slow_cinema"}, {**contract(), "motion": "still_camera"},
    {**contract(), "visual_style": "photorealistic"}, {**contract(), "visual_style": []},
])
def test_invalid_or_mismatched_contract_rejected_before_enqueue(value):
    with pytest.raises(ValueError):
        store.create({"brief": "完整的独立动漫短剧故事", "format": "animation", "creative_contract": value})
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM projects").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_absent_contract_preserves_historical_prompts_context_and_capabilities():
    project = store.create({"brief": "现有 API 不需要新增创作契约字段"})
    assert "creative_contract" not in project
    assert resolve(project) is None and instructions(project) == style_prompt(project) == ""
    assert "creative_contract" not in schemas.project_context(project)
    assert adapt_system(creative.SCRIPT_SYSTEM, project) is creative.SCRIPT_SYSTEM
    capabilities = storyboard.model_capabilities(project, {}, {})
    assert "creative_contract" not in capabilities and "continuous_character_action_required" not in capabilities


@pytest.mark.parametrize("profile", [False, True])
def test_all_registered_creative_roles_receive_binding_guidance_exactly_once(profile):
    project = {"format": "animation", "creative_contract": contract()}
    if profile:
        project["production_profile"] = normalize_profile("compact6")
    for role in ("creator", "independent_critic", "screenwriter", "visual_designer",
                 "storyboard_director", "executive_producer"):
        original = load_prompt(role)
        rendered = adapt_system(original, project)
        twice = adapt_system(rendered, project)
        assert str(twice) == str(rendered) and twice.transforms == rendered.transforms
        assert rendered.count("CREATIVE_CONTRACT_JSON:") == 1
        assert rendered.spec == original.spec and "creative_contract" in rendered.transforms
        assert "self-contained short drama" in rendered and "No episode setup" in rendered
        assert "Camera movement alone is insufficient" in rendered
        assert "No photorealistic live-action appearance" in rendered
        assert "visible mouths stay closed" in rendered


def test_contract_change_invalidates_role_cache_but_legacy_cache_remains_reusable(monkeypatch):
    project = {"id": "a123456789ab", "slug": "factory-a123456789ab", "format": "animation"}
    calls = []
    def reply(**request):
        calls.append(request)
        return {"result": "valid"}
    monkeypatch.setattr(llm, "json_call", reply)
    schema = schemas.object_schema({"result": schemas.text_schema(1, 20)})
    def call(current):
        return schemas.cached_json_call(current, {"llm": {"model": "fixture"}},
            "screenwriter", "contract-cache", "Fixed historical rules", {}, schema)
    call(project)
    call(project)
    assert len(calls) == 1
    anime = {**project, "creative_contract": contract()}
    call(anime)
    call(anime)
    call({**project, "creative_contract": contract("stylized_3d")})
    call(project)
    assert len(calls) == 3
    assert "CREATIVE_CONTRACT_JSON" not in calls[0]["system"]
    assert "2D anime animation" in calls[1]["system"] and "Stylized 3D" in calls[2]["system"]


def test_canonical_approved_episode_and_director_keep_the_same_contract():
    project = {"id": "a123456789ab", "slug": "factory-a123456789ab", "format": "animation",
               "duration_seconds": 60, "creative_contract": contract()}
    episode = creative._canonical_episode(fixture(schemas.episode_schema()), project)
    assert episode["creative_contract"] == project["creative_contract"]
    assert storyboard._episode_context(episode)["creative_contract"] == project["creative_contract"]
    capabilities = storyboard.model_capabilities(project, {}, episode)
    assert capabilities["continuous_character_action_required"] is True
    assert capabilities["synchronized_lip_motion"] is False
    assert "2D anime animation" in capabilities["visual_constraints"]
    assert all(shot["lip_sync"] is False for shot in episode["shots"])
    assert all("bodies and closed mouths still" not in shot["motion_prompt"] for shot in episode["shots"])


def test_supervisor_selects_with_explicit_contract_and_no_new_authority():
    project = store.create({"brief": "一个短剧故事在最终选择后完整闭环", "format": "animation",
                            "creative_contract": contract()})
    store.update(project["id"], {"proposals": [{"id": "A", "eligible": True, "ending": "核心冲突解决"}],
        "creation_review": {"passed": True}, "proposals_complete_generation": 1})
    gate = store.gate(project["id"], "story", [{"id": "A"}], "选择故事", 1)
    context = supervision.context_for(store.get(project["id"]), gate)
    assert context["project"]["creative_contract"] == contract()
    assert "approve_final" in context["constraints"]["forbidden"]


def test_illustration_keeps_honest_still_camera_contract():
    project = {"format": "motion_comic", "creative_contract": contract("illustrated", "still_camera")}
    assert "only the programmatic camera moves" in instructions(project)
    capabilities = storyboard.model_capabilities(project, {}, {})
    assert capabilities["continuous_character_action_required"] is False
    assert capabilities["video_profile"] is None
