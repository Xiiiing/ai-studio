from __future__ import annotations
import json, math, re, subprocess, wave
from fractions import Fraction
from pathlib import Path
import av
import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from .core import ROOT, load_episode, read_json, write_json, sha256, media_receipt, within
from .pipeline import selected_media

SR=48000
FFMPEG=imageio_ffmpeg.get_ffmpeg_exe()

def command(args):
    p=subprocess.run([FFMPEG,"-hide_banner","-loglevel","error",*map(str,args)],capture_output=True,text=True,timeout=1800)
    if p.returncode:raise RuntimeError(p.stderr[-8000:])
    return p

def probe(path,full=False):
    with av.open(str(path)) as container:
        streams=[{"type":s.type,"codec":s.codec_context.name} for s in container.streams]
        v=next((s for s in container.streams if s.type=="video"),None)
        duration=float(container.duration/av.time_base) if container.duration else 0.0
        result={"duration":duration,"streams":streams}
        if v:
            result.update(width=v.width,height=v.height,fps=float(v.average_rate or 0),expected_frames=v.frames)
            if full:
                count=0;first=None;last=None;differences=[];bad=0;previous=None;frames=[]
                for frame in container.decode(v):
                    count+=1
                    if count%24==1:
                        arr=np.asarray(frame.to_image().resize((180,320))).astype(np.float32)
                        if not np.isfinite(arr).all():bad+=1
                        if previous is not None:differences.append(float(np.mean(np.abs(arr-previous))))
                        previous=arr
                result.update(decoded_video_frames=count,finite=bad==0,mean_sampled_frame_change=float(np.mean(differences)) if differences else 0)
    if full:
        with av.open(str(path)) as container:
            aud=next((s for s in container.streams if s.type=="audio"),None)
            count=0;samples=0;peak=0.0;audio_finite=True
            if aud:
                for frame in container.decode(aud):
                    count+=1;samples+=frame.samples;values=frame.to_ndarray()
                    if not np.isfinite(values).all():audio_finite=False
                    scale=float(np.iinfo(values.dtype).max) if np.issubdtype(values.dtype,np.integer) else 1.0
                    peak=max(peak,float(np.max(np.abs(values.astype(np.float64))))/scale)
                rate=aud.codec_context.sample_rate
                result.update(audio_sample_rate=rate,audio_channels=aud.codec_context.channels,audio_seconds=samples/rate)
            result.update(decoded_audio_frames=count,decoded_audio_samples=samples,audio_finite=audio_finite,audio_peak=peak)
    return result

def wav_audio(path):
    p=subprocess.run([FFMPEG,"-hide_banner","-loglevel","error","-i",str(path),"-f","f32le","-ac","1","-ar",str(SR),"-"],capture_output=True,check=True,timeout=120)
    return np.frombuffer(p.stdout,dtype="<f4").copy()

def write_wav(path,data):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    y=np.clip(data,-1,1)
    with wave.open(str(p),"wb") as f:
        f.setnchannels(y.shape[1] if y.ndim==2 else 1);f.setsampwidth(2);f.setframerate(SR)
        f.writeframes((y*32767).astype("<i2").tobytes())

def stamp(seconds,ass=False):
    total=int(round(seconds*(100 if ass else 1000)))
    units=100 if ass else 1000;h=total//(3600*units);m=total//(60*units)%60;s=total//units%60;f=total%units
    return f"{h}:{m:02}:{s:02}.{f:02}" if ass else f"{h:02}:{m:02}:{s:02},{f:03}"

def chunks(text,max_chars=15):
    clauses=[c for c in re.split(r"(?<=[，。！？；])",text) if c]
    out=[]
    for clause in clauses:
        while len(clause)>max_chars:out.append(clause[:max_chars]);clause=clause[max_chars:]
        if clause:out.append(clause)
    return out

def piano(freq,duration=5.5):
    t=np.arange(int(duration*SR),dtype=np.float64)/SR
    y=np.zeros_like(t)
    for harmonic in range(1,8):
        decay=np.exp(-t*(0.7+harmonic*.24))
        y+=(1/harmonic**1.8)*decay*np.sin(2*np.pi*freq*harmonic*(1+0.0004*harmonic)*t)
    y*=1-np.exp(-t*160)
    return y.astype(np.float32)*0.035

def soundscape(data):
    duration=sum(float(s["seconds"]) for s in data["shots"]);n=round(duration*SR)
    design=data.get("sound_design",{})
    rng=np.random.default_rng(design.get("seed",20261009))
    white=rng.standard_normal(n).astype(np.float32)
    # Diffuse ventilation and a low carriage hum.
    spectrum=np.fft.rfft(white);freq=np.fft.rfftfreq(n,1/SR)
    spectrum*=1/np.sqrt(1+(freq/900)**4)
    air=np.fft.irfft(spectrum,n).astype(np.float32)
    t=np.arange(n,dtype=np.float32)/SR
    ambience=(air*.006+np.sin(2*np.pi*49.8*t)*.0025+np.sin(2*np.pi*99.6*t)*.001)
    envelope=np.ones(n,dtype=np.float32)
    for a,b,gain in design.get("ambience_duck",[]):envelope[max(0,round(a*SR)):min(n,round(b*SR))]*=gain
    fade_start=float(design.get("ambience_fade_start",max(0,duration-12)))
    ending=np.clip((t-fade_start)/max(duration-fade_start,.1),0,1);ambience*=envelope*(1-.7*ending)
    stereo=np.column_stack([ambience,np.roll(ambience,421)]).astype(np.float32)
    music=np.zeros_like(stereo)
    notes=design.get("piano_notes",[(duration*f,hz) for f,hz in [(0.25,146.83),(0.4,174.61),(0.6,220),(0.76,293.66),(0.88,349.23)]])
    for onset,hz in notes:
        if onset<0 or onset>=duration:continue
        y=piano(hz);i=round(onset*SR);end=min(n,i+len(y));y=y[:end-i]
        music[i:end,0]+=y*.8;music[i:end,1]+=y*.74
        for delay,gain in [(0.19,.22),(0.43,.16),(0.81,.09)]:
            j=i+round(delay*SR);e=min(n,j+len(y))
            if e>j:music[j:e,1]+=y[:e-j]*gain
    fade=np.minimum(np.maximum((duration-1.2-t)/1.8,0),1)
    music*=fade[:,None]
    # A small tactile button press and an understated step at the threshold.
    for at,gain,decay in design.get("tactile_events",[]):
        if at<0 or at>=duration:continue
        length=int(.3*SR);tt=np.arange(length)/SR
        click=rng.standard_normal(length).astype(np.float32)*np.exp(-tt/decay)*gain
        pos=round(at*SR);end=min(n,pos+length)
        stereo[pos:end,:]+=click[:end-pos,None]
    return stereo,music

def prepare_audio(folder,data):
    n=round(sum(s["seconds"] for s in data["shots"])*SR)
    voice=np.zeros((n,2),dtype=np.float32);cues=[];receipt=[];offset=0
    selection=folder/"audio/dialogue/selection.json"
    choices={x["id"]:x for x in read_json(selection)["lines"]} if selection.exists() else {}
    for shot in data["shots"]:
        for line in shot.get("dialogue",[]):
            choice=choices.get(line["id"])
            path=within(ROOT,choice["wav_path"]) if choice else folder/"audio/dialogue"/(line["id"]+".wav")
            if not path.exists():raise FileNotFoundError(path)
            if choice and sha256(path)!=choice["wav_sha256"]:raise RuntimeError("Selected voice checksum mismatch: "+line["id"])
            voice_receipt=read_json(path.with_suffix(".json"))
            expected_text=choice.get("original_script_text",voice_receipt.get("text","")) if choice else voice_receipt.get("text","")
            normalize=lambda value:re.sub(r"[\W_]+","",value)
            if normalize(expected_text)!=normalize(line["text"]) or voice_receipt.get("role")!=line["role"]:raise RuntimeError("Voice take no longer matches episode dialogue: "+line["id"])
            samples=wav_audio(path);seconds=len(samples)/SR
            start=float(line.get("start",.3));available=float(shot["seconds"])-start-.15
            rate=max(1,seconds/available)
            if rate>1.25:raise RuntimeError(f"{line['id']} voice {seconds:.2f}s exceeds its {available:.2f}s slot; adjust line or timing")
            adjusted=folder/"audio/edit"/(line["id"]+".wav");adjusted.parent.mkdir(parents=True,exist_ok=True)
            filters=([f"atempo={rate:.6f}"] if rate>1.01 else [])+["loudnorm=I=-20:TP=-3:LRA=7"]
            command(["-y","-i",path,"-af",",".join(filters),"-ar",SR,"-ac","1",adjusted]);samples=wav_audio(adjusted)
            if line.get("audio_effect")=="telephone":
                f=np.fft.rfftfreq(len(samples),1/SR);z=np.fft.rfft(samples)
                z*=(1-np.exp(-(f/280)**4))/(1+(f/3600)**6)
                samples=np.fft.irfft(z,len(samples)).astype(np.float32)
            peak=max(float(np.max(np.abs(samples))),1e-6);samples*=min(1.0,.73/peak)
            pos=round((offset+start)*SR);end=pos+len(samples)
            if end>n:raise ValueError("Audio extends beyond final frame")
            voice[pos:end,0]+=samples;voice[pos:end,1]+=samples
            pieces=chunks(line["text"]);weights=[max(1,len(re.sub(r"[，。！？；、,.!?]","",x))) for x in pieces]
            cursor=offset+start;total=sum(weights);actual=len(samples)/SR
            for text,w in zip(pieces,weights):
                finish=cursor+actual*w/total;cues.append({"start":cursor,"end":finish,"text":text,"line":line["id"]});cursor=finish
            receipt.append({"id":line["id"],"source":media_receipt(path),"processed":media_receipt(adjusted),"original_seconds":seconds,"tempo":rate,"start":offset+start,"edited_seconds":actual})
        if shot.get("lip_sync",False):
            begin=round(offset*SR);end=round((offset+shot["seconds"])*SR)
            write_wav(folder/"audio/lipsync"/(shot["id"]+".wav"),voice[begin:end,0])
        offset+=float(shot["seconds"])
    ambience,music=soundscape(data)
    mix=voice+ambience+music
    peak=np.max(np.abs(mix));gain=min(1.0,.93/max(float(peak),1e-6));mix*=gain
    for name,samples in [("dialogue",voice),("ambience",ambience),("music",music),("mix_premaster",mix)]:write_wav(folder/"audio/stems"/(name+".wav"),samples)
    mastered=folder/"audio/stems/final_mix.wav"
    command(["-y","-i",folder/"audio/stems/mix_premaster.wav","-af","loudnorm=I=-16:TP=-1.5:LRA=9","-ar",SR,"-ac","2",mastered])
    write_json(folder/"audio/edit_manifest.json",{"lines":receipt,"mix_gain":gain,"music":"Original algorithmic sparse piano composition","ambience":"Original synthesized carriage soundscape"})
    return mastered,cues

def write_subtitles(folder,cues,duration=None):
    release=folder/"release";release.mkdir(exist_ok=True)
    srt="\n\n".join(f"{i}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}" for i,c in enumerate(cues,1))+"\n"
    (release/f"{folder.name}.zh.srt").write_text(srt,encoding="utf-8")
    header="[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Dialogue,Noto Sans CJK SC,48,&H00FFFFFF,&H00FFFFFF,&H00101010,&H50000000,0,0,0,0,100,100,1,0,1,2.5,0.5,2,80,80,190,1\nStyle: Disclosure,Noto Sans CJK SC,27,&H40FFFFFF,&H40FFFFFF,&H80101010,&H80000000,0,0,0,0,100,100,0,0,1,1.3,0,7,54,54,58,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    text=header+"\n".join(f"Dialogue: 0,{stamp(c['start'],True)},{stamp(c['end'],True)},Dialogue,,0,0,0,,{c['text'].replace(chr(10),r'\N')}" for c in cues)+"\n"
    duration=float(duration) if duration is not None else max((cue["end"] for cue in cues),default=0.0)
    text+="Dialogue: 1,0:00:00.00,"+stamp(duration,True)+",Disclosure,,0,0,0,,AI 生成 · 剧情虚构\n"
    (release/f"{folder.name}.zh.ass").write_text(text,encoding="utf-8")
    return release/f"{folder.name}.zh.ass"

def font(size):
    paths=[ROOT/"assets/fonts/NotoSansCJKsc-Regular.otf",Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    p=next((p for p in paths if p.exists()),None)
    return ImageFont.truetype(str(p),size) if p else ImageFont.load_default()

def contact_sheet(slug):
    folder,data=load_episode(slug);cells=[]
    for s in data["shots"]:
        chosen=folder/"renders"/s["id"]/"selected.json"
        if chosen.exists():
            path=selected_media(folder,"renders",s["id"])
            with av.open(str(path)) as c:
                v=c.streams.video[0];target=int(s["seconds"]*12);frame=None
                for i,f in enumerate(c.decode(v)):
                    frame=f
                    if i>=target:break
                pic=frame.to_image()
        else:pic=Image.open(selected_media(folder,"assets/shots",s["id"]))
        pic=pic.convert("RGB").resize((180,320))
        tile=Image.new("RGB",(200,370),(12,18,23));tile.paste(pic,(10,10));d=ImageDraw.Draw(tile);d.text((12,338),s["id"],font=font(18),fill=(240,239,232));cells.append(tile)
    out=Image.new("RGB",(200*7,370*math.ceil(len(cells)/7)),(12,18,23))
    for i,pic in enumerate(cells):out.paste(pic,((i%7)*200,(i//7)*370))
    dest=folder/"quality/contact_sheet.jpg";dest.parent.mkdir(parents=True,exist_ok=True);out.save(dest,quality=92);return dest

def assemble(slug):
    folder,data=load_episode(slug);release=folder/"release";release.mkdir(exist_ok=True)
    write_json(folder/"quality/release_state.json",{"status":"assembling"})
    mix,cues=prepare_audio(folder,data);ass=write_subtitles(folder,cues,sum(float(s["seconds"]) for s in data["shots"]))
    segments=[];source=[];edit=folder/"edit";edit.mkdir(exist_ok=True)
    for s in data["shots"]:
        video=selected_media(folder,"renders",s["id"]);p=probe(video)
        selected=read_json(folder/"renders"/s["id"]/"selected.json")
        if selected.get("origin")=="local_lipsync" and sha256(folder/"audio/lipsync"/(s["id"]+".wav"))!=selected["input_audio"]["sha256"]:raise RuntimeError("Lip sync uses an outdated voice edit: "+s["id"])
        if p["duration"]+.05<float(s["seconds"]):raise RuntimeError("Video is shorter than its timeline slot: "+s["id"])
        segment=edit/(s["id"]+".mp4")
        command(["-y","-i",video,"-an","-t",s["seconds"],"-vf",f"crop={data['width']}:{data['height']},setsar=1,fps=24","-c:v","libx264","-preset","fast","-crf","17","-pix_fmt","yuv420p",segment])
        segments.append(segment);source.append({"id":s["id"],"source":media_receipt(video),"probe":p,"edit_seconds":s["seconds"]})
    listing=edit/"concat.txt";listing.write_text("\n".join("file '"+str(p)+"'" for p in segments)+"\n")
    native=release/f"{slug}_native_720p.mp4"
    command(["-y","-f","concat","-safe","0","-i",listing,"-i",mix,"-map","0:v:0","-map","1:a:0","-c:v","copy","-c:a","aac","-b:a","256k","-ar",SR,"-movflags","+faststart","-shortest",native])
    clean=release/f"{slug}_1080p_clean.mp4"
    command(["-y","-i",native,"-vf","scale=1080:1920:flags=lanczos,setsar=1","-c:v","libx264","-preset","slow","-crf","17","-c:a","copy","-movflags","+faststart",clean])
    subtitled=release/f"{slug}_1080p_zh.mp4"
    # Project paths are controlled and contain no filter metacharacters.
    command(["-y","-i",native,"-vf","scale=1080:1920:flags=lanczos,setsar=1,ass="+str(ass),"-c:v","libx264","-preset","slow","-crf","17","-c:a","copy","-movflags","+faststart",subtitled])
    sheet=contact_sheet(slug)
    image=Image.open(selected_media(folder,"assets/shots","S01")).convert("RGB").resize((1080,1920))
    overlay=Image.new("RGBA",image.size,(0,0,0,0));draw=ImageDraw.Draw(overlay)
    draw.rectangle((0,1360,1080,1920),fill=(6,13,20,180))
    draw.text((90,1470),data["title"],font=font(min(132,900//max(1,len(data["title"])))),fill=(248,242,225,255))
    draw.text((96,1650),data.get("english_title",slug.replace("-"," ").upper()),font=font(32),fill=(212,178,127,255))
    draw.text((96,1750),data.get("poster_tagline","原创 AI 短剧"),font=font(35),fill=(237,236,230,255))
    draw.text((96,1840),"AI 生成 · 剧情虚构",font=font(27),fill=(187,199,202,255))
    Image.alpha_composite(image.convert("RGBA"),overlay).convert("RGB").save(release/"cover.jpg",quality=95)
    manifest={"title":data["title"],"episode":slug,"planned_seconds":sum(s["seconds"] for s in data["shots"]),"source_generation":[data["generation"]["width"],data["generation"]["height"]],"native_crop":[data["width"],data["height"]],"delivery":[1080,1920],"fps":24,"sources":source,"artifacts":[media_receipt(p) for p in [native,clean,subtitled,release/"cover.jpg",release/f"{folder.name}.zh.srt",release/f"{folder.name}.zh.ass"]],"limitations":{"resolution":f"Native generated {data['generation']['width']}x{data['generation']['height']}, center-cropped to {data['width']}x{data['height']}. 1080x1920 is postproduction Lanczos resampling.","lip_sync":"Separate TTS soundtrack; consult shot-level lip-sync quality records.","visual_review":"Technical verification and sampled visual inspection are separate gates.","ai_disclosure":"The subtitled version and cover display AI 生成 · 剧情虚构; the clean and native versions retain uncaptioned editing masters."}}
    write_json(release/"manifest.json",manifest)
    return manifest

def verify(slug):
    folder,data=load_episode(slug);release=folder/"release"
    write_json(folder/"quality/release_state.json",{"status":"verifying"})
    manifest=read_json(release/"manifest.json");checks=[]
    for artifact in manifest["artifacts"]:
        p=ROOT/artifact["path"]
        if sha256(p)!=artifact["sha256"]:raise RuntimeError("Artifact checksum mismatch: "+str(p))
        if p.suffix==".mp4":
            result=probe(p,full=True)
            expected=round(sum(s["seconds"] for s in data["shots"])*24)
            expected_size=[data["width"],data["height"]] if p.name.endswith("_native_720p.mp4") else [1080,1920]
            expected_seconds=expected/24
            passed=(result["decoded_video_frames"]==expected
                    and [result["width"],result["height"]]==expected_size
                    and abs(result["duration"]-expected_seconds)<.1
                    and result["decoded_audio_frames"]>0
                    and abs(result.get("audio_seconds",0)-expected_seconds)<.1
                    and result.get("audio_channels")==2
                    and result.get("audio_sample_rate")==SR
                    and result.get("audio_finite",False)
                    and abs(result["fps"]-24)<.01 and result["finite"])
            checks.append({"file":str(p.relative_to(ROOT)),**result,"passed":passed})
            if not passed:raise RuntimeError("Technical media validation failed: "+str(p))
    result={"manifest_sha256":sha256(release/"manifest.json"),"technical_passed":len(checks)==3 and all(c["passed"] for c in checks),"checks":checks,"visual_review":"See quality/visual_review.json; full decode does not certify appearance or identity.","subtitle_lines":len((release/f"{folder.name}.zh.srt").read_text().split("\n\n"))}
    write_json(folder/"quality/technical_report.json",result)
    if not result["technical_passed"]:raise RuntimeError("Expected three validated film exports")
    write_json(folder/"quality/release_state.json",{"status":"verified","manifest_sha256":result["manifest_sha256"]})
    return result
