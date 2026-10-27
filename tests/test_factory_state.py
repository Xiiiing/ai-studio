"""Recovery and decision invariants. Expensive model adapters are isolated here."""
from __future__ import annotations

import copy
import pytest
from factory import store, workflows as wf


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(store,"DATA",tmp_path/"runtime")
    monkeypatch.setattr(store,"DATABASE",tmp_path/"runtime/studio.sqlite3")
    monkeypatch.setattr(wf,"DATA",tmp_path/"runtime")
    monkeypatch.setattr(wf,"CHECKPOINTS",tmp_path/"runtime/checkpoints.sqlite3")
    monkeypatch.setattr(wf,"ROOT",tmp_path)
    store.initialize()
    return tmp_path


def payload(**extra):
    return {"brief":"原创成年角色的温暖悬疑小故事，保留明确结局。",
            "workflow_version":1,"team_mode":"guided",
            "duration_seconds":60,"budget":{"gpu_minutes":30,"max_attempts":2},**extra}


def test_creation_replay_and_request_conflict(isolated):
    p=store.create(payload(request_id="same-request"))
    assert store.create(payload(request_id="same-request"))["id"]==p["id"]
    with pytest.raises(ValueError):
        store.create(payload(request_id="same-request",title="different"))
    assert len(store.list_projects())==1
    assert store.queue_summary()["queued"]==1
    with pytest.raises(ValueError):
        store.create(payload(budget={"gpu_minutes":float("nan")}))


def test_decision_is_validated_and_replay_safe(isolated):
    p=store.create(payload())
    gate=store.gate(p["id"],"story",[{"id":"A"},{"id":"B"}],"选故事",1)
    with pytest.raises(ValueError):
        store.decide(p["id"],{"type":"style","choice":"A","decision_id":gate["id"]})
    with pytest.raises(ValueError):
        store.decide(p["id"],{"type":"story","choice":"unknown","decision_id":gate["id"]})
    request={"type":"story","choice":"A","decision_id":gate["id"]}
    store.decide(p["id"],request)
    store.decide(p["id"],request)
    assert len(store.get(p["id"])["answers"])==1
    with pytest.raises(ValueError):
        store.decide(p["id"],{**request,"choice":"B"})


def test_decision_while_previous_job_finishes_keeps_wakeup(isolated):
    p=store.create(payload())
    old_job=store.claim_job()
    gate=store.gate(p["id"],"story",[{"id":"A"}],"选故事",1)
    store.decide(p["id"],{"type":"story","choice":"A","decision_id":gate["id"]})
    assert store.queue_summary()["queued"]==1
    store.finish_job(old_job["id"])
    assert store.claim_job()["project_id"]==p["id"]


def test_failed_review_does_not_write_completion_marker(isolated):
    p=store.create(payload())
    with pytest.raises(wf.ReviewBlocked):
        wf._apply(p["id"],{"proposals_complete_generation":1,"proposals":[{"id":"A"}],
                            "needs_decision":{"type":"review","message":"因果不成立"}})
    q=store.get(p["id"])
    assert "proposals_complete_generation" not in q
    assert q["needs_decision"]["message"]=="因果不成立"


def test_restart_recovery_and_budget_authorization(isolated):
    p=store.create(payload())
    job=store.claim_job()
    assert store.recover_jobs()==1
    assert store.claim_job()["id"]==job["id"]
    store.finish_job(job["id"],"budget")
    store.update(p["id"],{"status":"blocked","needs_decision":{"type":"budget"}})
    with pytest.raises(ValueError):
        store.action(p["id"],{"action":"resume"})
    with pytest.raises(ValueError):
        store.action(p["id"],{"action":"resume","gpu_minutes":20})
    store.action(p["id"],{"action":"resume","gpu_minutes":45})
    assert store.get(p["id"])["budget"]["gpu_minutes"]==45


def test_three_gates_survive_connection_reopen_without_duplicate_production(isolated,monkeypatch):
    calls={"research":0,"proposals":0,"script":0,"visual":0,"production":0}
    def research(s):
        calls["research"]+=1
        return {"route":"continue"}
    def proposals(s):
        calls["proposals"]+=1
        store.update(s["project_id"],{"proposals":[{"id":k,"title":"故事"+k} for k in "ABC"]})
        return {"route":"continue"}
    def screenplay(s):
        calls["script"]+=1
        script={"title":"已选故事","cast":{"female":"Adult woman","male":"Adult man"},"shots":[]}
        store.update(s["project_id"],{"script":script,"episode":script,"styles":[
            {"id":"style-"+str(i),"name":"画风"+str(i),"prompt":"ink illustration"} for i in range(1,4)]})
        return {"route":"continue"}
    def visual(s):
        calls["visual"]+=1
        return {"route":"continue"}
    def production(s):
        calls["production"]+=1
        store.update(s["project_id"],{"delivery":{"video_url":"/media/test-fixture-only.mp4"}})
        return {"route":"continue"}
    for name,func in [("research",research),("proposals",proposals),("screenplay",screenplay),
                      ("visual_development",visual),("production",production)]:
        monkeypatch.setattr(wf,name,func)
    p=store.create(payload(is_validation=True))
    pid=p["id"]
    wf.advance(pid)
    assert store.get(pid)["status"]=="awaiting_story"
    wf.advance(pid)
    assert calls["proposals"]==1
    gate=store.get(pid)["decisions"][-1]
    store.decide(pid,{"type":"story","choice":"B","decision_id":gate["id"]})
    wf.advance(pid)
    assert store.get(pid)["status"]=="awaiting_style"
    gate=store.get(pid)["decisions"][-1]
    store.decide(pid,{"type":"style","choice":"style-2","decision_id":gate["id"]})
    wf.advance(pid)
    assert store.get(pid)["status"]=="awaiting_final"
    wf.advance(pid)
    assert calls["production"]==1
    gate=store.get(pid)["decisions"][-1]
    store.decide(pid,{"type":"final","choice":"approve","decision_id":gate["id"]})
    wf.advance(pid)
    wf.advance(pid)
    assert store.get(pid)["status"]=="approved"
    assert store.get(pid)["accepted_by"]=="validation_agent"
    assert calls=={"research":1,"proposals":1,"script":1,"visual":1,"production":1}


def test_pause_prevents_starting_next_graph_node(isolated):
    p=store.create(payload())
    store.action(p["id"],{"action":"pause"})
    with pytest.raises(wf.ProjectPaused):
        wf.advance(p["id"])

def test_pause_is_not_cleared_when_gate_is_created(isolated):
    p=store.create(payload())
    store.action(p["id"],{"action":"pause"})
    store.gate(p["id"],"story",[{"id":"A"}],"故事选择",1)
    current=store.get(p["id"])
    assert current["pause_requested"] and current["status"]=="paused"
    assert store.claim_job() is None


def test_stale_queued_wakeup_cannot_bypass_new_block(isolated):
    p=store.create(payload())
    running=store.claim_job()
    gate=store.gate(p["id"],"story",[{"id":"A"}],"选故事",1)
    store.decide(p["id"],{"type":"story","choice":"A","decision_id":gate["id"]})
    store.update(p["id"],{"status":"blocked","needs_decision":{"type":"production"}})
    store.finish_job(running["id"],"failed after wakeup")
    assert store.claim_job() is None
    assert wf.advance(p["id"])["blocked"]
    assert store.get(p["id"])["status"]=="blocked"


def test_wall_budget_extension_does_not_rewrite_script(isolated):
    p=store.create(payload())
    store.update(p["id"],{"status":"blocked","selected_story":"B",
        "production":{"elapsed_seconds":181*60},"needs_decision":{"type":"budget","exhausted_limits":["wall"]}})
    with pytest.raises(ValueError):
        store.action(p["id"],{"action":"resume","gpu_minutes":60})
    with pytest.raises(ValueError):
        store.action(p["id"],{"action":"resume","wall_minutes":180.5})
    store.action(p["id"],{"action":"resume","wall_minutes":240})
    q=store.get(p["id"])
    assert q["budget"]["wall_minutes"]==240 and q["budget"]["gpu_minutes"]==30
    assert q["script_generation"]==1


def test_repropose_recovers_after_database_commit_before_graph_checkpoint(isolated,monkeypatch):
    count={"proposals":0,"crashed":False}
    monkeypatch.setattr(wf,"research",lambda s:{"route":"continue"})
    def proposals(s):
        count["proposals"]+=1
        p=store.get(s["project_id"],False)
        store.update(p["id"],{"proposals":[{"id":k,"title":"故事"+k,"eligible":k!="A"} for k in "ABC"]})
        return {"story_generation":p["proposal_generation"]}
    monkeypatch.setattr(wf,"proposals",proposals)
    p=store.create(payload())
    wf.advance(p["id"])
    gate=store.get(p["id"])["decisions"][-1]
    assert [o["id"] for o in gate["options"]]==["B","C"]
    store.decide(p["id"],{"type":"story","choice":"__repropose__","decision_id":gate["id"]})
    original=store.commit_decision
    def commit_then_crash(*args,**kwargs):
        result=original(*args,**kwargs)
        if not count["crashed"]:
            count["crashed"]=True
            raise RuntimeError("simulated process loss after database commit")
        return result
    monkeypatch.setattr(store,"commit_decision",commit_then_crash)
    with pytest.raises(RuntimeError):
        wf.advance(p["id"])
    assert store.get(p["id"])["proposal_generation"]==2
    wf.advance(p["id"])
    q=store.get(p["id"])
    assert q["status"]=="awaiting_story" and q["decisions"][-1]["id"].endswith(":story:2")
    assert q["proposal_generation"]==2 and count["proposals"]==2


def test_final_revision_effect_survives_replayed_apply(isolated,monkeypatch):
    p=store.create(payload())
    gate=store.gate(p["id"],"final",[{"id":"revise"}],"审片",1)
    store.decide(p["id"],{"type":"final","choice":"revise","feedback":"只改第三镜道具",
                           "decision_id":gate["id"]})
    answer=store.answer_for(p["id"],gate["id"])
    state={"project_id":p["id"],"final_answer":answer}
    wf.apply_final(state)
    wf.apply_final(state)
    q=store.get(p["id"])
    assert q["production_revision"]==2 and q["script_generation"]==2
    assert q["revision_request"]["feedback"]=="只改第三镜道具"


@pytest.fixture
def script_retry_project(isolated):
    p = store.create(payload())
    episode = {"shots": [{"id": "S%02d" % i, "prompt": "已保存画面%d" % i}
                         for i in range(1, 16)]}
    store.update(p["id"], {"selected_story": "A", "episode": episode, "status": "blocked",
                          "needs_decision": {"type": "script_review", "message": "修改后半段"}})
    return store.get(p["id"])


def test_retry_explicit_shot_scope_is_deduplicated_and_replay_safe(script_retry_project):
    p = script_retry_project
    text = "修改S07、S08的道具交接，保留S01至S06"
    request = {"action": "retry", "feedback": text, "shot_ids": ["S07", "S08", "S07"]}
    store.action(p["id"], request)
    q = store.get(p["id"])
    assert q["script_feedback"] == {"notes": text, "shot_ids": ["S07", "S08"]}
    assert q["script_generation"] == p["script_generation"] + 1
    assert q["episode"] == p["episode"] and q["status"] == "queued"
    store.action(p["id"], request)
    assert store.get(p["id"])["script_generation"] == q["script_generation"]


@pytest.mark.parametrize("extra", [
    {"shot_ids": "S07"},
    {"shot_ids": []},
    {"shot_ids": None},
    {"shot_ids": ["S99"]},
    {"shot_ids": ["S07", 8]},
    {"shot_ids": ["S07"], "feedback": "保留S07不变"},
    {"shot_ids": ["S07"], "feedback": "修改S08"},
    {"shot_ids": ["S07"], "feedback": {}},
    {"feedback": "修改S01至S07，保留S01至S06"},
    {"feedback": "仅修改S99"},
])
def test_retry_rejects_invalid_shot_scope_without_mutation(script_retry_project, extra):
    p = script_retry_project
    queue_before = store.queue_summary()
    with pytest.raises(ValueError):
        store.action(p["id"], {"action": "retry", "feedback": "重做指定镜头", **extra})
    assert store.get(p["id"]) == p
    assert store.queue_summary() == queue_before


@pytest.mark.parametrize("text", [
    "仅修改并重排 S07、S08、S09、S10、S11、S12、S13、S14、S15，保留 S01 至 S06 的内容、人物和美术设定。",
    "让整集情绪更克制，结局更清楚。",
])
def test_retry_keeps_legacy_local_and_global_feedback_text(script_retry_project, text):
    store.action(script_retry_project["id"], {"action": "retry", "feedback": text})
    q = store.get(script_retry_project["id"])
    assert q["script_feedback"] == text
    assert q["episode"] == script_retry_project["episode"]


def test_edit_feedback_retry_shares_explicit_scope_validation(script_retry_project):
    p = script_retry_project
    store.update(p["id"], {"selected_style": "style-one", "revision_request": {"feedback": "原修改说明"},
                          "needs_decision": {"type": "edit_feedback"}})
    store.action(p["id"], {"action": "retry", "feedback": "替换S09道具", "shot_ids": ["S09", "S09"]})
    q = store.get(p["id"])
    assert q["revision_request"]["feedback"] == {"notes": "替换S09道具", "shot_ids": ["S09"]}
    assert q["script_generation"] == p["script_generation"]


LOCAL_REVISION_IDS = ["S03", "S05", "S09", "S11", "S13"]


@pytest.fixture
def production_block_project(script_retry_project, isolated):
    p = script_retry_project
    episode = copy.deepcopy(p["episode"])
    for shot in episode["shots"]:
        shot.update(seconds=4, dialogue=[{"id": shot["id"] + "_L1", "text": "原对白"}],
                    continuity="原连续性", reference_group="female")
    ledger = {"gpu_seconds": 123.5, "elapsed_seconds": 456.0,
              "completed": 11, "history_marker": "cumulative-production-ledger"}
    store.update(p["id"], {
        "selected_style": "style-two", "script": episode, "episode": episode,
        # Historical recovery records carry no observed cross-shot evidence.
        "creative_quality_contract": None, "model_capability_snapshot": None,
        "script_review": {"passed": True},
        "shots": [{"id": s["id"], "status": "failed" if s["id"] == "S05" else "passed"}
                  for s in episode["shots"]],
        "production": ledger, "production_revision": 4, "script_generation": 6,
        "budget": {**p["budget"], "max_attempts": 3},
        "status": "blocked", "stage": "production", "pause_requested": True,
        "needs_decision": {"type": "production", "message": "retry limit",
                          "details": {"asset": "S05", "stage": "image", "attempts": 3}},
    })
    for index in range(1, 4):
        take = isolated / "production" / p["slug"] / "assets/shots/S05" / ("take_%03d" % index)
        take.mkdir(parents=True)
        (take / "attempt.json").write_text('{"source_signature":"approved-old-input","attempt":%d}' % index)
        (take / "image.png").write_bytes(("old-image-%d" % index).encode())
    return store.get(p["id"])


def _local_revision_request(project, **extra):
    return {"action": "revise", "expected_revision": project["revision"],
            "shot_ids": LOCAL_REVISION_IDS, "feedback": "简化选中镜头的构图，保留原对白和时长。",
            **extra}


def _old_take_bytes(root, project):
    folder = root / "production" / project["slug"] / "assets"
    return {str(path.relative_to(folder)): path.read_bytes()
            for path in folder.rglob("*") if path.is_file()}


def test_production_block_revision_preserves_inputs_budget_takes_and_replays(production_block_project, isolated):
    p = production_block_project
    original_files = _old_take_bytes(isolated, p)
    request = _local_revision_request(p, shot_ids=LOCAL_REVISION_IDS + ["S03"])
    accepted = store.action(p["id"], request)
    q = store.get(p["id"])
    assert q["status"] == "queued" and q["stage"] == "revision" and not q["pause_requested"]
    assert q["production_revision"] == p["production_revision"] + 1
    assert q["script_generation"] == p["script_generation"] + 1
    assert accepted["revision"] == p["revision"] + 1
    assert q["revision_request"]["feedback"] == {"notes": request["feedback"], "shot_ids": LOCAL_REVISION_IDS}
    assert q["revision_request"]["source_revision"] == p["revision"]
    assert q["revision_request"]["source_production_revision"] == p["production_revision"]
    assert q["revision_request"]["blocked_reason"] == p["needs_decision"]
    assert q["revision_request"]["choice"] == "revise"
    assert q["revision_request"]["origin"] == "production_block"
    for key in ["script", "episode", "shots", "budget", "production", "selected_story", "selected_style", "decisions"]:
        assert q[key] == p[key]
    assert _old_take_bytes(isolated, p) == original_files
    queue = store.queue_summary()
    store.action(p["id"], request)
    assert store.get(p["id"]) == q
    assert store.queue_summary() == queue
    with pytest.raises(ValueError, match="已经受理"):
        store.action(p["id"], {**request, "feedback": "不同的局部修改意见"})
    assert store.get(p["id"]) == q


@pytest.mark.parametrize("change", [
    {"expected_revision": None}, {"expected_revision": True}, {"expected_revision": -1},
    {"feedback": ""}, {"feedback": "   "}, {"feedback": {}}, {"feedback": "改" * 4001},
    {"shot_ids": None}, {"shot_ids": []}, {"shot_ids": ["S99"]},
    {"shot_ids": ["S05"], "feedback": "保留S05不变"},
    {"shot_ids": ["S05"], "feedback": "修改S09"},
])
def test_production_block_revision_rejects_invalid_request_without_mutation(production_block_project, isolated, change):
    p = production_block_project
    queue = store.queue_summary()
    files = _old_take_bytes(isolated, p)
    with pytest.raises(ValueError):
        store.action(p["id"], _local_revision_request(p, **change))
    assert store.get(p["id"]) == p
    assert store.queue_summary() == queue
    assert _old_take_bytes(isolated, p) == files


def test_production_block_revision_requires_explicit_ids(production_block_project):
    p = production_block_project
    request = _local_revision_request(p)
    del request["shot_ids"]
    with pytest.raises(ValueError, match="shot_ids"):
        store.action(p["id"], request)
    assert store.get(p["id"]) == p


@pytest.mark.parametrize("change", [
    {"status": "producing"}, {"needs_decision": {"type": "budget"}},
    {"selected_style": None}, {"selected_story": None},
])
def test_production_block_revision_requires_correct_project_state(production_block_project, change):
    p = production_block_project
    store.update(p["id"], change)
    p = store.get(p["id"])
    with pytest.raises(ValueError, match="仅制作阻断"):
        store.action(p["id"], _local_revision_request(p))
    assert store.get(p["id"]) == p


def test_production_resume_keeps_old_revision_and_attempt_budget(production_block_project, isolated):
    p = production_block_project
    files = _old_take_bytes(isolated, p)
    store.action(p["id"], {"action": "resume"})
    q = store.get(p["id"])
    for key in ["production_revision", "script_generation", "production", "budget", "script", "episode"]:
        assert q[key] == p[key]
    assert _old_take_bytes(isolated, p) == files
    assert not q.get("revision_request")


@pytest.mark.parametrize("crash_after_prepare", [False, True])
def test_production_block_revision_recovers_durable_checkpoint_before_generating(
        production_block_project, isolated, monkeypatch, crash_after_prepare):
    from factory import creative, production as engine
    p = production_block_project
    pid = p["id"]
    calls = []
    files = _old_take_bytes(isolated, p)
    config = {"configurable": {"thread_id": pid}, "recursion_limit": 100}
    # Fixture only: persist the checkpoint immediately before its production node.
    with wf.durable_graph() as graph:
        graph.update_state(config, {"project_id": pid, "route": "continue"}, as_node="apply_style")

    def revise(project, settings, checkpoint, feedback):
        calls.append("revise")
        assert project["production"] == p["production"]
        assert feedback["shot_ids"] == LOCAL_REVISION_IDS
        episode = copy.deepcopy(project["episode"])
        for shot in episode["shots"]:
            if shot["id"] in LOCAL_REVISION_IDS:
                shot["prompt"] = "A revised close composition for " + shot["id"]
                shot["render_style_prompt"] = "Soft watercolor, muted palette."
        return {"script": episode, "episode": episode, "script_review": {"passed": True},
                "needs_decision": None}

    def generate(project, settings, checkpoint, paused, **kwargs):
        calls.append("produce")
        assert project["revision_prepared"] == p["production_revision"] + 1
        assert project["production"] == p["production"] and project["budget"] == p["budget"]
        for old, new in zip(p["script"]["shots"], project["script"]["shots"]):
            if old["id"] not in LOCAL_REVISION_IDS:
                assert new == old
            else:
                assert new["prompt"] != old["prompt"]
                assert new["dialogue"] == old["dialogue"] and new["seconds"] == old["seconds"]
        assert _old_take_bytes(isolated, p) == files
        return {"video_url": "/fixture-only-revision.mp4"}

    monkeypatch.setattr(creative, "revise_script", revise)
    monkeypatch.setattr(engine, "generate_project", generate)
    monkeypatch.setattr(wf, "load_config", lambda: {})
    monkeypatch.setattr(wf, "production_config", lambda project, settings: settings)
    store.action(pid, _local_revision_request(p))
    if crash_after_prepare:
        apply = wf._apply
        crashed = []
        def stop_after_persist(project_id, patch):
            result = apply(project_id, patch)
            if patch.get("revision_prepared") and not crashed:
                crashed.append(True)
                raise RuntimeError("fixture crash after prepared commit")
            return result
        monkeypatch.setattr(wf, "_apply", stop_after_persist)
        with pytest.raises(RuntimeError, match="fixture crash"):
            wf.advance(pid)
        assert calls == ["revise"]
        assert store.get(pid)["revision_prepared"] == p["production_revision"] + 1
        store.update(pid, {"status": "failed", "needs_decision": {"type": "runtime"}})
        store.action(pid, {"action": "retry"})
    wf.advance(pid)
    q = store.get(pid)
    assert calls == ["revise", "produce"]
    assert q["status"] == "awaiting_final"
    assert q["delivery_complete_revision"] == p["production_revision"] + 1
    assert len(q["decisions"]) == len(p["decisions"]) + 1
    assert q["decisions"][-1]["type"] == "final"
    wf.advance(pid)
    assert calls == ["revise", "produce"]
    assert _old_take_bytes(isolated, p) == files


def test_production_block_revision_failed_review_must_be_reviewed_again(
        production_block_project, isolated, monkeypatch):
    from factory import creative, production as engine
    p = production_block_project
    pid = p["id"]
    calls = []
    config = {"configurable": {"thread_id": pid}, "recursion_limit": 100}
    with wf.durable_graph() as graph:
        graph.update_state(config, {"project_id": pid, "route": "continue"}, as_node="apply_style")
    original_issue = {"type": "script_review", "message": "道具持有人矛盾",
                      "issues": [{"severity": "major", "detail": "无交接却换持有人"}]}

    def revise(project, settings, checkpoint, feedback):
        calls.append(("review", feedback["notes"]))
        episode = copy.deepcopy(project["episode"])
        if len(calls) == 1:
            return {"script": episode, "episode": episode, "script_review": {"passed": False},
                    "needs_decision": copy.deepcopy(original_issue)}
        assert feedback["notes"] == "明确选中镜头的道具交接"
        return {"script": episode, "episode": episode, "script_review": {"passed": True},
                "needs_decision": None}

    def generate(project, *args, **kwargs):
        assert project["script_review"]["passed"] is True
        calls.append(("produce", project["revision_prepared"]))
        return {"video_url": "/fixture-reviewed-revision.mp4"}

    monkeypatch.setattr(creative, "revise_script", revise)
    monkeypatch.setattr(engine, "generate_project", generate)
    monkeypatch.setattr(wf, "load_config", lambda: {})
    monkeypatch.setattr(wf, "production_config", lambda project, settings: settings)
    store.action(pid, _local_revision_request(p))
    with pytest.raises(wf.ReviewBlocked) as failed:
        wf.advance(pid)
    q = store.get(pid)
    assert q["revision_request"]["feedback"]["shot_ids"] == LOCAL_REVISION_IDS
    assert q.get("revision_prepared") != q["production_revision"]
    assert q["needs_decision"]["type"] == "edit_feedback"
    assert q["needs_decision"]["review"] == original_issue
    assert not any(name == "produce" for name, _ in calls)
    # Even a stale prepared marker cannot send a rejected screenplay to the renderer.
    store.update(pid, {"revision_prepared": q["production_revision"]})
    with pytest.raises(wf.ReviewBlocked):
        wf.production({"project_id": pid})
    store.update(pid, {"status": "blocked", "needs_decision": failed.value.details})
    store.action(pid, {"action": "retry", "shot_ids": LOCAL_REVISION_IDS,
                       "feedback": "明确选中镜头的道具交接"})
    retried = store.get(pid)
    assert retried["production_revision"] == q["production_revision"]
    assert retried["script_generation"] == q["script_generation"]
    assert retried["production"] == p["production"] and retried["budget"] == p["budget"]
    wf.advance(pid)
    assert [name for name, _ in calls] == ["review", "review", "produce"]
    assert store.get(pid)["status"] == "awaiting_final"



def test_resume_captures_existing_video_boundary_without_resetting_accounting(production_block_project,isolated,monkeypatch):
    import json
    from studio import core
    monkeypatch.setattr(core,'ROOT',isolated)
    p=production_block_project;root=isolated/'production'/p['slug']
    (root/'factory_owner.json').write_text(json.dumps({'project_id':p['id']}))
    receipt=root/'renders/S01/take_001/result.json';receipt.parent.mkdir(parents=True)
    receipt.write_text('{}')
    q=store.action(p['id'],{'action':'resume','expected_revision':p['revision'],'review_only_existing_videos':True})
    assert q['production']['review_only_video_results']==[str(receipt.relative_to(isolated))]
    assert q['production']['review_only_existing_videos'] is True
    assert {k:v for k,v in q['production'].items() if k not in {'review_only_video_results','review_only_existing_videos'}}==p['production']
    assert q['budget']==p['budget'] and q['production_revision']==p['production_revision']
    assert receipt.read_text()=='{}'


def test_resume_rejects_ambiguous_review_only_flag(production_block_project):
    p=production_block_project
    with pytest.raises(ValueError,match='布尔值'):
        store.action(p['id'],{'action':'resume','expected_revision':p['revision'],'review_only_existing_videos':'yes'})
    assert store.get(p['id'])['status']=='blocked'
