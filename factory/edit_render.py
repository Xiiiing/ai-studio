"""One CPU FFmpeg render path for timeline previews and deliverables."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
import uuid

import av
from studio import core
from studio.media import FFMPEG
from .edit_timeline import FPS, verified, positions
from .scheduler import ProductionPaused


def command(args, control=None):
    if control and control():
        raise ProductionPaused('剪辑渲染已取消，原素材与剪辑版本保留')
    process = subprocess.Popen([FFMPEG, '-nostdin','-hide_banner','-loglevel','error', *map(str,args)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 1800
    try:
        while True:
            if control and control():
                raise ProductionPaused('剪辑渲染已取消，原素材与剪辑版本保留')
            if time.monotonic() > deadline:
                raise TimeoutError('剪辑渲染超过30分钟')
            try:
                _, error = process.communicate(timeout=.25)
                break
            except subprocess.TimeoutExpired:
                pass
        if process.returncode:
            raise ValueError('剪辑渲染失败：' + error.decode(errors='replace')[-1800:])
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()


def encode(args):
    return [*args,'-r',FPS,'-c:v','libx264','-threads','1','-preset','fast','-crf','18',
            '-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-ac','2','-movflags','+faststart']


def stamp(frames, ass=False):
    millis = round(frames / FPS * 1000)
    hours, rem = divmod(millis,3600000)
    minutes, rem = divmod(rem,60000)
    sec, ms = divmod(rem,1000)
    return f'{hours}:{minutes:02}:{sec:02}.{ms//10:02}' if ass else f'{hours:02}:{minutes:02}:{sec:02},{ms:03}'


def subtitle_files(document, folder):
    srt, ass = folder/'subtitles.zh.srt', folder/'subtitles.zh.ass'
    cues = document['subtitles']
    srt.write_text('\n\n'.join(f"{i}\n{stamp(c['start_frame'])} --> {stamp(c['end_frame'])}\n{c['text']}" for i,c in enumerate(cues,1))+'\n',encoding='utf-8')
    width,height = document['resolution']
    header = f'''[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,Noto Sans CJK SC,{max(16,round(width*.052))},&H00FFFFFF,&H00FFFFFF,&H00101010,&H50000000,0,0,0,0,100,100,0,0,1,2,0,2,18,18,{round(height*.08)},1
[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
'''
    def escape(value):
        return value.replace('\\','＼').replace('{','｛').replace('}','｝').replace('\n',r'\N').replace('\r','')
    lines = [f"Dialogue: 0,{stamp(c['start_frame'],True)},{stamp(c['end_frame'],True)},Default,,0,0,0,,{escape(c['text'])}" for c in cues]
    lines.append(f"Dialogue: 0,0:00:00.00,{stamp(positions(document['clips'])[-1]['end_frame'],True)},Default,,0,0,0,,AI 生成 · 剧情虚构")
    ass.write_text(header+'\n'.join(lines)+'\n',encoding='utf-8')
    return srt,ass


def inspect(path, frames, resolution):
    """Decode every sample; intended fade-to-black frames are valid editorial content."""
    actual_frames = 0
    with av.open(str(path)) as container:
        video = next(iter(container.streams.video),None)
        if not video or [video.width,video.height] != resolution or abs(float(video.average_rate)-FPS) > .01:
            raise ValueError('剪辑输出视频尺寸或帧率错误')
        for _ in container.decode(video):
            actual_frames += 1
    with av.open(str(path)) as container:
        stream = next(iter(container.streams.audio),None)
        if not stream:
            raise ValueError('剪辑成片缺少音轨')
        audio_seconds = sum(f.samples for f in container.decode(stream)) / stream.codec_context.sample_rate
    if actual_frames != frames or abs(audio_seconds-frames/FPS) > .1:
        raise ValueError('剪辑输出视频或音轨长度与时间线不符')
    return {'status':'pass','video_frames':actual_frames,'audio_seconds':audio_seconds,
            'scope':'full_decode_frame_count_audio_duration_not_semantic_quality'}


def render(document, sources, folder, control=None):
    folder = Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    # A partial result is never adopted and no prior media is overwritten.
    session = folder/('render-'+uuid.uuid4().hex)
    session.mkdir()
    width,height = document['resolution']
    spans = positions(document['clips'])
    frames = spans[-1]['end_frame']
    parts = []
    for index,clip in enumerate(document['clips']):
        source = sources[clip['source_id']]
        path = verified(source['receipt'],{'id':source['owner_id'],'slug':source['owner_slug']})
        part = session/f'part-{index:04}.mp4'
        duration = (clip['out_frame']-clip['in_frame'])/FPS
        filters = f"[0:v]fps={FPS},trim=start_frame={clip['in_frame']}:end_frame={clip['out_frame']},setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,format=yuv420p[v];"
        audio_input = '0:a' if source['has_audio'] else '1:a'
        audio_start = clip['in_frame']/FPS if source['has_audio'] else 0
        filters += f"[{audio_input}]atrim=start={audio_start:.9f}:duration={duration:.9f},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,volume={clip['source_gain']},apad,atrim=duration={duration:.9f}[a]"
        inputs = ['-i',path]
        if not source['has_audio']:
            inputs += ['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
        command(encode(['-n',*inputs,'-filter_complex_threads','1','-filter_complex',filters,'-map','[v]','-map','[a]','-frames:v',clip['out_frame']-clip['in_frame']])+[part],control)
        parts.append(part)
    joined = parts[0]
    accumulated = document['clips'][0]['out_frame']-document['clips'][0]['in_frame']
    for index,part in enumerate(parts[1:],1):
        clip = document['clips'][index]
        transition = clip['transition']
        target = session/f'join-{index:04}.mp4'
        filters = '[0:v]settb=1/24,setpts=N,fps=24[v0];[1:v]settb=1/24,setpts=N,fps=24[v1];'
        if transition['kind'] == 'cut':
            filters += '[v0][0:a][v1][1:a]concat=n=2:v=1:a=1[v][a]'
        else:
            duration = transition['frames']/FPS
            offset = (accumulated-transition['frames'])/FPS
            name = 'fade' if transition['kind'] == 'dissolve' else 'fadeblack'
            filters += f'[v0][v1]xfade=transition={name}:duration={duration:.9f}:offset={offset:.9f}[v];[0:a][1:a]acrossfade=d={duration:.9f}:c1=tri:c2=tri[a]'
        accumulated += clip['out_frame']-clip['in_frame']-transition['frames']
        command(encode(['-n','-i',joined,'-i',part,'-filter_complex_threads','1','-filter_complex',filters,
                        '-map','[v]','-map','[a]','-frames:v',accumulated])+[target],control)
        joined = target
    clean = session/'episode_clean.mp4'
    inputs,filters = ['-i',joined], []
    labels = ['[base]']
    filters.append(f'[0:a]apad,atrim=duration={frames/FPS:.9f},asetpts=PTS-STARTPTS[base]')
    for index,track in enumerate(document['audio'],1):
        source = sources[track['source_id']]
        path = verified(source['receipt'],{'id':source['owner_id'],'slug':source['owner_slug']})
        inputs += ['-i',path]
        delay = round(track['start_frame']/FPS*1000)
        filters.append(f"[{index}:a]atrim=start={track['in_frame']/FPS:.9f}:end={track['out_frame']/FPS:.9f},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,volume={track['gain']},adelay={delay}|{delay}[a{index}]")
        labels.append(f'[a{index}]')
    filters.append(''.join(labels)+f'amix=inputs={len(labels)}:duration=first:normalize=0,alimiter=limit=0.95:latency=1,apad,atrim=duration={frames/FPS:.9f}[mix]')
    command(['-n',*inputs,'-filter_complex_threads','1','-filter_complex',';'.join(filters),'-map','0:v','-map','[mix]',
             '-c:v','copy','-c:a','aac','-ar','48000','-ac','2','-t',frames/FPS,'-movflags','+faststart',clean],control)
    srt,ass = subtitle_files(document,session)
    final = session/'episode.mp4'
    command(encode(['-n','-i',clean,'-vf','ass='+str(ass),'-map','0:v','-map','0:a','-frames:v',frames])+[final],control)
    qa = {'clean':inspect(clean,frames,document['resolution']),'subtitled':inspect(final,frames,document['resolution'])}
    core.write_json(session/'timeline.json',{'schema':document['schema'],'document':document,'positions':spans,'duration_seconds':frames/FPS})
    core.write_json(session/'technical_qa.json',qa)
    files = [final,clean,srt,ass,session/'timeline.json',session/'technical_qa.json']
    result = {'path':str(final.relative_to(core.ROOT)),'video_url':'/media/'+str(final.relative_to(core.ROOT)),
        'clean_url':'/media/'+str(clean.relative_to(core.ROOT)),'subtitle_url':'/media/'+str(srt.relative_to(core.ROOT)),
        'timeline_url':'/media/'+str((session/'timeline.json').relative_to(core.ROOT)),
        'duration_seconds':frames/FPS,'fps':FPS,'resolution':document['resolution'],'technical_qa':qa,
        'artifacts':[core.media_receipt(p) for p in files], 'semantic_status':'not_reviewed',
        'status':'draft_preview_not_approved','source_signature':core.fingerprint(document)}
    core.write_json(session/'manifest.json',result)
    return result
