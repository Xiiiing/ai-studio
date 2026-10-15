"""Deterministic remote scheduling with no network, GPU, or paid service."""
import copy
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from factory import cloud_budget as costs, minimax_video as video, remote_tasks as remote, store, worker
from factory.providers import ProviderError
from studio import core
from test_factory_cloud_budget import isolated, line_for, authorize, request_for


@pytest.fixture
def simulated(isolated, tmp_path, monkeypatch):
    p, profile, frame = isolated
    clock = [1000.0]
    monkeypatch.setattr(remote.time, "time", lambda: clock[0])
    def no_sleep(_):
        raise AssertionError("A cooperative cloud phase must not sleep in the executor")
    monkeypatch.setattr(video.time, "sleep", no_sleep)
    service = {"calls": [], "status": "Processing", "error": None, "tasks": 0}
    def request(profile, route, **kwargs):
        service["calls"].append((route, kwargs))
        if route == "/video_generation":
            service["tasks"] += 1
            return {"task_id": "task_" + str(service["tasks"])}
        if service["error"]:
            raise ProviderError("network", "simulated transient failure")
        if route == "/query/video_generation":
            task = kwargs["params"]["task_id"]
            return {"task_id": task, "status": service["status"], "file_id": "file_" + task}
        if route == "/files/retrieve":
            return {"file": {"file_id": kwargs["params"]["file_id"], "download_url": "https://mock.invalid/video"}}
        raise AssertionError(route)
    monkeypatch.setattr(video, "video_request", request)
    monkeypatch.setattr(video, "download_video", lambda profile, url, path, **kw: path.write_bytes(b"offline media"))
    def setup(project):
        line = line_for(project, profile, frame)
        batch = costs.prepare_batch(project, profile, [line])
        authorize(project, profile, batch)
        def run():
            return video.generate_video(profile, line["prompt"], frame, tmp_path / project["id"],
                                        source_signature=line["source_signature"], step=True,
                                        before_submit=lambda request: costs.reserve(project["id"], batch["id"], line, profile, request),
                                        event=lambda task: costs.reconcile_task(project["id"], line, task, profile))
        return run, line
    return p, profile, frame, clock, service, setup


def test_submit_poll_download_are_separate_durable_phases(simulated):
    p, profile, frame, clock, service, setup = simulated
    run, line = setup(p)
    with pytest.raises(remote.ProductionDeferred) as submitted:
        run()
    assert submitted.value.phase == "remote_poll" and len(service["calls"]) == 1
    ledger = costs.summary(p["id"])["tasks"][0]
    assert ledger["task_id"] == "task_1" and ledger["phase"] == "remote_poll"
    assert ledger["next_poll_at"] == submitted.value.next_poll_at
    with pytest.raises(remote.ProductionDeferred):
        run()  # Claiming too early makes zero extra API requests.
    assert len(service["calls"]) == 1
    clock[0] = submitted.value.next_poll_at
    with pytest.raises(remote.ProductionDeferred) as polling:
        run()
    assert len(service["calls"]) == 2
    service["status"] = "Success"
    clock[0] = polling.value.next_poll_at
    with pytest.raises(remote.ProductionDeferred) as download:
        run()
    assert download.value.phase == "remote_download"
    assert costs.summary(p["id"])["tasks"][0]["file_id"] == "file_task_1"
    result, receipt = run()
    assert result.read_bytes() == b"offline media" and receipt["phase"] == "quality_review"
    assert service["tasks"] == 1
    before = len(service["calls"])
    assert run()[0] == result and len(service["calls"]) == before


def test_two_cloud_projects_and_a_local_project_make_progress(simulated, monkeypatch):
    p, profile, frame, clock, service, setup = simulated
    p2 = store.create({"brief": "第二个原创项目测试交替推进", "format": "animation"})
    p2 = store.update(p2["id"], {"episode": copy.deepcopy(p["episode"])})
    local = store.create({"brief": "本地不冲突的工作不应被云等待阻塞"})
    # This fixture switches to a synthetic clock after the first project was
    # created. Explicit creation order removes wall-clock/UUID tie artifacts.
    with store.connection() as db:
        for ordinal, project in enumerate((p, p2, local)):
            db.execute("UPDATE jobs SET created=? WHERE project_id=?", (clock[0] + ordinal, project["id"]))
    runs = {p["id"]: setup(p)[0], p2["id"]: setup(p2)[0]}
    progressed = []
    def advance(pid):
        if pid in runs:
            runs[pid]()
        progressed.append(pid)
        store.update(pid, {"status": "awaiting_final"})
    monkeypatch.setattr(worker, "advance", advance)
    first = store.claim_job()
    worker.handle(first)
    second = store.claim_job()
    worker.handle(second)
    assert {first["project_id"], second["project_id"]} == set(runs)
    third = store.claim_job()
    assert third["project_id"] == local["id"]
    worker.handle(third)
    assert progressed == [local["id"]] and service["tasks"] == 2
    assert store.claim_job() is None
    # Each pending project polls once. None monopolizes the executor with sleeps.
    clock[0] += profile["poll_interval_seconds"]
    for _ in range(2):
        worker.handle(store.claim_job())
    assert service["tasks"] == 2 and len(progressed) == 1
    service["status"] = "Success"
    clock[0] += profile["poll_interval_seconds"]
    for _ in range(4):
        job = store.claim_job()
        assert job is not None
        worker.handle(job)
    assert set(progressed) == set(runs) | {local["id"]}
    assert service["tasks"] == 2
    assert all(costs.summary(pid)["tasks"][0]["status"] == "downloaded" for pid in runs)


def test_claim_is_atomic_and_a_recovered_lease_rejects_stale_completion(isolated):
    p, profile, frame = isolated
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: store.claim_job(), range(16)))
    claimed = [v for v in claims if v is not None]
    assert len(claimed) == 1
    old = claimed[0]
    assert store.recover_jobs() == 1
    new = store.claim_job()
    assert new["id"] == old["id"] and new["lease"] > old["lease"]
    assert not store.finish_job(old["id"], lease=old["lease"])
    assert not remote.defer_job(old, remote.ProductionDeferred(0))
    assert remote.owns_job(new)
    assert store.finish_job(new["id"], lease=new["lease"])
    assert not remote.owns_job(new)


def test_duplicate_wakeup_cannot_claim_a_running_project(isolated):
    p, profile, frame = isolated
    running = store.claim_job()
    with store.connection() as db:
        store._enqueue(db, p["id"])
    assert store.claim_job() is None
    assert store.finish_job(running["id"], lease=running["lease"])
    assert store.claim_job()["project_id"] == p["id"]


def test_pause_excludes_manual_pause_but_keeps_cloud_wait_and_money(simulated):
    p, profile, frame, clock, service, setup = simulated
    run, line = setup(p)
    job = store.claim_job()
    with pytest.raises(remote.ProductionDeferred) as outcome:
        run()
    remote.defer_job(job, outcome.value)
    clock[0] += 10
    store.action(p["id"], {"action": "pause"})
    before = costs.summary(p["id"])
    assert store.get(p["id"])["production"]["elapsed_seconds"] == 10
    clock[0] += 1000
    assert store.claim_job() is None
    store.action(p["id"], {"action": "resume"})
    assert store.claim_job() is not None
    assert store.get(p["id"])["production"]["elapsed_seconds"] == 10
    assert costs.summary(p["id"])["tasks"] == before["tasks"]


def test_global_remote_capacity_is_bounded_without_consuming_a_third_intent(isolated):
    p, profile, frame = isolated
    lines = [line_for(p, profile, frame, "S0" + str(i)) for i in range(1, 4)]
    batch = costs.prepare_batch(p, profile, lines)
    authorize(p, profile, batch)
    for line in lines[:2]:
        costs.reserve(p["id"], batch["id"], line, profile, request_for(line))
    with pytest.raises(remote.ProductionDeferred) as capacity:
        costs.reserve(p["id"], batch["id"], lines[2], profile, request_for(lines[2]))
    assert capacity.value.phase == "remote_submit"
    assert len(costs.summary(p["id"])["tasks"]) == remote.MAX_REMOTE_INFLIGHT == 2
    costs.reconcile_task(p["id"], lines[0], {"status": "downloaded", "task_id": "proven_done"}, profile)
    costs.reserve(p["id"], batch["id"], lines[2], profile, request_for(lines[2]))
    assert len(costs.summary(p["id"])["tasks"]) == 3


def test_transient_poll_failures_back_off_and_eventually_require_attention(simulated):
    p, profile, frame, clock, service, setup = simulated
    run, line = setup(p)
    with pytest.raises(remote.ProductionDeferred) as submitted:
        run()
    service["error"] = True
    clock[0] = submitted.value.next_poll_at
    delays = []
    for _ in range(remote.MAX_POLL_FAILURES - 1):
        with pytest.raises(remote.ProductionDeferred) as retry:
            run()
        delays.append(retry.value.next_poll_at - clock[0])
        clock[0] = retry.value.next_poll_at
    assert delays == sorted(delays) and all(0 < value <= 300 for value in delays)
    with pytest.raises(video.VideoPending) as blocked:
        run()
    assert blocked.value.code == "video_status_unavailable" and service["tasks"] == 1
    assert costs.summary(p["id"])["tasks"][0]["poll_failures"] == remote.MAX_POLL_FAILURES
    service["error"] = None
    service["status"] = "Success"
    with pytest.raises(remote.ProductionDeferred):
        run()  # Explicit retry can observe success; it cannot post a new task.
    run()
    assert service["tasks"] == 1


def test_crash_after_saved_task_id_resumes_with_one_post(isolated, tmp_path, monkeypatch):
    p, profile, frame = isolated
    line = line_for(*isolated)
    batch = costs.prepare_batch(p, profile, [line])
    authorize(p, profile, batch)
    old = store.claim_job()
    folder, posts = tmp_path / "crash", tmp_path / "posts.txt"
    def child():
        def request(*args, **kwargs):
            with posts.open("a") as handle:
                handle.write("POST\n")
                handle.flush()
                os.fsync(handle.fileno())
            return {"task_id": "saved_task"}
        video.video_request = request
        def event(task):
            if task.get("task_id"):
                os._exit(19)
        video.generate_video(profile, line["prompt"], frame, folder, source_signature=line["source_signature"], step=True,
                             event=event, before_submit=lambda r: costs.reserve(p["id"], batch["id"], line, profile, r))
    process = multiprocessing.get_context("fork").Process(target=child)
    process.start()
    process.join(10)
    assert process.exitcode == 19
    assert core.read_json(folder / "remote_task.json")["task_id"] == "saved_task"
    assert store.recover_jobs() == 1 and not remote.owns_job(old)
    claimed = store.claim_job()
    calls = []
    def poll(profile, route, **kwargs):
        calls.append(route)
        assert route == "/query/video_generation"
        return {"task_id": "saved_task", "status": "Success", "file_id": "saved_file"}
    monkeypatch.setattr(video, "video_request", poll)
    with pytest.raises(remote.ProductionDeferred) as result:
        video.generate_video(profile, line["prompt"], frame, folder, source_signature=line["source_signature"], step=True,
                             event=lambda task: costs.reconcile_task(p["id"], line, task, profile))
    assert result.value.phase == "remote_download" and calls == ["/query/video_generation"]
    assert posts.read_text() == "POST\n"
    assert costs.summary(p["id"])["tasks"][0]["file_id"] == "saved_file"
    remote.defer_job(claimed, result.value)


def test_unknown_post_does_not_become_a_scheduled_retry(isolated, tmp_path, monkeypatch):
    p, profile, frame = isolated
    line = line_for(*isolated)
    batch = costs.prepare_batch(p, profile, [line])
    authorize(p, profile, batch)
    calls = []
    def uncertain(*args, **kwargs):
        calls.append("POST")
        raise ProviderError("network", "lost response")
    monkeypatch.setattr(video, "video_request", uncertain)
    for _ in range(3):
        with pytest.raises(video.VideoPending) as result:
            video.generate_video(profile, line["prompt"], frame, tmp_path / "unknown", source_signature=line["source_signature"],
                                 step=True, before_submit=lambda r: costs.reserve(p["id"], batch["id"], line, profile, r))
        assert result.value.code == "video_submission_unknown"
    assert calls == ["POST"]


def test_additive_migration_keeps_original_snapshot_and_saved_ids(isolated):
    p, profile, frame = isolated
    snapshot = store.get(p["id"])
    line = line_for(*isolated)
    batch = costs.prepare_batch(p, profile, [line])
    authorize(p, profile, batch)
    costs.reserve(p["id"], batch["id"], line, profile, request_for(line))
    costs.reconcile_task(p["id"], line, {"task_id": "existing", "file_id": "file", "status": "generated", "next_poll_at": 1234}, profile)
    before = costs.summary(p["id"])
    store.initialize()
    store.initialize()
    after = costs.summary(p["id"])
    assert before == after
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM factory_migrations WHERE name='remote_phases_v1'").fetchone()[0] == 1
    assert store.get(p["id"])["brief"] == snapshot["brief"]


def test_langgraph_checkpoint_reentry_preserves_gate_and_remote_task(simulated, tmp_path, monkeypatch):
    from factory import workflows as wf
    from langgraph.graph import StateGraph, START, END
    from langgraph.types import interrupt
    p, profile, frame, clock, service, setup = simulated
    store.update(p["id"], {"workflow_version": 1, "team_mode": "guided"})
    run, line = setup(p)
    monkeypatch.setattr(wf, "DATA", tmp_path / "graph")
    monkeypatch.setattr(wf, "CHECKPOINTS", tmp_path / "graph/checkpoints.sqlite3")
    def gate(state):
        decision = store.gate(state["project_id"], "style", [{"id": "go"}], "fixture approval", 1)
        answer = interrupt(decision)
        assert answer["choice"] == "go"
        return {"route": "continue"}
    def production(state):
        path, receipt = run()
        store.update(state["project_id"], {"delivery": {"task_id": receipt["task_id"], "path": str(path)},
                                          "status": "approved"})
        return {"route": "done"}
    def build(checkpointer):
        graph = StateGraph(wf.State)
        graph.add_node("gate", gate)
        graph.add_node("production", production)
        graph.add_edge(START, "gate")
        graph.add_edge("gate", "production")
        graph.add_edge("production", END)
        return graph.compile(checkpointer=checkpointer)
    monkeypatch.setattr(wf, "build_graph", build)
    worker.handle(store.claim_job(p["id"]))
    decision = store.get(p["id"])["decisions"][-1]
    assert service["tasks"] == 0
    store.decide(p["id"], {"type": "style", "choice": "go", "decision_id": decision["id"]})
    worker.handle(store.claim_job(p["id"]))
    assert store.get(p["id"])["status"] == "waiting_remote" and service["tasks"] == 1
    store.recover_jobs()  # A new graph connection reads the saved failed node.
    clock[0] += profile["poll_interval_seconds"]
    service["status"] = "Success"
    worker.handle(store.claim_job(p["id"]))
    assert costs.summary(p["id"])["tasks"][0]["file_id"] == "file_task_1"
    worker.handle(store.claim_job(p["id"]))
    result = store.get(p["id"])
    assert result["status"] == "approved" and result["delivery"]["task_id"] == "task_1"
    assert len(result["answers"]) == len(result["decisions"]) == 1
    assert service["tasks"] == 1

