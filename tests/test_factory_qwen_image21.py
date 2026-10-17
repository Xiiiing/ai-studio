"""Wkp Qwen-Image-2.1 typed inference contract and replay safety (CPU-only)."""
import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from factory import gpu_service
from factory import qwen_image21 as image21


def request():
    return {
        "request_id": "a" * 64,
        "kind": "qwen_image21",
        "prompt": "An adult fictional woman in a train station.",
        "negative_prompt": "watermark",
        "seed": 20261017,
        "steps": 8,
        "width": 512,
        "height": 768,
        "inputs": [],
        "timeout_seconds": 900,
        "max_gpu_seconds": 900,
    }


def _image_reference(tmp_path, fmt="PNG"):
    f = tmp_path / "ref.png"
    Image.new("RGB", (64, 96), "navy").save(f, format=fmt)
    raw = f.read_bytes()
    return {"name": f.name,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "data": base64.b64encode(raw).decode()}


def _inner(payload):
    fields = {"prompt", "negative_prompt", "seed", "steps",
              "width", "height", "inputs"}
    return {k: payload[k] for k in fields if k in payload}


def test_image_request_is_bounded_and_no_unsafe_extensions(tmp_path):
    req = request()
    assert gpu_service.validate_request(req)["kind"] == "qwen_image21"
    for field, value in [
        ("prompt", ""), ("seed", -1), ("seed", 3.5),
        ("steps", 0), ("steps", 41), ("height", 767),
        ("width", 2048), ("width", True), ("inputs", [None]),
    ]:
        bad = copy.deepcopy(req)
        bad[field] = value
        with pytest.raises((TypeError, ValueError)):
            gpu_service.validate_request(bad)
    with pytest.raises(ValueError):
        gpu_service.validate_request({**req, "model": "/unsafe/model.safetensors"})
    ref = _image_reference(tmp_path)
    assert image21.validate_request({**_inner(req), "inputs": [ref]})["inputs"]
    bad = copy.deepcopy(req)
    bad["inputs"] = [{**ref, "name": "../../hijack.png"}]
    with pytest.raises(ValueError):
        gpu_service.validate_request(bad)
    bad["inputs"] = [{**ref, "sha256": "0" * 64}]
    with pytest.raises(ValueError):
        gpu_service.validate_request(bad)


def test_graph_is_fixed_and_reference_inputs_are_expanded(tmp_path):
    req = _inner(request())
    graph = image21.build_graph(req, [], "wkp-image21/" + "b" * 20)
    assert graph["1"]["inputs"]["unet_name"] == image21.IMAGE_MODEL
    assert graph["2"]["inputs"]["device"] == "cpu"
    assert graph["5"]["inputs"]["width"] == 512
    assert "images" in graph["4"]["inputs"]
    ref = _image_reference(tmp_path)
    edit = image21.build_graph(
        {**req, "inputs": [ref]}, ["abcdef.png"],
        "wkp-image21/" + "c" * 20)
    assert edit["4"]["inputs"]["images.image_1"] == ["20", 0]
    assert edit["4"]["inputs"]["resolution"] == 0
    assert "images" not in edit["4"]["inputs"]
    assert edit["5"]["inputs"]["height"] == 768
    with pytest.raises(ValueError):
        image21.build_graph(req, [], "../evil")


def test_comfy_submission_is_durable_verified_and_not_resubmitted(tmp_path, monkeypatch):
    root = tmp_path / "root"
    image21_root = root / "runtime/qwen-image-2.1"
    output = image21_root / "output/wkp-image21"
    output.mkdir(parents=True)
    sample = output / "safe_00001_.png"
    Image.new("RGB", (512, 768), "navy").save(sample)
    monkeypatch.setattr(image21, "ROOT", root)
    monkeypatch.setattr(image21, "MODEL_ROOT", image21_root)
    monkeypatch.setattr(image21, "INPUT_DIR", image21_root / "input")
    monkeypatch.setattr(image21, "OUTPUT_DIR", image21_root / "output")
    monkeypatch.setattr(image21, "installed", lambda: True)
    calls = []

    def api(route, payload=None, timeout=12):
        calls.append(route)
        if route == "/prompt":
            assert payload["prompt"]["1"]["inputs"]["unet_name"] == image21.IMAGE_MODEL
            return {"prompt_id": "b" * 36}
        return {
            "b" * 36: {
                "status": {"status_str": "success"},
                "outputs": {"8": {"images": [{
                    "filename": "safe_00001_.png",
                    "subfolder": "wkp-image21", "type": "output"}]}}
            }}
    monkeypatch.setattr(image21, "_json_request", api)
    dest = root / "runtime/inference/task/output"
    params = _inner(request())
    file, receipt = image21.render(params, dest, timeout_seconds=40, max_gpu_seconds=40)
    assert file.exists()
    assert Image.open(file).size == (512, 768)
    assert receipt["model"] == "Qwen-Image-2.1"
    assert receipt["sha256"] == hashlib.sha256(file.read_bytes()).hexdigest()
    assert (dest / "image21_submission.json").exists()
    assert calls.count("/prompt") == 1
    file2, receipt2 = image21.render(params, dest, timeout_seconds=40)
    assert file2 == file and receipt2["sha256"] == receipt["sha256"]
    assert calls.count("/prompt") == 1
    with pytest.raises(RuntimeError, match="incompatible inputs"):
        image21.render({**params, "prompt": "Another image"}, dest)


def test_unknown_submission_never_replays(tmp_path, monkeypatch):
    image21_root = tmp_path / "runtime/qwen-image-2.1"
    monkeypatch.setattr(image21, "ROOT", tmp_path)
    monkeypatch.setattr(image21, "MODEL_ROOT", image21_root)
    monkeypatch.setattr(image21, "INPUT_DIR", image21_root / "input")
    monkeypatch.setattr(image21, "OUTPUT_DIR", image21_root / "output")
    monkeypatch.setattr(image21, "installed", lambda: True)
    posts = []
    def api(route, payload=None, timeout=12):
        posts.append(route)
        raise TimeoutError("Simulated uncertainty after POST")
    monkeypatch.setattr(image21, "_json_request", api)
    dest = tmp_path / "runtime/inference/other/output"
    params = _inner(request())
    with pytest.raises(TimeoutError):
        image21.render(params, dest)
    assert json.loads((dest / "image21_submission.json").read_text())["state"] == "dispatching"
    with pytest.raises(RuntimeError, match="not resubmitting"):
        image21.render(params, dest)
    assert posts.count("/prompt") == 1


def test_native_image_dimensions_are_normalized_without_losing_original(tmp_path):
    source = tmp_path / "native.png"
    Image.new("RGB", (512, 768), "navy").save(source)
    destination = tmp_path / "out" / "image.png"
    destination.parent.mkdir()
    assert image21.normalize_image(source, destination, 256, 384) == [512, 768]
    assert Image.open(destination).size == (256, 384)
    assert Image.open(destination.parent / "image_native.png").size == (512, 768)
    with pytest.raises(RuntimeError, match="geometry"):
        image21.normalize_image(source, destination, 512, 512)
    with pytest.raises(RuntimeError, match="geometry"):
        image21.normalize_image(source, destination, 768, 1152)


def test_inference_engine_returns_verified_artifact(monkeypatch, tmp_path):
    def fake_render(payload, dest, **kwargs):
        dest.mkdir(parents=True)
        path = dest / "image.png"
        Image.new("RGB", (256, 384), "navy").save(path)
        return path, {"provider": "comfyui_qwen_image21", "gpu_seconds": 2.5,
                      "model": "Qwen-Image-2.1"}
    monkeypatch.setattr(image21, "render", fake_render)
    engine = gpu_service.InferenceEngine({"gpu_cards": [4]})
    req = request()
    assert gpu_service.validate_request(req) == req
    result = engine.execute(req, tmp_path / "job")
