"""Bound eager INT8 intermediates without replacing quantization arithmetic.

Comfy Kitchen 0.2.26 retains a full INT32 GEMM result and all scaled chunks,
then allocates a second complete output with cat(). On TITAN this can exceed
VRAM even when every model weight is offloaded. Each activation row is
independent: invoking the unchanged upstream operation on bounded row slices
retains ConvRot, quantization, integer accumulation, scaling and bias order.
Only the owned H3 Comfy subprocess registers this adapter.
"""
from functools import wraps
import inspect
import logging
from types import SimpleNamespace


def row_chunked(original, *, max_rows=1024, scratch_bytes=64 * 1024 * 1024):
    expected = {"x", "weight", "weight_scale", "bias", "out_dtype", "convrot", "convrot_groupsize", "input_act"}
    parameters = inspect.signature(original).parameters
    if set(parameters) != expected:
        raise RuntimeError("H3 INT8 adapter requires the verified Comfy Kitchen int8_linear signature")
    default_dtype = parameters["out_dtype"].default
    if type(max_rows) is not int or max_rows < 1 or type(scratch_bytes) is not int or scratch_bytes < 1:
        raise ValueError("INT8 chunk bounds must be positive integers")

    @wraps(original)
    def bounded(x, weight, weight_scale, bias=None, out_dtype=default_dtype,
                convrot=False, convrot_groupsize=256, input_act=None):
        shape = x.shape
        flat = x.reshape(-1, shape[-1])
        rows, features = flat.shape
        columns = weight.shape[0]
        # Account conservatively for INT32 accumulation, FP32 scaling copies,
        # activation quantization/rotation and FP16/BF16 output per chunk.
        chunk_rows = max(1, min(max_rows, scratch_bytes // max(1, columns * 16 + features * 16)))
        kwargs = {"bias": bias, "out_dtype": out_dtype, "convrot": convrot,
                  "convrot_groupsize": convrot_groupsize, "input_act": input_act}
        if rows <= chunk_rows:
            return original(x, weight, weight_scale, **kwargs)
        output = x.new_empty((rows, columns), dtype=out_dtype)
        for start in range(0, rows, chunk_rows):
            stop = min(rows, start + chunk_rows)
            # No full-size INT32 result or list of output chunks is retained.
            block = original(flat[start:stop], weight, weight_scale, **kwargs)
            output[start:stop] = block
            del block
        return output.reshape(*shape[:-1], columns)

    bounded._pilot_h3_chunked_int8 = True
    return bounded


def install(registry):
    """Use the public backend API; preserve all other eager functions/constraints."""
    original = registry.get_implementation("int8_linear", backend="eager")
    if getattr(original, "_pilot_h3_chunked_int8", False):
        return
    names = registry.list_backends()["eager"]["capabilities"]
    implementations = {name: registry.get_implementation(name, backend="eager") for name in names}
    constraints = {name: registry.get_constraints("eager", name) for name in names}
    implementations["int8_linear"] = row_chunked(original)
    registry.register("eager", SimpleNamespace(**implementations), constraints)
    logging.info("H3 worker: bounded eager INT8 row chunks enabled; upstream arithmetic retained")
