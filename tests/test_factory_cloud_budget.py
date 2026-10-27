"""Paid POST boundaries: all media/HTTP are temporary fixtures, never live APIs."""
import copy
import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from factory import cloud_budget as costs, config, minimax_video as video, store
from factory.providers import ProviderError
from factory.scheduler import ProductionNeedsDecision
from factory.video_providers import normalize_video_profile
from studio import core


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path / "db")
    monkeypatch.setattr(store, "DATABASE", tmp_path / "db/studio.sqlite3")
    store.initialize()
    p = store.create({"brief": "成年人物在车站重逢的原创小故事", "format": "animation"})
    episode = {"cast": {"lead": "Adult actor"}, "shots": [
        {"id": "S01", "seconds": 4, "prompt": "A person waits", "motion_prompt": "A person turns"}]}
    p = store.update(p["id"], {"episode": episode, "script": episode})
    profile = {"profile": "cloud", **normalize_video_profile({
        "provider": "minimax", "enabled": True, "model": "MiniMax-Hailuo-2.3",
        "base_url": "http://127.0.0.1:18888/v1", "network_mode": "loopback", "api_key_env": "FIXTURE_KEY",
        "pricing": {"unit_cost_micros": 1000000, "currency": "CNY", "source": "test estimate, not a tariff"}})}
    frame = tmp_path / "frame.png"
    Image.new("RGB", (384, 512), "gray").save(frame)
    return p, profile, frame


def line_for(p, profile, frame, shot="S01", take="take_001"):
    return costs.request_line(p["id"], shot, {"take": take, "attempt": 1, "source_signature": "shot-v1",
                                            "prompt": "An adult person turns"}, core.sha256(frame), profile, 4)


def request_for(line):
    return {"request_fingerprint": line["request_signature"], "source_signature": line["source_signature"]}


def authorize(p, profile, batch, **limits):
    payload = {"batch_id": batch["id"], "expected_revision": store.get(p["id"])["revision"],
               "max_submissions": len(batch["scope"]["lines"]),
               "max_estimated_cost_micros": batch["scope"]["estimated_total_micros"], **limits}
    costs.authorize(p["id"], payload, profile)
    return payload


@pytest.fixture
def fake_video(monkeypatch):
    calls = []
    def request(profile, route, **kwargs):
        calls.append(route)
        if route == "/video_generation":
            return {"task_id": "9007199254740993"}
        if route == "/query/video_generation":
            return {"task_id": "9007199254740993", "status": "Success", "file_id": "9007199254740995"}
        if route == "/files/retrieve":
            return {"file": {"file_id": "9007199254740995", "download_url": "https://example.invalid/mock"}}
        raise AssertionError("Unexpected route")
    monkeypatch.setattr(video, "video_request", request)
    monkeypatch.setattr(video, "download_video", lambda profile, url, dest, **kw: dest.write_bytes(b"fixture-video"))
    return calls


def test_adapter_has_no_implicit_authorization(isolated, fake_video, tmp_path):
    p, profile, frame = isolated
    with pytest.raises(video.VideoPending) as exc:
        video.generate_video(profile, "An adult person turns", frame, tmp_path / "take", source_signature="shot-v1")
    assert exc.value.code == "cloud_authorization_required"
    assert not fake_video and not (tmp_path / "take/remote_task.json").exists()


def test_quote_without_authorization_never_reserves(isolated):
    p, profile, frame = isolated
    line = line_for(*isolated)
    with pytest.raises(ProductionNeedsDecision) as exc:
        costs.ensure_batch(p, profile, [line])
    assert exc.value.reason == "cloud_authorization_required"
    summary = costs.summary(p["id"])
    assert summary["quote"]["shot_count"] == summary["quote"]["estimated_submissions"] == 1
    assert summary["quote"]["estimated_total_micros"] == 1000000
    assert summary["quote"]["connection"]["model"] == profile["model"]
    assert summary["billing_hard_limit"] is False and summary["tasks"] == []


def test_unknown_price_needs_separate_confirmation(isolated):
    p, profile, frame = isolated
    profile.pop("pricing")
    line = line_for(p, profile, frame)
    batch = costs.prepare_batch(p, profile, [line])
    assert batch["scope"]["estimated_total_micros"] is None
    with pytest.raises(ValueError, match="未知费用"):
        authorize(p, profile, batch)
    authorize(p, profile, batch, confirm_unknown_cost=True)
    costs.reserve(p["id"], batch["id"], line, profile, request_for(line))
    assert costs.summary(p["id"])["tasks"][0]["estimated_cost_micros"] is None


@pytest.mark.parametrize("value", [[], False, {"unit_cost_micros": True}, {"unit_cost_micros": -1},
                                   {"unit_cost_micros": float("nan")}, {"currency": "cny"}, {"billing_hard_limit": True}])
def test_price_validation_never_claims_confirmed_billing(value):
    with pytest.raises(ValueError):
        costs.pricing(value)


def test_authorization_cas_idempotency_and_cumulative_caps(isolated):
    p, profile, frame = isolated
    lines = [line_for(p, profile, frame, shot) for shot in ("S01", "S02")]
    batch = costs.prepare_batch(p, profile, lines)
    payload = authorize(p, profile, batch, max_submissions=1)
    first = store.get(p["id"])
    assert costs.authorize(p["id"], payload, profile)["revision"] == first["revision"]
    assert len(costs.summary(p["id"])["authorizations"]) == 1
    with pytest.raises(ValueError, match="更新"):
        costs.authorize(p["id"], {**payload, "max_submissions": 2}, profile)
    with pytest.raises(ProductionNeedsDecision, match="授权不足"):
        costs.ensure_batch(p, profile, lines)
    costs.reserve(p["id"], batch["id"], lines[0], profile, request_for(lines[0]))
    with pytest.raises(ProductionNeedsDecision, match="超出"):
        costs.reserve(p["id"], batch["id"], lines[1], profile, request_for(lines[1]))
    authorize(p, profile, batch, max_submissions=2)
    assert len(costs.summary(p["id"])["tasks"]) == 1
    costs.reserve(p["id"], batch["id"], lines[1], profile, request_for(lines[1]))
    assert len(costs.summary(p["id"])["tasks"]) == 2


def test_insufficient_estimate_and_confirmed_overage_stop_new_posts(isolated):
    p, profile, frame = isolated
    lines = [line_for(p, profile, frame, shot) for shot in ("S01", "S02")]
    batch = costs.prepare_batch(p, profile, lines)
    authorize(p, profile, batch, max_estimated_cost_micros=999999)
    with pytest.raises(ProductionNeedsDecision):
        costs.reserve(p["id"], batch["id"], lines[0], profile, request_for(lines[0]))
    authorize(p, profile, batch)
    costs.reserve(p["id"], batch["id"], lines[0], profile, request_for(lines[0]))
    costs.confirm_cost(p["id"], {"intent_id": lines[0]["intent_id"], "amount_micros": 3000000,
                                "currency": "CNY", "evidence": "fixture invoice, no real charge"})
    with pytest.raises(ProductionNeedsDecision):
        costs.reserve(p["id"], batch["id"], lines[1], profile, request_for(lines[1]))
    assert costs.summary(p["id"])["totals"]["CNY"]["confirmed_cost_micros"] == 3000000


@pytest.mark.parametrize("change", ["production_revision", "shot", "model", "pricing"])
def test_changed_production_or_model_invalidates_authorization(isolated, change):
    p, profile, frame = isolated
    line = line_for(*isolated)
    batch = costs.prepare_batch(p, profile, [line])
    authorize(p, profile, batch)
    if change == "production_revision":
        store.update(p["id"], {"production_revision": 2})
    elif change == "shot":
        episode = copy.deepcopy(p["episode"])
        episode["shots"][0]["prompt"] = "A different shot"
        store.update(p["id"], {"episode": episode})
    elif change == "model":
        profile["model"] = "different-model"
    else:
        profile["pricing"]["unit_cost_micros"] = 2000000
    with pytest.raises(ProductionNeedsDecision) as exc:
        costs.reserve(p["id"], batch["id"], line, profile, request_for(line))
    assert exc.value.reason == "cloud_parameters_changed"
    assert costs.summary(p["id"])["tasks"] == []


def test_unknown_intent_cannot_be_bypassed_with_a_new_take(isolated):
    p, profile, frame = isolated
    original = line_for(*isolated)
    batch = costs.prepare_batch(p, profile, [original])
    authorize(p, profile, batch)
    costs.reserve(p["id"], batch["id"], original, profile, request_for(original))
    replacement = line_for(p, profile, frame, take="take_002")
    next_batch = costs.prepare_batch(p, profile, [replacement])
    authorize(p, profile, next_batch)
    with pytest.raises(ProductionNeedsDecision) as exc:
        costs.reserve(p["id"], next_batch["id"], replacement, profile, request_for(replacement))
    assert exc.value.reason == "cloud_unresolved_prior_task"


def test_first_frame_payload_and_authorized_hash_use_the_same_bytes(isolated):
    p, profile, frame = isolated
    previous = core.sha256(frame)
    Image.new("RGB", (384, 512), "white").save(frame)
    with pytest.raises(ValueError, match="changed before authorization"):
        video._first_frame(frame, previous)


def test_two_threads_cannot_reserve_one_intent_twice(isolated):
    p, profile, frame = isolated
    line = line_for(*isolated)
    batch = costs.prepare_batch(p, profile, [line])
    authorize(p, profile, batch)
    def reserve():
        try:
            return costs.reserve(p["id"], batch["id"], line, profile, request_for(line))["intent_id"]
        except ProductionNeedsDecision:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: reserve(), range(16)))
    assert sum(value is not None for value in results) == 1
    assert len(costs.summary(p["id"])["tasks"]) == 1


def test_two_processes_share_the_same_budget_cap(isolated):
    p, profile, frame = isolated
    lines = [line_for(p, profile, frame, shot) for shot in ("S01", "S02")]
    batch = costs.prepare_batch(p, profile, lines)
    authorize(p, profile, batch, max_submissions=1)
    ctx = multiprocessing.get_context("fork")
    gate, results = ctx.Event(), ctx.Queue()
    def consume(line):
        gate.wait(5)
        try:
            costs.reserve(p["id"], batch["id"], line, profile, request_for(line))
            results.put(True)
        except ProductionNeedsDecision:
            results.put(False)
    children = [ctx.Process(target=consume, args=(line,)) for line in lines]
    for child in children:
        child.start()
    gate.set()
    for child in children:
        child.join(10)
        assert child.exitcode == 0
    assert sum(results.get(timeout=2) for _ in children) == 1
    assert len(costs.summary(p["id"])["tasks"]) == 1


@pytest.mark.parametrize("crash", ["after_reservation", "after_post"])
def test_process_death_never_reposts_an_uncertain_intent(isolated, tmp_path, crash):
    p, profile, frame = isolated
    line = line_for(*isolated)
    batch = costs.prepare_batch(p, profile, [line])
    authorize(p, profile, batch)
    folder, posts = tmp_path / "take", tmp_path / "posts.txt"
    def reserve(request):
        result = costs.reserve(p["id"], batch["id"], line, profile, request)
        if crash == "after_reservation":
            os._exit(17)
        return result
    def effect(*args, **kwargs):
        with posts.open("a") as handle:
            handle.write("POST\n")
            handle.flush()
            os.fsync(handle.fileno())
        os._exit(18)
    def child_run():
        video.video_request = effect
        video.generate_video(profile, line["prompt"], frame, folder, source_signature="shot-v1", before_submit=reserve)
    child = multiprocessing.get_context("fork").Process(target=child_run)
    child.start()
    child.join(10)
    assert child.exitcode == (17 if crash == "after_reservation" else 18)
    assert len(costs.summary(p["id"])["tasks"]) == 1
    with pytest.raises((video.VideoPending, ProductionNeedsDecision)):
        video.generate_video(profile, line["prompt"], frame, folder, source_signature="shot-v1",
                             before_submit=lambda request: costs.reserve(p["id"], batch["id"], line, profile, request))
    assert (posts.read_text().count("POST") if posts.exists() else 0) == (0 if crash == "after_reservation" else 1)


def test_task_id_recovers_ledger_after_journal_event_crash(isolated, fake_video, tmp_path):
    p, profile, frame = isolated
    line = line_for(*isolated)
    batch = costs.prepare_batch(p, profile, [line])
    authorize(p, profile, batch)
    folder = tmp_path / "take"
    def crash_event(task):
        if task["status"] == "submitted":
            raise RuntimeError("fixture crash after journal, before DB observation")
    with pytest.raises(RuntimeError, match="fixture crash"):
        video.generate_video(profile, line["prompt"], frame, folder, source_signature="shot-v1", event=crash_event,
                             before_submit=lambda request: costs.reserve(p["id"], batch["id"], line, profile, request))
    assert costs.summary(p["id"])["tasks"][0]["task_id"] is None
    costs.reconcile_task(p["id"], line, core.read_json(folder / "remote_task.json"), profile)
    raw, task = video.generate_video(profile, line["prompt"], frame, folder, source_signature="shot-v1",
                                    event=lambda task: costs.reconcile_task(p["id"], line, task, profile))
    assert raw.read_bytes() == b"fixture-video"
    assert fake_video.count("/video_generation") == 1
    assert costs.summary(p["id"])["tasks"][0]["file_id"] == "9007199254740995"
    assert video.generate_video(profile, line["prompt"], frame, folder, source_signature="shot-v1")[0] == raw


def test_legacy_known_task_can_download_without_new_authorization(isolated, fake_video, tmp_path):
    p, profile, frame = isolated
    line = line_for(*isolated)
    folder = tmp_path / "legacy"
    task = {"connection": {k: v for k, v in profile.items() if k != "profile"},
            "request_fingerprint": line["request_signature"], "source_signature": "shot-v1",
            "task_id": "9007199254740993", "file_id": "9007199254740995", "status": "generated"}
    core.write_json(folder / "remote_task.json", task)
    costs.reconcile_task(p["id"], line, task, profile)
    video.generate_video(profile, line["prompt"], frame, folder, source_signature="shot-v1")
    ledger = costs.summary(p["id"])
    assert "/video_generation" not in fake_video
    assert ledger["tasks"][0]["estimated_cost_micros"] is None and ledger["authorizations"] == []


def test_pause_resume_and_migration_preserve_all_ledger_rows(isolated):
    p, profile, frame = isolated
    line = line_for(*isolated)
    batch = costs.prepare_batch(p, profile, [line])
    authorize(p, profile, batch)
    costs.reserve(p["id"], batch["id"], line, profile, request_for(line))
    before = costs.summary(p["id"])
    store.action(p["id"], {"action": "pause"})
    store.initialize()
    store.action(p["id"], {"action": "resume"})
    after = costs.summary(p["id"])
    assert after["tasks"] == before["tasks"] and after["authorizations"] == before["authorizations"]
    assert costs.prepare_batch(store.get(p["id"]), profile, [line])["id"] == batch["id"]


def test_frozen_graph_profile_cannot_ignore_changed_saved_settings(isolated, tmp_path, monkeypatch):
    p, profile, frame = isolated
    settings = {"video_profiles": {"cloud": {k: v for k, v in profile.items() if k != "profile"}},
                "video_roles": {"video_generator": "cloud"}}
    path = tmp_path / "config.json"
    core.write_json(path, settings)
    monkeypatch.setattr(config, "config_read_path", lambda: path)
    costs.validate_live_profile(profile)
    settings["video_profiles"]["cloud"]["model"] = "new-model"
    core.write_json(path, settings)
    with pytest.raises(ProductionNeedsDecision, match="连接发生变化"):
        costs.validate_live_profile(profile)


def test_real_production_gate_and_unknown_result_keep_one_take(isolated, tmp_path, monkeypatch):
    from factory import model_capabilities, production
    p, profile, frame = isolated
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setattr(production, "ROOT", tmp_path)
    monkeypatch.setattr(costs, "validate_live_profile", lambda profile: None)
    posts = []
    def uncertain(*args, **kwargs):
        posts.append("POST")
        raise ProviderError("network", "fixture response lost")
    monkeypatch.setattr(video, "video_request", uncertain)
    settings = {"video_connection": profile, "image_width": 384, "image_height": 512,
                "video_width": 384, "video_height": 512, "semantic_enabled": False}
    # This isolated renderer selects MiniMax explicitly, rather than the
    # installation's disabled default connection. Keep the actual request guard.
    model_capabilities.adopt(p, settings)
    p = store.update(p["id"], {"model_capability_snapshot": p["model_capability_snapshot"],
                              "model_capability_history": p.get("model_capability_history", [])})
    assert model_capabilities.active(p, settings, "video")["provider"] == "minimax"
    def run_once():
        run = production._Run(store.get(p["id"]), settings, lambda patch: store.update(p["id"], patch), None)
        target = run._target("S01", run.folder / "renders/S01", "An adult person turns", [frame],
                             kind="video", seconds=4, shot=run.shots[0], review=False)
        run._render_targets([target], "diffusion_video")
    with pytest.raises(ProductionNeedsDecision) as exc:
        run_once()
    assert exc.value.reason == "cloud_authorization_required" and posts == []
    quote = costs.summary(p["id"])["quote"]
    authorize(p, profile, {"id": quote["id"], "scope": quote})
    for _ in range(2):
        with pytest.raises(ProductionNeedsDecision):
            run_once()
    assert posts == ["POST"]
    assert len(list((tmp_path / "production" / p["slug"] / "renders/S01").glob("take_*"))) == 1
    assert costs.summary(p["id"])["tasks"][0]["status"] == "submission_unknown"
