"""Series identity and exact reference versions; no GPU or paid service."""
import copy
import hashlib
import http.client
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

import pytest
from PIL import Image

from factory import store, series_bible as bible, series_assets as assets, series_bindings as bindings
from factory import production, schemas, workflows as wf, web
from factory.series_production import character_states
from studio import core


def pattern(path, variant=0, size=(128, 128)):
    digest = hashlib.sha256(str(variant).encode()).digest()
    pixels = bytes((x * 17 + y * 11 + digest[(x + y + c) % len(digest)]) % 256
                   for y in range(size[1]) for x in range(size[0]) for c in range(3))
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.frombytes("RGB", size, pixels).save(path)
    return core.media_receipt(path)


def document():
    return {"schema_version": 1, "world": "虚构车站，角色均为成年人。", "visual_rules": "朴素电影光照。",
            "characters": [{"character_id": "char_mei", "name": "梅", "identity_constraints": "固定成年面孔、短发。", "default_wardrobe": "灰色外套"},
                           {"character_id": "char_lin", "name": "林", "identity_constraints": "成年长发人物。", "default_wardrobe": "白色外套"}],
            "scenes": [{"entity_id": "station", "name": "站台", "description": "蓝色长椅与单个出口。"}],
            "props": [{"entity_id": "ticket", "name": "车票", "description": "一张有编号的纸质车票。"}]}


def episode():
    return {"cast": {"female": "Adult woman with short hair", "male": "Adult man with long hair"},
            "shots": [{"id": "S01", "seconds": 4, "prompt": "She waits", "reference_group": "female",
                       "continuity_state": {"mode": "continuous", "opening_state": "The woman waits at the station.", "ending_state": "She remains at the station.", "allowed_changes": []}},
                      {"id": "S02", "seconds": 4, "prompt": "He reads", "reference_group": "male",
                       "continuity_state": {"mode": "scene_cut", "opening_state": "The man reads in his room.", "ending_state": "The same man continues reading.", "allowed_changes": ["Cut from the station to the man's room."]}},
                      {"id": "S03", "seconds": 4, "prompt": "He reaches the same station", "reference_group": "male", "reference_shots": ["S01"],
                       "continuity_state": {"mode": "time_skip", "opening_state": "The same man arrives at the station.", "ending_state": "He remains at the station.", "allowed_changes": ["Omit the journey from his room to the station."]}}]}


@pytest.fixture
def series(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setattr(production, "ROOT", tmp_path)
    monkeypatch.setattr(schemas, "ROOT", tmp_path)
    monkeypatch.setattr(store, "DATA", tmp_path / "runtime")
    monkeypatch.setattr(store, "DATABASE", tmp_path / "runtime/studio.sqlite3")
    monkeypatch.setattr(wf, "ROOT", tmp_path)
    monkeypatch.setattr(wf, "DATA", tmp_path / "runtime")
    monkeypatch.setattr(wf, "CHECKPOINTS", tmp_path / "runtime/checkpoints.sqlite3")
    store.initialize()
    p = store.create({"brief": "一个成年人在车站重逢的原创故事", "series_key": "station-series"})
    bible.save_bible(p["id"], {"expected_revision": p["revision"], "expected_version": 0, "document": document()})
    p = initialize_project(p["id"])
    return p


def initialize_project(pid):
    p = store.get(pid)
    folder = core.ROOT / "production" / p["slug"]
    core.write_json(folder / "factory_owner.json", {"project_id": p["id"]})
    media = pattern(folder / "source/reference.png")
    shots = copy.deepcopy(episode()["shots"])
    shots[0]["image"] = media
    return store.update(pid, {"status": "paused", "episode": episode(), "script": episode(), "shots": shots,
                              "script_review": {"passed": True}, "selected_story": "story-A"})


def mutation(pid, action, **fields):
    return bible.mutate(pid, {"action": action, "expected_revision": store.get(pid)["revision"], **fields})


def create_asset(pid, *, kind="character", entity_id="char_mei", asset_id=None, expected_version=0, approve=True):
    value = mutation(pid, "plan", kind=kind, entity_id=entity_id, expected_version=expected_version,
                     **({"asset_id": asset_id} if asset_id else {}))
    for action, fields in (("register", {"source_key": "shot:S01"}), ("review", {}), ("approve", {"confirmed": True})):
        if action == "approve" and not approve:
            break
        value = mutation(pid, action, asset_id=value["asset_id"], version=value["version"],
                         expected_asset_revision=value["revision"], **fields)
    return value


def bind(pid, value, shots=("S01",), role="female"):
    return mutation(pid, "bind", asset_id=value["asset_id"], version=value["version"], shot_ids=list(shots), role=role)


def test_new_episodes_pin_bible_and_share_the_same_approved_reference(series):
    value = create_asset(series["id"])
    p1 = bind(series["id"], value)
    p2 = store.create({"brief": "同一系列的第二集原创故事", "series_key": series["series_key"]})
    assert p2["series_bible"]["version"] == p1["series_bible"]["version"] == 1
    p2 = initialize_project(p2["id"])
    p2 = bind(p2["id"], value)
    first = bindings.resolved_references(p1, p1["episode"]["shots"][0])[0]
    second = bindings.resolved_references(p2, p2["episode"]["shots"][0])[0]
    assert first["path"] == second["path"] and first["sha256"] == second["sha256"]
    updated = document()
    updated["visual_rules"] = "新版本冷色光照；不覆盖旧剧集设定。"
    mutation(p1["id"], "save_bible", expected_version=1, document=updated)
    assert store.get(p1["id"])["series_bible"]["version"] == store.get(p2["id"])["series_bible"]["version"] == 1
    p3 = store.create({"brief": "采用新版系列设定的第三集", "series_key": series["series_key"]})
    assert p3["series_bible"]["version"] == 2
    assert schemas.project_context(p3)["series_bible"]["document"]["visual_rules"] == updated["visual_rules"]


def test_generated_is_not_approved_and_approval_requires_confirmation(series):
    value = create_asset(series["id"], approve=False)
    assert value["status"] == "reviewed" and value["data"]["review"]["technical"]["status"] == "pass"
    with pytest.raises(ValueError, match="明确批准"):
        bind(series["id"], value)
    with pytest.raises(ValueError, match="明确确认"):
        mutation(series["id"], "approve", asset_id=value["asset_id"], version=1, expected_asset_revision=value["revision"])
    value = mutation(series["id"], "approve", asset_id=value["asset_id"], version=1,
                     expected_asset_revision=value["revision"], confirmed=True)
    assert value["status"] == "approved" and value["data"]["approved_by"] == "producer"


def test_failed_media_validation_cannot_be_approved(series):
    p = store.get(series["id"])
    path = core.ROOT / p["shots"][0]["image"]["path"]
    Image.new("RGB", (128, 128), "black").save(path)
    p["shots"][0]["image"] = core.media_receipt(path)
    store.update(p["id"], {"shots": p["shots"]})
    value = create_asset(p["id"], approve=False)
    assert value["data"]["review"]["technical"]["status"] == "fail"
    with pytest.raises(ValueError, match="验证未通过"):
        mutation(p["id"], "approve", asset_id=value["asset_id"], version=1, expected_asset_revision=value["revision"], confirmed=True)


@pytest.mark.parametrize("stage", ["before_register", "before_approve", "before_reuse"])
def test_tampered_assets_fail_closed_at_each_boundary(series, stage):
    pid = series["id"]
    if stage == "before_register":
        value = mutation(pid, "plan", kind="character", entity_id="char_mei")
        source = core.ROOT / store.get(pid)["shots"][0]["image"]["path"]
        source.write_bytes(b"tampered")
        with pytest.raises(ValueError, match="校验失败"):
            mutation(pid, "register", asset_id=value["asset_id"], version=1, expected_asset_revision=1, source_key="shot:S01")
    else:
        value = create_asset(pid, approve=stage == "before_reuse")
        path = assets.reference_path(value, require_approved=False)
        path.write_bytes(b"tampered")
        with pytest.raises(ValueError, match="校验失败"):
            if stage == "before_reuse":
                bind(pid, value)
            else:
                mutation(pid, "approve", asset_id=value["asset_id"], version=1, expected_asset_revision=value["revision"], confirmed=True)


def test_approved_version_is_immutable_and_not_an_alias_of_source(series):
    pid = series["id"]
    value = create_asset(pid)
    original = assets.reference_path(value).read_bytes()
    source = core.ROOT / store.get(pid)["shots"][0]["image"]["path"]
    source.write_bytes(b"changed source, approved copy remains independent")
    assert assets.reference_path(value).read_bytes() == original
    for action, fields in (("register", {"source_key": "shot:S01"}), ("review", {}), ("approve", {"confirmed": True})):
        with pytest.raises(ValueError, match="不可覆盖"):
            mutation(pid, action, asset_id=value["asset_id"], version=1, expected_asset_revision=value["revision"], **fields)


def test_source_ownership_and_arbitrary_file_paths_are_rejected(series):
    pid = series["id"]
    value = mutation(pid, "plan", kind="character", entity_id="char_mei")
    fields = {"asset_id": value["asset_id"], "version": 1, "expected_asset_revision": 1}
    with pytest.raises(ValueError, match="已登记"):
        mutation(pid, "register", **fields, source_key="../../etc/passwd")
    with pytest.raises(ValueError, match="未知"):
        mutation(pid, "register", **fields, source_key="shot:S01", path="/etc/passwd")
    owner = core.ROOT / "production" / series["slug"] / "factory_owner.json"
    core.write_json(owner, {"project_id": "another-owner"})
    with pytest.raises(ValueError, match="归属"):
        mutation(pid, "register", **fields, source_key="shot:S01")


def test_new_versions_report_only_dependent_shots_and_never_auto_replace(series):
    first = create_asset(series["id"])
    p = bind(series["id"], first)
    second = create_asset(p["id"], asset_id=first["asset_id"], expected_version=1)
    current = bible.summary(p["id"])
    latest = next(v for v in current["assets"] if v["version"] == 2)
    assert latest["affected_shots"] == [{"project_id": p["id"], "direct_shot_ids": ["S01"],
                                           "affected_shot_ids": ["S01", "S03"], "pinned_versions": [1]}]
    assert store.get(p["id"])["series_references"]["S01"][0]["version"] == 1
    before = copy.deepcopy(store.get(p["id"])["budget"])
    p = bind(p["id"], second)
    assert p["series_references"]["S01"][0]["version"] == 2
    assert p["production_edits"][-1]["shot_ids"] == ["S01", "S03"] and p["budget"] == before
    assert assets.reference_path(first).is_file()


def test_stable_ids_cannot_change_identity_or_series(series):
    value = create_asset(series["id"])
    with pytest.raises(ValueError, match="稳定资产"):
        mutation(series["id"], "plan", asset_id=value["asset_id"], expected_version=1, kind="character", entity_id="char_lin")
    other = store.create({"brief": "另一个独立系列的原创故事", "series_key": "other-series"})
    other = initialize_project(other["id"])
    with pytest.raises(ValueError, match="同系列"):
        bind(other["id"], value)


def test_asset_and_project_compare_and_swap_are_atomic(series):
    value = create_asset(series["id"], approve=False)
    payload = {"action": "approve", "expected_revision": store.get(series["id"])["revision"],
               "asset_id": value["asset_id"], "version": 1, "expected_asset_revision": value["revision"], "confirmed": True}
    def approve(_):
        try:
            return bible.mutate(series["id"], payload)["status"]
        except ValueError:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(approve, range(2)))
    assert sorted(results) == ["approved", "conflict"]
    with pytest.raises(ValueError, match="项目已更新"):
        bible.mutate(series["id"], {"action": "bind", "expected_revision": 1, "asset_id": value["asset_id"], "version": 1, "shot_ids": ["S01"], "role": "female"})


def test_character_states_are_explicit_not_identity_mutations():
    state = {"female": {"wardrobe": "raincoat", "held_props": ["ticket"], "position": "left", "story_state": "knows the truth"}}
    assert character_states({"character_states": state}, {"female": "fixed identity"}) == state
    for bad in ({"female": {"face": "another person"}}, {"unknown": {"position": "left"}}, {"female": {"held_props": "ticket"}}):
        with pytest.raises(ValueError):
            character_states({"character_states": bad}, {"female": "fixed identity"})


def test_real_shot_cache_reuses_unrelated_shots_but_invalidates_updated_reference(series, monkeypatch):
    from factory import model_capabilities
    pid = series["id"]
    first = create_asset(pid)
    bind(pid, first)
    calls = []
    class Renderer:
        def copy_input(self, path):
            return str(path)
        def execute(self, graph, folder, kind, **kwargs):
            assert kind == "image"
            calls.append(str(folder))
            receipt = pattern(folder / "image.png", json.dumps(graph, sort_keys=True))
            core.write_json(folder / "result.json", receipt)
            return folder / "image.png"
    @contextmanager
    def worker(*args, **kwargs):
        yield Renderer()
    monkeypatch.setattr(production.Scheduler, "worker", worker)
    settings = {"image_width": 128, "image_height": 128, "semantic_enabled": False, "max_attempts": 2}
    # The cache test selects a standalone FLUX renderer; explicitly bind its
    # capabilities rather than inheriting the installation's unrelated defaults.
    current = store.get(pid)
    model_capabilities.adopt(current, settings)
    store.update(pid, {"model_capability_snapshot": current["model_capability_snapshot"],
                       "model_capability_history": current.get("model_capability_history", [])})
    assert model_capabilities.active(store.get(pid), settings, "image")["enabled"]
    def render():
        run = production._Run(store.get(pid), settings, lambda patch: store.update(pid, patch), None)
        run._characters_and_images()
        return {shot["id"]: shot["image"] for shot in run.shots}
    before = render()
    assert not any("characters/female" in call for call in calls)
    assert before["S01"]["asset_references"][0]["version"] == 1
    count = len(calls)
    assert render() == before and len(calls) == count
    next_version = create_asset(pid, asset_id=first["asset_id"], expected_version=1)
    bind(pid, next_version)
    after = render()
    assert after["S01"]["source_signature"] != before["S01"]["source_signature"]
    assert after["S02"] == before["S02"]
    assert after["S03"]["source_signature"] != before["S03"]["source_signature"]
    assert len(calls) == count + 2
    assert after["S01"]["asset_references"][0]["version"] == 2
    assert (core.ROOT / before["S01"]["path"]).is_file()


def test_local_edit_reroutes_saved_final_gate_without_repeating_creative_gates(series, monkeypatch):
    from langgraph.graph import StateGraph, START, END
    calls = []
    def style(state):
        calls.append("style")
        return {"route": "continue"}
    def produce(state):
        calls.append("production")
        project = store.get(state["project_id"])
        store.update(project["id"], {"delivery": {"fixture": True}, "delivery_complete_revision": project["production_revision"]})
        return {"route": "continue", "final_generation": project["production_revision"]}
    def build(checkpointer):
        graph = StateGraph(wf.State)
        graph.add_node("apply_style", style)
        graph.add_node("production", produce)
        graph.add_node("final_decision", wf.final_decision)
        graph.add_edge(START, "apply_style")
        graph.add_edge("apply_style", "production")
        graph.add_edge("production", "final_decision")
        graph.add_edge("final_decision", END)
        return graph.compile(checkpointer=checkpointer)
    monkeypatch.setattr(wf, "build_graph", build)
    p = store.update(series["id"], {"status": "queued", "pause_requested": False, "selected_style": "style-A",
                                          "workflow_version":1,"team_mode":"guided"})
    wf.advance(p["id"])
    old_gate = store.get(p["id"])["decisions"][-1]["id"]
    reference = create_asset(p["id"])
    bind(p["id"], reference)
    with pytest.raises(ValueError):
        store.decide(p["id"], {"type": "final", "choice": "approve", "decision_id": old_gate})
    assert store.get(p["id"])["delivery_history"][0]["delivery"] == {"fixture": True}
    store.action(p["id"], {"action": "resume"})
    wf.advance(p["id"])
    wf.advance(p["id"])
    assert calls == ["style", "production", "production"]
    p = store.get(p["id"])
    assert p["status"] == "awaiting_final" and p["decisions"][-1]["id"] != old_gate
    assert p["decisions"][0]["status"] == "superseded"


def test_binding_cannot_change_a_running_project_or_skip_human_gates(series):
    value = create_asset(series["id"])
    p = store.update(series["id"], {"status": "queued"})
    job = store.claim_job(p["id"])
    assert job
    store.update(p["id"], {"status": "paused"})
    with pytest.raises(ValueError, match="正在更新"):
        bind(p["id"], value)
    store.finish_job(job["id"], lease=job["lease"])
    store.update(p["id"], {"selected_style": "style-A", "selected_story": None})
    with pytest.raises(ValueError, match="人工决策门"):
        bind(p["id"], value)
    assert store.get(p["id"]).get("series_references") is None


def test_standalone_and_legacy_series_projects_keep_their_snapshot_shape(series):
    standalone = store.create({"brief": "不属于系列的原有单集测试项目"})
    legacy = store.create({"brief": "只有旧式记忆、没有正式设定的项目", "series_key": "legacy-memory"})
    for p in (standalone, legacy):
        assert "series_bible" not in p and "series_references" not in p
        assert "series_bible" not in schemas.project_context(p)
        with store.connection() as db:
            before = db.execute("SELECT snapshot FROM projects WHERE id=?", (p["id"],)).fetchone()[0]
        store.initialize()
        assert bible.summary(p["id"])["assets"] == []
        with store.connection() as db:
            assert db.execute("SELECT snapshot FROM projects WHERE id=?", (p["id"],)).fetchone()[0] == before


def test_scene_and_prop_references_are_attested_and_join_only_the_selected_shot(series):
    pid = series["id"]
    scene = create_asset(pid, kind="scene", entity_id="station")
    prop = create_asset(pid, kind="prop", entity_id="ticket")
    bind(pid, scene, shots=("S02",), role="station")
    p = bind(pid, prop, shots=("S02",), role="ticket")
    refs = bindings.resolved_references(p, p["episode"]["shots"][1])
    assert {value["kind"] for value in refs} == {"scene", "prop"}
    assert all(value["version"] == 1 and value["sha256"] for value in refs)
    assert bindings.resolved_references(p, p["episode"]["shots"][0]) == []
    assert p["production_edits"][-1]["shot_ids"] == ["S02"]


def test_symlinked_asset_version_cannot_escape_the_reference_library(series, tmp_path):
    value = create_asset(series["id"])
    reference = assets.reference_path(value)
    original_folder = reference.parent
    outside = tmp_path / "outside-library"
    original_folder.rename(outside)
    original_folder.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="Path escapes"):
        assets.reference_path(value)


def test_unresolved_cloud_task_blocks_binding_and_its_transitive_dependents(series):
    from factory import cloud_budget
    pid = series["id"]
    value = create_asset(pid)
    # S03 depends on S01. Updating S01 cannot bypass S03's unknown paid intent.
    with store.connection() as db:
        db.execute("INSERT INTO cloud_tasks(intent_id,project_id,shot_id,attempt,take,provider,model,request_signature,"
                   "status,currency,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("unknown", pid, "S03", 1, "take_001", "minimax", "fixture", "sig", "submitting", "CNY", 1, 1))
    before = store.get(pid)
    with pytest.raises(ValueError, match="尚有未核对云任务"):
        bind(pid, value)
    assert store.get(pid)["production_revision"] == before["production_revision"]
    assert store.get(pid).get("series_references") is None
    assert cloud_budget.summary(pid)["tasks"][0]["status"] == "submitting"


def test_series_http_endpoints_keep_origin_and_cas_guards(series):
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(payload=None, origin=None):
        client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        client.request("GET" if payload is None else "POST", "/api/factory/projects/" + series["id"] + "/series",
                       body=json.dumps(payload) if payload is not None else None, headers=headers)
        response = client.getresponse()
        result = response.status, json.loads(response.read())
        client.close()
        return result
    try:
        code, library = request()
        assert code == 200 and library["bible"]["version"] == 1
        payload = {"action": "plan", "expected_revision": library["revision"], "expected_version": 0,
                   "kind": "scene", "entity_id": "station"}
        assert request(payload, "https://evil.invalid")[0] == 403
        assert request({**payload, "expected_revision": 1})[0] == 409
        code, result = request(payload)
        assert code == 202 and result["result"]["status"] == "planned"
        assert request({**payload, "action": "delete_all"})[0] == 409
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
