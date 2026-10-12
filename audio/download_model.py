#!/usr/bin/env python3
"""Download the pinned official Qwen3-TTS model and record verifiable provenance."""
import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
from huggingface_hub import HfApi, snapshot_download

REPO_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
REVISION = "0c0e3051f131929182e2c023b9537f8b1c68adfe"

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--endpoint", default="https://huggingface.co")
    p.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "models/tts/Qwen3-TTS-12Hz-1.7B-CustomVoice")
    a = p.parse_args()
    info = HfApi(endpoint=a.endpoint, token=False).model_info(REPO_ID, revision=REVISION, files_metadata=True, timeout=30)
    print(json.dumps({"status": "download", "repo_id": REPO_ID, "revision": info.sha, "endpoint": a.endpoint}), flush=True)
    a.output.mkdir(parents=True, exist_ok=True)
    snapshot_download(REPO_ID, revision=REVISION, local_dir=str(a.output), endpoint=a.endpoint,
                      max_workers=3, ignore_patterns=[".gitattributes"], token=False)
    files = {}
    for f in info.siblings:
        if f.rfilename == ".gitattributes":
            continue
        path = a.output / f.rfilename
        if not path.is_file():
            raise FileNotFoundError(path)
        h = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                h.update(chunk)
        sha = h.hexdigest()
        expected = getattr(getattr(f, "lfs", None), "sha256", None)
        if expected and sha != expected:
            raise ValueError(f"Hash mismatch: {f.rfilename}")
        files[f.rfilename] = {"size": path.stat().st_size, "sha256": sha, "upstream_lfs_sha256": expected}
    receipt = {"repo_id": REPO_ID, "revision": info.sha, "endpoint": a.endpoint, "files": files,
               "license": "Apache-2.0", "downloaded_at": datetime.now(timezone.utc).isoformat()}
    (a.output / "download_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "verified", "files": len(files), "bytes": sum(x["size"] for x in files.values()), "output": str(a.output)}), flush=True)

if __name__ == "__main__":
    main()
