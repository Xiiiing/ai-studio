"""CPU checks for bounded row dispatch; no Comfy/Torch installation required."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

_path = Path(__file__).parents[1] / "nodes/pilot_device_loaders/int8_memory.py"
_spec = importlib.util.spec_from_file_location("h3_int8_memory", _path)
adapter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(adapter)


class Array(np.ndarray):
    def new_empty(self, shape, *, dtype):
        return np.empty(shape, dtype=dtype).view(Array)


def reference(x, weight, weight_scale, bias=None, out_dtype=np.float16,
              convrot=False, convrot_groupsize=256, input_act=None):
    """Independent row-local INT8 reference including rotation and fused activation."""
    x = np.asarray(x)
    if input_act == "swiglu":
        gate, up = np.split(x, 2, axis=-1)
        x = (gate / (1 + np.exp(-gate))) * up
    if convrot:
        h = np.ones((1, 1), dtype=x.dtype)
        while len(h) < convrot_groupsize:
            h = np.block([[h, h], [h, -h]])
        x = (x.reshape(-1, convrot_groupsize) @ h / np.sqrt(convrot_groupsize)).reshape(x.shape)
    scale = np.maximum(np.max(np.abs(x), axis=-1, keepdims=True) / 127, 1e-30)
    q = np.clip(np.rint(x / scale), -128, 127).astype(np.int8)
    accumulated = q.astype(np.int32) @ weight.astype(np.int32).T
    result = (accumulated.astype(np.float32) * (scale * weight_scale)).astype(out_dtype)
    if bias is not None:
        result = result + bias.astype(out_dtype)
    return result


@pytest.mark.parametrize("convrot", [False, True])
@pytest.mark.parametrize("per_channel", [False, True])
@pytest.mark.parametrize("dtype", [np.float16, np.float32])
@pytest.mark.parametrize("input_act", [None, "swiglu"])
def test_chunks_retain_row_quantization_rotation_scales_bias_dtype(convrot, per_channel, dtype, input_act):
    rng = np.random.default_rng(9)
    x = rng.standard_normal((2, 19, 16 if input_act else 8)).astype(np.float32).view(Array)
    x[0, 0] = 0
    weight = rng.integers(-100, 100, (11, 8), dtype=np.int8)
    scales = rng.uniform(.001, .1, 11 if per_channel else 1).astype(np.float32)
    bias = rng.standard_normal(11).astype(np.float32)
    calls = []

    def original(x, weight, weight_scale, bias=None, out_dtype=np.float16,
                 convrot=False, convrot_groupsize=256, input_act=None):
        calls.append(x.shape[0])
        return reference(x, weight, weight_scale, bias, out_dtype, convrot, convrot_groupsize, input_act)

    kwargs = dict(bias=bias, out_dtype=dtype, convrot=convrot, convrot_groupsize=4, input_act=input_act)
    actual = adapter.row_chunked(original, max_rows=7)(x, weight, scales, **kwargs)
    expected = reference(x, weight, scales, **kwargs)
    np.testing.assert_array_equal(actual, expected)
    assert actual.dtype == dtype and actual.shape == (2, 19, 11)
    assert calls == [7, 7, 7, 7, 7, 3]


def test_backend_registration_preserves_other_operations_constraints_and_is_idempotent():
    untouched = lambda: "same operation"
    implementations = {"int8_linear": reference, "other": untouched}
    constraints = {"int8_linear": object(), "other": object()}
    registrations = []

    def register(name, module, limits):
        registrations.append((name, module, limits))
        implementations.update(vars(module))

    registry = SimpleNamespace(
        get_implementation=lambda name, backend: implementations[name],
        list_backends=lambda: {"eager": {"capabilities": list(implementations)}},
        get_constraints=lambda backend, name: constraints[name], register=register)
    adapter.install(registry)
    adapter.install(registry)
    assert len(registrations) == 1
    assert registrations[0][0] == "eager"
    assert registrations[0][1].other is untouched
    assert registrations[0][2] == constraints
    assert implementations["int8_linear"] is not reference


def test_future_backend_signature_fails_explicitly():
    with pytest.raises(RuntimeError, match="verified Comfy Kitchen"):
        adapter.row_chunked(lambda x, new_argument: x)
