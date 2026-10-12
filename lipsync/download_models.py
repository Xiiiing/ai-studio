#!/usr/bin/env python3
"""Download pinned official inference weights and record verification evidence."""
import hashlib, json, os, time, urllib.request, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "models/lipsync"
MANIFEST = BASE / "manifest.json"
AGENT = "drama-studio-lipsync-setup"
SOURCES = [
    ("ByteDance/LatentSync-1.6", "c42c7e6c8e9c213626389fa7d9a3c444b8536353", ["latentsync_unet.pt", "whisper/tiny.pt"], "checkpoints"),
    ("stabilityai/sd-vae-ft-mse", "31f26fdeee1355a5c34592e401dd41e45d25a493", ["config.json", "diffusion_pytorch_model.safetensors"], "vae"),
]
BUFFALO_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
BUFFALO_SIZE = 288621354

def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def save(data):
    BASE.mkdir(parents=True, exist_ok=True)
    terms_path = ROOT / "lipsync/model_terms.json"
    if terms_path.is_file():
        data["model_terms_path"] = str(terms_path.relative_to(ROOT))
        data["model_terms_sha256"] = sha(terms_path)
        data["model_terms"] = json.loads(terms_path.read_text(encoding="utf-8"))
    tmp = MANIFEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, MANIFEST)

def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.load(response)

def fetch(url, destination, size, expected_sha=None):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        digest = sha(destination)
        if destination.stat().st_size != size or (expected_sha and digest != expected_sha):
            raise RuntimeError("Existing artifact does not match expected bytes/hash: " + str(destination))
        return digest
    part = destination.with_suffix(destination.suffix + ".part")
    last_error = None
    for attempt in range(3):
        offset = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": AGENT}
        if offset:
            headers["Range"] = "bytes=%d-" % offset
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=90) as response:
                if offset and response.status == 206:
                    content_range = response.headers.get("Content-Range", "")
                    if not content_range.startswith("bytes %d-" % offset):
                        raise RuntimeError("Invalid resumed Content-Range: " + content_range)
                    mode = "ab"
                elif response.status == 200:
                    offset = 0
                    mode = "wb"
                else:
                    raise RuntimeError("Unexpected download status: %s" % response.status)
                print("DOWNLOAD", destination.name, "offset", offset, "expected", size, flush=True)
                with part.open(mode) as out:
                    for block in iter(lambda: response.read(8 * 1024 * 1024), b""):
                        out.write(block)
                    out.flush()
                    os.fsync(out.fileno())
            if part.stat().st_size != size:
                raise RuntimeError("Wrong download size: %s" % part.stat().st_size)
            digest = sha(part)
            if expected_sha and digest != expected_sha:
                raise RuntimeError("SHA256 mismatch: " + str(destination))
            os.replace(part, destination)
            print("VERIFIED", destination.name, size, digest, flush=True)
            return digest
        except Exception as exc:
            last_error = exc
            print("DOWNLOAD_ATTEMPT_FAILED", destination.name, attempt + 1, type(exc).__name__, str(exc)[:200], flush=True)
            if attempt == 2:
                raise
            time.sleep(2)
    raise last_error

def main():
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {"status": "downloading", "files": []}
    entries = {item["path"]: item for item in manifest.get("files", [])}
    manifest.update({"status": "downloading", "source_commit": "a229c3948406bc2cf6eaf4873e662e70c6a04746", "updated_at": time.time()})
    save(manifest)
    for repo, revision, names, subdir in SOURCES:
        info = get_json("https://huggingface.co/api/models/" + repo + "/revision/" + revision + "?blobs=true")
        if info["sha"] != revision:
            raise RuntimeError("Hub revision mismatch")
        metadata = {f["rfilename"]: f for f in info["siblings"]}
        for name in names:
            item = metadata[name]
            expected = (item.get("lfs") or {}).get("sha256")
            target = BASE / subdir / name
            url = "https://huggingface.co/" + repo + "/resolve/" + revision + "/" + name
            digest = fetch(url, target, item["size"], expected)
            blob = item.get("blobId")
            if not expected and blob:
                body = target.read_bytes()
                git_hash = hashlib.sha1(("blob %d\0" % len(body)).encode() + body).hexdigest()
                if git_hash != blob:
                    raise RuntimeError("Git blob checksum mismatch: " + name)
            entry = {"path": str(target.relative_to(BASE)), "bytes": target.stat().st_size, "sha256": digest, "source": url, "source_revision": revision, "upstream_sha256": expected, "upstream_git_blob": blob, "verification": "upstream_lfs_sha256" if expected else "upstream_git_blob_and_local_sha256"}
            entries[entry["path"]] = entry
            manifest["files"] = list(entries.values())
            manifest["updated_at"] = time.time()
            save(manifest)
    archive = BASE / "buffalo_l-v0.7.zip"
    old = entries.get("buffalo_l-v0.7.zip", {})
    digest = fetch(BUFFALO_URL, archive, BUFFALO_SIZE, old.get("sha256"))
    # The official legacy GitHub release publishes no SHA256 digest.
    # ZIP CRC is checked, then local SHA256 is pinned for all subsequent reads.
    with zipfile.ZipFile(archive) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError("Corrupt buffalo_l archive member: " + bad)
        names = [n for n in z.namelist() if Path(n).name in {"det_10g.onnx", "2d106det.onnx"}]
        if {Path(n).name for n in names} != {"det_10g.onnx", "2d106det.onnx"}:
            raise RuntimeError("Required buffalo_l detector/landmarks not found")
        for member in names:
            target = BASE / "checkpoints/auxiliary/models/buffalo_l" / Path(member).name
            target.parent.mkdir(parents=True, exist_ok=True)
            data = z.read(member)
            target.write_bytes(data)
            rel = str(target.relative_to(BASE))
            entries[rel] = {"path": rel, "bytes": len(data), "sha256": sha(target), "source": BUFFALO_URL, "archive_member": member, "verification": "official_archive_zip_crc_and_pinned_local_sha256"}
    entries["buffalo_l-v0.7.zip"] = {"path": "buffalo_l-v0.7.zip", "bytes": BUFFALO_SIZE, "sha256": digest, "source": BUFFALO_URL, "upstream_sha256": None, "verification": "official_release_size_zip_crc_and_pinned_local_sha256"}
    checkpoint_link = ROOT / "lipsync/vendor/LatentSync/checkpoints"
    if checkpoint_link.is_symlink():
        if checkpoint_link.resolve() != (BASE / "checkpoints").resolve():
            raise RuntimeError("Unexpected checkpoint symlink target")
    elif checkpoint_link.exists():
        raise RuntimeError("Refusing to replace an existing checkpoints directory")
    else:
        checkpoint_link.symlink_to(BASE / "checkpoints", target_is_directory=True)
    manifest.update({"status": "verified", "files": list(entries.values()), "updated_at": time.time(), "notes": ["LatentSync and VAE model tensors matched upstream Hugging Face LFS SHA256.", "Legacy official buffalo_l GitHub release has no published SHA256; archive size and ZIP CRC checked, local SHA256 pinned and rechecked on reuse."]})
    save(manifest)
    print("MODELS_READY", len(entries), str(MANIFEST), flush=True)

if __name__ == "__main__":
    main()
