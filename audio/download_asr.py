#!/usr/bin/env python3
"""Download the pinned small Whisper conversion used for blind dialogue checks."""
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
from huggingface_hub import HfApi, snapshot_download

REPO_ID = "Systran/faster-whisper-small"
REVISION = "536b0662742c02347bc0e980a01041f333bce120"
TARGET = Path(__file__).resolve().parents[1] / "models/tts/faster-whisper-small"

def main():
    info = HfApi(token=False).model_info(REPO_ID, revision=REVISION, files_metadata=True, timeout=30)
    patterns = ["config.json", "model.bin", "tokenizer.json", "vocabulary.*", "preprocessor_config.json", "README.md"]
    snapshot_download(REPO_ID, revision=REVISION, local_dir=str(TARGET),
                      allow_patterns=patterns, token=False, max_workers=2)
    files = {}
    for upstream in info.siblings:
        path = TARGET / upstream.rfilename
        if not path.is_file():
            continue
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
                h.update(chunk)
        sha = h.hexdigest()
        expected = getattr(getattr(upstream, "lfs", None), "sha256", None)
        if expected and expected != sha:
            raise ValueError(f"Checksum mismatch: {upstream.rfilename}")
        files[upstream.rfilename] = {"sha256": sha, "bytes": path.stat().st_size, "upstream_lfs_sha256": expected}
    for required in ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]:
        if required not in files:
            raise FileNotFoundError(TARGET / required)
    receipt = {"repo": REPO_ID, "revision": info.sha, "files": files,
               "created_at": datetime.now(timezone.utc).isoformat()}
    (TARGET / "download_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "verified", "model": str(TARGET), "bytes": sum(f["bytes"] for f in files.values())}), flush=True)

if __name__ == "__main__":
    main()
