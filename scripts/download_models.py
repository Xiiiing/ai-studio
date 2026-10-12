from __future__ import annotations
import argparse, concurrent.futures, hashlib, json, os, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPECS = [
("Comfy-Org/flux2-klein-4B","5f526678002e43af5551dadb73ce2e8c91b43afe","split_files/diffusion_models/flux-2-klein-4b.safetensors"),
("Comfy-Org/flux2-klein-4B","5f526678002e43af5551dadb73ce2e8c91b43afe","split_files/text_encoders/qwen_3_4b.safetensors"),
("Comfy-Org/flux2-klein-4B","5f526678002e43af5551dadb73ce2e8c91b43afe","split_files/vae/flux2-vae.safetensors"),
("Comfy-Org/Wan_2.2_ComfyUI_Repackaged","ee6f4a40737a995bf5818954cfce6d59443b0f04","split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors"),
("Comfy-Org/Wan_2.2_ComfyUI_Repackaged","ee6f4a40737a995bf5818954cfce6d59443b0f04","split_files/text_encoders/umt5_xxl_fp16.safetensors"),
("Comfy-Org/Wan_2.2_ComfyUI_Repackaged","ee6f4a40737a995bf5818954cfce6d59443b0f04","split_files/vae/wan2.2_vae.safetensors"),
]
def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(8*1024*1024),b""):h.update(b)
    return h.hexdigest()
def get_meta(repo,rev,name):
    directory=name.rsplit("/",1)[0]
    url=f"https://huggingface.co/api/models/{repo}/tree/{rev}/{directory}?expand=false&limit=100"
    with urllib.request.urlopen(url,timeout=30) as f: rows=json.load(f)
    entry=next(x for x in rows if x["path"]==name)
    return {"bytes":entry["size"],"sha256":entry.get("lfs",{}).get("oid")}
def download(spec):
    repo,rev,name=spec
    path=ROOT/"models"/Path(name).relative_to("split_files")
    path.parent.mkdir(parents=True,exist_ok=True)
    receipt=path.with_suffix(".source.json")
    meta=get_meta(repo,rev,name)
    url=f"https://huggingface.co/{repo}/resolve/{rev}/{name}?download=true"
    if path.is_file() and path.stat().st_size==meta["bytes"]:
        h=digest(path)
        if not meta["sha256"] or h==meta["sha256"]:
            print("MODEL_VERIFIED",path.name,flush=True)
            return {"file":str(path.relative_to(ROOT)),"repo":repo,"revision":rev,**meta,"sha256":h}
        raise RuntimeError(f"Existing final model checksum mismatch: {path}")
    tmp=path.with_suffix(path.suffix+".part")
    start=tmp.stat().st_size if tmp.exists() else 0
    headers={"Range":f"bytes={start}-"} if start else {}
    print("MODEL_DOWNLOAD",path.name,meta["bytes"],"resume",start,flush=True)
    with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=90) as r:
        if start and (r.status!=206 or not r.headers.get("Content-Range","").startswith(f"bytes {start}-")):
            raise RuntimeError("Server did not honor partial file range; retain for inspection")
        with tmp.open("ab" if start else "wb") as f:
            last=time.monotonic(); count=start
            while True:
                block=r.read(8*1024*1024)
                if not block:break
                f.write(block); count+=len(block)
                if time.monotonic()-last>=30:
                    print("MODEL_PROGRESS",path.name,round(100*count/meta["bytes"],1),flush=True);last=time.monotonic()
            f.flush();os.fsync(f.fileno())
    if tmp.stat().st_size!=meta["bytes"]:raise RuntimeError("Incomplete model: "+str(path))
    h=digest(tmp)
    if meta["sha256"] and h!=meta["sha256"]:raise RuntimeError("Model checksum mismatch: "+str(path))
    tmp.rename(path)
    result={"file":str(path.relative_to(ROOT)),"repo":repo,"revision":rev,**meta,"sha256":h,"downloaded_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}
    receipt.write_text(json.dumps(result,indent=2)+"\n")
    print("MODEL_READY",path.name,flush=True);return result
def main():
    p=argparse.ArgumentParser();p.add_argument("--family",choices=["all","image","video"],default="all");a=p.parse_args()
    specs=SPECS if a.family=="all" else [s for s in SPECS if ("flux2" in s[0])==(a.family=="image")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(download,specs))
    out=ROOT/"models"/f"manifest-{a.family}.json";out.write_text(json.dumps(results,indent=2)+"\n")
if __name__=="__main__":main()
