#!/usr/bin/env python3
"""Fetch official, pinned SyncNet evaluation weights; never alter inference manifest."""
import json, os, time
from pathlib import Path
from download_models import fetch, get_json

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "models/lipsync"
REPO = "ByteDance/LatentSync-1.5"
REVISION = "32a20d29aead0498e3e885e90dbbe8027da1b61b"
FILES = {
    "auxiliary/syncnet_v2.model": (54573114, "961e8696f888fce4f3f3a6c3d5b3267cf5b343100b238e79b2659bff2c605442"),
    "auxiliary/sfd_face.pth": (89844381, "d54a87c2b7543b64729c9a25eafd188da15fd3f6e02f0ecec76ae1b30d86c491"),
}

def main():
    metadata_url = "https://huggingface.co/api/models/" + REPO + "/revision/" + REVISION + "?blobs=true"
    info = get_json(metadata_url)
    if info["sha"] != REVISION:
        raise RuntimeError("Official evaluation model revision mismatch")
    metadata = {f["rfilename"]: f for f in info["siblings"]}
    record = {
        "status": "downloading", "purpose": "independent SyncNet evaluation, not lip generation",
        "repo": REPO, "revision": REVISION, "metadata_url": metadata_url,
        "repo_model_card_license": info.get("cardData", {}).get("license"),
        "commercial_release_cleared": False,
        "terms_note": "The repository card declares openrail++; individual auxiliary weight provenance/terms are not independently cleared. This evaluation does not remove the InsightFace inference weight non-commercial-research restriction.",
        "files": [],
    }
    manifest = BASE / "evaluation_manifest.json"
    def save():
        record["updated_at"] = time.time()
        temporary = manifest.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, manifest)
    save()
    for name, (size, digest) in FILES.items():
        item = metadata[name]
        if item["size"] != size or item["lfs"]["sha256"] != digest:
            raise RuntimeError("Official pinned metadata mismatch: " + name)
        target = BASE / "checkpoints" / name
        url = "https://huggingface.co/" + REPO + "/resolve/" + REVISION + "/" + name
        got = fetch(url, target, size, digest)
        record["files"].append({"path": str(target.relative_to(BASE)), "bytes": size, "sha256": got,
                                "source": url, "source_revision": REVISION,
                                "upstream_sha256": digest, "verification": "upstream_lfs_sha256"})
        save()
    record["status"] = "verified"
    save()
    print(json.dumps(record, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
