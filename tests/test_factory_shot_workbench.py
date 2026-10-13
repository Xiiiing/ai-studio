"""Local shot edits and selection use real files/SQLite, never a paid API."""
import copy
import http.client
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

import pytest

from factory import store, production, audio_editor, web, schemas
from factory import shot_edits, shot_workbench as board
from factory.scheduler import ProductionNeedsDecision
from studio import core
from test_factory_series import pattern


@pytest.fixture
def project(tmp_path, monkeypatch):
    for module in (core, production, audio_editor, schemas):
        monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(store, "DATA", tmp_path / "db")
    monkeypatch.setattr(store, "DATABASE", tmp_path / "db/studio.sqlite3")
    store.initialize()
    p = store.create({"brief": "成年角色在站台等待并归还丢失的书籍"})
    episode = {"cast": {"lead": "A fictional adult with short black hair"}, "shots": [
        {"id": "S01", "seconds": 4, "prompt": "A person at a station", "motion_prompt": "A slow camera push", "camera_motion": "hold", "characters": ["lead"]},
        {"id": "S02", "seconds": 4, "prompt": "A quiet bench", "motion_prompt": "Static camera", "camera_motion": "hold", "characters": ["lead"]},
        {"id": "S03", "seconds": 4, "prompt": "The person returns a book", "reference_shots": ["S01"], "characters": ["lead"]}]}
    p = store.update(p["id"], {"status": "paused", "episode": episode, "script": copy.deepcopy(episode),
                              "selected_story": "A", "selected_style": "style-A", "script_review": {"passed": True},
                              "styles": [{"id": "style-A", "prompt": "Hand drawn illustration"}]})
    return p


def context(p):
    return production._Run(p, {"image_width": 128, "image_height": 128, "semantic_enabled": False},
                           lambda patch: store.update(p["id"], patch), None)


def target(run, sid="S01"):
    shot = next(s for s in run.shots if s["id"] == sid)
    return run._target(sid, run.folder / "assets/shots" / sid, shot["prompt"], shot=shot)


def fixture_candidate(p, take, variant=0, eligible=True):
    run = context(p)
    current = target(run)
    folder = current["folder"] / take
    receipt = pattern(folder / "image.png", variant)
    qa = {"technical": {"status": "pass"}, "eligible_for_selection": eligible, "status": "pass" if eligible else "fail"}
    receipt.update(qa=qa, source_signature=current["signature"], take=take,
                   plan_signature=board.plan_signature(p, current["shot"], "image"), attempt=int(take[5:]))
    core.write_json(folder / "result.json", receipt)
    core.write_json(folder / "attempt.json", {"take": take, "attempt": int(take[5:]), "stage": "image", "state": "passed" if eligible else "failed",
                                              "source_signature": current["signature"], "plan_signature": receipt["plan_signature"],
                                              "prompt": current["prompt"], "model": "fixture", "parameters": {"seed": variant}, "qa": qa})
    core.write_json(current["folder"] / "selected.json", receipt)
    return receipt


def edit(p, **payload):
    return shot_edits.mutate(p["id"], "S01", {"expected_revision": store.get(p["id"])["revision"], **payload})


def test_candidate_comparison_and_producer_selection_override_automatic_choice(project, monkeypatch):
    first = fixture_candidate(project, "take_001", 1)
    second = fixture_candidate(project, "take_002", 2)
    before = core.sha256(core.ROOT / first["path"])
    snapshot = board.snapshot(project["id"])
    assert len(snapshot["shots"][0]["candidates"]) == 2
    chosen = edit(project, action="select", stage="image", take="take_001", confirmed=True)
    def no_generation(*args, **kwargs):
        raise AssertionError("Selecting an existing candidate must not invoke generation")
    monkeypatch.setattr(production.Scheduler, "worker", no_generation)
    run = context(chosen)
    result = run._render_targets([target(run)], "story_frames")[0]
    assert result["sha256"] == first["sha256"] != second["sha256"]
    assert result["producer_selection"]["actor"] == "producer"
    assert core.sha256(core.ROOT / first["path"]) == before
    assert board.snapshot(project["id"])["shots"][0]["candidates"][1]["producer_selected"]


def test_edit_changes_only_target_and_keeps_source_and_accounting(project):
    old = fixture_candidate(project, "take_001")
    before = context(project)
    first, other = target(before)["signature"], target(before, "S02")["signature"]
    store.update(project["id"], {"production": {"gpu_seconds": 17, "elapsed_seconds": 42}})
    changed = edit(project, action="edit", changes={"prompt": "The adult returns the book at the blue bench"})
    assert changed["production_edits"][-1]["shot_ids"] == ["S01", "S03"]
    assert changed["episode"]["shots"][1] == project["episode"]["shots"][1]
    assert changed["production"]["gpu_seconds"] == 17 and changed["production"]["elapsed_seconds"] == 42
    after = context(changed)
    assert target(after)["signature"] != first
    assert target(after, "S02")["signature"] == other
    assert core.sha256(core.ROOT / old["path"]) == old["sha256"]
    assert not board.snapshot(project["id"])["shots"][0]["candidates"][0]["selectable"]


def test_identical_parameter_retake_requires_explicit_intent_and_new_signature(project):
    first = target(context(project))["signature"]
    with pytest.raises(ValueError, match="没有实际修改"):
        edit(project, action="edit", changes={})
    changed = edit(project, action="edit", changes={}, regenerate=True, stage="image")
    assert target(context(changed))["signature"] != first
    assert changed["episode"]["shots"][0]["render_revisions"] == {"image": 1}


def test_video_only_changes_keep_image_and_unrelated_references(project):
    before = target(context(project))["signature"]
    changed = edit(project, action="edit", changes={"motion_prompt": "Camera moves to the right"}, stage="video")
    assert target(context(changed))["signature"] == before
    assert changed["production_edits"][-1]["shot_ids"] == ["S01"]


@pytest.mark.parametrize("changes", [{"seconds": 30}, {"model": "paid-unapproved"}, {"prompt": ""}, {"camera_motion": "fly"},
                                      {"character_states": {"lead": {"identity": "replace face"}}}])
def test_unsupported_parameters_are_rejected_without_mutation(project, changes):
    revision = store.get(project["id"])["revision"]
    with pytest.raises(ValueError):
        edit(project, action="edit", changes=changes)
    assert store.get(project["id"])["revision"] == revision


def test_cas_serializes_concurrent_edits_and_old_clients_cannot_overwrite(project):
    expected = project["revision"]
    def update(index):
        try:
            return shot_edits.mutate(project["id"], "S01", {"action": "edit", "expected_revision": expected,
                                                            "changes": {"prompt": "Version " + str(index)}})
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(update, range(8)))
    assert sum(value is not None for value in results) == 1
    assert len(store.get(project["id"])["production_edits"]) == 1


def test_failed_unconfirmed_tampered_and_arbitrary_candidates_are_not_selectable(project):
    fixture_candidate(project, "take_001", eligible=False)
    with pytest.raises(ValueError, match="验证"):
        edit(project, action="select", stage="image", take="take_001", confirmed=True)
    valid = fixture_candidate(project, "take_002", 2)
    with pytest.raises(ValueError, match="明确"):
        edit(project, action="select", stage="image", take="take_002")
    (core.ROOT / valid["path"]).write_bytes(b"corruption")
    with pytest.raises(ValueError):
        edit(project, action="select", stage="image", take="take_002", confirmed=True)
    with pytest.raises(ValueError):
        edit(project, action="select", stage="image", take="../../other", confirmed=True)


def test_candidate_symlink_escape_is_reported_and_cannot_be_selected(project, tmp_path):
    value = fixture_candidate(project, "take_001")
    path = core.ROOT / value["path"]
    outside = tmp_path / "private.png"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    snapshot = board.snapshot(project["id"])
    assert snapshot["errors"] and snapshot["shots"][0]["candidates"] == []
    with pytest.raises(ValueError):
        edit(project, action="select", stage="image", take="take_001", confirmed=True)


def test_running_job_and_unresolved_cloud_prevent_visual_edits(project):
    store.update(project["id"], {"status": "queued"})
    job = store.claim_job(project["id"])
    with pytest.raises(ValueError, match="正在更新"):
        edit(project, action="edit", changes={"prompt": "A new frame"})
    store.finish_job(job["id"], lease=job["lease"])
    store.update(project["id"], {"status": "paused"})
    with store.connection() as db:
        db.execute("INSERT INTO cloud_tasks(intent_id,project_id,shot_id,attempt,take,provider,model,request_signature,status,currency,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("unknown", project["id"], "S01", 1, "take_001", "minimax", "fixture", "x", "submission_unknown", "CNY", 0, 0))
    with pytest.raises(ValueError, match="未核对"):
        edit(project, action="edit", changes={"prompt": "A new frame"})


def test_approved_delivery_is_archived_and_requires_new_final_gate(project):
    store.update(project["id"], {"status": "approved", "delivery": {"path": "original.mp4"}})
    result = edit(project, action="edit", changes={"camera_motion": "push_in"})
    assert result["delivery_history"][0]["delivery"] == {"path": "original.mp4"}
    assert result["delivery"] is None and result["status"] == "paused"


def test_new_project_and_legacy_candidates_are_readable(project):
    plain = store.create({"brief": "尚无素材的新项目不需要迁移"})
    assert board.snapshot(plain["id"])["shots"] == []
    candidate = fixture_candidate(project, "take_001")
    path = core.ROOT / candidate["path"]
    for name in ("result.json", "attempt.json"):
        data = core.read_json(path.parent / name)
        data.pop("plan_signature")
        core.write_json(path.parent / name, data)
    store.update(project["id"], {"shots": [{"id": "S01", "image": candidate}]})
    assert board.snapshot(project["id"])["shots"][0]["candidates"][0]["selectable"]


def test_loopback_shot_api_cas_origin_and_no_paid_call(project):
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(suffix, payload=None, origin=None):
        client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        client.request("GET" if payload is None else "POST", "/api/factory/projects/" + project["id"] + "/shots" + suffix,
                       body=json.dumps(payload) if payload is not None else None, headers=headers)
        response = client.getresponse()
        result = response.status, json.loads(response.read())
        client.close()
        return result
    try:
        code, snapshot = request("")
        assert code == 200 and len(snapshot["shots"]) == 3
        payload = {"action": "edit", "expected_revision": snapshot["revision"], "changes": {"camera_motion": "pan_left"}}
        assert request("/S01", payload, "https://evil.invalid")[0] == 403
        assert request("/S01", payload)[0] == 202
        assert request("/S01", payload)[0] == 409
        assert store.get(project["id"])["production_edits"][-1]["shot_ids"] == ["S01"]
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
