"""Resume the actual production/client loop without any GPU or remote model call."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

from factory import capabilities, gpu_client, production, scheduler
from factory.scheduler import ProductionNeedsDecision, ProductionPaused
from studio import core


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for module in (core, production, gpu_client, scheduler):
        monkeypatch.setattr(module, "ROOT", tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("cami cannot start or inspect a local GPU")
    monkeypatch.setattr(scheduler, "gpu_snapshot", forbidden)
    monkeypatch.setattr(scheduler.FactoryComfyWorker, "__enter__", forbidden)
    monkeypatch.setattr(scheduler.GpuLease, "__enter__", forbidden)
    monkeypatch.setattr(production, "inspect_image", lambda *args: {"status": "pass", "issues": []})
    monkeypatch.setattr(production, "inspect_video", lambda *args: {"status": "pass", "issues": []})
    project = {"id": "remote-resume", "slug": "factory-remote-resume", "format": "motion_comic",
               "styles": [{"id": "approved", "prompt": "Approved watercolor"}], "selected_style": "approved",
               "episode": {"cast": {"female": "Adult woman in blue coat"},
                           "shots": [{"id": "S01", "seconds": 4, "prompt": "A woman returns a key", "dialogue": []}]}}
    config = {"gpu_service": {"base_url": "http://127.0.0.1:8791/inference", "poll_seconds": .01},
              "max_gpu_seconds": 500, "max_wall_seconds": 500, "max_attempts": 2,
              "image_width": 288, "image_height": 512, "output_width": 288, "output_height": 512}
    run = production._Run(project, config, None, None)
    return run


@pytest.fixture
def transport(monkeypatch):
    """The real GPUClient persists identities and reconciles these mock HTTP results."""
    state = {"jobs": {}, "posts": [], "gets": [], "paused": False, "fail_count": 0,
             "pause_first": False, "blobs": {}}
    def request(client, route, payload=None, *, timeout=None, missing=False):
        if payload is not None:
            assert route == "/jobs"
            state["posts"].append(copy.deepcopy(payload))
            ident = payload["request_id"]
            if ident not in state["jobs"]:
                status = "failed" if len(state["posts"]) <= state["fail_count"] else "completed"
                if state["pause_first"] and len(state["posts"]) == 1:
                    status = "running"
                blob = (payload["output_kind"] + " fixture media " + ident).encode()
                state["blobs"][ident] = blob
                state["jobs"][ident] = {
                    "id": ident, "status": status,
                    "result": {"receipt": {"execution_host": "test-wkp", "gpu_seconds": 4.25, "elapsed_seconds": 10},
                               "artifacts": [{"index": 0, "name": "fixture.bin", "bytes": len(blob),
                                              "sha256": hashlib.sha256(blob).hexdigest()}]}}
            return copy.deepcopy(state["jobs"][ident])
        ident = route.removeprefix("/jobs/")
        state["gets"].append(ident)
        answer = state["jobs"].get(ident)
        if answer and answer["status"] == "running":
            state["paused"] = True
            raise ProductionPaused("Simulated connection interruption after accepted inference", "remote_gpu_unavailable")
        return copy.deepcopy(answer)
    def download(client, ident, artifact, target):
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(state["blobs"][ident])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
        return path
    monkeypatch.setattr(gpu_client.GPUClient, "request", request)
    monkeypatch.setattr(gpu_client.GPUClient, "download", download)
    return state


def image_target(run):
    return run._target("S01", run.folder / "images/S01", "A woman returns a key", shot=run.shots[0])


def test_accepted_job_without_local_result_resumes_same_take(runtime, transport, monkeypatch):
    run = runtime
    target = image_target(run)
    transport["pause_first"] = True
    with pytest.raises(ProductionPaused):
        run._render_targets([target], "images")
    assert transport["paused"]
    assert len(transport["posts"]) == 1
    folder = target["folder"] / "take_001"
    assert core.read_json(folder / "submission.json")["gpu_service_job_id"] == transport["posts"][0]["request_id"]
    assert not (folder / "result.json").exists()
    job = next(iter(transport["jobs"].values()))
    job["status"] = "completed"
    def no_new_worker(*args, **kwargs):
        pytest.fail("Existing accepted inference must resume before opening a generation worker")
    monkeypatch.setattr(run.scheduler, "worker", no_new_worker)
    selected = run._render_targets([target], "images")
    assert len(transport["posts"]) == 1
    assert selected[0]["take"] == "take_001"
    assert core.read_json(folder / "attempt.json")["state"] == "passed"
    assert (folder / "result.json").is_file()
    assert sorted(path.name for path in target["folder"].glob("take_*")) == ["take_001"]
    assert run.budget.snapshot()["gpu_seconds"] == 4.25


@pytest.mark.parametrize("fail_count", [1, 2])
def test_terminal_failure_has_one_bounded_new_attempt(runtime, transport, fail_count):
    run = runtime
    target = image_target(run)
    transport["fail_count"] = fail_count
    if fail_count == 1:
        selected = run._render_targets([target], "images")
        assert selected[0]["take"] == "take_002"
    else:
        with pytest.raises(ProductionNeedsDecision) as stopped:
            run._render_targets([target], "images")
        assert stopped.value.reason == "retry_limit"
    assert len(transport["posts"]) == 2
    first = target["folder"] / "take_001"
    assert (first / "submission_error.json").is_file()
    assert core.read_json(first / "attempt.json")["state"] == "failed"
    assert len(list(target["folder"].glob("take_*"))) == 2
    assert run.budget.snapshot()["gpu_seconds"] == 8.5


def test_terminal_submission_error_left_by_restart_marks_old_take_failed(runtime, transport):
    run = runtime
    target = image_target(run)
    record = run._new_attempt(target)
    folder = target["folder"] / record["take"]
    core.write_json(folder / "submission.json", {"gpu_service_job_id": "f" * 64})
    core.write_json(folder / "submission_error.json", {"status": "failed", "job_id": "f" * 64})
    assert core.read_json(folder / "attempt.json")["state"] == "rendering"
    selected = run._render_targets([target], "images")
    assert core.read_json(folder / "attempt.json")["state"] == "failed"
    assert selected[0]["take"] == "take_002"
    assert len(transport["posts"]) == 1
    assert "f" * 64 not in transport["gets"]
    assert len(list(target["folder"].glob("take_*"))) == 2


def test_accepted_repair_keyframe_resumes_before_parent_submission_same_take(runtime, transport):
    run = runtime
    frame = run.folder / "approved.png"
    frame.write_bytes(b"approved image bytes")
    target = run._target("S01", run.folder / "videos/S01", "A woman gently returns the key", [frame],
                         kind="video", seconds=4, shot=run.shots[0])
    record = run._new_attempt(target)
    record["repair"] = {"regenerate_keyframe": True, "prompt_prefix": "Preserve approved identity."}
    run._save_attempt(target, record)
    transport["pause_first"] = True
    with pytest.raises(ProductionPaused):
        run._render_targets([target], "video")
    parent = target["folder"] / "take_001"
    assert (parent / "repair_keyframe/submission.json").exists()
    assert not (parent / "submission.json").exists()
    assert len(transport["posts"]) == 1
    repair_id = transport["posts"][0]["request_id"]
    transport["jobs"][repair_id]["status"] = "completed"
    selected = run._render_targets([target], "video")
    assert len(transport["posts"]) == 2
    assert transport["posts"][0]["output_kind"] == "image"
    assert transport["posts"][1]["output_kind"] == "video"
    assert selected[0]["take"] == "take_001"
    assert len(list(target["folder"].glob("take_*"))) == 1
    assert (parent / "repair_keyframe/result.json").exists()
    assert (parent / "result.json").exists()
    assert core.read_json(parent / "repair_keyframe/submission.json")["gpu_service_job_id"] == repair_id
    assert run.budget.snapshot()["gpu_seconds"] == 8.5


@pytest.mark.parametrize("available", [True, False])
def test_remote_capabilities_do_not_depend_on_local_models_or_probe_local_gpus(tmp_path, monkeypatch, available):
    monkeypatch.setattr(capabilities, "ROOT", tmp_path)
    monkeypatch.setattr(capabilities, "DATA", tmp_path / "data")
    def no_gpu(*args, **kwargs):
        pytest.fail("Remote inference configuration must not query cami nvidia-smi")
    monkeypatch.setattr(capabilities.subprocess, "run", no_gpu)
    monkeypatch.setattr(capabilities, "resolve_llm", lambda *args: {"model": "test-qwen", "base_url": "http://fake.invalid/v1"})
    monkeypatch.setattr(capabilities, "check_model", lambda *args, **kwargs: {"data": [{"id": "test-qwen"}]})
    calls = []
    def health(client, route, *, timeout):
        calls.append((route, timeout))
        if not available:
            raise ProductionPaused("Wkp offline", "remote_gpu_unavailable")
        return {"execution_host": "wkp-fixture", "gpu_cards": [4, 5, 6, 7],
                "models": {"image": True, "video": True, "audio": True}}
    monkeypatch.setattr(gpu_client.GPUClient, "request", health)
    observed = capabilities._observe({"production": {"gpu_service": {"base_url": "http://127.0.0.1:8791/inference"}}})
    assert calls == [("/health", 3)]
    assert observed["gpus"] == []
    assert observed["inference_service"]["available"] is available
    for kind in ("image", "video", "audio"):
        assert observed[kind]["available"] is available
        if available:
            assert observed[kind]["execution_host"] == "wkp-fixture"
    if not available:
        assert "不可用" in observed["inference_service"]["message"]
