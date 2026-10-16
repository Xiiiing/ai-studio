"""Pinned local vision artifacts and bounded GGUF identity checks (no tensor reads)."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import struct
import urllib.request

QWEN38_PROJECTOR = {
    "repository": "ggml-org/Qwen3.8-27B-GGUF",
    "revision": "71bc7b627595dc8a91039addd9c791ae548d6747",
    "filename": "mmproj-Qwen3.8-27B-BF16.gguf",
    "sha256": "de2a49866988ea272c43dc2a43ee2662c25725a717ca5b9059ea420a97f53fe8",
    "bytes": 931145888,
}


def gguf_metadata(path):
    """Reject malformed/oversized headers without loading a model into memory."""
    formats = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f",
               7: "?", 10: "Q", 11: "q", 12: "d"}
    with Path(path).open("rb") as handle:
        def unpack(fmt):
            size = struct.calcsize("<" + fmt)
            data = handle.read(size)
            if len(data) != size:
                raise ValueError("Truncated GGUF header")
            return struct.unpack("<" + fmt, data)[0]

        def string():
            size = unpack("Q")
            if size > 2 * 1024 * 1024:
                raise ValueError("Oversized GGUF metadata string")
            data = handle.read(size)
            if len(data) != size:
                raise ValueError("Truncated GGUF string")
            return data.decode("utf-8")

        def value(kind, depth=0):
            if handle.tell() > 32 * 1024 * 1024 or depth > 2:
                raise ValueError("Oversized GGUF metadata")
            if kind == 8:
                return string()
            if kind == 9:
                subtype, count = unpack("I"), unpack("Q")
                if count > 1000000:
                    raise ValueError("Oversized GGUF metadata array")
                # Token lists are intentionally consumed but not retained.
                for _ in range(count):
                    value(subtype, depth + 1)
                return None
            if kind not in formats:
                raise ValueError("Unsupported GGUF metadata type")
            return unpack(formats[kind])

        if handle.read(4) != b"GGUF" or unpack("I") not in (2, 3):
            raise ValueError("Expected GGUF v2/v3")
        unpack("Q")  # tensor count
        count = unpack("Q")
        if count > 10000:
            raise ValueError("Oversized GGUF metadata count")
        result = {}
        for _ in range(count):
            key = string()
            result[key] = value(unpack("I"))
        return result


def verify_pair(model, projector):
    base, vision = gguf_metadata(model), gguf_metadata(projector)
    def identity(data):
        names = [str(v).lower() for k, v in data.items()
                 if k in {"general.name", "general.basename"} or k.endswith("repo_url")]
        return any("qwen3.8-27b" in name for name in names)
    if not identity(base) or not identity(vision):
        raise ValueError("Both GGUF components must identify Qwen3.8-27B")
    if vision.get("general.architecture") != "clip" or vision.get("general.type") != "mmproj" or vision.get("clip.has_vision_encoder") is not True:
        raise ValueError("Expected Qwen3.8 vision projector metadata")
    return {"model_name": base.get("general.name"), "projector_name": vision.get("general.name"),
            "projector_type": vision.get("clip.projector_type"), "vision": True}


def prepare_projector(directory):
    """Explicit installation only; preserves existing files and publishes after hashing."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    spec = QWEN38_PROJECTOR
    destination = directory / spec["filename"]
    def verified(path):
        with path.open("rb") as handle:
            return path.stat().st_size == spec["bytes"] and hashlib.file_digest(handle, "sha256").hexdigest() == spec["sha256"]
    if destination.exists():
        if not verified(destination):
            raise ValueError("Existing projector differs from pinned artifact; preserved")
        return destination
    temporary = destination.with_suffix(".part")
    # Exclusive temporary file prevents concurrent installers from sharing bytes.
    try:
        with temporary.open("xb") as output:
            url = "https://huggingface.co/" + spec["repository"] + "/resolve/" + spec["revision"] + "/" + spec["filename"]
            with urllib.request.urlopen(url, timeout=120) as response:
                count = 0
                while block := response.read(8 * 1024 * 1024):
                    count += len(block)
                    if count > spec["bytes"]:
                        raise ValueError("Projector exceeds pinned size")
                    output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if not verified(temporary):
            raise ValueError("Projector checksum/size mismatch")
        # Publish without replacing a file another process may have created.
        os.link(temporary, destination)
        return destination
    finally:
        # Do not remove a different installer's pre-existing partial download.
        if 'output' in locals():
            temporary.unlink(missing_ok=True)
