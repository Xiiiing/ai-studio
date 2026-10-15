"""Durable low-level inference boundary; CPU-only fake engines, no model calls."""
import base64
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import http.client
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from factory import gpu_service as gpu
from studio.core import read_json, write_json
from studio.graphs import flux_image, wan_video


def request(ident="a" * 64):
    return {"request_id": ident, "kind": "comfy", "graph": flux_image("A fictional adult in watercolor.", 42),
            "inputs": [], "output_kind": "image", "timeout_seconds": 900, "max_gpu_seconds": 900}


class Engine:
    def __init__(self):
        self.calls = []
    def execute(self, payload, folder):
        # Intent must be durable before the first side effect.
        assert read_json(folder / "job.json")["status"] == "running"
        self.calls.append(copy.deepcopy(payload))
        artifacts = folder / "artifacts"
        artifacts.mkdir()
        (artifacts / "image.png").write_bytes(b"generated media")
        return {"receipt": {"execution_host": "test-wkp", "gpu_seconds": 7},
                "artifacts": [{"index": 0, "name": "image.png", "bytes": 15,
                               "sha256": hashlib.sha256(b"generated media").hexdigest()}]}
    def health(self):
        return {"ok": True}
    def sources(self, payload, folder):
        return {"expected": {"L01": {"source_sha256": "exact-source", "key": "original-key"}}}


def test_duplicate_concurrent_submission_has_one_persistent_intent_and_execution(tmp_path):
    engine = Engine()
    store = gpu.InferenceStore(tmp_path, engine, start_worker=False)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: store.submit(request()), range(16)))
        assert {state["status"] for state in results} == {"queued"}
        assert store.queue.qsize() == 1
        assert not engine.calls
        assert "request" not in results[0]
        store.execute_one("a" * 64)
        store.execute_one("a" * 64)
        assert len(engine.calls) == 1
        assert store.submit(request())["status"] == "completed"
        assert store.artifact("a" * 64, 0).read_bytes() == b"generated media"
        changed = request()
        changed["graph"]["4"]["inputs"]["text"] = "Different render"
        with pytest.raises(gpu.Conflict, match="different inference inputs"):
            store.submit(changed)
        assert len(engine.calls) == 1
    finally:
        store.close()


def test_restart_recovers_queued_but_never_replays_interrupted_inference(tmp_path):
    engine = Engine()
    store = gpu.InferenceStore(tmp_path, engine, start_worker=False)
    store.submit(request("a" * 64))
    store.submit(request("b" * 64))
    interrupted = tmp_path / ("b" * 64) / "job.json"
    state = read_json(interrupted)
    state["status"] = "running"
    write_json(interrupted, state)
    store.close()
    restored = gpu.InferenceStore(tmp_path, engine, start_worker=False)
    try:
        assert restored.queue.qsize() == 1
        assert restored.get("b" * 64)["status"] == "unresolved"
        restored.execute_one("b" * 64)
        assert not engine.calls
        restored.execute_one("a" * 64)
        assert restored.get("a" * 64)["status"] == "completed"
        assert len(engine.calls) == 1
        assert restored.submit(request("b" * 64))["status"] == "unresolved"
    finally:
        restored.close()


def test_process_ownership_and_artifact_integrity_are_enforced(tmp_path):
    store = gpu.InferenceStore(tmp_path, Engine(), start_worker=False)
    try:
        with pytest.raises(RuntimeError, match="Another inference service"):
            gpu.InferenceStore(tmp_path, Engine(), start_worker=False)
        store.submit(request())
        with pytest.raises(gpu.Conflict, match="not ready"):
            store.artifact("a" * 64, 0)
        store.execute_one("a" * 64)
        store.artifact("a" * 64, 0).write_bytes(b"changed bytes")
        with pytest.raises(gpu.Conflict, match="receipt"):
            store.artifact("a" * 64, 0)
        with pytest.raises(FileNotFoundError):
            store.get("b" * 64)
        with pytest.raises(ValueError):
            store.get("../../etc/passwd")
    finally:
        store.close()


def test_failed_inference_retains_usage_and_is_not_reexecuted(tmp_path):
    class Failed(Engine):
        def execute(self, payload, folder):
            self.calls.append(payload)
            error = RuntimeError("bounded inference failed")
            error.gpu_receipt = {"execution_host": "test-wkp", "gpu_seconds": 11.5}
            raise error
    engine = Failed()
    store = gpu.InferenceStore(tmp_path, engine, start_worker=False)
    try:
        store.submit(request())
        store.execute_one("a" * 64)
        state = store.get("a" * 64)
        assert state["status"] == "failed"
        assert state["result"]["receipt"]["gpu_seconds"] == 11.5
        store.execute_one("a" * 64)
        assert store.submit(request())["status"] == "failed"
        assert len(engine.calls) == 1
    finally:
        store.close()


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(kind="generate_project"),
    lambda p: p.update(project={"id": "business-project"}),
    lambda p: p["graph"]["1"].update(class_type="ExecutePython"),
    lambda p: p["graph"]["1"]["inputs"].update(unet_name="../../private/model.safetensors"),
    lambda p: p["graph"]["12"]["inputs"].update(filename_prefix="../production/destroy"),
    lambda p: p["graph"]["5"]["inputs"].update(batch_size=1000),
    lambda p: p["graph"]["5"]["inputs"].update(width=8192),
    lambda p: p["graph"]["1"]["inputs"].update(unet_name=["4", 0]),
    lambda p: p.update(timeout_seconds=float("inf")),
    lambda p: p.update(max_gpu_seconds=-1),
])
def test_untrusted_graph_and_business_payloads_rejected_before_persistence(tmp_path, mutate):
    store = gpu.InferenceStore(tmp_path, Engine(), start_worker=False)
    try:
        payload = request()
        mutate(payload)
        with pytest.raises((ValueError, TypeError)):
            store.submit(payload)
        assert not list(tmp_path.glob("*/job.json"))
        assert store.queue.empty()
    finally:
        store.close()


def test_existing_flux_references_and_fastwan_graphs_fit_allowlist():
    data = b"encoded image contents"
    image = {"name": "frame.png", "data": base64.b64encode(data).decode(), "sha256": hashlib.sha256(data).hexdigest()}
    payload = request()
    payload.update(inputs=[image], graph=flux_image("Reference", 12, references=["frame.png"]))
    assert gpu.validate_request(payload) == payload
    payload.update(output_kind="video", graph=wan_video("Walking", 12, "frame.png"))
    gpu.validate_request(payload)
    profile = {"lora": "Wan2_2_5B_FastWanFullAttn_lora_rank_128_bf16.safetensors", "sigmas": [1, .757, .522, 0], "sampler": "lcm"}
    payload["graph"] = wan_video("Walking", 12, "frame.png", profile=profile)
    gpu.validate_request(payload)
    payload["inputs"][0]["sha256"] = "a" * 64
    with pytest.raises(ValueError, match="digest"):
        gpu.validate_request(payload)


def test_tts_contract_has_only_bounded_inference_inputs():
    payload = {"lines": [{"id": "S01_L01", "role": "female", "text": "把钥匙还给你", "emotion": "平静"}],
               "voices": {"female": {"speaker": "Serena", "language": "Chinese"}},
               "options": {"tts_dtype": "float32", "tts_max_new_tokens": 768},
               "project_profile": "compact6"}
    assert gpu.validate_speech(payload) == payload
    for extra in ({"command": "shell"}, {"options": {"model": "/arbitrary/path"}},
                  {"lines": [{"id": "../escape", "role": "female", "text": "hello"}]},
                  {"options": {"tts_max_new_tokens": 999999}}):
        with pytest.raises(ValueError):
            gpu.validate_speech({**payload, **extra})


@pytest.fixture
def server(tmp_path):
    store = gpu.InferenceStore(tmp_path, Engine(), start_worker=False)
    instance = gpu.ThreadingHTTPServer(("127.0.0.1", 0), gpu.Handler)
    instance.store = store
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()
        store.close()


def call_http(server, method, path, payload=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    body = json.dumps(payload) if payload is not None else None
    connection.request(method, path, body=body, headers={"Content-Type": "application/json", **(headers or {})})
    response = connection.getresponse()
    result = response.status, response.read()
    connection.close()
    return result


def test_real_http_job_submit_conflict_404_and_binary_receipt(server):
    assert call_http(server, "GET", "/health")[0] == 200
    assert call_http(server, "GET", "/jobs/" + "b" * 64)[0] == 404
    assert call_http(server, "POST", "/jobs", request())[0] == 202
    changed = request()
    changed["timeout_seconds"] = 500
    assert call_http(server, "POST", "/jobs", changed)[0] == 409
    server.store.execute_one("a" * 64)
    assert call_http(server, "GET", "/jobs/" + "a" * 64 + "/artifacts/0") == (200, b"generated media")
    assert call_http(server, "GET", "/jobs/" + "a" * 64 + "/artifacts/2")[0] == 404
    assert call_http(server, "GET", "/jobs/../../etc/passwd")[0] == 404
    status, body = call_http(server, "POST", "/tts/sources", {"lines": [{"id": "L01", "role": "female", "text": "您好"}]})
    assert status == 200
    assert json.loads(body)["expected"]["L01"]["key"] == "original-key"


def test_http_host_origin_and_request_bounds_admitted_before_jobs(server):
    assert call_http(server, "POST", "/jobs", request(), {"Host": "other.invalid"})[0] == 403
    assert call_http(server, "POST", "/jobs", request(), {"Origin": "https://evil.invalid"})[0] == 403
    assert call_http(server, "POST", "/jobs", request(), {"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert call_http(server, "POST", "/jobs", request(), {"Transfer-Encoding": "chunked"})[0] == 400
    assert call_http(server, "POST", "/jobs", request(), {"Content-Length": str(gpu.MAX_BODY + 1)})[0] == 413
    assert server.store.queue.empty()


def test_fixed_model_proxy_retains_credentials_on_server_and_never_orchestrates(monkeypatch):
    from factory import providers
    calls = []
    profiles = {
        "qwen38_primary": {"base_url": "http://127.0.0.1:8095/v1", "model": "primary-model",
                           "api_key_credential": "wkp-primary", "request_timeout_seconds": 480},
        "qwen38_secondary": {"base_url": "http://127.0.0.1:8097/v1", "model": "critic-model"},
        "qwen_vision_existing": {"base_url": "http://127.0.0.1:8090/v1", "model": "vision-model"},
    }
    def proxy(base, route, payload=None, **kwargs):
        calls.append((base, route, payload, kwargs))
        return {"model": "primary-model", "choices": []}
    monkeypatch.setattr(providers, "request_json", proxy)
    engine = gpu.InferenceEngine({}, profiles)
    message = {"model": "primary-model", "messages": [{"role": "user", "content": "写一句话"}], "max_tokens": 10}
    engine.model("primary", "/v1/chat/completions", message, "POST")
    assert calls[-1][0:3] == ("http://127.0.0.1:8095/v1", "/chat/completions", message)
    assert calls[-1][3]["profile"]["api_key_credential"] == "wkp-primary"
    engine.model("critic", "/props", None, "GET")
    assert calls[-1][0:2] == ("http://127.0.0.1:8097", "/props")
    engine.model("vision", "/v1/models", None, "GET")
    assert calls[-1][0:2] == ("http://127.0.0.1:8090/v1", "/models")
    engine.model("primary", "/tokenize", {"content": "model input", "add_special": False}, "POST")
    assert calls[-1][0:2] == ("http://127.0.0.1:8095", "/tokenize")
    count = len(calls)
    for name, route, payload, method in (
        ("other", "/v1/models", None, "GET"),
        ("primary", "/factory/projects", None, "GET"),
        ("primary", "/props", {}, "POST"),
        ("primary", "/v1/chat/completions", {**message, "model": "unapproved"}, "POST"),
        ("primary", "/v1/chat/completions", {**message, "stream": True}, "POST"),
        ("primary", "/v1/chat/completions", {**message, "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "http://private-server/secret"}}]}]}, "POST"),
    ):
        with pytest.raises((FileNotFoundError, ValueError)):
            engine.model(name, route, payload, method)
    assert len(calls) == count


def test_health_reports_file_presence_without_running_gpu_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu, "ROOT", tmp_path)
    image = tmp_path / "models/diffusion_models/flux-2-klein-4b.safetensors"
    image.parent.mkdir(parents=True)
    image.touch()
    health = gpu.InferenceEngine({"gpu_cards": [4, 5, 6, 7]}).health()
    assert health["gpu_cards"] == [4, 5, 6, 7]
    assert health["models"] == {"image": True, "video": False, "audio": False}
    assert health["execution_host"]
    assert health["workflow_execution"] is False
    assert health["model_check"] == "installed_files_only_not_inference"


def test_speech_sources_and_artifacts_match_original_driver_contract(tmp_path, monkeypatch):
    from factory import speech
    monkeypatch.setattr(gpu, "ROOT", tmp_path)
    voices = tmp_path / "audio/voices.json"
    voices.parent.mkdir()
    voices.write_text(json.dumps({"female": {"speaker": "Serena", "language": "Chinese"}}))
    expected = {"L01": {"source": {"line": {"id": "L01", "role": "female", "text": "你好"}},
                         "source_sha256": "line-sha", "key": "line-key", "model_state": {"files": {}}}}
    captured = []
    def sources(session, lines, fixed_voices):
        captured.append((lines, fixed_voices, session.config, session.project))
        return copy.deepcopy(expected)
    def batch(session, lines, fixed_voices, actual):
        assert actual == expected
        output = session.folder / "L01.wav"
        output.write_bytes(b"wav byte sequence")
        metadata = output.with_suffix(".json")
        metadata.write_text(json.dumps({"source": expected["L01"]["source"], "source_sha256": "line-sha"}))
        return {"L01": {"path": str(output.relative_to(tmp_path)),
                        "metadata_path": str(metadata.relative_to(tmp_path)), "source_signature": "line-key"}}
    monkeypatch.setattr(speech.SpeechMixin, "_speech_sources", sources)
    monkeypatch.setattr(speech.SpeechMixin, "_speech_batch", batch)
    engine = gpu.InferenceEngine({"gpu_cards": [4], "tts_seed": 20261012})
    folder = tmp_path / "runtime/inference/job"
    folder.mkdir(parents=True)
    payload = {"kind": "tts", "lines": [{"id": "L01", "role": "female", "text": "你好"}],
               "voices": {"female": {"speaker": "Vivian", "language": "Chinese"}},
               "options": {"tts_dtype": "float32"}, "project_profile": "compact6"}
    assert engine.sources(payload, folder) == {"expected": expected}
    result = engine.execute(payload, folder)
    assert result["expected"] == expected
    assert result["records"]["L01"]["wave_artifact_index"] == 0
    assert result["records"]["L01"]["metadata_artifact_index"] == 1
    assert captured[0][1]["female"]["speaker"] == "Vivian"
    assert captured[0][3] == {"production_profile": "compact6"}
    assert result["receipt"]["execution_host"]
    for artifact in result["artifacts"]:
        path = folder / "artifacts" / artifact["name"]
        assert artifact["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    metadata = json.loads((folder / "artifacts" / result["artifacts"][1]["name"]).read_text())
    assert metadata["source"] == expected["L01"]["source"]
    assert str(tmp_path) not in json.dumps(metadata)
