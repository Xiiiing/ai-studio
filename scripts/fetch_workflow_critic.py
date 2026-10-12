"""Fetch the pinned public text-critic weight and verify its published digest."""
from __future__ import annotations
import hashlib
import json
import time
import urllib.request
from pathlib import Path
from studio.core import ROOT, write_json

REVISION = "46c7e1df907ca15264d495d74c1205136a0ef746"
NAME = "Qwen3.5-9B-Q6_K.gguf"
SIZE = 7458301152
SHA256 = "91898433cf5ce0a8f45516a4cc3e9343b6e01d052d01f684309098c66a326c59"
URL = "https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/" + REVISION + "/" + NAME

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def main():
    target = ROOT / "models" / "workflow" / NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    if not target.exists():
        part = target.with_suffix(target.suffix + ".part")
        for attempt in range(1, 4):
            offset = part.stat().st_size if part.exists() else 0
            if offset > SIZE:
                raise ValueError("Partial weight exceeds expected length; retained for inspection")
            if offset == SIZE:
                break
            request = urllib.request.Request(URL, headers={"User-Agent": "drama-workflow-model-fetch/1",
                                                           **({"Range": "bytes=%d-" % offset} if offset else {})})
            try:
                with urllib.request.urlopen(request, timeout=40) as response:
                    if offset and (response.status != 206 or not response.headers.get("Content-Range", "").startswith("bytes %d-" % offset)):
                        raise ValueError("Server did not honor verified partial download range")
                    last_log = time.monotonic()
                    mode = "ab" if offset else "wb"
                    with part.open(mode) as output:
                        while True:
                            block = response.read(8 * 1024 * 1024)
                            if not block:
                                break
                            output.write(block)
                            offset += len(block)
                            if offset > SIZE:
                                raise ValueError("Weight response exceeds expected size")
                            if time.monotonic() - last_log >= 15:
                                print(json.dumps({"stage": "download", "bytes": offset, "total": SIZE, "attempt": attempt}), flush=True)
                                last_log = time.monotonic()
                if part.stat().st_size == SIZE:
                    break
            except (OSError, TimeoutError) as exc:
                print(json.dumps({"stage": "download_retry", "attempt": attempt, "error_type": type(exc).__name__, "bytes": part.stat().st_size if part.exists() else 0}), flush=True)
                if attempt == 3:
                    raise
        if part.stat().st_size != SIZE or digest(part) != SHA256:
            raise ValueError("Downloaded weight failed size or SHA256 verification; partial retained")
        part.replace(target)
    if target.stat().st_size != SIZE or digest(target) != SHA256:
        raise ValueError("Existing weight failed size or SHA256 verification")
    receipt = {"schema": "workflow.model_download.v1", "base_model": "Qwen/Qwen3.5-9B",
               "quantization_publisher": "unsloth", "repository": "unsloth/Qwen3.5-9B-GGUF",
               "revision": REVISION, "source_url": URL, "path": str(target.relative_to(ROOT)),
               "bytes": SIZE, "sha256": SHA256, "verified": True,
               "elapsed_seconds": round(time.monotonic() - started, 3)}
    write_json(ROOT / "runtime/factory/workflow_upgrade_20261012/model_download.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
