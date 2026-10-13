"""Cloud video integration with real CPU media checks, never a paid API call."""
from __future__ import annotations

import copy
import http.client
import json
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image

from factory import config, providers, web
from factory.audio_editor import FFMPEG
from factory.minimax_video import VideoPending
from factory.production import _Run
from factory.quality import inspect_video
from factory.scheduler import ProductionNeedsDecision, Scheduler
from factory import video_production
from studio import core

PROJECT_ID = "012345abcdef"


def remote_profile():
    return {"provider": "minimax", "enabled": True, "network_mode": "loopback",
            "base_url": "http://127.0.0.1:19999/custom/v1", "model": "MiniMax-Hailuo-2.3",
            "api_key_env": "TEST_VIDEO_KEY", "duration": 6, "resolution": "768P"}


@pytest.fixture
def run_fixture(tmp_path, monkeypatch):
    import factory.production as production
    import factory.audio_editor as audio_editor
    # These normalization tests mock the remote transport and its authorization
    # boundary. Separate ledger tests exercise the real transactional boundary.
    from factory import cloud_budget
    monkeypatch.setattr(cloud_budget, "ensure_batch", lambda *args: {"id": "fixture"})
    monkeypatch.setattr(cloud_budget, "reconcile_task", lambda *args: None)
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setattr(production, "ROOT", tmp_path)
    monkeypatch.setattr(audio_editor, "ROOT", tmp_path)
    monkeypatch.setenv("TEST_VIDEO_KEY", "fixture-video-secret")
    def no_gpu(*args, **kwargs):
        raise AssertionError("A cloud video task acquired a local ComfyUI/GPU worker")
    monkeypatch.setattr(Scheduler, "worker", no_gpu)
    shot = {"id": "S01", "seconds": 1, "prompt": "A person walks", "motion_prompt": "The person turns"}
    project = {"id": PROJECT_ID, "slug": "factory-" + PROJECT_ID, "format": "animation",
               "budget": {"max_attempts": 2}, "episode": {"cast": {"lead": "adult actor"}, "shots": [shot]},
               "shots": [], "production": {}}
    settings = {"video_connection": {"profile": "cloud", **remote_profile()}, "max_attempts": 2,
                "image_width": 320, "image_height": 576, "video_width": 320, "video_height": 576,
                "output_width": 360, "output_height": 640, "semantic_enabled": False,
                "max_gpu_seconds": 30, "max_wall_seconds": 60}
    run = _Run(project, settings, None, None)
    frame = run.folder / "frame.png"
    Image.new("RGB", (320, 576), "navy").save(frame)
    def target(current):
        return current._target("S01", current.folder / "renders/S01", "A person turns", [frame],
                               kind="video", seconds=1, shot=current.shots[0], review=False)
    def make_media(folder):
        output = folder / "source.mp4"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                        "-f", "lavfi", "-i", "testsrc=size=320x576:rate=12:duration=2",
                        "-an", "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", str(output)],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=20)
        return output
    return run, settings, target, make_media


def test_selected_cloud_video_uses_real_timeline_normalization_and_no_gpu(run_fixture, monkeypatch):
    run, settings, target, make_media = run_fixture
    calls = []
    def generate(profile, prompt, first_frame, folder, **kwargs):
        calls.append((profile["profile"], profile["model"]))
        folder.mkdir(parents=True, exist_ok=True)
        task = {"provider": "minimax", "model": profile["model"], "task_id": "9007199254740993",
                "file_id": "9007199254740995", "status": "downloaded",
                "source_signature": kwargs["source_signature"]}
        core.write_json(folder / "remote_task.json", task)
        kwargs["event"](task)
        return make_media(folder), task
    monkeypatch.setattr(video_production, "generate_video", generate)
    selected = run._render_targets([target(run)], "diffusion_video")[0]
    assert calls == [("cloud", "MiniMax-Hailuo-2.3")]
    assert selected["generation_kind"] == "cloud_diffusion_video"
    assert selected["task_id"] == "9007199254740993"
    assert selected["provider"] == "minimax" and selected["profile"] == "cloud"
    qa = inspect_video(run.receipt_path(selected), 1, (320, 576))
    assert qa["status"] == "pass" and qa["decoded_frames"] == 24
    assert run.budget.snapshot()["gpu_seconds"] == 0
    second = _Run(run.project, settings, None, None)
    assert second._render_targets([target(second)], "diffusion_video")[0]["sha256"] == selected["sha256"]
    assert len(calls) == 1


def test_cloud_resume_reuses_take_after_wait_interruption(run_fixture, monkeypatch):
    run, settings, target, make_media = run_fixture
    submissions = []
    invocations = []
    def generate(profile, prompt, first_frame, folder, **kwargs):
        invocations.append(str(folder))
        journal = folder / "remote_task.json"
        if not journal.exists():
            submissions.append("task-1")
            task = {"provider": "minimax", "model": profile["model"], "task_id": "task-1",
                    "status": "submitted", "source_signature": kwargs["source_signature"]}
            core.write_json(journal, task)
            kwargs["event"](task)
            raise VideoPending("The task is still running", "video_wait_timeout", {"task_id": "task-1"})
        task = core.read_json(journal)
        task.update(file_id="file-1", status="downloaded")
        core.write_json(journal, task)
        return make_media(folder), task
    monkeypatch.setattr(video_production, "generate_video", generate)
    with pytest.raises(ProductionNeedsDecision, match="still running"):
        run._render_targets([target(run)], "diffusion_video")
    resumed = _Run(run.project, settings, None, None)
    result = resumed._render_targets([target(resumed)], "diffusion_video")[0]
    assert result["task_id"] == "task-1" and submissions == ["task-1"]
    assert len(invocations) == 2 and len(set(invocations)) == 1
    assert len(list((run.folder / "renders/S01").glob("take_*"))) == 1


def test_switching_model_does_not_hide_an_unresolved_paid_task(run_fixture, monkeypatch):
    run, settings, target, make_media = run_fixture
    current = target(run)
    attempt = run._new_attempt(current)
    journal = current["folder"] / attempt["take"] / "remote_task.json"
    core.write_json(journal, {"task_id": "pending-task", "status": "submitted",
                              "source_signature": current["signature"]})
    changed = copy.deepcopy(settings)
    changed["video_connection"]["model"] = "another-video-model"
    other = _Run(run.project, changed, None, None)
    with pytest.raises(ProductionNeedsDecision, match="unresolved"):
        other._render_targets([target(other)], "diffusion_video")


@pytest.fixture
def api_fixture(tmp_path, monkeypatch):
    example = Path(config.__file__).resolve().parents[1] / "config/factory.example.json"
    settings = json.loads(example.read_text())
    path = tmp_path / "factory.json"
    core.write_json(path, settings)
    monkeypatch.setattr(config, "config_read_path", lambda: path)
    monkeypatch.setattr(config, "config_write_path", lambda: path)
    monkeypatch.setattr(web, "load_config", lambda: core.read_json(path))
    monkeypatch.setenv("TEST_VIDEO_KEY", "fixture-video-secret")
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(method, route, value=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        payload = None if value is None else json.dumps(value)
        headers = {"Content-Type": "application/json"} if value is not None else {}
        connection.request(method, route, payload, headers)
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        return response.status, body
    yield request, path
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_http_video_registry_cas_and_old_text_client_preserve_bindings(api_fixture):
    request, path = api_fixture
    status, inventory = request("GET", "/api/factory/models")
    assert status == 200 and inventory["video_roles"] == {"video_generator": "local_wan"}
    profiles = copy.deepcopy(inventory["video_profiles"])
    profiles["cloud"] = remote_profile()
    update = {"profiles": inventory["editable_profiles"], "roles": inventory["role_assignments"],
              "expected_revision": inventory["revision"], "video_profiles": profiles,
              "video_roles": {"video_generator": "cloud"}}
    status, updated = request("POST", "/api/factory/models", update)
    assert status == 200 and updated["video_roles"]["video_generator"] == "cloud"
    assert request("POST", "/api/factory/models", update)[0] == 409
    old_client = {"profiles": updated["editable_profiles"], "roles": updated["role_assignments"],
                  "expected_revision": updated["revision"]}
    assert request("POST", "/api/factory/models", old_client)[0] == 200
    assert core.read_json(path)["video_roles"]["video_generator"] == "cloud"


def test_http_video_preflight_never_calls_generation(api_fixture, monkeypatch):
    request, path = api_fixture
    settings = core.read_json(path)
    settings.update(video_profiles={"cloud": remote_profile()}, video_roles={"video_generator": "cloud"})
    core.write_json(path, settings)
    import factory.video_transport as transport
    def unexpected(*args, **kwargs):
        raise AssertionError("Configuration preflight attempted network activity")
    monkeypatch.setattr(transport, "_transfer", unexpected)
    status, result = request("POST", "/api/factory/models/test", {"kind": "video", "profile_id": "cloud"})
    assert status == 200 and result["status"] == "configured"
    assert result["network_checked"] is False and result["generation"] is False
    assert "fixture-video-secret" not in json.dumps(result)
    assert request("POST", "/api/factory/models/test",
                   {"kind": "video", "profile_id": "cloud", "base_url": "http://example.com"})[0] == 409


def test_http_flow_uses_exact_project_and_preserves_origin_checks(api_fixture, monkeypatch):
    request, path = api_fixture
    import factory.team_flow as flow
    calls = []
    def get(pid):
        calls.append(pid)
        return {"id": pid, "revision": 7}
    monkeypatch.setattr(web.store, "get", get)
    monkeypatch.setattr(flow, "build_flow", lambda project: {"project_id": project["id"], "nodes": [], "edges": []})
    status, result = request("GET", "/api/factory/projects/" + PROJECT_ID + "/flow")
    assert status == 200 and result["project_id"] == PROJECT_ID and calls == [PROJECT_ID]
