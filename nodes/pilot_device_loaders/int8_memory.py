"""Bound eager INT8 intermediates without replacing quantization arithmetic.

Comfy Kitchen 0.2.26 retains a full INT32 GEMM result and all scaled chunks,
then allocates a second complete output with cat(). On TITAN this can exceed
VRAM even when every model weight is offloaded. Each activation row is
independent after preprocessing. Keep activation/ConvRot at their original
full shape (CUDA half GEMM rounding depends on that shape), then invoke the
unchanged upstream operation on bounded row slices for integer accumulation,
scaling and bias.
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
        # ConvRot's half-precision Hadamard GEMM must retain its original shape:
        # rotating individual chunks can change CUDA accumulation/rounding.
        # Reuse the helpers referenced by this exact upstream implementation.
        if convrot or input_act not in (None, "none"):
            helpers = original.__globals__
            required = ("_apply_input_act", "_build_hadamard", "_rotate_activation")
            if any(not callable(helpers.get(name)) for name in required):
                raise RuntimeError("H3 INT8 adapter requires verified upstream preprocessing helpers")
            x = helpers["_apply_input_act"](x, input_act)
            if convrot:
                if x.shape[-1] % convrot_groupsize:
                    raise ValueError("ConvRot group size must divide input features")
                hadamard = helpers["_build_hadamard"](convrot_groupsize, device=x.device, dtype=x.dtype)
                x = helpers["_rotate_activation"](x, hadamard, convrot_groupsize)
            flat = x.reshape(-1, x.shape[-1])
            kwargs.update(convrot=False, input_act=None)
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


def install(registry, *, scratch_mib=64):
    """Use the public backend API; preserve all other eager functions/constraints."""
    if type(scratch_mib) is not int or not 64 <= scratch_mib <= 512:
        raise ValueError("H3 INT8 scratch budget must be an integer within 64..512 MiB")
    original = registry.get_implementation("int8_linear", backend="eager")
    if getattr(original, "_pilot_h3_chunked_int8", False):
        return
    names = registry.list_backends()["eager"]["capabilities"]
    implementations = {name: registry.get_implementation(name, backend="eager") for name in names}
    constraints = {name: registry.get_constraints("eager", name) for name in names}
    implementations["int8_linear"] = row_chunked(original, scratch_bytes=scratch_mib * 1024 * 1024)
    registry.register("eager", SimpleNamespace(**implementations), constraints)
    logging.info("H3 worker: bounded eager INT8 row chunks enabled (%s MiB scratch); upstream arithmetic retained", scratch_mib)
