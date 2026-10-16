"""Advice uses durable worker jobs and never substitutes for independent review."""
import copy
import json

import pytest

from factory import attention_actions, operator_console, review_advice as advice, schemas, store, worker


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path / "data")
    monkeypatch.setattr(store, "DATABASE", tmp_path / "data/studio.sqlite3")
    monkeypatch.setattr(schemas, "ROOT", tmp_path)
    store.initialize()
    config = {"llm_roles": {advice.ROLE: "boss"}}
    monkeypatch.setattr(advice, "load_config", lambda: config)
    monkeypatch.setattr(advice, "resolve_llm", lambda *_: {
        "profile": "boss", "model": "fixture-manager", "configured": True, "enabled": True})
    def forbid(*a, **k):
        raise AssertionError("Unexpected model call")
    monkeypatch.setattr(advice, "cached_json_call", forbid)
    return tmp_path, config


def blocked(**patch):
    p = store.create({"brief": "两名成年人澄清一场误会的原创短剧"})
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='completed' WHERE project_id=?", (p["id"],))
    return store.update(p["id"], {
        "status": "blocked", "stage": "story_review", "needs_decision": {
            "type": "review", "kind": "creative_review", "message": "因果审核未通过",
            "issues": [{"code": "causality", "severity": "blocking", "description": "结尾缺乏行动依据", "suggestion": "补齐前置行动"}]},
        "proposals": [{"id": "A", "title": "误会", "synopsis": "当前故事全稿", "eligible": False}], **patch})


def proposal():
    return {"recommendation": "repair", "rationale": "只需补齐结尾前的行动证据。",
            "feedback": "补齐主角行动依据，保留角色及未涉及设定；修改后重新独立审核。",
            "risks": ["不可省略独立审核"], "decisions": []}


def request(p):
    return store.action(p["id"], {"action": "request_review_advice", "expected_revision": p["revision"]})


def test_request_only_queues_and_duplicate_is_idempotent(env):
    p = blocked()
    queued = request(p)
    duplicate = request(p)
    assert queued == duplicate
    assert queued["status"] == "blocked" and queued["needs_decision"] == p["needs_decision"]
    assert queued["review_advice"]["status"] == "queued"
    with store.connection() as db:
        rows = db.execute("SELECT * FROM jobs WHERE status='queued'").fetchall()
    assert len(rows) == 1 and rows[0]["kind"] == "review_advice"
    view = operator_console.workspace(p["id"])
    assert view["operator"]["job_running"] is True
    assert view["operator"]["attention"]["review_advice"]["current"] is True
    assert not next(v for v in view["operator"]["actions"] if v["id"] == "provide_feedback")["enabled"]
    with pytest.raises(ValueError, match="收尾"):
        store.action(p["id"], {"action": "provide_feedback", "expected_revision": queued["revision"], "feedback": "修正因果"})


def test_worker_finishes_advice_without_advancing_or_touching_needs(env, monkeypatch):
    p = blocked()
    request(p)
    called = []
    monkeypatch.setattr(advice, "cached_json_call", lambda *a, **k: called.append(a) or proposal())
    monkeypatch.setattr(worker, "advance", lambda *_: pytest.fail("Advice must not advance"))
    job = store.claim_job()
    assert job["kind"] == "review_advice"
    worker.handle(job)
    result = store.get(p["id"], False)
    assert len(called) == 1
    assert called[0][2] == advice.ROLE and called[0][4] == advice.SYSTEM
    assert called[0][5]["project"]["needs_decision"] == p["needs_decision"]
    assert result["review_advice"]["proposal"] == proposal()
    assert result["status"] == "blocked" and result["needs_decision"] == p["needs_decision"]
    assert result["budget"] == p["budget"] and not result.get("answers")
    assert store.queue_summary()["running"] == 0
    assert store.claim_job() is None
    assert advice.snapshot(result)["current"] is True
    worker.handle(job)
    assert len(called) == 1


def test_real_cached_contract_saves_call_receipt(env, monkeypatch):
    from factory import config, llm
    root, _ = env
    p = blocked(screenplay_draft={"title": "完整草稿", "shots": [{"id": "S01", "action": "未被截断的行为"}]})
    request(p)
    monkeypatch.setattr(advice, "cached_json_call", schemas.cached_json_call)
    monkeypatch.setattr(config, "llm_identity", lambda *_: {"model": "fixture-manager", "profile": "boss"})
    seen = []
    def fake_wire(**kw):
        seen.append(kw)
        assert kw["schema"] == advice.SCHEMA
        assert kw["payload"]["project"]["draft"] == p["screenplay_draft"]
        folder = kw["output_dir"] / "calls/real-contract-fixture"
        folder.mkdir(parents=True)
        (folder / "receipt.json").write_text(json.dumps({"status": "succeeded", "role": kw["role"], "model": "fixture-manager"}))
        return proposal()
    monkeypatch.setattr(llm, "json_call", fake_wire)
    worker.handle(store.claim_job())
    assert len(seen) == 1
    receipts = list(root.rglob("receipt.json"))
    assert len(receipts) == 1 and json.loads(receipts[0].read_text())["status"] == "succeeded"
    assert list(root.rglob("cache/review-advice-*.json"))


def test_unknown_request_after_restart_is_not_reposted(env, monkeypatch):
    p = blocked()
    request(p)
    job = store.claim_job()
    def interrupted(*a, **k):
        raise KeyboardInterrupt("simulate worker death after submit")
    monkeypatch.setattr(advice, "cached_json_call", interrupted)
    with pytest.raises(KeyboardInterrupt):
        advice.run(p["id"], job)
    pending = store.get(p["id"], False)["review_advice"]
    assert pending["status"] == "requested"
    store.recover_jobs()
    recovered = store.claim_job()
    monkeypatch.setattr(advice, "cached_json_call", lambda *a, **k: pytest.fail("Unknown request must not replay"))
    worker.handle(recovered)
    assert store.queue_summary()["running"] == 0
    saved = store.get(p["id"], False)
    record = saved["review_advice"]
    assert record["status"] == "manual" and record["reason_code"] == "previous_request_unresolved"
    assert record["model_source"] == "request" and "proposal" not in record
    assert record["run_id"] == pending["run_id"] and record["input_signature"] == pending["input_signature"]
    assert not advice.retry_unconfigured(record)
    assert operator_console.workspace(p["id"])["operator"]["job_running"] is False


@pytest.mark.parametrize("change", [{"pause_requested": True, "status": "paused"},
    {"brief": "由出品人明确修改的新目标"}, {"script_generation": 2}])
def test_late_advice_cannot_override_human_or_new_version(env, monkeypatch, change):
    p = blocked()
    request(p)
    def delayed(*a, **k):
        store.update(p["id"], change)
        return proposal()
    monkeypatch.setattr(advice, "cached_json_call", delayed)
    worker.handle(store.claim_job())
    saved = store.get(p["id"], False)
    assert saved["review_advice"]["status"] == "superseded"
    for key, value in change.items():
        assert saved[key] == value
    assert saved["needs_decision"] == p["needs_decision"]
    assert "proposal" not in advice.snapshot(saved)


def test_lost_lease_cannot_publish_advice(env, monkeypatch):
    p = blocked()
    request(p)
    job = store.claim_job()
    def delayed(*a, **k):
        store.recover_jobs()
        return proposal()
    monkeypatch.setattr(advice, "cached_json_call", delayed)
    advice.run(p["id"], job)
    assert store.get(p["id"], False)["review_advice"]["status"] == "superseded"


def test_missing_model_and_invalid_output_have_explicit_manual_fallback(env, monkeypatch):
    p = blocked()
    request(p)
    job = store.claim_job()
    result = advice.run(p["id"], job, config={})
    assert result["status"] == "manual" and result["reason_code"] == "supervisor_not_configured"
    store.finish_job(job["id"], lease=job["lease"])
    store.update(p["id"], {"script_feedback": "新的用户约束"})
    request(store.get(p["id"], False))
    monkeypatch.setattr(advice, "cached_json_call", lambda *a, **k: {**proposal(), "feedback": "x" * 3001})
    worker.handle(store.claim_job())
    record = store.get(p["id"], False)["review_advice"]
    assert record["status"] == "manual" and record["reason_code"] == "model_failure"
    assert "proposal" not in record


@pytest.mark.parametrize("need,status", [({"type": "budget"}, "blocked"), ({"type": "runtime"}, "blocked"),
    ({"type": "production"}, "blocked"), ({"type": "review", "kind": "source_rights"}, "blocked"),
    ({"type": "review", "code": "cloud_fees"}, "blocked"), ({"type": "review"}, "awaiting_final"),
    ({"type": "review"}, "approved")])
def test_budget_rights_runtime_and_final_are_not_editorial_advice(env, need, status):
    p = blocked(needs_decision=need, status=status)
    assert not advice.eligible(p)
    assert "request_review_advice" not in [a["id"] for a in attention_actions.descriptors(p)]
    with pytest.raises(ValueError, match="创作审核"):
        request(p)


def test_stale_request_rejected_and_adoption_uses_normal_feedback_recovery(env, monkeypatch):
    p = blocked()
    with pytest.raises(ValueError, match="更新"):
        store.action(p["id"], {"action": "request_review_advice", "expected_revision": p["revision"] - 1})
    with pytest.raises(ValueError, match="expected_revision"):
        store.action(p["id"], {"action": "request_review_advice"})
    request(p)
    monkeypatch.setattr(advice, "cached_json_call", lambda *a, **k: proposal())
    worker.handle(store.claim_job())
    current = store.get(p["id"], False)
    result = store.action(p["id"], {"action": "provide_feedback", "expected_revision": current["revision"],
                                   "feedback": current["review_advice"]["proposal"]["feedback"]})
    assert result["proposal_generation"] == p["proposal_generation"] + 1
    assert result["story_feedback"] == proposal()["feedback"]
    assert result["status"] == "queued" and result.get("workflow_restart_from") == "proposals"
    assert not advice.snapshot(result)["current"]
    assert store.claim_job()["kind"] == "workflow"


def test_public_projection_drops_unexpected_private_fields(env):
    p = blocked()
    p["review_advice"] = {**advice.queued_record(p), "private_key": "never-project", "proposal": {"bad": True}}
    result = advice.snapshot(p)
    assert "private_key" not in result and "proposal" not in result


def test_configuring_previously_missing_role_allows_explicit_retry(env, monkeypatch):
    p = blocked()
    request(p)
    job = store.claim_job()
    advice.run(p["id"], job, config={})
    store.finish_job(job["id"], lease=job["lease"])
    before = store.get(p["id"], False)
    assert advice.retry_unconfigured(before["review_advice"])
    available = next(a for a in attention_actions.descriptors(before) if a["id"] == "request_review_advice")
    assert available["enabled"] is True
    queued = request(before)
    assert queued["review_advice"]["status"] == "queued"
    assert queued["review_advice"]["run_id"] != before["review_advice"]["run_id"]
    monkeypatch.setattr(advice, "cached_json_call", lambda *a, **k: proposal())
    worker.handle(store.claim_job())
    assert store.get(p["id"], False)["review_advice"]["status"] == "ready"


def test_complete_feedback_priority_and_current_draft_are_not_duplicated(env):
    p = blocked(story_feedback="保留故事大方向", script_feedback="最新具体要求", revision_request={"feedback": "最终局部修改"},
                screenplay_draft={"title": "当前稿"}, script={"title": "旧批准稿"}, episode={"title": "旧生成稿"})
    context = advice.context_for(p)["project"]
    assert context["draft"] == {"title": "当前稿"}
    assert "script" not in context and "episode" not in context and "screenplay_draft" not in context
    assert [i["content"] for i in context["producer_feedback"]["instructions"]] == ["最终局部修改", "最新具体要求", "保留故事大方向"]


def test_additive_job_kind_accepts_old_and_new_backup_schemas(env):
    from factory.backup_sqlite import schema
    with store.connection() as db:
        assert "jobs" in [r["name"] for r in db.execute("SELECT name FROM sqlite_master")]
        assert schema(db, "studio.sqlite3")["migrations"] == ["remote_phases_v1"]
        db.execute("ALTER TABLE jobs DROP COLUMN kind")
        assert schema(db, "studio.sqlite3")["migrations"] == ["remote_phases_v1"]
    store.initialize()
    with store.connection() as db:
        assert "kind" in {r["name"] for r in db.execute("PRAGMA table_info(jobs)")}


@pytest.mark.parametrize("command", ["retry", "resume", "delegate_control", "revise"])
def test_queued_advice_blocks_other_mutations_even_from_direct_api(env, command):
    p = blocked()
    queued = request(p)
    with pytest.raises(ValueError, match="AI 分析任务"):
        store.action(p["id"], {"action": command, "expected_revision": queued["revision"], "feedback": "修正因果"})
    assert store.get(p["id"], False)["status"] == "blocked"


def test_exhausted_creative_workflow_automatically_requests_advice_once(env, monkeypatch):
    from factory.workflows import ReviewBlocked
    p = store.create({"brief": "成年人通过主动行动澄清误会"})
    job = store.claim_job()
    called = []
    def blocked_advance(pid):
        raise ReviewBlocked("自动返修用尽", {"kind": "creative_review", "issues": [
            {"description": "结尾动机需要证据", "severity": "blocking"}]})
    monkeypatch.setattr(worker, "advance", blocked_advance)
    monkeypatch.setattr(advice, "cached_json_call", lambda *a, **k: called.append(a) or proposal())
    worker.handle(job)
    saved = store.get(p["id"], False)
    assert saved["status"] == "blocked" and saved["review_advice"]["status"] == "ready"
    assert len(called) == 1 and saved["needs_decision"]["issues"][0]["description"] == "结尾动机需要证据"
    assert store.queue_summary()["running"] == 0 and not store.claim_job()
    worker.handle(job)
    assert len(called) == 1


@pytest.mark.parametrize("kind", ["source_rights", "visual_review"])
def test_non_editorial_reviewblocked_never_calls_advisor(env, monkeypatch, kind):
    from factory.workflows import ReviewBlocked
    p = store.create({"brief": "不应将权限问题解释为剧作修改"})
    def blocked_advance(pid):
        raise ReviewBlocked("需要出品人确认", {"kind": kind})
    monkeypatch.setattr(worker, "advance", blocked_advance)
    worker.handle(store.claim_job())
    assert "review_advice" not in store.get(p["id"], False)


def resumable_script(**patch):
    return blocked(stage="script_review", selected_story="A", creative_round=2,
                   script_review_complete_round=[1, 2], screenplay_complete_round=[1, 2],
                   script_review_route="repair", screenplay_draft={"title": "第二轮草稿"},
                   draft_review={"passed": False, "issues": [{"description": "补足行动依据"}]},
                   script_review={"passed": False},
                   creative_review_rounds=[{"round": 1, "review": {"passed": False}}, {"round": 2, "review": {"passed": False}}],
                   needs_decision={"type": "script_review", "kind": "script_review", "issues": [{"description": "补足行动依据"}]}, **patch)


def continue_review(p):
    return store.action(p["id"], {"action": "continue_auto_review", "expected_revision": p["revision"]})


def test_old_review_continues_same_checkpoint_without_resetting_versions(env):
    p = resumable_script()
    before = copy.deepcopy(p)
    assert advice.can_continue_auto_review(p)
    assert any(a["id"] == "continue_auto_review" and a["inputs"] == [] for a in attention_actions.descriptors(p))
    current = continue_review(p)
    for key in ("script_generation", "creative_round", "creative_review_rounds", "screenplay_draft", "draft_review",
                "script_review_complete_round", "screenplay_complete_round", "script_review_route", "budget"):
        assert current[key] == before[key]
    assert current["status"] == "queued" and "needs_decision" not in current
    assert not current.get("workflow_restart_from")
    assert continue_review(p) == current  # Delayed duplicate does not add a second job.
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE project_id=? AND status='queued'", (p["id"],)).fetchone()[0] == 1
    assert store.claim_job()["kind"] == "workflow"


@pytest.mark.parametrize("patch", [{"workflow_version": 1}, {"creative_round": 4},
    {"script_review_complete_round": [2, 2]}, {"screenplay_complete_round": [1, 1]},
    {"script_generation": 2}, {"draft_review": {"passed": True}}, {"selected_style": "ink"},
    {"revision_request": {"feedback": "修改成片"}}, {"delivery": {"video_url": "/media/clip.mp4"}},
    {"workflow_restart_from": "screenplay"}, {"needs_decision": {"type": "budget"}}])
def test_auto_continue_rejects_ineligible_or_exhausted_review_without_queueing(env, patch):
    p = resumable_script()
    p = store.update(p["id"], patch)
    assert not advice.can_continue_auto_review(p)
    with pytest.raises(ValueError, match="可继续"):
        continue_review(p)
    assert store.queue_summary()["queued"] == 0


def test_auto_continue_respects_current_config_limit_and_revision(env, monkeypatch):
    p = resumable_script()
    monkeypatch.setattr(advice, "load_config", lambda: {"creative": {"max_review_rounds": 2}})
    with pytest.raises(ValueError, match="上限"):
        continue_review(p)
    assert store.get(p["id"], False)["creative_round"] == 2
    monkeypatch.setattr(advice, "load_config", lambda: {"creative": {"max_review_rounds": 4}})
    with pytest.raises(ValueError, match="更新"):
        store.action(p["id"], {"action": "continue_auto_review", "expected_revision": p["revision"] - 1})
    with pytest.raises(ValueError, match="expected_revision"):
        store.action(p["id"], {"action": "continue_auto_review"})
    assert store.queue_summary()["queued"] == 0


def test_auto_continue_rejects_advice_and_workflow_queue(env):
    p = resumable_script()
    queued = request(p)
    with pytest.raises(ValueError, match="收尾"):
        continue_review(queued)
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='cancelled' WHERE status='queued'")
        store._enqueue(db, p["id"])
    with pytest.raises(ValueError, match="任务排队"):
        continue_review(store.get(p["id"], False))


def test_auto_continue_rejects_unsettled_cloud_and_gpu_intents(env, monkeypatch):
    root, _ = env
    monkeypatch.setattr(advice, "ROOT", root)
    p = resumable_script()
    with store.connection() as db:
        db.execute("INSERT INTO cloud_tasks(intent_id,project_id,shot_id,attempt,take,provider,model,request_signature,status,currency,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("pending", p["id"], "S01", 1, "take1", "fixture", "fixture", "signature", "submission_unknown", "CNY", 1, 1))
    with pytest.raises(ValueError, match="云任务"):
        continue_review(p)
    with store.connection() as db:
        db.execute("UPDATE cloud_tasks SET status='failed'")
    folder = root / "production" / p["slug"] / "take1"
    folder.mkdir(parents=True)
    (folder / "submission.json").write_text(json.dumps({"gpu_service_job_id": "a" * 64, "state": "running"}))
    with pytest.raises(ValueError, match="GPU"):
        continue_review(p)
    assert store.queue_summary()["queued"] == 0
    (folder / "remote_result.json").write_text(json.dumps({"id": "a" * 64, "status": "completed"}))
    assert continue_review(p)["status"] == "queued"


@pytest.mark.parametrize("decisions", [["repair"], ["human_decision"], [], ["retry?"], ["human_decision?"], ["repair??"]])
def test_human_decision_requires_substantive_questions_and_preserves_manual_recovery(env, monkeypatch, decisions):
    from jsonschema import ValidationError
    invalid = {**proposal(), "recommendation": "human_decision", "decisions": decisions}
    with pytest.raises((ValueError, ValidationError)):
        advice.validate_proposal(invalid)
    p = blocked()
    request(p)
    calls = []
    monkeypatch.setattr(advice, "cached_json_call", lambda *a, **k: calls.append(True) or invalid)
    worker.handle(store.claim_job())
    saved = store.get(p["id"], False)
    assert saved["review_advice"]["status"] == "manual"
    assert saved["review_advice"]["reason_code"] == "model_failure"
    assert "proposal" not in saved["review_advice"]
    assert saved["needs_decision"] == p["needs_decision"] and saved["status"] == "blocked"
    assert store.queue_summary()["running"] == 0 and len(calls) == 1
    # Returning to the same intent cannot cause a second model call.
    assert request(saved)["review_advice"]["status"] == "manual"
    assert len(calls) == 1


def test_advisor_distinguishes_authorized_repair_from_real_unresolved_choice(env, monkeypatch):
    from jsonschema import ValidationError
    valid = {**proposal(), "recommendation": "human_decision",
             "rationale": "用户同时要求保留角色关系并改为首次见面，两个目标冲突。",
             "decisions": ["是否保留现有角色关系，还是按你的新目标调整为陌生人？"]}
    advice.validate_proposal(valid)
    advice.validate_proposal(proposal())
    with pytest.raises((ValueError, ValidationError)):
        advice.validate_proposal({**valid, "recommendation": "repair"})
    p = blocked()
    request(p)
    monkeypatch.setattr(advice, "cached_json_call", lambda *a, **k: valid)
    worker.handle(store.claim_job())
    saved = store.get(p["id"], False)
    assert saved["review_advice"]["status"] == "ready"
    assert saved["review_advice"]["proposal"] == valid
    assert saved["status"] == "blocked" and saved["needs_decision"] == p["needs_decision"]


def test_old_token_only_advice_is_not_exposed_as_a_valid_public_proposal(env):
    p = blocked()
    p["review_advice"] = {**advice.queued_record(p), "status": "ready", "proposal": {
        **proposal(), "recommendation": "human_decision", "decisions": ["repair"]}}
    assert "proposal" not in advice.snapshot(p)
