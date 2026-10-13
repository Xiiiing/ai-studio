"""Production profile contracts are CPU-only and opt-in for historical snapshots."""
import copy
import csv
import json
from unittest import mock

import pytest
from jsonschema import Draft202012Validator

from factory import schemas, store, creative, production, storyboard
from factory import production_profiles as profiles, profile_creative, profile_references
from studio import core, planning
from profile_fixtures import creative_reply, run_mock_profile
from test_factory_series import series, create_asset


@pytest.mark.parametrize("name,count,total,characters", [("legacy15", 15, 60, 2), ("compact6", 6, 30, 2), ("ensemble10", 10, 50, 3)])
def test_normalized_presets_roundtrip_without_changing_inputs(name, count, total, characters):
    original = copy.deepcopy(profiles.PRESETS)
    value = profiles.normalize(name)
    assert value["shot_count"] == len(value["shot_seconds"]) == count
    assert sum(value["shot_seconds"]) == value["duration_seconds"] == total
    assert len(value["characters"]) == characters
    assert profiles.normalize(value) == value and profiles.PRESETS == original


@pytest.mark.parametrize("changes", [
    {"shot_count": True}, {"schema_version": True}, {"duration_seconds": float('nan')},
    {"duration_seconds": 1000}, {"shot_seconds": [4] * 5}, {"shot_seconds": [4] * 6},
    {"shot_seconds": [3.01, 5, 6, 4, 5, 6]}, {"characters": []},
    {"characters": [{"character_id": "a", "voice_id": "female"}] * 2},
    {"characters": [{"character_id": "a"}]}, {"characters": [{"character_id": "../a", "voice_id": "female"}]},
    {"characters": [{"character_id": "a", "voice_id": "female", "identity": "not a voice parameter"}]},
    {"model": "unapproved"}, {"preset": "unbounded"}])
def test_invalid_profiles_fail_early(changes):
    with pytest.raises(ValueError):
        profiles.normalize({"preset": "compact6", **changes})


def test_unequal_durations_and_stable_voice_ids_are_explicit():
    characters = [{"character_id": "person_A", "voice_id": "deep_voice"}, {"character_id": "person_B", "voice_id": "soft_voice"}]
    p = {"production_profile": profiles.normalize({"preset": "compact6", "shot_seconds": [3, 4, 5, 6, 5, 7], "characters": characters})}
    assert schemas.duration_plan(p) == [3, 4, 5, 6, 5, 7]
    registered = {"deep_voice": {"speaker": "Serena"}, "soft_voice": {"speaker": "Uncle_Fu"}}
    voices = profiles.voices_for(p, registered)
    assert voices["person_A"]["speaker"] == "Serena" and voices["person_B"]["speaker"] == "Uncle_Fu"
    with pytest.raises(ValueError, match="尚未登记"):
        profiles.voices_for(p, {"female": "Serena", "male": "Uncle_Fu"})
    assert characters[0].get("name") is None


def test_schema_system_and_director_share_identical_role_contract():
    project = {"production_profile": profiles.normalize("ensemble10")}
    schema = schemas.episode_schema(project=project)
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["shots"]["minItems"] == 10
    assert list(schema["properties"]["cast"]["properties"]) == profiles.roles(project)
    assert profile_creative.adapt_schema(schema, project) == schema
    system = profile_creative.adapt_system(creative.SCRIPT_SYSTEM, project)
    assert "恰好15镜" not in system and "female和male" not in system
    assert "char_witness" in system and "恰好10镜" in system
    assert profile_creative.adapt_system(system, project) == system
    direction = storyboard.storyboard_schema(["S01"], project)
    assert "char_witness" in direction["properties"]["shots"]["properties"]["S01"]["properties"]["reference_group"]["enum"]


def test_old_projects_keep_exact_legacy_schema_and_duration_plan():
    project = {"duration_seconds": 75}
    assert schemas.duration_plan(project) == [5] * 15
    assert schemas.duration_plan({"duration_seconds": "61"}) == [5] + [4] * 14
    assert profile_creative.adapt_schema(schemas.PROPOSALS_SCHEMA, project) is schemas.PROPOSALS_SCHEMA
    assert profile_creative.adapt_system(creative.SCRIPT_SYSTEM, project) == creative.SCRIPT_SYSTEM
    assert "production_profile" not in schemas.project_context(project)


def test_model_duration_and_dialogue_occupancy_cannot_exceed_specification():
    p = {"production_profile": profiles.normalize("compact6")}
    episode = {"cast": {role: "Fictional adult" for role in profiles.roles(p)}, "shots": [
        {"id": "S%02d" % (i + 1), "seconds": value, "characters": ["char_lead"], "dialogue": []}
        for i, value in enumerate(schemas.duration_plan(p))]}
    profiles.validate_episode(episode, p, {"provider": "minimax", "duration": 6})
    with pytest.raises(ValueError, match="云视频模型"):
        profiles.validate_episode(episode, p, {"provider": "minimax", "duration": 5})
    episode["shots"][0]["dialogue"] = [{"role": "char_lead", "text": "这是明显超出镜头声音槽位的长对白" * 3, "start": .2}]
    with pytest.raises(ValueError, match="对白预估"):
        profiles.validate_episode(episode, p)


def test_profile_changes_participate_in_creative_and_media_signatures(tmp_path, monkeypatch):
    from test_factory_creative import fixture
    project = {"id": "a123456789ab", "slug": "factory-a123456789ab", "production_profile": profiles.normalize("compact6")}
    monkeypatch.setattr(core, "ROOT", tmp_path); monkeypatch.setattr(production, "ROOT", tmp_path)
    raw = fixture(schemas.episode_schema(project=project))
    for shot in raw["shots"]:
        shot["characters"] = ["char_lead"]
    project["episode"] = creative._canonical_episode(raw, project)
    run = production._Run(project, {"semantic_enabled": False}, None, None)
    def target(current):
        return current._target("S01", current.folder / "assets/S01", current.shots[0]["prompt"], shot=current.shots[0])
    before = target(run)["signature"]
    changed = copy.deepcopy(project)
    changed["production_profile"]["characters"][0]["voice_id"] = "narrator"
    assert target(production._Run(changed, {"semantic_enabled": False}, None, None))["signature"] != before
    assert schemas.project_context(project)["production_profile"] == profiles.resolve(project)


def test_profile_creation_and_fixed_reference_binding_validate_approved_identity(series):
    value = create_asset(series["id"])
    character = {"character_id": value["entity_id"], "voice_id": "female", "reference_asset": {"asset_id": value["asset_id"], "version": value["version"]}}
    p = store.create({"brief": "新剧集复用已批准人物与声线", "series_key": series["series_key"],
                      "production_profile": {"preset": "compact6", "characters": [character]}})
    assert p["duration_seconds"] == 30
    episode = {"cast": {value["entity_id"]: "A fictional adult"}, "shots": [{"id": "S01", "characters": [value["entity_id"]]}, {"id": "S02", "characters": []}]}
    p = store.update(p["id"], {"episode": episode})
    assert p["series_references"]["S01"][0]["asset_id"] == value["asset_id"]
    assert p["series_references"]["S02"] == []
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM series_dependencies WHERE project_id=?", (p["id"],)).fetchone()[0] == 1
    with pytest.raises(ValueError, match="同一系列"):
        store.create({"brief": "不能越过系列身份边界的引用", "production_profile": {"preset": "compact6", "characters": [character]}})


def test_profile_http_catalog_and_creation_fail_before_any_generation(series):
    import http.client
    import threading
    from http.server import ThreadingHTTPServer
    from factory import web
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(path, payload=None):
        client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        client.request("GET" if payload is None else "POST", "/api/factory/" + path,
                       body=json.dumps(payload) if payload is not None else None,
                       headers={"Content-Type": "application/json"})
        response = client.getresponse(); value = response.status, json.loads(response.read()); client.close()
        return value
    try:
        code, catalog = request("production-profiles")
        assert code == 200 and catalog == profiles.catalog()
        code, value = request("projects", {"brief": "显式新规格的离线创建验证", "production_profile": "compact6"})
        assert code == 202 and value["project"]["production_profile"] == profiles.normalize("compact6")
        assert value["project"]["status"] == "queued" and value["project"]["shots"] == []
        assert request("projects", {"brief": "无效的时长配置必须被拒绝", "duration_seconds": 1000, "production_profile": "compact6"})[0] == 409
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


@pytest.mark.parametrize("preset", ["compact6", "ensemble10", "legacy15"])
def test_mock_end_to_end_profile_preserves_voice_cache_timeline_and_export(preset):
    summary, project, calls = run_mock_profile(preset)
    expected = profiles.normalize(preset)
    assert summary["shots"] == expected["shot_count"]
    assert summary["duration_seconds"] == expected["duration_seconds"]
    assert summary["technical_qa"]["subtitled"]["status"] == "pass"
    assert summary["paid_api_calls"] == 0 and summary["artistic_quality_passed"] is False
    assert project["delivery"]["production_profile"] == expected
    rows = list(planning.storyboard_rows(project["episode"]))
    assert len(rows) == expected["shot_count"]
    assert {row["character_id"] for row in rows} == set(profiles.roles(project))
    assert {row["voice_id"] for row in rows} <= {"female", "male", "narrator"}
    assert float(rows[-1]["timeline_end"]) == expected["duration_seconds"]
    writer = next(call for call in calls if call["role"] == "screenwriter")
    assert writer["schema"]["properties"]["shots"]["minItems"] == expected["shot_count"]
    assert all(call["payload"].get("project", {}).get("production_profile") == expected for call in calls if "project" in call["payload"])
