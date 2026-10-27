"""Actual production-input/cache integration, without network or paid inference."""
import base64
import copy

import pytest

from factory import store, production, schemas, material_library as library, material_production as integration
from factory.shot_workbench import plan_signature
from factory.scheduler import ProductionNeedsDecision
from studio import core
from test_factory_material_library import project, material, picture, select, confirm


def bundle(project, values):
    library.mutate_project(project["id"], {"action": "select", "expected_revision": store.get(project["id"])["revision"],
        "references": [{"asset_id": value["asset_id"], "version": value["version"], "role": role, **extra} for value, role, extra in values]})
    return confirm(project)


def episode():
    return {"cast": {"female": "Adult woman with short hair", "male": "Adult man with long hair"}, "visual_style": "watercolor",
            "shots": [{"id": "S01", "seconds": 4, "prompt": "An adult woman waits on a station platform", "motion_prompt": "She turns gently", "characters": ["female"], "dialogue": [], "reference_group": "female",
                       "continuity_state": {"mode": "continuous", "opening_state": "The woman waits on the platform.", "ending_state": "The woman has turned gently.", "allowed_changes": ["The approved turn."]}},
                      {"id": "S02", "seconds": 4, "prompt": "An adult man reads in a room", "motion_prompt": "He turns a page", "characters": ["male"], "dialogue": [], "reference_group": "male",
                       "continuity_state": {"mode": "scene_cut", "opening_state": "The man reads in a room.", "ending_state": "The same man has turned a page.", "allowed_changes": ["Cut from the platform to the man's room."]}},
                      {"id": "S03", "seconds": 4, "prompt": "He reaches the same platform", "motion_prompt": "He stops", "characters": ["male"], "dialogue": [], "reference_group": "male", "reference_shots": ["S01"],
                       "continuity_state": {"mode": "time_skip", "opening_state": "The same man reaches the platform after leaving his room.", "ending_state": "He has stopped on the platform.", "allowed_changes": ["Omit the journey from the room to the platform."]}}]}


def run(project, monkeypatch, evaluator=None):
    monkeypatch.setattr(production, "ROOT", core.ROOT)
    monkeypatch.setattr(schemas, "ROOT", core.ROOT)
    return production._Run(project, {"max_attempts": 1, "max_wall_seconds": 120, "max_gpu_seconds": 120}, lambda patch: None, None, evaluator)


def test_confirmed_character_images_enter_render_inputs_including_silent_roles(project, monkeypatch):
    female, male = material(name="林夏"), material(name="张明")
    project = bundle(project, [(female, "female", {}), (male, "male", {})])
    project = store.update(project["id"], {"episode": episode(), "script": episode(), "status": "paused"})
    engine = run(project, monkeypatch)
    captured = []
    def render(targets, phase):
        if not targets:
            return
        assert phase == "story_frames"  # Confirmed identities do not get regenerated.
        for target in targets:
            captured.append(target)
            path = target["folder"] / "test.png"; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(picture()))
            target["shot"]["image"] = core.media_receipt(path)
    monkeypatch.setattr(engine, "_render_targets", render)
    engine._characters_and_images()
    first = next(target for target in captured if target["key"] == "S01")
    assert first["references"][0] == library.image_path(female["data"]["images"][0])
    assert first["asset_references"][0]["asset_id"] == female["asset_id"]
    assert first["shot"]["material_constraints"][0]["document"]["name"] == "林夏"
    assert "confirmed_materials" in first["prompt"]


def test_material_update_changes_only_dependent_shot_cache_and_reports_transitive_impact(project):
    value = material()
    project = bundle(project, [(value, "female", {})])
    project = store.update(project["id"], {"episode": episode(), "script": episode(), "status": "paused", "selected_story": "A", "script_review": {"passed": True}})
    before = {shot["id"]: plan_signature(project, shot, "image") for shot in episode()["shots"]}
    revised = library.mutate({"action": "revise", "asset_id": value["asset_id"], "expected_version": value["version"], "document": {"name": "林夏新造型", "description": "相同身份，新的默认外套"}})
    with store.connection() as db:
        assert library.impact(db, value["asset_id"], revised["version"])[0]["shot_ids"] == ["S01", "S03"]
    project = bundle(project, [(revised, "female", {})])
    after = {shot["id"]: plan_signature(project, shot, "image") for shot in episode()["shots"]}
    assert before["S01"] != after["S01"] and before["S02"] == after["S02"]
    assert project["production_edits"][-1]["shot_ids"] == ["S01", "S03"]


def test_scene_and_composition_only_join_their_selected_shot(project, monkeypatch):
    female, scene = material(), material(kind="scene", name="站台")
    doc = {"name": "林夏在站台", "members": [{"asset_id": female["asset_id"], "version": female["version"], "role": "female", "box": [.1, .1, .6, .9]}], "scene": {"asset_id": scene["asset_id"], "version": scene["version"]}}
    composition = library.mutate({"action": "create", "kind": "composition", "document": doc})
    composition = library.mutate({"action": "upload", "asset_id": composition["asset_id"], "expected_version": composition["version"], "image_base64": picture()})
    project = bundle(project, [(female, "female", {}), (scene, scene["asset_id"], {"shot_ids": ["S01"]}), (composition, composition["asset_id"], {"shot_ids": ["S01"]})])
    project = store.update(project["id"], {"episode": episode(), "script": episode(), "status": "paused"})
    engine = run(project, monkeypatch)
    refs, prompt, receipts = engine._material_shot_inputs(engine.shots[0], ["female"], [library.image_path(female["data"]["images"][0])])
    assert refs[0] == library.image_path(composition["data"]["images"][0])
    assert {r["kind"] for r in receipts} == {"character", "scene", "composition"}
    assert "normalized image region" in prompt
    assert not library.shot_references(project, engine.shots[1])


def test_existing_style_is_reused_without_generating_three_previews(project, monkeypatch):
    style = material(kind="style", name="水彩画风")
    project = bundle(project, [(style, style["asset_id"], {})])
    project = store.update(project["id"], {"episode": episode(), "script": episode(), "styles": integration.styles(project)})
    engine = run(project, monkeypatch)
    monkeypatch.setattr(engine, "_render_targets", lambda *args: pytest.fail("must reuse confirmed style pixels"))
    styles = engine.previews()
    assert len(styles) == 1 and styles[0]["preview"]["sha256"] == style["data"]["images"][0]["sha256"]
    engine.selected_style = styles[0]
    assert engine._approved_preview_reference() == (None, None)
    assert schemas.project_context(project)["confirmed_materials"][0]["document"]["name"] == "水彩画风"


def test_reference_limit_fails_before_any_paid_submission(project, monkeypatch):
    engine = run({**project, "episode": episode()}, monkeypatch)
    with pytest.raises(ProductionNeedsDecision, match="分阶段"):
        engine._check_material_reference_limit([core.ROOT / "dummy"] * 5)


def test_material_snapshot_tampering_is_not_a_valid_approved_reference(project):
    value = material(); project = bundle(project, [(value, "female", {})])
    project["material_package"]["references"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="不匹配"):
        integration.resolved(project)


def test_unknown_cloud_tasks_block_reference_switch(project):
    value = material(); project = bundle(project, [(value, "female", {})])
    project = store.update(project["id"], {"episode": episode(), "script": episode(), "status": "paused"})
    revised = library.mutate({"action": "revise", "asset_id": value["asset_id"], "expected_version": value["version"], "document": {"name": "新版"}})
    with store.connection() as db:
        db.execute("INSERT INTO cloud_tasks(intent_id,project_id,shot_id,attempt,take,provider,model,request_signature,status,currency,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("unknown-intent", project["id"], "S03", 1, "take_001", "minimax", "mock", "signature", "unresolved", "CNY", 0, 0))
    select(project, revised)
    with pytest.raises(ValueError, match="云任务"):
        confirm(project)


@pytest.mark.parametrize("verdict,expected", [("mismatch", "fail"), ("uncertain", "unavailable"), ("match", "pass")])
def test_visual_review_has_separate_material_checks_and_keeps_all_references(project, monkeypatch, verdict, expected):
    from factory import llm
    value = material()
    path = library.image_path(value["data"]["images"][0])
    captured = {}
    def request(*args, **kwargs):
        captured["schema"] = args[4]; captured["content"] = kwargs["content"]
        issues = []
        if verdict == 'mismatch':
            issues = [{'code': 'off_prompt', 'severity': 'blocking',
                'detail': 'The candidate uses a different medium from the confirmed material.',
                'nature': 'OBJECTIVE', 'location': 'candidate:0 at 0 seconds',
                'impact': 'The explicitly approved rendering medium is absent.',
                'repair_target': 'Restore the confirmed medium while retaining the approved character.',
                'evidence_refs': ['candidate:0', 'reference:0'], 'requires_choice': False}]
        return {"status": "pass", "score": 95, "summary": "fixture verdict", "issues": issues,
                "material_alignment": {"identity": "match", "style": verdict, "scene": "not_applicable", "correspondence": "match", "evidence": "visible fixture evidence"}}
    monkeypatch.setattr(llm, "_request", request)
    result = llm.review_media(project, {"id": "S01", "material_constraints": [{"kind": "character", "role": "female"}]}, path, [path] * 5,
                              {"llm": {"model": "fixture-vision"}, "quality": {"semantic_enabled": True}})
    assert "material_alignment" in captured["schema"]["required"]
    assert sum(item["type"] == "image_url" for item in captured["content"]) == 6
    assert result["reference_count"] == 5 and result["status"] == expected


def test_real_render_cache_reuses_unrelated_shots_after_material_switch(project, monkeypatch):
    from contextlib import contextmanager
    from factory import model_capabilities
    from test_factory_series import pattern
    import json
    value = material(); project = bundle(project, [(value, "female", {})])
    project = store.update(project["id"], {"episode": episode(), "script": episode(), "status": "paused"})
    monkeypatch.setattr(production, "ROOT", core.ROOT)
    calls = []
    class Renderer:
        def copy_input(self, path): return str(path)
        def execute(self, graph, folder, kind, **kwargs):
            calls.append(str(folder))
            receipt = pattern(folder / "image.png", json.dumps(graph, sort_keys=True))
            core.write_json(folder / "result.json", receipt)
            return folder / "image.png"
    @contextmanager
    def worker(*args, **kwargs): yield Renderer()
    monkeypatch.setattr(production.Scheduler, "worker", worker)
    settings = {"image_width": 128, "image_height": 128, "semantic_enabled": False}
    # Bind this synthetic FLUX renderer without bypassing the frozen capability
    # checks that precede every real target submission.
    model_capabilities.adopt(project, settings)
    project = store.update(project["id"], {"model_capability_snapshot": project["model_capability_snapshot"],
                                          "model_capability_history": project.get("model_capability_history", [])})
    assert model_capabilities.active(project, settings, "image")["enabled"]
    def render():
        engine = production._Run(store.get(project["id"]), settings, lambda patch: store.update(project["id"], patch), None)
        engine._characters_and_images()
        return {shot["id"]: shot["image"] for shot in engine.shots}
    before = render(); count = len(calls)
    assert render() == before and len(calls) == count
    newer = library.mutate({"action": "revise", "asset_id": value["asset_id"], "expected_version": value["version"], "document": {"name": "林夏", "description": "固定身份，新的灰色外套"}})
    bundle(store.get(project["id"]), [(newer, "female", {})])
    after = render()
    assert after["S02"] == before["S02"] and len(calls) == count + 2
    assert after["S01"]["asset_references"][0]["version"] == newer["version"]
    assert after["S03"]["source_signature"] != before["S03"]["source_signature"]
    assert (core.ROOT / before["S01"]["path"]).is_file()


def test_storyboard_selects_only_confirmed_scene_and_composition_ids(project):
    from factory.storyboard import storyboard_schema, _episode_context
    scene = material(kind="scene", name="站台")
    project = bundle(project, [(scene, "station", {})])
    fields = storyboard_schema(["S01"], project)["properties"]["shots"]["properties"]["S01"]
    assert fields["properties"]["scene_id"]["enum"] == ["", "station"]
    assert "scene_id" in fields["required"]
    story = episode(); story["shots"][0]["scene_id"] = "station"
    assert _episode_context(story)["shots"][0]["scene_id"] == "station"


def test_confirmed_library_style_uses_the_existing_gate_without_asking_for_a_second_choice(project, monkeypatch):
    from factory import workflows, supervision
    style = material(kind="style", name="水彩")
    project = bundle(project, [(style, style["asset_id"], {})])
    project = store.update(project["id"], {"episode": episode(), "script": episode(), "styles": integration.styles(project),
        "script_complete_generation": 1, "style_previews_generation": 1, "script_review": {"passed": True}, "status": "visual_development"})
    monkeypatch.setattr(supervision, "supervise_gate", lambda *args: pytest.fail("material confirmation already chooses this style"))
    result = workflows.style_decision({"project_id": project["id"]})
    assert result["style_answer"]["actor"] == "producer_material_confirmation"
    assert result["style_answer"]["choice"] == project["styles"][0]["id"]
