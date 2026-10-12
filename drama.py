#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from studio.core import ROOT,load_episode,write_json
from studio.pipeline import make_images,make_video,voice,status

def main():
    p=argparse.ArgumentParser(description="本地AI短剧制作：剧本、人物、分镜、视频、配音、剪辑和质检")
    sub=p.add_subparsers(dest="command",required=True)
    for name in ["plan","images","video","voice","mix","lipsync","assemble","verify","status","produce"]:
        q=sub.add_parser(name);q.add_argument("episode",nargs="?",default="the-last-stop")
        if name in ["images","video","produce"]:
            q.add_argument("--gpus",default="1,2" if name=="images" else "1,2,3,4")
            q.add_argument("--take",default="take_01")
        if name in ["video","produce"]:q.add_argument("--profile",choices=["quality","fastwan"],default="quality")
        if name=="produce":q.add_argument("--skip-lipsync",action="store_true",help="Skip only when the edit uses no visible speaking mouths")
        if name in ["images","video","lipsync"]:q.add_argument("--shots",nargs="+")
        if name=="lipsync":
            q.add_argument("--take",default="lipsync_01");q.add_argument("--adopt",action="store_true")
    a=p.parse_args();folder,data=load_episode(a.episode)
    if hasattr(a,"take"):
        import re
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,60}",a.take):p.error("Invalid take name")
    if a.command=="plan":
        lines=[line for s in data["shots"] for line in s.get("dialogue",[])]
        write_json(folder/"dialogue.json",lines)
        from studio.planning import export_storyboard
        storyboard=export_storyboard(data,folder/"storyboard.csv")
        print(json.dumps({"title":data["title"],"shots":len(data["shots"]),"planned_seconds":sum(s["seconds"] for s in data["shots"]),"dialogue_lines":len(lines),"storyboard_csv":str((folder/"storyboard.csv").relative_to(ROOT)),"storyboard_rows":storyboard["rows"]},ensure_ascii=False,indent=2))
    elif a.command=="images":make_images(a.episode,[int(x) for x in a.gpus.split(",")],a.take,a.shots)
    elif a.command=="video":make_video(a.episode,a.shots,[int(x) for x in a.gpus.split(",")],a.take,a.profile)
    elif a.command=="lipsync":
        from studio.lipsync import synchronize
        print(json.dumps(synchronize(a.episode,a.shots,a.take,a.adopt),ensure_ascii=False,indent=2))
    elif a.command=="voice":voice(a.episode)
    elif a.command=="mix":
        from studio.media import prepare_audio,write_subtitles
        mix,cues=prepare_audio(folder,data);write_subtitles(folder,cues)
        print(json.dumps({"mix":str(mix),"cues":len(cues)},ensure_ascii=False))
    elif a.command in ["assemble","verify"]:
        from studio.media import assemble,verify
        print(json.dumps((assemble if a.command=="assemble" else verify)(a.episode),ensure_ascii=False,indent=2))
    elif a.command=="status":print(json.dumps(status(a.episode),ensure_ascii=False,indent=2))
    elif a.command=="produce":
        cards=[int(x) for x in a.gpus.split(",")]
        make_images(a.episode,cards[:2],a.take)
        make_video(a.episode,None,cards,a.take,a.profile)
        voice(a.episode)
        from studio.media import assemble,verify,prepare_audio,write_subtitles
        mix,cues=prepare_audio(folder,data);write_subtitles(folder,cues)
        if not a.skip_lipsync and any(s.get("lip_sync",False) for s in data["shots"]):
            from studio.lipsync import synchronize
            synchronize(a.episode,None,"lipsync_"+a.take,adopt=True)
        assemble(a.episode);print(json.dumps(verify(a.episode),ensure_ascii=False,indent=2))
if __name__=="__main__":main()
