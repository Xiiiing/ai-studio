"""Inspect a real factory delivery and emit evidence without approving it."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

from studio.core import ROOT, read_json, write_json
from . import store


def checksum(path):
    digest=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""):
            digest.update(chunk)
    return digest.hexdigest()


def confined(value):
    if not value:
        return None
    path=Path(value)
    path=(path if path.is_absolute() else ROOT/path).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise ValueError("Artifact escapes project root")
    return path


def validate(pid):
    import av
    import numpy as np
    from PIL import Image,ImageDraw,ImageFont

    p=store.get(pid)
    folder=ROOT/"production"/p["slug"]
    delivery=p.get("delivery") or {}
    report={"schema":"autonomous_factory.acceptance.v1","checked_at":store.now(),
        "project_id":pid,"title":p["title"],"deployment_root":str(ROOT),
        "project_status":p["status"],"is_validation":p.get("is_validation",False),
        "human_final_approval":any(a.get("type")=="final" and a.get("choice")=="approve" and a.get("actor")=="producer"
                                   for a in p.get("answers",{}).values()),
        "script_shots":len((p.get("script") or {}).get("shots",[])),
        "requested_duration_seconds":p.get("duration_seconds"),
        "planned_duration_seconds":sum(s.get("seconds",0) for s in (p.get("script") or {}).get("shots",[])),
        "research":{"status":p.get("research",{}).get("status"),"source_count":len(p.get("sources",[])),
                    "trend_verification":p.get("research",{}).get("trend_verification")},
        "proposals":{"count":len(p.get("proposals",[])),
                     "eligible":sum(v.get("eligible",False) for v in p.get("proposals",[]))},
        "styles":{"count":len(p.get("styles",[])),
                  "generated":sum(bool(v.get("preview_url")) for v in p.get("styles",[]))},
        "production":p.get("production"),"budget":p.get("budget"),"decisions":p.get("answers"),
        "services":{},"checks":{}}
    for unit in ["drama-factory-web.service","drama-factory-worker.service"]:
        r=subprocess.run(["systemctl","is-active",unit],capture_output=True,text=True,timeout=10)
        report["services"][unit]=r.stdout.strip()
    checks=report["checks"]
    checks["script_passed"]=bool(p.get("script_review",{}).get("passed"))
    checks["three_real_style_previews"]=(report["styles"]["count"]==3 and report["styles"]["generated"]==3
        and all(s.get("preview_path") and confined(s["preview_path"]).is_file() for s in p.get("styles",[])))
    checks["all_fifteen_planned_shots_present"]=(report["script_shots"]==15
        and [s.get("id") for s in p.get("shots",[])]==[s.get("id") for s in (p.get("script") or {}).get("shots",[])])
    checks["script_matches_requested_duration"]=abs(report["planned_duration_seconds"]-float(report["requested_duration_seconds"] or 0))<0.001
    checks["all_shots_semantically_passed"]=bool(p.get("shots")) and all(s.get("qa",{}).get("semantic",{}).get("status")=="pass" for s in p.get("shots",[]))
    checks["delivery_present"]=bool(delivery)
    old=ROOT/"production/the-last-stop/release/manifest.json"
    report["preserved_original_manifest_sha256"]=checksum(old) if old.exists() else None
    checks["original_manifest_unchanged"]=report["preserved_original_manifest_sha256"]=="c4a057b541b0469beb8533b2e5ff3823be66dea41abd76c6967daa18c6b56dfb"
    checks["service_health"]=all(v=="active" for v in report["services"].values())
    calls=[]
    for receipt in folder.rglob("receipt.json"):
        if "/calls/" in str(receipt):
            try:
                item=read_json(receipt)
                calls.append({"path":str(receipt.relative_to(ROOT)),"role":item.get("role"),
                              "model":item.get("model"),"status":item.get("status"),
                              "elapsed_seconds":item.get("elapsed_seconds")})
            except (ValueError,OSError):
                pass
    report["model_calls"]=calls
    report["artifact_checks"]=[]
    for artifact in delivery.get("artifacts",[]):
        path=confined(artifact["path"])
        valid=path.is_file() and checksum(path)==artifact.get("sha256")
        report["artifact_checks"].append({"path":str(path.relative_to(ROOT)),"valid":valid})
    checks["delivery_checksums"]=bool(report["artifact_checks"]) and all(a["valid"] for a in report["artifact_checks"])
    path=confined(delivery.get("path"))
    if path and path.exists():
        with av.open(str(path)) as container:
            stream=container.streams.video[0]
            info={"path":str(path.relative_to(ROOT)),"bytes":path.stat().st_size,
                  "sha256":checksum(path),"width":stream.width,"height":stream.height,
                  "fps":float(stream.average_rate),"audio_streams":len(container.streams.audio)}
            frames=0
            samples=[]
            desired={round(sum(s.get("seconds",0) for s in p["shots"][:i])*24+min(24,s.get("seconds",4)*12))
                     for i,s in enumerate(p["shots"])}
            for frame in container.decode(stream):
                if frames in desired:
                    samples.append(frame.to_image())
                frames+=1
            info.update(decoded_frames=frames,duration_seconds=frames/info["fps"],sampled_frames=len(samples))
        with av.open(str(path)) as container:
            energy=0.; values=0
            for frame in container.decode(audio=0):
                data=frame.to_ndarray().astype("float64")
                if frame.format.is_planar:
                    pass
                energy+=float(np.square(data).sum())
                values+=data.size
            info["audio_rms"]=float((energy/max(1,values))**.5)
        report["video"]=info
        checks["full_video_decodes"]=frames==round(report["planned_duration_seconds"]*24)
        checks["video_matches_requested_duration"]=abs(info["duration_seconds"]-float(report["requested_duration_seconds"] or 0))<=1/24
        checks["delivery_duration_receipt_matches_video"]=abs(info["duration_seconds"]-float(delivery.get("duration_seconds",0)))<=1/24
        checks["portrait_delivery"]=info["width"]==720 and info["height"]==1280 and info["fps"]==24
        checks["audible_audio_track"]=info["audio_streams"]>0 and info["audio_rms"]>0.0001
        if samples:
            tile_w,tile_h=180,320
            cols=5
            rows=(len(samples)+cols-1)//cols
            sheet=Image.new("RGB",(cols*tile_w,rows*(tile_h+30)),(22,24,25))
            draw=ImageDraw.Draw(sheet)
            for i,im in enumerate(samples):
                x,y=(i%cols)*tile_w,(i//cols)*(tile_h+30)
                sheet.paste(im.resize((tile_w,tile_h)),(x,y))
                draw.text((x+8,y+tile_h+6),"S%02d · %ss"%(i+1,sum(s.get("seconds",0) for s in p["shots"][:i])),fill=(240,238,224))
            preview=ROOT/"runtime/factory"/(pid+"-contact-sheet.jpg")
            sheet.save(preview,quality=91)
            report["contact_sheet"]=str(preview.relative_to(ROOT))
    checks["awaits_or_has_final_decision"]=p["status"] in {"awaiting_final","approved"}
    report["result"]="passed_automated_checks_pending_producer" if checks and all(checks.values()) else "incomplete"
    destination=ROOT/"runtime/factory/acceptance_report.json"
    write_json(destination,report)
    write_json(folder/"acceptance_report.json",report)
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("project")
    args=parser.parse_args()
    report=validate(args.project)
    print(json.dumps({k:v for k,v in report.items() if k not in {"model_calls","decisions"}},ensure_ascii=False,indent=2))
    raise SystemExit(0 if report["result"].startswith("passed_") else 2)
