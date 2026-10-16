"""Durability, authorization and identity-review boundaries for asset jobs."""
import copy
import uuid

import pytest

from factory import store, material_tasks as tasks, material_library as library, cloud_media, cloud_budget
from factory.scheduler import ProductionNeedsDecision
from studio import core
from test_factory_material_library import project, material, picture, select, confirm


def configuration(paid=False):
    return {"llm": {"base_url": "http://mock.invalid", "model": "mock-vision", "vision": True},
            "image_profiles": {"image": {"provider": "dashscope_image", "model": "qwen-image-3.0", "enabled": True,
                                          "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "network_mode": "remote", "allowed_hosts": ["dashscope.aliyuncs.com"], "api_key_env": "TEST_MATERIAL_KEY",
                                          "pricing": {"unit_cost_micros": 1000000, "currency": "CNY", "source": "test estimate"}}} if paid else {"image": {"provider": "legacy", "model": "FLUX.2-klein-4B"}},
            "image_roles": {"image_generator": "image"}}


def create(project=None, operation="generate", value=None, config=None, **fields):
    payload = {"request_id": uuid.uuid4().hex, "operation": operation, "kind": "character", "document": {"name": "林夏", "description": "成年短发女性"}, **fields}
    if project: payload["project_id"] = project["id"]
    if value: payload.update(asset_id=value["asset_id"], version=value["version"], document=value["data"]["document"])
    return tasks.create(payload, config or configuration())


def fake_render(task, prompt, refs, folder, step):
    import base64
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "image.png"; path.write_bytes(base64.b64decode(picture()))
    return path, core.media_receipt(path, model="mock-image", provider="fixture")


def authorize(task, **fields):
    return tasks.control(task["id"], {"action": "authorize", "expected_lease": task["lease"], "signature": task["signature"], "confirmed": True, **fields})


def test_global_ai_job_does_not_create_fake_projects_and_reuses_identical_inputs(project, monkeypatch):
    calls = []
    monkeypatch.setattr(tasks, "render_image", lambda *args: (calls.append(args[4]), fake_render(*args))[1])
    monkeypatch.setattr(tasks, "vision", lambda *args, **kwargs: {"status": "pass", "summary": "fixture only"})
    task = create()
    job = tasks.claim(); tasks.run(job)
    with store.connection() as db:
        finished = tasks.read(db, task["id"])
        assert db.execute("SELECT count(*) FROM projects").fetchone()[0] == 1
        result = library.get(db, finished["result"]["asset_id"])
        assert result["status"] == "candidate" and result["library_key"] == "global"
    assert finished["status"] == "completed" and calls == ["0:0"]
    assert create()["id"] == task["id"]


def test_describe_returns_editable_draft_and_preserves_original_identity(project, monkeypatch):
    value = material()
    monkeypatch.setattr(tasks, "vision", lambda *args, **kwargs: {"name": "林夏", "description": "识图建议"})
    task = create(operation="describe", value=value)
    tasks.run(tasks.claim())
    with store.connection() as db:
        finished = tasks.read(db, task["id"])
        assert finished["result"]["draft"] is True
        assert library.get(db, value["asset_id"])["data"]["document"]["description"] != "识图建议"


def test_requires_vision_capable_connection_for_image_analysis(project):
    config = configuration(); config.update(llm_profiles={"text": {"base_url": "http://localhost:8000/v1", "model": "text-only", "vision": False, "context_tokens": 8192}}, llm_roles={"visual_critic": "text"})
    with pytest.raises(ValueError, match="vision"):
        create(operation="describe", value=material(), config=config)


def test_request_id_cannot_be_reused_for_different_generation(project):
    payload = {"request_id": "stable", "operation": "generate", "kind": "scene", "document": {"name": "站台"}}
    tasks.create(payload, configuration())
    with pytest.raises(ValueError, match="request_id"):
        tasks.create({**payload, "document": {"name": "另一个站台"}}, configuration())


def test_generation_review_failure_blocks_formal_approval(project, monkeypatch):
    monkeypatch.setattr(tasks, "render_image", fake_render)
    monkeypatch.setattr(tasks, "vision", lambda *args, **kwargs: {"status": "fail", "identity": "mismatch"})
    task = create(project)
    tasks.run(tasks.claim())
    with store.connection() as db: output = tasks.read(db, task["id"])["result"]
    value = library.inventory(project["id"])["assets"][0]
    assert value["asset_id"] == output["asset_id"]
    select(project, value)
    with pytest.raises(ValueError, match="视觉审核"):
        confirm(project)


def test_paid_task_requires_matching_authorization_before_claim(project):
    task = create(config=configuration(True))
    assert task["status"] == "waiting_authorization" and task["quote"]["submissions"] == 1
    assert tasks.claim() is None
    with pytest.raises(ValueError, match="金额"):
        authorize(task, max_cost_micros=1)
    authorized = authorize(task)
    assert authorized["authorization"]["max_submissions"] == 1
    assert tasks.claim()["id"] == task["id"]


def test_unknown_price_needs_explicit_acknowledgement(project):
    config = configuration(True); config["image_profiles"]["image"]["pricing"] = {}
    task = create(config=config)
    with pytest.raises(ValueError, match="未知价格"):
        authorize(task)
    assert authorize(task, allow_unpriced=True)["status"] == "queued"


def test_uncertain_paid_post_is_never_repeated_after_restart(project, monkeypatch):
    posts = []
    monkeypatch.setattr(cloud_budget, "validate_live_profile", lambda p: None)
    def failed_request(profile, body):
        posts.append(body)
        raise TimeoutError("provider may have accepted the payment")
    monkeypatch.setattr(cloud_media, "_request", failed_request)
    task = create(config=configuration(True)); authorize(task)
    job = tasks.claim(); tasks.run(job)
    with store.connection() as db:
        failed = tasks.read(db, task["id"])
        assert db.execute("SELECT count(*) FROM material_submissions").fetchone()[0] == 1
    tasks.control(task["id"], {"action": "resume", "expected_lease": failed["lease"], "signature": failed["signature"]})
    tasks.run(tasks.claim())
    with store.connection() as db: retried = tasks.read(db, task["id"])
    assert len(posts) == 1 and retried["status"] == "needs_attention"
    assert "禁止自动重投" in retried["error"]


def test_recovery_fences_old_worker_and_honors_pause(project):
    task = create()
    old = tasks.claim(); tasks.recover(); newer = tasks.claim()
    with pytest.raises(Exception, match="执行权"):
        tasks.boundary(old)
    assert not tasks._save_result(old, {"bad": True}, "completed")
    tasks.control(task["id"], {"action": "pause", "expected_lease": newer["lease"], "signature": newer["signature"]})
    assert tasks._save_result(newer, {"progress": 1})
    with store.connection() as db: assert tasks.read(db, task["id"])["status"] == "pause_requested"
    tasks.recover()
    with store.connection() as db: assert tasks.read(db, task["id"])["status"] == "paused"


def test_multistage_composition_keeps_all_references_under_model_limit(project, monkeypatch):
    characters = [material(name="人物" + str(i)) for i in range(4)]
    scene = material(kind="scene", name="站台")
    style = material(kind="style", name="水彩")
    document = {"name": "四人站台", "members": [{"asset_id": value["asset_id"], "version": value["version"], "role": "char_" + str(i)} for i, value in enumerate(characters)], "scene": {"asset_id": scene["asset_id"], "version": scene["version"]}}
    seen = []
    def render(*args):
        seen.extend(args[2]); assert len(args[2]) <= 4
        return fake_render(*args)
    monkeypatch.setattr(tasks, "render_image", render)
    monkeypatch.setattr(tasks, "vision", lambda *args, **kwargs: {"status": "pass"})
    task = create(operation="compose", kind="composition", document=document, style={"asset_id": style["asset_id"], "version": style["version"]})
    assert task["quote"]["submissions"] == 2
    tasks.run(tasks.claim())
    assert {library.image_path(value["data"]["images"][0]) for value in [*characters, scene, style]} <= set(seen)


def test_public_task_never_exposes_private_config_or_provider_response(project):
    task = create(config=configuration(True))
    assert "config" not in task and "api_key_env" not in str(task)


def test_backup_restores_material_versions_and_task_owner_without_starting_generation(project, tmp_path):
    from factory import backup
    from langgraph.checkpoint.sqlite import SqliteSaver
    import sqlite3
    value = material()
    task = create(operation="describe", value=value)
    with sqlite3.connect(store.DATA / "checkpoints.sqlite3") as db:
        SqliteSaver(db).setup()
    (store.DATA / "worker.lock").touch()
    destination = tmp_path.parent / ("materials-backup-" + uuid.uuid4().hex)
    backup.create(core.ROOT, destination, maintenance_confirmed=True, data_dir=store.DATA)
    assert backup.verify(destination)["verified"] is True
    with sqlite3.connect(destination / "runtime/factory/studio.sqlite3") as db:
        assert db.execute("SELECT library_key FROM material_tasks WHERE id=?", (task["id"],)).fetchone()[0] == "global"
        assert db.execute("SELECT count(*) FROM material_versions WHERE asset_id=?", (value["asset_id"],)).fetchone()[0] == 2


def test_saved_paid_response_resumes_download_without_a_second_post(project, monkeypatch):
    from factory import video_transport
    import base64
    calls = []
    monkeypatch.setattr(cloud_budget, "validate_live_profile", lambda p: None)
    monkeypatch.setattr(cloud_media, "_request", lambda *args: (calls.append("post"), {"model": "qwen-image-3.0", "id": "saved-request", "data": [{"url": "https://download.example/private-signed-url"}], "usage": {"output_images": 1}})[1])
    monkeypatch.setattr(video_transport, "download_video", lambda *args: (_ for _ in ()).throw(TimeoutError("download interrupted")))
    monkeypatch.setattr(tasks, "vision", lambda *args, **kwargs: {"status": "pass"})
    task = create(config=configuration(True)); authorize(task)
    tasks.run(tasks.claim())
    with store.connection() as db: failed = tasks.read(db, task["id"])
    tasks.control(task["id"], {"action": "resume", "expected_lease": failed["lease"], "signature": failed["signature"]})
    monkeypatch.setattr(video_transport, "download_video", lambda profile, url, path: path.write_bytes(base64.b64decode(picture())))
    tasks.run(tasks.claim())
    with store.connection() as db: finished = tasks.read(db, task["id"])
    assert finished["status"] == "completed" and calls == ["post"]
    assert "private-signed-url" not in str(tasks.public(finished))


def test_failed_local_generation_keeps_cumulative_resource_accounting(project, monkeypatch):
    def fail(task, prompt, refs, folder, step, budget):
        budget.base_gpu += 10
        raise RuntimeError("fixture local generation failure")
    monkeypatch.setattr(tasks, "_render_image", fail)
    task = create(); tasks.run(tasks.claim())
    with store.connection() as db: failed = tasks.read(db, task["id"])
    assert failed["result"]["production"]["gpu_seconds"] >= 10
    tasks.control(task["id"], {"action": "resume", "expected_lease": failed["lease"], "signature": failed["signature"]})
    tasks.run(tasks.claim())
    with store.connection() as db: retried = tasks.read(db, task["id"])
    assert retried["result"]["production"]["gpu_seconds"] >= 20


def test_backup_rejects_changed_metadata_behind_a_confirmed_material_version(project, tmp_path):
    from factory import backup
    from langgraph.checkpoint.sqlite import SqliteSaver
    import sqlite3
    value = material(); select(project, value); confirm(project)
    with sqlite3.connect(store.DATA / "checkpoints.sqlite3") as db: SqliteSaver(db).setup()
    (store.DATA / "worker.lock").touch()
    with store.connection() as db:
        data = library.get(db, value["asset_id"], value["version"])["data"]
        data["document"]["name"] = "unexpected identity replacement"
        db.execute("UPDATE material_versions SET data=? WHERE asset_id=? AND version=?", (store.encoded(data), value["asset_id"], value["version"]))
    destination = tmp_path.parent / ("bad-material-backup-" + uuid.uuid4().hex)
    with pytest.raises(backup.BackupError, match="material metadata"):
        backup.create(core.ROOT, destination, maintenance_confirmed=True, data_dir=store.DATA)
    assert not destination.exists()
