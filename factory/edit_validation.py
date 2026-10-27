"""Explicit anonymous CPU media for isolated editor acceptance; never model output."""
from __future__ import annotations

import subprocess
from studio import core
from studio.media import FFMPEG
from . import store


def seed_validation_project():
    """Create a NEW validation record only; callers configure isolated store/ROOT."""
    project = store.create({'brief':'隔离剪辑验收：两段CPU测试图案和测试音，不是真实剧情、画质或模型效果。','title':'CPU时间线操作样例（非模型成片）'})
    pid=project['id']
    base=core.ROOT/'production'/project['slug']
    base.mkdir(parents=True,exist_ok=True)
    core.write_json(base/'factory_owner.json',{'project_id':pid})
    release=base/'release'/'synthetic-editor-source'
    release.mkdir(parents=True)
    clips=[]
    for index in (1,2):
        folder=base/'renders'/f'S{index:02}'/'take_001'
        folder.mkdir(parents=True)
        video=folder/'video.mp4'
        subprocess.run([FFMPEG,'-nostdin','-hide_banner','-loglevel','error','-n','-f','lavfi','-i',
            f'testsrc2=size=160x288:rate=24:duration=3','-vf',f'hue=h={index*70}',
            '-an','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',video],check=True,timeout=30)
        clips.append({'id':f'S{index:02}','seconds':3,'action':f'CPU测试图案{index}的运动，非真实人物动作。',
            'beat_ids':[f'B{index}'],'prompt':'SYNTHETIC CPU FIXTURE NOT MODEL QUALITY','camera_motion':'hold',
            'video':{**core.media_receipt(video),'qa':{'technical':{'status':'pass'},'semantic':{'status':'not_reviewed'},
                'eligible_for_selection':False,'fixture':'synthetic_cpu_not_model_quality'}},'dialogue':[]})
    voice=release/'voice.wav'
    music=release/'music.wav'
    for path,frequency,duration in ((voice,440,1),(music,220,6)):
        subprocess.run([FFMPEG,'-nostdin','-hide_banner','-loglevel','error','-n','-f','lavfi','-i',
            f'sine=frequency={frequency}:sample_rate=48000:duration={duration}','-ac','2',path],check=True,timeout=15)
    core.write_json(release/'audio_edit.json',{'dialogue':[{'id':'line1','shot':'S01','start':.5,'seconds':1,
        'processed':core.media_receipt(voice),'source':core.media_receipt(voice)}],'fixture':'sine_tone_not_speech'})
    core.write_json(release/'timeline.json',{'fps':24,'duration_seconds':6,'shots':[
        {'shot':s['id'],'seconds':3,'video':s['video']} for s in clips],
        'subtitles':[{'start':.5,'end':1.5,'text':'CPU测试音，不是人物配音','shot':'S01'}],
        'fixture':'synthetic_cpu_not_model_generation'})
    receipts=[core.media_receipt(release/n) for n in ('timeline.json','audio_edit.json')]
    delivery={'source_signature':core.fingerprint(receipts),'artifacts':receipts,'resolution':[160,288],
        'duration_seconds':6,'fps':24,'semantic_status':'not_reviewed','fixture':'synthetic_cpu_not_model_quality'}
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        project=store._read(db,pid)
        project.update(status='awaiting_final',is_validation=True,pause_requested=True,delivery=delivery,
            episode={'shots':clips,'cast':{}},script={'shots':clips,'cast':{}},shots=clips,
            outline={'beats':[{'id':f'B{i}','description':f'展示CPU测试图案{i}，不证明模型效果。'} for i in (1,2)]})
        db.execute("UPDATE jobs SET status='cancelled' WHERE project_id=? AND status='queued'",(pid,))
        store._write(db,project)
    return store.get(pid,False)
