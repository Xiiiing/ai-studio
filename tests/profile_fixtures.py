"""Shared deterministic short-film fixtures; never a production model adapter."""
from __future__ import annotations

import copy
import shutil
import uuid
from pathlib import Path
from unittest import mock

from factory import creative, llm, schemas, production, production_profiles as profiles
from studio import core
from test_factory_creative import fixture
import test_factory_production as media


def creative_reply(calls):
    def reply(**request):
        calls.append(copy.deepcopy(request))
        schema, payload = request["schema"], request["payload"]
        value = fixture(schema)
        properties = schema["properties"]
        project = payload.get("project", {})
        ids = profiles.roles(project) if project else ["female", "male"]
        if "proposals" in properties:
            for index, proposal in enumerate(value["proposals"]):
                proposal["title"] = "模拟短剧" + str(index)
                for role, character in zip(ids, proposal["characters"]):
                    character["role"] = role
        elif "reviews" in properties:
            for index, review in enumerate(value["reviews"]):
                review["proposal_id"] = "ABC"[index]
        elif "causal_beats" in properties:
            for role, character in zip(ids, value["characters"]):
                character["role"] = role
            instruction = payload["producer_feedback"]["instructions"][0]
            content = instruction["content"]
            value["requirements"] = [{"instruction": "依据出品人要求完成闭合故事", "source_id": instruction["source"], "source_quote": str(content)[:100]}]
            value["excluded_elements"] = []
        elif "checks" in properties:
            evidence = payload["evidence_catalog"][0]["id"]
            for check in list(value["checks"].values()) + list(value["requirements_review"].values()):
                check.update(status="pass", evidence_ids=[evidence])
            value["originality"]["evidence_ids"] = [evidence]
            value["status"] = "pass"
        elif "shots" in properties and isinstance(value["shots"], list):
            ids = list(value["cast"])
            for index, shot in enumerate(value["shots"]):
                role = ids[index % len(ids)]
                shot["reference_group"] = role
                if "characters" in shot:
                    shot["characters"] = [role]
                shot["dialogue"] = [{"role": role, "text": "请看这里", "start": .2, "emotion": "平静"}]
        elif "shots" in properties and isinstance(value["shots"], dict):
            for sid, shot in value["shots"].items():
                original = next(s for s in payload["current_episode"]["shots"] if s["id"] == sid)
                shot["reference_group"] = original["reference_group"]
            value["blocked_shots"] = {}
        return value
    return reply


def run_mock_profile(preset="compact6", destination=None):
    """Actual CPU media export with explicit synthetic images and sine-wave speech."""
    case = media.FactorySafetyTests(methodName="runTest")
    case.setUp()
    calls = []
    try:
        profile = profiles.normalize(preset)
        project = case.project
        case.ident = uuid.uuid4().hex[:12]
        case.folder = core.ROOT / "production" / ("factory-" + case.ident)
        project.update(id=case.ident, slug="factory-" + case.ident)
        project.update(production_profile=profile, duration_seconds=profile["duration_seconds"],
                       title="规格模拟样片", brief="成年人物在车站归还一本书并解释误会的原创短片", creation_mode="original")
        project.pop("episode", None)
        voices = core.read_json(core.ROOT / "audio/voices.json")
        voices["narrator"] = copy.deepcopy(voices["male"])
        core.write_json(core.ROOT / "audio/voices.json", voices)
        with mock.patch.object(schemas, "ROOT", core.ROOT), mock.patch.object(llm, "json_call", side_effect=creative_reply(calls)):
            config = {"llm": {"model": "mock-only-no-inference", "base_url": "http://mock.invalid"},
                      "creative": {"separate_storyboard": True}}
            project.update(creative.create_proposals(project, config, case.save))
            project["selected_story"] = "A"
            project.update(creative.create_script_and_styles(project, config, case.save))
        if not project["script_review"]["passed"]:
            raise AssertionError("Mock creative pipeline failed its structural checks")
        with mock.patch.object(production.Scheduler, "audio_lease", media.fake_audio_lease), \
                mock.patch("factory.speech.subprocess.run", side_effect=media.FixtureSpeech.invoke), \
                mock.patch.object(production.Scheduler, "worker", media.fake_worker):
            production.generate_style_previews(project, case.config, case.save)
            project["selected_style"] = project["styles"][0]["id"]
            delivery = production.generate_project(project, case.config, case.save)
            project["delivery"] = delivery
            image_count, voice_count = len(media.FixtureWorker.calls), len(media.FixtureSpeech.calls)
            resumed = production.generate_project(project, case.config, case.save)
            if resumed["source_signature"] != delivery["source_signature"] or len(media.FixtureWorker.calls) != image_count or len(media.FixtureSpeech.calls) != voice_count:
                raise AssertionError("Resumed mock profile did not reuse its existing media")
        summary = {"preset": preset, "shots": len(project["shots"]), "duration_seconds": delivery["duration_seconds"],
                   "characters": profiles.roles(project), "technical_qa": delivery["technical_qa"],
                   "production_profile_signature": delivery["production_profile_signature"],
                   "model_calls": len(calls), "image_fixture_calls": image_count, "speech_fixture_calls": voice_count,
                   "fixture": "synthetic_images_and_sine_wave_audio_not_model_inference",
                   "paid_api_calls": 0, "artistic_quality_passed": False}
        if destination is not None:
            destination = Path(destination)
            destination.mkdir(parents=True, exist_ok=False)
            shutil.copytree(case.folder, destination / "production" / project["slug"])
            core.write_json(destination / "project.json", project)
            core.write_json(destination / "summary.json", summary)
        return summary, project, calls
    finally:
        case.tearDown()
        case.doCleanups()
