from __future__ import annotations
import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "Kijai/WanVideo_comfy"
REMOTE_PATH = "FastWan/Wan2_2_5B_FastWanFullAttn_lora_rank_128_bf16.safetensors"
ORIGINAL_REPO = "FastVideo/FastWan2.2-TI2V-5B-FullAttn-Diffusers"


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=45) as response:
        return json.load(response)


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main():
    folder = ROOT / "models/loras"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / Path(REMOTE_PATH).name
    receipt = target.with_suffix(".source.json")
    pending = target.with_suffix(".download.json")
    if pending.exists():
        metadata = json.loads(pending.read_text())
    elif receipt.exists():
        metadata = json.loads(receipt.read_text())
    else:
        upstream = fetch_json(f"https://huggingface.co/api/models/{REPO}")
        revision = upstream["sha"]
        rows = fetch_json(f"https://huggingface.co/api/models/{REPO}/tree/{revision}/FastWan?expand=false&limit=100")
        selected = next(row for row in rows if row["path"] == REMOTE_PATH)
        expected_sha = selected["lfs"]["oid"]
        original = fetch_json(f"https://huggingface.co/api/models/{ORIGINAL_REPO}")
        metadata = {
            "repo": REPO,
            "revision": revision,
            "remote_path": REMOTE_PATH,
            "file": str(target.relative_to(ROOT)),
            "bytes": selected["size"],
            "expected_sha256": expected_sha,
            "upstream_model_repo": ORIGINAL_REPO,
            "upstream_model_revision": original["sha"],
            "upstream_license": original.get("cardData", {}).get("license"),
            "source_model_card": f"https://huggingface.co/{ORIGINAL_REPO}",
            "source_blog": "https://haoailab.com/blogs/fastvideo_post_training/",
            "url": f"https://huggingface.co/{REPO}/resolve/{revision}/{REMOTE_PATH}?download=true",
        }
        pending.write_text(json.dumps(metadata, indent=2) + "\n")
    if target.exists():
        actual = digest(target)
        if target.stat().st_size != metadata["bytes"] or actual != metadata["expected_sha256"]:
            raise RuntimeError("Existing final model does not match the pinned source; preserved")
    else:
        part = target.with_suffix(target.suffix + ".part")
        start = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={start}-"} if start else {}
        print("FASTWAN_DOWNLOAD", metadata["revision"], metadata["bytes"], "resume", start, flush=True)
        request = urllib.request.Request(metadata["url"], headers=headers)
        with urllib.request.urlopen(request, timeout=90) as response:
            if start and (response.status != 206 or not response.headers.get("Content-Range", "").startswith(f"bytes {start}-")):
                raise RuntimeError("Resume range not honored; partial file preserved")
            with part.open("ab" if start else "wb") as stream:
                count = start
                reported = time.monotonic()
                while block := response.read(8 * 1024 * 1024):
                    stream.write(block)
                    count += len(block)
                    if time.monotonic() - reported >= 30:
                        print("FASTWAN_PROGRESS", round(100 * count / metadata["bytes"], 1), flush=True)
                        reported = time.monotonic()
                stream.flush()
                os.fsync(stream.fileno())
        actual = digest(part)
        if part.stat().st_size != metadata["bytes"] or actual != metadata["expected_sha256"]:
            raise RuntimeError("Downloaded model size/checksum does not match pinned source")
        part.rename(target)
    metadata.update(sha256=actual, verified_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    receipt.write_text(json.dumps(metadata, indent=2) + "\n")
    print("FASTWAN_READY", json.dumps(metadata), flush=True)


if __name__ == "__main__":
    main()
