"""H3 routing and recovery contracts; CPU fixtures, never production inference."""
import contextlib
import copy
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock, MagicMock

import pytest
from PIL import Image

from factory import gpu_client, gpu_service, providers, scheduler, video_providers
from factory.production import _Run
from scripts import configure_wkp_h3, deploy_cami
from studio import core, h3
from studio.graphs import h3_video


@pytest.mark.parametrize("seconds", [1, 3, 4, 5, 6, 10, 15, 73 / 24])
def test_h3_duration_grid_covers_timeline(seconds):
    frames = h3.frame_count(seconds)
    assert frames % 17 == 5 and frames >= seconds * 24
    assert frames - seconds * 24 < 17
    graph = h3_video("An adult slowly turns toward the door.", 42, "frame.png", seconds=seconds)
    gpu_service.validate_graph(graph, {"frame.png"}, "video")
    assert graph["6"]["inputs"]["first_frame"] == ["18", 0]


@pytest.mark.parametrize("mutate", [
    lambda g: g["6"]["inputs"].pop("first_frame"),
    lambda g: g["6"]["inputs"].update(length=144),
    lambda g: g["6"]["inputs"].update(width=720),
    lambda g: g["6"]["inputs"].update(width=1536, height=1536),
    lambda g: g["8"]["inputs"].update(noise_seed=True),
    lambda g: g["17"]["inputs"].update(lora_name="unverified.safetensors"),
    lambda g: g["4"]["inputs"].update(placement="cpu"),
    lambda g: g["10"]["inputs"].update(steps=20),
    lambda g: g["16"]["inputs"].update(filename_prefix="../outside"),
    lambda g: g.update(extra={"class_type": "SaveLatent", "inputs": {}}),
])
def test_h3_boundary_rejects_modified_recipe(mutate):
    graph = h3_video("An adult slowly turns.", 42, "frame.png")
    mutate(graph)
    with pytest.raises(ValueError):
        gpu_service.validate_graph(graph, {"frame.png"}, "video")


def test_h3_pair_is_leased_and_accounted_together(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(scheduler.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(scheduler, "gpu_snapshot", lambda *args, **kw: {
        4: {"free_mib": 20000, "utilization": 0},
        5: {"free_mib": 20000, "utilization": 20},
        6: {"free_mib": 20000, "utilization": 0},
    })
    calls = []
    @contextlib.contextmanager
    def worker(cards, port, tag, **kw):
        calls.append((cards, kw))
        yield object()
    monkeypatch.setattr(scheduler, "FactoryComfyWorker", worker)
    cfg = {"gpu_cards": [4, 5, 6], "resource_wait_seconds": 0}
    budget = scheduler.Budget(cfg)
    with scheduler.Scheduler(cfg, budget).worker("h3-fixture", h3=True):
        assert budget.snapshot()["active_gpu_leases"] == 2
        now[0] = 3
    assert calls[0][0] == [4, 6]
    assert calls[0][1]["h3"] is True and calls[0][1]["reserve_vram"] == 8
    assert budget.snapshot()["gpu_seconds"] == 6


def test_h3_rejects_single_gpu_configuration():
    cfg = {"gpu_cards": [4]}
    with pytest.raises(scheduler.ProductionNeedsDecision, match="two configured"):
        with scheduler.Scheduler(cfg, scheduler.Budget(cfg)).worker("h3-fixture", h3=True):
            pytest.fail("A one-card H3 worker was started")


def test_h3_launch_preserves_native_quantization_and_existing_lora(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "ROOT", tmp_path)
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setattr(h3, "require_installation", lambda: None)
    monkeypatch.setenv("DRAMA_H3_LORA_DIR", str(tmp_path / "existing-loras"))
    lease = MagicMock()
    monkeypatch.setattr(scheduler, "GpuLease", lambda *a, **kw: lease)
    child = Mock(pid=42)
    child.poll.return_value = None
    launch = Mock(return_value=child)
    monkeypatch.setattr(scheduler.subprocess, "Popen", launch)
    monkeypatch.setattr(scheduler, "api", lambda *a, **kw: {})
    worker = scheduler.FactoryComfyWorker([4, 6], 8299, "h3-fixture", reserve_vram=8, h3=True)
    try:
        worker._start()
        argv = launch.call_args.args[0]
        assert "--disable-smart-memory" in argv
        assert not {"--fp16-unet", "--fp16-text-enc", "--fp32-vae"}.intersection(argv)
        assert launch.call_args.kwargs["env"]["CUDA_VISIBLE_DEVICES"] == "4,6"
        assert str(tmp_path / "existing-loras") in (worker.folder / "models.yaml").read_text()
    finally:
        worker.__exit__(None, None, None)
    lease.__exit__.assert_called_once()


def test_old_wan_health_cannot_authorize_h3_submission(tmp_path, monkeypatch):
    client = gpu_client.GPUClient({"base_url": "http://127.0.0.1:8791"})
    calls = []
    def request(route, *args, **kw):
        calls.append(route)
        return {"ok": True, "models": {"video": True}, "gpu_cards": [4, 5]}
    monkeypatch.setattr(client, "request", request)
    workload = {"kind": "comfy", "graph": h3_video("An adult turns.", 42, "frame.png")}
    with pytest.raises(scheduler.ProductionNeedsDecision) as error:
        client.run(workload, tmp_path, scheduler.Budget({}))
    assert error.value.reason == "remote_h3_unavailable"
    assert calls == ["/health"] and not (tmp_path / "submission.json").exists()


def test_h3_rejects_wrong_model_receipt_before_download(tmp_path, monkeypatch):
    worker = gpu_client.RemoteComfyWorker({"base_url": "http://127.0.0.1:8791"}, scheduler.Budget({}), "test", None)
    graph = h3_video("An adult turns.", 42, "frame.png")
    core.write_json(tmp_path / "workflow_api.json", graph)
    monkeypatch.setattr(worker.client, "download", lambda *a: pytest.fail("Mismatched H3 output was downloaded"))
    answer = {"id": "a" * 64, "result": {"receipt": {"provider": "comfyui_wan"}, "artifacts": [{}]}}
    with pytest.raises(scheduler.ProductionNeedsDecision, match="submitted model"):
        worker._finish(answer, tmp_path, core.fingerprint(graph), "video")


@pytest.mark.parametrize("profile", [video_providers.H3_PROFILE, video_providers.H3_PRODUCTION_PROFILE,
                                    {**video_providers.H3_PRODUCTION_PROFILE, "steps": 25, "shift_video": 10, "sampler": "heun"}])
def test_h3_production_uses_selected_recipe_and_reuses_verified_selection(tmp_path, monkeypatch, profile):
    import factory.production as production
    from factory.audio_editor import FFMPEG
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setattr(production, "ROOT", tmp_path)
    calls = []
    class Worker:
        def copy_input(self, path):
            return "frame.png"
        def execute(self, graph, destination, kind, **kw):
            assert kind == "video"
            gpu_service.validate_graph(graph, {"frame.png"}, kind)
            calls.append(copy.deepcopy(graph))
            path = destination / "video.mp4"
            frames = graph["6"]["inputs"]["length"]
            subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-f", "lavfi", "-i", "testsrc=size=160x288:rate=24", "-frames:v", str(frames),
                "-an", "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", str(path)],
                check=True, capture_output=True, timeout=20)
            core.write_json(destination / "result.json", core.media_receipt(path, graph_sha256=core.fingerprint(graph)))
            return path
    @contextlib.contextmanager
    def worker(self, tag, control=None, *, h3=False):
        assert h3 is True
        yield Worker()
    monkeypatch.setattr(scheduler.Scheduler, "worker", worker)
    monkeypatch.setattr(production, "wan_video", lambda *a, **kw: pytest.fail("H3 fell back to Wan"))
    project = {"id": "h3fixture01", "format": "animation", "episode": {
        "cast": {"lead": "Fictional adult"}, "shots": [{"id": "S01", "seconds": 3,
            "prompt": "An adult slowly turns.", "motion_prompt": "An adult slowly turns."}]}}
    cfg = {"video_connection": {"profile": "wkp_h3", **profile},
           "image_width": 160, "image_height": 288, "video_width": 160, "video_height": 288,
           "output_width": 360, "output_height": 640, "semantic_enabled": False, "max_attempts": 1}
    run = _Run(project, cfg, None, None)
    frame = run.folder / "frame.png"
    Image.new("RGB", (160, 288), "navy").save(frame)
    def target(current):
        return current._target("S01", current.folder / "renders/S01", "An adult slowly turns.", [frame],
            kind="video", seconds=3, shot=current.shots[0], review=False)
    selected = run._render_targets([target(run)], "diffusion_video")[0]
    assert selected["model"] == h3.MODEL and selected["provider"] == "comfyui_h3"
    assert selected["qa"]["technical_passed"]
    attempt = run.shots[0]["attempts"][0]
    assert attempt["parameters"]["frames"] == 73 and attempt["parameters"]["steps"] == h3.recipe(profile)["steps"]
    assert ("17" in calls[0]) == (h3.recipe(profile)["quality"] == "preview")
    assert selected["model_revision"] == h3.recipe(profile)["revision"]
    second = _Run(run.project, cfg, None, None)
    assert second._render_targets([target(second)], "diffusion_video")[0]["sha256"] == selected["sha256"]
    assert len(calls) == 1


def test_h3_profiles_preserve_history_and_production_is_new_identity():
    legacy = video_providers.normalize_video_profile(video_providers.H3_PROFILE)
    assert legacy == video_providers.H3_PROFILE
    formal = video_providers.normalize_video_profile(video_providers.H3_PRODUCTION_PROFILE)
    assert formal == video_providers.video_presets()["comfyui_h3"]
    graph = h3_video("An adult waves.", 42, "frame.png", 768, 1344, 5, profile=formal)
    gpu_service.validate_graph(graph, {"frame.png"}, "video")
    assert "17" not in graph
    assert graph["2"]["inputs"]["model"] == ["1", 0]
    assert graph["10"]["inputs"]["steps"] == 20
    changed = h3_video("An adult waves.", 42, "frame.png", 768, 1344, 5, profile={**formal, "steps": 25})
    gpu_service.validate_graph(changed, {"frame.png"}, "video")
    assert core.fingerprint(graph) != core.fingerprint(changed)
    assert "lora" not in h3.dependencies(formal)
    assert "lora" in h3.dependencies(legacy)


def test_h3_base_health_does_not_require_preview_lora(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "ROOT", tmp_path / "project")
    monkeypatch.setattr(core, "COMFY", tmp_path / "comfy")
    monkeypatch.setenv("DRAMA_H3_LORA_DIR", str(tmp_path / "loras"))
    for path in h3.dependencies({"quality": "production"}).values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    health = h3.installation()
    assert health["installed"] is False and health["missing"] == ["lora"]
    assert health["recipes"]["production"]["installed"] is True
    assert h3.backend_ready(health, [4, 5], video_providers.H3_PRODUCTION_PROFILE)
    h3.require_installation({"quality": "production"})
    with pytest.raises(ValueError, match="lora"):
        h3.require_installation({"quality": "preview"})


@pytest.mark.parametrize("field,value", [("steps", 4), ("steps", 51), ("steps", True),
    ("shift_video", float("nan")), ("shift_audio", float("inf")), ("shift_video", 0),
    ("sampler", "custom"), ("quality", "unknown"), ("revision", h3.REVISION)])
def test_h3_formal_profile_rejects_unbounded_or_mismatched_sampling(field, value):
    with pytest.raises(ValueError):
        video_providers.normalize_video_profile({**video_providers.H3_PRODUCTION_PROFILE, field: value})


def test_h3_production_readiness_requires_new_recipe_without_breaking_legacy(tmp_path, monkeypatch):
    client = gpu_client.GPUClient({"base_url": "http://127.0.0.1:8791"})
    backend = {"model": h3.MODEL, "revision": h3.REVISION, "installed": True, "gpu_count": 2}
    monkeypatch.setattr(client, "request", lambda *args: {"gpu_cards": [4, 5], "video_backends": {"comfyui_h3": backend}})
    assert client.require_h3() == backend
    with pytest.raises(scheduler.ProductionNeedsDecision):
        client.require_h3(video_providers.H3_PRODUCTION_PROFILE)
    backend["recipes"] = {"production": {"revision": h3.BASE_REVISION, "installed": True}}
    assert client.require_h3(video_providers.H3_PRODUCTION_PROFILE) == backend


def test_h3_receipt_cannot_claim_different_production_parameters(tmp_path, monkeypatch):
    worker = gpu_client.RemoteComfyWorker({"base_url": "http://127.0.0.1:8791"}, scheduler.Budget({}), "formal", None)
    graph = h3_video("An adult waves.", 42, "frame.png", profile=video_providers.H3_PRODUCTION_PROFILE)
    core.write_json(tmp_path / "workflow_api.json", graph)
    monkeypatch.setattr(worker.client, "download", lambda *args: pytest.fail("Mismatched production output was downloaded"))
    receipt = {"provider": "comfyui_h3", "model": h3.MODEL, "model_revision": h3.BASE_REVISION,
               "graph_sha256": core.fingerprint(graph), "generation_parameters": {**h3.graph_recipe(graph), "steps": 25}}
    with pytest.raises(scheduler.ProductionNeedsDecision):
        worker._finish({"result": {"artifacts": [{}], "receipt": receipt}}, tmp_path, core.fingerprint(graph), "video")


def test_explicit_model_routes_select_new_vision_and_keep_defaults(monkeypatch):
    profiles = {"qwen_vision_existing": {"model": "old", "base_url": "http://127.0.0.1:8090/v1"},
                "qwen38_vision": {"model": "new", "base_url": "http://127.0.0.1:8099/v1"}}
    calls = []
    monkeypatch.setattr(providers, "request_json", lambda base, *args, **kwargs: calls.append(base) or {})
    gpu_service.InferenceEngine({}, profiles).model("vision", "/v1/models", None, "GET")
    gpu_service.InferenceEngine({}, profiles, {"vision": "qwen38_vision"}).model("vision", "/v1/models", None, "GET")
    assert calls == ["http://127.0.0.1:8090/v1", "http://127.0.0.1:8099/v1"]
    for routes in ({"vision": "missing"}, {"other": "qwen38_vision"}, []):
        with pytest.raises(ValueError):
            gpu_service.InferenceEngine({}, profiles, routes)


@pytest.mark.parametrize("modalities,valid", [(["text"], False), (["text", "image", "video"], True), (None, True)])
def test_vision_check_uses_advertised_inputs(monkeypatch, modalities, valid):
    catalog = {"data": [{"id": "actual-vision", "architecture": {"input_modalities": modalities}}]}
    monkeypatch.setattr(providers, "list_models", lambda *a, **kw: catalog)
    profile = {"model": "actual-vision", "vision": True, "enabled": True}
    if valid:
        assert providers.check_model(profile) == catalog
    else:
        with pytest.raises(providers.ProviderError) as exc:
            providers.check_model(profile)
        assert exc.value.code == "vision_unsupported"


def test_migration_uses_actual_model_ids_and_preserves_unrelated_settings():
    paths = {key: Path("/private") / key for key in deploy_cami.TLS_ENV}
    current = deploy_cami.new_configuration(paths)
    current["video_profiles"] = {"local_wan": copy.deepcopy(video_providers.LOCAL_PROFILE)}
    current["video_roles"] = {"video_generator": "local_wan"}
    current["production"]["video_profile"] = "fastwan"
    current["private_setting"] = {"keep": True}
    class Client:
        base = deploy_cami.WKP
        def require_h3(self):
            return {"installed": True}
        def request(self, route):
            name = route.split("/")[2]
            if route.endswith("/models"):
                return {"data": [{"id": "actual-" + name}]}
            return {"modalities": {"vision": name == "vision"}, "default_generation_settings": {"n_ctx": 16384}}
    updated = configure_wkp_h3.verified_configuration(current, Client())
    assert updated["video_profiles"]["local_wan"]["enabled"] is False
    assert updated["video_roles"]["video_generator"] == "wkp_h3"
    assert updated["llm_profiles"]["vision"]["model"] == "actual-vision"
    assert updated["llm_profiles"]["primary"]["vision"] is False
    assert updated["private_setting"] == current["private_setting"]
    assert current["video_roles"]["video_generator"] == "local_wan"
