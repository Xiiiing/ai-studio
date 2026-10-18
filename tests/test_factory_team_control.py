"""Operator controls exercise durable queues and real HTTP without vendor calls."""
from __future__ import annotations

import copy
import json
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from factory import store, team_ledger
from studio import core
from studio.http_security import HTTPError
from test_factory_shot_workbench import project, fixture_candidate, edit
from test_factory_team_ledger import http_client, database_dump, task


def intervention(p, **changes):
    pid = p["id"]
    ident = changes.pop("task_id", "video:S01")
    current = task(pid, ident)
    value = {
        "request_id": "offline-intervention-1",
        "expected_control_revision": team_ledger.team_snapshot(pid)["control_revision"],
        "target_type": "task", "target_id": ident,
        "task_ids": [ident], "task_generations": {ident: current["generation"]},
        "instruction": "Keep the framing and costume; make the expression calmer.",
        "operation": "revise", "preserve": [], "after": "auto",
    }
    value.update(changes)
    return value


def action(p, ident, name, **changes):
    value = {
        "action": name, "request_id": "offline-action-" + name,
        "expected_control_revision": team_ledger.team_snapshot(p["id"])["control_revision"],
        "expected_generation": task(p["id"], ident)["generation"],
    }
    value.update(changes)
    return value


def test_preview_is_read_only_and_video_work_keeps_image_and_unrelated_shots(project, http_client):
    from factory import team_control

    payload = intervention(project)
    before = database_dump()
    direct = team_control.preview(project["id"], payload)
    status, value = http_client("POST", f"/api/factory/projects/{project['id']}/interventions/preview", payload)
    assert status == 200 and value == direct
    assert {"video:S01", "video_review:S01", "edit:delivery", "technical_review:delivery"} <= set(value["affected_tasks"])
    assert not {"image:S01", "video:S02", "image:S03"} & set(value["affected_tasks"])
    assert database_dump() == before


def test_duplicate_instruction_retry_succeeds_after_cas_revision_changes(project):
    from factory import team_control

    payload = intervention(project, operation="retry")
    first = team_control.submit(project["id"], payload)
    before = database_dump()
    replay = team_control.submit(project["id"], payload)
    assert replay == first and database_dump() == before
    with pytest.raises((ValueError, HTTPError)):
        team_control.submit(project["id"], {**payload, "instruction": "A different request using the same id."})
    assert database_dump() == before


def test_production_progress_does_not_invalidate_control_revision(project):
    from factory import team_control

    payload = intervention(project, operation="retry")
    store.update(project["id"], {"production": {"elapsed_seconds": 14, "gpu_seconds": 7}, "progress": 55})
    store.event(project["id"], "Synthetic progress; no new operator arrangement.")
    assert team_ledger.team_snapshot(project["id"])["control_revision"] == payload["expected_control_revision"]
    team_control.submit(project["id"], payload)
    assert team_ledger.team_snapshot(project["id"])["control_revision"] > payload["expected_control_revision"]


@pytest.mark.parametrize("changes", [
    {"target_type": "task", "target_id": "video:S02"},
    {"target_type": "member", "target_id": "image_generator"},
    {"target_type": "department", "target_id": "sound"},
    {"target_type": "leader", "target_id": "dialogue_editor"},
    {"task_ids": ["video:S01", "video:S99"], "task_generations": {"video:S01": 1, "video:S99": 1}},
])
def test_scope_cannot_expand_outside_target_ownership(project, changes):
    from factory import team_control

    before = database_dump()
    with pytest.raises((ValueError, KeyError, HTTPError)):
        team_control.submit(project["id"], intervention(project, **changes))
    assert database_dump() == before


def test_mismatched_task_generation_cannot_replace_newer_work(project):
    from factory import team_control

    payload = intervention(project, task_generations={"video:S01": 0})
    before = database_dump()
    with pytest.raises((ValueError, HTTPError)):
        team_control.submit(project["id"], payload)
    assert database_dump() == before


def test_control_reads_and_preview_share_http_origin_boundary(project, http_client):
    for route, method, body in (("interventions", "GET", None),
                                ("interventions/preview", "POST", intervention(project)),
                                ("interventions", "POST", intervention(project)),
                                ("tasks/video:S01/actions", "POST", action(project, "video:S01", "pause"))):
        before = database_dump()
        status, value = http_client(method, f"/api/factory/projects/{project['id']}/{route}", body,
                                   headers={"Origin": "https://cross-site.example"})
        assert status == 403 and value["error"]
        assert database_dump() == before


def test_image_rework_reports_conflict_with_explicitly_preserved_dependent_video(project):
    from factory import team_control

    payload = intervention(project, task_id="image:S01", preserve=["video:S03"])
    before = database_dump()
    preview = team_control.preview(project["id"], payload)
    assert {"image:S03", "image_review:S03", "video:S03", "video_review:S03"} <= set(preview["affected_tasks"])
    assert any(row["task_id"] == "video:S03" for row in preview["conflicts"])
    assert preview["preserved_tasks"] == ["video:S03"]
    assert database_dump() == before


def test_active_call_drains_without_publishing_then_instruction_restarts_only_target(project):
    from factory import team_control

    pid = project["id"]
    store.update(pid, {"status": "producing", "pause_requested": False})
    job = store.claim_job()
    old = team_ledger.begin_unit(pid, "video", "S01", input_version="old-input")
    old_episode = copy.deepcopy(store.get(pid, False)["episode"])
    payload = intervention(project)
    accepted = team_control.submit(pid, payload)
    assert accepted["instruction"]["status"] == "queued"
    assert team_control.process_pending(pid) is None
    with pytest.raises(team_control.TeamInterventionPending):
        team_control.admit(pid, "video", "S02")
    with pytest.raises(team_control.TeamInterventionPending):
        team_control.assert_publishable(pid, "video", "S01", "old-input")
    assert store.get(pid, False)["episode"] == old_episode

    assert not team_ledger.finish_unit(old, artifact=f"production/factory-{pid}/old.mp4",
                                       details={"remote_task_id": "paid-task-kept", "cost": 3.0})
    store.finish_job(job["id"], lease=job["lease"])
    assert team_control.process_pending(pid)["status"] == "applied"
    current = store.get(pid, False)
    first, other, dependent = current["episode"]["shots"]
    assert payload["instruction"] in first["motion_prompt"]
    assert first["prompt"] == old_episode["shots"][0]["prompt"]
    assert other == old_episode["shots"][1] and dependent == old_episode["shots"][2]
    assert current["script"] == current["episode"]
    assert current["status"] == "queued" and not current["pause_requested"]
    historical = next(row for row in task(pid, "video:S01")["attempts"] if row["run_id"] == old["run_id"])
    assert historical["status"] == "superseded" and historical["details"]["remote_task_id"] == "paid-task-kept"
    store.initialize()
    assert team_control.process_pending(pid) is None
    assert store.get(pid, False)["episode"] == current["episode"]
    team_control.admit(pid, "video", "S02")


def test_overlapping_commands_require_reconciliation_before_old_requirements_apply(project):
    from factory import team_control

    pid = project["id"]
    first = team_control.submit(pid, intervention(project, instruction="First explicit requirement."))
    second = team_control.submit(pid, intervention(project, request_id="offline-intervention-2",
                                                   instruction="A newer conflicting requirement."))
    generation = task(pid, "video:S01")["generation"]
    episode = copy.deepcopy(store.get(pid, False)["episode"])
    result = team_control.process_pending(pid)
    assert result["status"] == "needs_decision"
    values = team_control.instructions(pid)["instructions"]
    assert next(row for row in values if row["id"] == first["instruction"]["id"])["status"] == "needs_decision"
    assert next(row for row in values if row["id"] == second["instruction"]["id"])["status"] == "queued"
    assert store.get(pid, False)["episode"] == episode
    assert task(pid, "video:S01")["generation"] == generation
    store.initialize()
    assert team_control.process_pending(pid)["status"] == "needs_decision"
    assert store.get(pid, False)["episode"] == episode
    team_control.dismiss(pid, first["instruction"]["id"], {
        "request_id": "dismiss-obsolete-requirement",
        "expected_control_revision": team_ledger.team_snapshot(pid)["control_revision"],
    })
    assert team_control.process_pending(pid)["status"] == "applied"
    prompt = store.get(pid, False)["episode"]["shots"][0]["motion_prompt"]
    assert "A newer conflicting requirement." in prompt and "First explicit requirement." not in prompt


def test_locked_result_blocks_existing_shot_edit_and_unlock_releases_it(project):
    from factory import team_control

    pid = project["id"]
    receipt = fixture_candidate(project, "take_001")
    token = team_ledger.begin_unit(pid, "image", "S01", input_version="image-v1")
    team_ledger.finish_unit(token, artifact=receipt)
    lock_payload = action(project, "image:S01", "lock")
    team_control.task_action(pid, "image:S01", lock_payload)
    assert team_control.is_locked(pid, "image", "S01")
    before = database_dump()
    with pytest.raises(ValueError, match="锁定"):
        edit(project, action="edit", changes={"prompt": "A replacement that would modify the locked input."})
    assert database_dump() == before
    # Retrying a successful action after its revision bump is also idempotent.
    team_control.task_action(pid, "image:S01", lock_payload)
    assert database_dump() == before
    request = intervention(project, task_id="image:S01")
    assert team_control.preview(pid, request)["conflicts"]
    accepted = team_control.submit(pid, request)
    assert accepted["instruction"]["status"] == "needs_decision"
    team_control.task_action(pid, "image:S01", action(project, "image:S01", "unlock"))
    assert not team_control.is_locked(pid, "image", "S01")
    assert team_control.process_pending(pid)["status"] == "applied"


def test_locked_unreviewed_media_can_gain_provenance_once_without_changing_bytes(project):
    from factory import team_control
    pid = project['id']
    receipt = fixture_candidate(project, 'take_001')
    raw = {k: receipt[k] for k in ('path', 'sha256', 'bytes')}
    token = team_ledger.begin_unit(pid, 'video', 'S01', input_version='original')
    team_ledger.finish_unit(token, artifact=raw)
    team_control.task_action(pid, 'video:S01', action(project, 'video:S01', 'lock'))
    current = store.get(pid, False)
    reviewed = {**raw, 'source_signature':'original', 'plan_signature':'original-plan', 'qa':{'status':'pass'}}
    shots = copy.deepcopy(current['shots']) or [copy.deepcopy(current['episode']['shots'][0])]
    shots[0]['video'] = reviewed
    store.update(pid, {'shots':shots})
    assert team_control.is_locked(pid, 'video', 'S01')
    before = database_dump()
    for field, value in [('sha256','different-bytes'), ('source_signature','changed-source'), ('plan_signature','changed-plan')]:
        modified = copy.deepcopy(shots)
        modified[0]['video'][field] = value
        with pytest.raises(ValueError, match='锁定'):
            store.update(pid, {'shots':modified})
        assert database_dump() == before


def test_legacy_publication_requires_proven_input_alias_and_retains_intervention_fence(project):
    from factory import team_control
    pid = project['id']
    token = team_ledger.begin_unit(pid, 'video', 'S01', input_version='legacy-source')
    team_ledger.finish_unit(token)
    with pytest.raises(team_control.TeamInterventionPending, match='输入版本'):
        team_control.assert_publishable(pid, 'video', 'S01', 'stable-source')
    with pytest.raises(team_control.TeamInterventionPending, match='输入版本'):
        team_control.assert_publishable(pid, 'video', 'S01', 'stable-source', compatible_input_versions=['unrelated-source'])
    team_control.assert_publishable(pid, 'video', 'S01', 'stable-source', compatible_input_versions=['legacy-source'])
    team_control.submit(pid, intervention(project))
    with pytest.raises(team_control.TeamInterventionPending, match='新要求'):
        team_control.assert_publishable(pid, 'video', 'S01', 'stable-source', compatible_input_versions=['legacy-source'])


def test_pause_resume_and_completion_acknowledgement_control_real_admission(project):
    from factory import team_control

    pid = project["id"]
    team_control.task_action(pid, "video:S01", action(project, "video:S01", "pause"))
    with pytest.raises(team_control.TeamInterventionPending):
        team_control.admit(pid, "video", "S01")
    team_control.admit(pid, "video", "S02")
    team_control.task_action(pid, "video:S01", action(project, "video:S01", "resume"))
    team_control.admit(pid, "video", "S01")
    team_control.task_action(pid, "video:S01", action(project, "video:S01", "hold_after"))
    token = team_ledger.begin_unit(pid, "video", "S01", input_version="input-v1")
    team_ledger.finish_unit(token)
    with pytest.raises(team_control.TeamInterventionPending):
        team_control.admit(pid, "video", "S02")
    team_control.task_action(pid, "video:S01", action(project, "video:S01", "release"))
    team_control.admit(pid, "video", "S02")


def test_pause_during_active_call_remains_effective_after_call_finishes(project):
    from factory import team_control

    pid = project["id"]
    token = team_ledger.begin_unit(pid, "video", "S01", input_version="active-input")
    team_control.task_action(pid, "video:S01", action(project, "video:S01", "pause"))
    assert team_ledger.finish_unit(token, artifact=f"production/factory-{pid}/finished.mp4")
    assert task(pid, "video:S01")["artifacts"][0]["path"].endswith("/finished.mp4")
    with pytest.raises(team_control.TeamInterventionPending):
        team_control.admit(pid, "video", "S01")
    team_control.task_action(pid, "video:S01", action(project, "video:S01", "resume"))
    team_control.admit(pid, "video", "S01")


def test_old_v2_enables_only_at_idle_boundary_while_v1_remains_observation_only(project, http_client):
    from factory import team_control

    old = store.create({"brief": "Historical project migration with no fabricated execution.", "workflow_version": 1})
    payload = {"action": "enable", "request_id": "offline-enable", "expected_control_revision": 0}
    before = database_dump()
    assert http_client("POST", f"/api/factory/projects/{old['id']}/team/actions", payload)[0] == 409
    assert database_dump() == before
    with store.connection() as db:
        snapshot = store._read(db, old["id"])
        snapshot.update(workflow_version=2, status="producing", episode=copy.deepcopy(project["episode"]))
        db.execute("UPDATE projects SET snapshot=? WHERE id=?", (store.encoded(snapshot), old["id"]))
    before = database_dump()
    with pytest.raises(ValueError, match="安全边界"):
        team_control.enable(old["id"], payload)
    assert database_dump() == before
    store.update(old["id"], {"status": "paused"})
    status, value = http_client("POST", f"/api/factory/projects/{old['id']}/team/actions", payload)
    assert status in {200, 202}
    assert team_ledger.team_snapshot(old["id"])["enabled"] is True
    rows = team_ledger.task_page(old["id"], limit=100)["tasks"]
    assert any(row["id"] == "image:S01" for row in rows)
    assert all(row["attempts"] == [] for row in rows)


def test_instruction_http_pagination_and_task_actions_survive_restart(project, http_client):
    from factory import team_control

    pid = project["id"]
    payload = intervention(project, operation="retry")
    status, submitted = http_client("POST", f"/api/factory/projects/{pid}/interventions", payload)
    assert status in {200, 202} and submitted["instruction"]["status"] == "queued"
    store.initialize()
    status, page = http_client("GET", f"/api/factory/projects/{pid}/interventions?offset=0&limit=1")
    assert status == 200 and page["total"] == 1 and page["limit"] == 1
    assert page["instructions"][0]["id"] == submitted["instruction"]["id"]
    before = database_dump()
    assert http_client("GET", f"/api/factory/projects/{pid}/interventions?limit=0")[0] == 400
    assert database_dump() == before
    status, value = http_client("POST", f"/api/factory/projects/{pid}/tasks/video:S02/actions",
                               action(project, "video:S02", "pause"))
    assert status in {200, 202} and value["task"]["status"] == "paused"


def test_enabling_existing_v2_imports_real_artifact_without_fabricating_attempt(project):
    from factory import team_control

    old = store.create({"brief": "An archived v2 project containing one real synthetic artifact.", "workflow_version": 1})
    snapshot = copy.deepcopy(project)
    snapshot.update(id=old["id"], slug=old["slug"], workflow_version=2, revision=old["revision"], status="paused")
    snapshot.pop("team_control_version", None)
    with store.connection() as db:
        db.execute("UPDATE projects SET snapshot=? WHERE id=?", (store.encoded(snapshot), old["id"]))
    receipt = fixture_candidate(snapshot, "take_001")
    store.update(old["id"], {"shots": [
        {"id": "S01", "image": receipt},
        {"id": "S02", "image": {"path": f"production/{old['slug']}/missing.png", "sha256": "0" * 64}},
    ]})
    assert not team_ledger.team_snapshot(old["id"])["enabled"]
    assert team_ledger.task_page(old["id"])["tasks"] == []
    team_control.enable(old["id"], {"action": "enable", "request_id": "import-existing-result",
                                    "expected_control_revision": 0})
    imported = task(old["id"], "image:S01")
    assert imported["status"] == "reused" and imported["attempts"] == []
    assert imported["model"] is None and imported["started_at"] is None
    assert imported["artifacts"][0]["sha256"] == receipt["sha256"]
    assert imported["artifacts"][0]["artifact_url"] == "/media/" + receipt["path"]
    assert task(old["id"], "image:S02")["status"] == "pending"


def test_video_retry_keeps_explicitly_preserved_frame_and_voice_versions(project):
    from factory import team_control

    pid = project["id"]
    episode = copy.deepcopy(project["episode"])
    episode["shots"][0]["dialogue"] = [{"id": "S01_D01", "role": "lead", "text": "This is your book."}]
    path = core.ROOT / "production" / project["slug"] / "audio/line.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x00\x00" * 2400)
    receipt = {"id": "S01_D01", "path": str(path.relative_to(core.ROOT)), "sha256": core.sha256(path),
               "source_signature": "unchanged-voice-input"}
    store.update(pid, {"episode": episode, "script": copy.deepcopy(episode),
                       "assets": {"audio": {"lines": [receipt]}}})
    image = fixture_candidate(store.get(pid, False), "take_001")
    for kind, scope, artifact in (("image", "S01", image), ("speech", "S01_D01", receipt)):
        token = team_ledger.begin_unit(pid, kind, scope, input_version=kind + "-original")
        team_ledger.finish_unit(token, artifact=artifact)
    prior_frame = task(pid, "image:S01")
    prior_voice = task(pid, "speech:S01_D01")
    payload = intervention(project, operation="retry", preserve=["image:S01", "speech:S01_D01"])
    assert team_control.preview(pid, payload)["conflicts"] == []
    team_control.submit(pid, payload)
    assert team_control.process_pending(pid)["status"] == "applied"
    assert task(pid, "image:S01") == prior_frame
    assert task(pid, "speech:S01_D01") == prior_voice
    assert store.get(pid, False)["assets"]["audio"]["lines"] == [receipt]
    assert core.sha256(path) == receipt["sha256"]


def test_unresolved_supplier_task_is_preserved_and_prevents_silent_repost(project):
    from factory import team_control
    from test_factory_job_api import intent

    pid = project["id"]
    old = team_ledger.begin_unit(pid, "video", "S01", input_version="submitted-source")
    intent(project)
    with store.connection() as db:
        before = [dict(row) for row in db.execute("SELECT * FROM cloud_tasks WHERE project_id=?", (pid,))]
    episode = copy.deepcopy(store.get(pid, False)["episode"])
    team_control.submit(pid, intervention(project, operation="retry"))
    result = team_control.process_pending(pid)
    assert result["status"] == "draining"
    with store.connection() as db:
        assert [dict(row) for row in db.execute("SELECT * FROM cloud_tasks WHERE project_id=?", (pid,))] == before
    assert store.get(pid, False)["episode"] == episode
    team_control.admit(pid, "stage", "production")
    team_control.admit(pid, "video", "S01")
    for kind, scope in (("video", "S02"), ("image", "S01"), ("speech", "S01_D01")):
        with pytest.raises(team_control.TeamInterventionPending):
            team_control.admit(pid, kind, scope)
    resumed = team_ledger.begin_unit(pid, "video", "S01", input_version="submitted-source")
    assert not team_ledger.finish_unit(old, artifact=f"production/factory-{pid}/old.mp4")
    assert not team_ledger.finish_unit(resumed, artifact=f"production/factory-{pid}/drained.mp4",
                                       details={"remote_task_id": "existing-task"})
    assert task(pid, "video:S01")["artifacts"] == []
    assert all(row["status"] == "superseded" for row in task(pid, "video:S01")["attempts"])
    with pytest.raises(team_control.TeamInterventionPending):
        team_control.assert_publishable(pid, "video", "S01", "submitted-source")
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM cloud_tasks WHERE project_id=?", (pid,)).fetchone()[0] == 1
        db.execute("UPDATE cloud_tasks SET status='downloaded',phase='quality_review' WHERE project_id=?", (pid,))
    assert team_control.process_pending(pid)["status"] == "applied"
    current = store.get(pid, False)
    assert current["episode"]["shots"][0]["render_revisions"]["video"] == 1
    assert current["episode"]["shots"][0]["motion_prompt"] == episode["shots"][0]["motion_prompt"]
    with store.connection() as db:
        rows = [dict(row) for row in db.execute("SELECT * FROM cloud_tasks WHERE project_id=?", (pid,))]
    assert len(rows) == 1 and rows[0]["task_id"] == "existing-task" and rows[0]["status"] == "downloaded"


def test_concurrent_operator_commands_serialize_one_arrangement_and_retry_idempotently(project):
    from factory import team_control

    pid = project["id"]
    baseline = task(pid, "video:S01")["generation"]
    payloads = [intervention(project, request_id=f"concurrent-command-{index}") for index in range(6)]

    def submit(payload):
        try:
            return team_control.submit(pid, payload)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=6) as workers:
        results = list(workers.map(submit, payloads))
    winners = [value for value in results if value is not None]
    assert len(winners) == 1 and team_control.instructions(pid)["total"] == 1
    assert task(pid, "video:S01")["generation"] == baseline + 1
    accepted = next(payload for payload, result in zip(payloads, results) if result is not None)
    with ThreadPoolExecutor(max_workers=4) as workers:
        replays = list(workers.map(submit, [accepted] * 4))
    assert all(row["instruction"]["id"] == winners[0]["instruction"]["id"] for row in replays)
    assert team_control.instructions(pid)["total"] == 1
    assert task(pid, "video:S01")["generation"] == baseline + 1


@pytest.mark.parametrize("state", ["paused", "awaiting_final"])
def test_real_worker_claims_idle_intervention_and_applies_once_before_workflow(project, monkeypatch, state):
    from factory import team_control, worker

    pid = project["id"]
    store.update(pid, {"status": state, "pause_requested": False})
    submitted = team_control.submit(pid, intervention(project, operation="retry"))
    job = store.claim_job(pid)
    assert job is not None and job["kind"] == "team_instruction"

    def no_model_work(*args):
        pytest.fail("A command job must apply its arrangement before starting a workflow")

    monkeypatch.setattr(worker, "advance", no_model_work)
    worker.handle(job)
    current = store.get(pid, False)
    assert current["status"] == "queued"
    assert current["episode"]["shots"][0]["render_revisions"]["video"] == 1
    assert team_control.instructions(pid)["instructions"][0]["id"] == submitted["instruction"]["id"]
    assert team_control.instructions(pid)["instructions"][0]["status"] == "applied"
    with store.connection() as db:
        saved = dict(db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone())
    assert saved["status"] == "completed" and saved["lease"] == job["lease"]
    before = database_dump()
    worker.handle(job)
    assert database_dump() == before
    workflow = store.claim_job(pid)
    assert workflow is not None and workflow["kind"] == "workflow"


def test_real_worker_releases_active_lease_before_applying_in_call_intervention(project, monkeypatch):
    from factory import team_control, worker

    pid = project["id"]
    store.update(pid, {"status": "producing", "pause_requested": False})
    job = store.claim_job(pid)
    assert job is not None and job["kind"] == "workflow"
    apply_lease_states = []
    process = team_control.process_pending

    def observe_application(target):
        with store.connection() as db:
            active = db.execute("SELECT COUNT(*) FROM jobs WHERE project_id=? AND status='running'", (target,)).fetchone()[0]
        apply_lease_states.append(active)
        return process(target)

    def vendor_call_then_boundary(target):
        token = team_ledger.begin_unit(target, "video", "S01", input_version="original-call")
        team_control.submit(target, intervention(project, operation="retry"))
        assert not team_ledger.finish_unit(token, artifact=f"production/factory-{target}/old-call.mp4",
                                           details={"remote_task_id": "completed-vendor-task"})
        team_control.admit(target, "video", "S02")

    monkeypatch.setattr(team_control, "process_pending", observe_application)
    monkeypatch.setattr(worker, "advance", vendor_call_then_boundary)
    worker.handle(job)
    assert apply_lease_states == [0]
    assert store.get(pid, False)["status"] == "queued"
    assert store.get(pid, False)["episode"]["shots"][0]["render_revisions"]["video"] == 1
    assert team_control.instructions(pid)["instructions"][0]["status"] == "applied"
    assert task(pid, "video:S01")["attempts"][0]["details"]["remote_task_id"] == "completed-vendor-task"
    queued = store.claim_job(pid)
    assert queued is not None and queued["kind"] == "workflow"


def test_real_worker_drains_deferred_supplier_then_applies_after_normal_graph_return(project, monkeypatch):
    from factory import team_control, worker
    from factory.remote_tasks import ProductionDeferred
    from test_factory_job_api import intent

    pid = project["id"]
    store.update(pid, {"status": "producing", "pause_requested": False})
    job = store.claim_job(pid)
    calls = []
    outcomes = []
    process = team_control.process_pending

    def observe_application(target):
        result = process(target)
        outcomes.append(result["status"] if result else None)
        return result

    def resume_existing_supplier(target):
        calls.append(target)
        if len(calls) == 1:
            intent(project)
            team_control.submit(target, intervention(project, operation="retry"))
            raise ProductionDeferred(time.time() + 10, details={"task_id": "existing-task"})
        team_control.admit(target, "video", "S01")
        with store.connection() as db:
            db.execute("UPDATE cloud_tasks SET status='downloaded',phase='quality_review' WHERE project_id=?", (target,))

    monkeypatch.setattr(team_control, "process_pending", observe_application)
    monkeypatch.setattr(worker, "advance", resume_existing_supplier)
    worker.handle(job)
    assert outcomes == ["draining"]
    assert not store.get(pid, False)["episode"]["shots"][0].get("render_revisions")
    assert store.claim_job(pid) is None
    with store.connection() as db:
        saved = dict(db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone())
        assert saved["status"] == "queued" and saved["phase"] == "remote_poll"
        db.execute("UPDATE jobs SET next_poll_at=0 WHERE id=?", (job["id"],))
    resumed = store.claim_job(pid)
    assert resumed["id"] == job["id"] and resumed["lease"] > job["lease"]
    worker.handle(resumed)
    assert outcomes == ["draining", "applied"]
    assert store.get(pid, False)["episode"]["shots"][0]["render_revisions"]["video"] == 1
    with store.connection() as db:
        rows = [dict(row) for row in db.execute("SELECT * FROM cloud_tasks WHERE project_id=?", (pid,))]
    assert len(rows) == 1 and rows[0]["task_id"] == "existing-task" and rows[0]["status"] == "downloaded"


def test_stage_requirements_reach_transport_and_force_retry_changes_validated_cache(project, monkeypatch):
    from factory import team_control, schemas, llm
    from factory.execution_context import current_execution

    pid = project["id"]
    calls = []

    def transport(**request):
        calls.append(copy.deepcopy(request))
        return {"result": "validated answer " + str(len(calls))}

    monkeypatch.setattr(llm, "json_call", transport)
    schema = schemas.object_schema({"result": schemas.text_schema(1, 40)})
    original_payload = {"approved_story": "Two fictional adults return a book."}

    def call():
        context = current_execution.set({"project_id": pid, "stage": "screenplay"})
        try:
            return schemas.cached_json_call(store.get(pid, False), {"llm": {"model": "offline-fixture"}},
                "screenwriter", "team-stage-cache", "Write the requested script.", original_payload, schema)
        finally:
            current_execution.reset(context)

    assert call() == call() and len(calls) == 1
    assert "producer_task_requirements" not in calls[0]["payload"]
    for index in (1, 2):
        command = intervention(project, task_id="stage:screenplay", operation="retry",
                               instruction="Keep the story but make the conflict clearer.",
                               request_id="same-stage-force-retry-" + str(index))
        team_control.submit(pid, command)
        assert team_control.process_pending(pid)["status"] == "applied"
        value = call()
        assert value == call() and len(calls) == index + 1
        delivered = calls[-1]["payload"]["producer_task_requirements"]
        assert delivered["stage"] == "screenplay" and delivered["requirements"] == command["instruction"]
        assert delivered["revision"] == index
    assert original_payload == {"approved_story": "Two fictional adults return a book."}


@pytest.mark.parametrize("profiled", [False, True], ids=["default", "explicit-profile"])
def test_registered_writer_prompt_accepts_intervention_and_retains_real_receipt_metadata(project, monkeypatch, profiled):
    from factory import schemas
    from factory.execution_context import current_execution
    from factory.production_profiles import normalize
    from factory.prompt_registry import load_prompt
    from test_factory_role_routing import routing_config, mock_transport, SCHEMA

    calls, _ = mock_transport(monkeypatch)
    writer = load_prompt("screenwriter")
    current = copy.deepcopy(project)
    if profiled:
        current["production_profile"] = normalize({"preset": "compact6"})
        current["duration_seconds"] = current["production_profile"]["duration_seconds"]
    payload = {"project": {}}

    def call():
        context = current_execution.set({"project_id": project["id"], "stage": "screenplay"})
        try:
            return schemas.cached_json_call(current, routing_config(), "screenwriter", "registered-team-writer",
                                            writer, payload, SCHEMA)
        finally:
            current_execution.reset(context)

    assert call() == {"ok": True}
    assert call() == {"ok": True} and len(calls) == 1
    current["team_stage_requirements"] = {"screenplay": "Keep the cast and clarify why the book is returned."}
    current["team_stage_revisions"] = {"screenplay": 1}
    assert call() == {"ok": True}
    assert call() == {"ok": True} and len(calls) == 2
    assert current["team_stage_requirements"]["screenplay"] in str(calls[-1]["request"]["messages"])
    folder = schemas.planning_dir(current)
    receipts = [json.loads(path.read_text()) for path in folder.glob("llm/calls/*/receipt.json")]
    assert len(receipts) == 2 and all(row["status"] == "succeeded" for row in receipts)
    assert all(row["prompt"]["status"] == "registered" and row["prompt"]["version"] == writer.spec.version for row in receipts)
    after = next(row for row in receipts if "producer_intervention" in row["prompt"]["transforms"])
    before = next(row for row in receipts if "producer_intervention" not in row["prompt"]["transforms"])
    assert after["prompt"]["template_sha256"] == before["prompt"]["template_sha256"] == writer.spec.template_sha256
    assert after["prompt"]["input_sha256"] != before["prompt"]["input_sha256"]
    assert after["prompt"]["rendered_sha256"] != before["prompt"]["rendered_sha256"]
    assert ("production_profile" in after["prompt"]["transforms"]) is profiled
    cache_records = [json.loads(path.read_text()) for path in folder.glob("cache/registered-team-writer-*.json")]
    assert len(cache_records) == 2 and all(row["prompt"]["status"] == "registered" for row in cache_records)
    assert payload == {"project": {}}


def test_same_text_cloud_retake_changes_source_and_intent_only_when_explicit(project):
    from factory import cloud_media

    profile = {"provider": "dashscope_tts", "model": "qwen3-tts-flash", "default_voice": "Cherry", "language": "Chinese"}
    engine = SimpleNamespace(config={"audio_connection": profile})
    line = {"id": "S01_D01", "role": "lead", "text": "This is your book."}
    base = cloud_media.speech_sources(engine, [line], {})[line["id"]]
    zero = cloud_media.speech_sources(engine, [{**line, "team_revision": 0}], {})[line["id"]]
    first = cloud_media.speech_sources(engine, [{**line, "team_revision": 1}], {})[line["id"]]
    again = cloud_media.speech_sources(engine, [{**line, "team_revision": 1}], {})[line["id"]]
    second = cloud_media.speech_sources(engine, [{**line, "team_revision": 2}], {})[line["id"]]
    assert base == zero and first == again
    assert len({base["key"], first["key"], second["key"]}) == 3
    body = {"model": profile["model"], "input": {"text": line["text"], "voice": "Cherry", "language_type": "Chinese"}}

    def intent(source, revision=None):
        record = {"source_signature": source["key"], "take": "tts_" + source["key"][:24], "attempt": 1}
        if revision is not None:
            record["team_revision"] = revision
        return cloud_media.make_line(project["id"], line["id"], record, profile, body, "audio")

    initial = intent(base)
    retake = intent(first, 1)
    assert initial == intent(zero, 0) and retake == intent(again, 1)
    assert initial["intent_id"] != retake["intent_id"] and initial["request_signature"] != retake["request_signature"]
    assert body["input"] == {"text": line["text"], "voice": "Cherry", "language_type": "Chinese"}


def test_conflicting_instruction_resume_persists_generation_before_planning_crash(project, monkeypatch):
    from factory import team_control

    pid = project["id"]
    receipt = fixture_candidate(project, "take_001")
    token = team_ledger.begin_unit(pid, "image", "S01", input_version="locked-original")
    team_ledger.finish_unit(token, artifact=receipt)
    team_control.task_action(pid, "image:S01", action(project, "image:S01", "lock"))
    saved = team_control.submit(pid, intervention(project, task_id="image:S01", operation="retry"))
    assert saved["instruction"]["status"] == "needs_decision"
    team_control.process_pending(pid)
    team_control.task_action(pid, "image:S01", action(project, "image:S01", "unlock"))
    real_plan = team_control._direct_plan

    def crash(*args):
        raise SystemExit("synthetic process interruption after persisted planning claim")

    monkeypatch.setattr(team_control, "_direct_plan", crash)
    with pytest.raises(SystemExit):
        team_control.process_pending(pid)
    version = task(pid, "image:S01")["generation"]
    pending = team_control.instructions(pid)["instructions"][0]
    assert pending["status"] == "planning"
    assert pending["accepted_generations"]["image:S01"] == version
    monkeypatch.setattr(team_control, "_direct_plan", real_plan)
    assert team_control.process_pending(pid)["status"] == "applied"
    assert task(pid, "image:S01")["generation"] == version
