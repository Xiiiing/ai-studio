from __future__ import annotations
import hashlib, json, os, re, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMFY = Path(os.environ.get("DRAMA_COMFY_HOME", str(ROOT.parent / "ComfyUI"))).expanduser().resolve()
PYTHON = Path(os.environ.get("DRAMA_MEDIA_PYTHON", str(ROOT / ".venv/bin/python")))
# Preserve the shared lease namespace on upgraded installations. New installations
# use their own runtime directory; peers can share DRAMA_GPU_LOCK_DIR explicitly.
_legacy_locks = ROOT.parent / "ai_short_drama/pilot_01/00_admin/runtime/gpu_locks"
GPU_LOCK_DIR = Path(os.environ.get("DRAMA_GPU_LOCK_DIR", str(
    _legacy_locks if _legacy_locks.is_dir() else ROOT / "runtime/gpu_locks"))).expanduser().resolve()

def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def write_json(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix=path.name+".",suffix=".tmp",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(value,f,ensure_ascii=False,indent=2);f.write("\n");f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def sha256(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(8*1024*1024),b""):h.update(block)
    return h.hexdigest()

def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def within(base,path):
    base=Path(base).resolve();p=(base/Path(path)).resolve()
    if not p.is_relative_to(base):raise ValueError("Path escapes project: "+str(path))
    return p

def episode_path(slug):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,60}",slug):raise ValueError("Invalid episode slug")
    return ROOT/"production"/slug

def load_episode(slug):
    folder=episode_path(slug);data=read_json(folder/"episode.json")
    if data.get("slug")!=slug:raise ValueError("Episode slug mismatch")
    shots=data.get("shots",[])
    if not shots:raise ValueError("Empty episode")
    if data.get("fps")!=24:raise ValueError("This production profile requires 24 fps")
    generation=data.get("generation",{})
    for axis in ["width","height"]:
        raw=generation.get(axis,0);crop=data.get(axis,0)
        if not isinstance(raw,int) or raw%32 or not 0<crop<=raw:raise ValueError("Invalid native/crop geometry")
    if abs(sum(float(s["seconds"]) for s in shots)-float(data.get("total_seconds",0)))>.001:raise ValueError("Episode duration does not match shots")
    seen=set();seen_dialogue=set()
    for s in shots:
        if not re.fullmatch(r"S[0-9]{2,3}",s["id"]) or s["id"] in seen:raise ValueError("Duplicate or invalid shot id")
        seen.add(s["id"])
        if not 0<float(s["seconds"])<=15:raise ValueError("Shot duration outside 0..15")
        if not str(s.get("prompt","")).strip():raise ValueError("Missing shot prompt")
        for line in s.get("dialogue",[]):
            if not re.fullmatch(r"[A-Za-z0-9_-]+",line["id"]) or line["id"] in seen_dialogue:raise ValueError("Invalid or duplicate dialogue id")
            seen_dialogue.add(line["id"])
            if not 0<=float(line.get("start",0))<float(s["seconds"])-.15:raise ValueError("Dialogue onset falls outside shot")
            if not str(line.get("text","")).strip() or line.get("role") not in data.get("cast",{}):raise ValueError("Missing dialogue or undefined role")
    return folder,data

def media_receipt(path,**extra):
    p=Path(path)
    return {"path":str(p.relative_to(ROOT)),"bytes":p.stat().st_size,"sha256":sha256(p),**extra}
