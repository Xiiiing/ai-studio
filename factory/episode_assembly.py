"""Verified scene/episode concatenation, offset subtitles and immutable releases."""
from __future__ import annotations

from pathlib import Path
import subprocess
import time
import uuid

from studio.core import ROOT, within, fingerprint, read_json, write_json, sha256, media_receipt
from studio.media import FFMPEG
from .quality import inspect_video
from .scheduler import ProductionPaused, ProductionNeedsDecision


def verified_artifact(unit, filename):
    artifact = next((a for a in unit['delivery']['artifacts'] if Path(a['path']).name == filename), None)
    if not artifact: raise ValueError('制作单元缺少交付文件：' + filename)
    path = within(ROOT, artifact['path'])
    if not path.is_relative_to((ROOT / 'production' / unit['slug']).resolve()):
        raise ValueError('单元交付文件归属不匹配')
    if not path.is_file() or sha256(path) != artifact['sha256'] or path.stat().st_size != artifact['bytes']:
        raise ValueError('单元交付文件与回执不匹配')
    return path


def boundary(control):
    if control and control(): raise ProductionPaused('已在单集合成的安全边界暂停')


def command(arguments, control=None):
    boundary(control)
    process = subprocess.Popen([FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', *map(str, arguments)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 1800
    try:
        while True:
            boundary(control)
            if time.monotonic() > deadline: raise TimeoutError('单集合成超过30分钟')
            try:
                _, error = process.communicate(timeout=.5)
                break
            except subprocess.TimeoutExpired: pass
        if process.returncode: raise RuntimeError(error.decode(errors='replace')[-2000:])
    finally:
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=3)
            except subprocess.TimeoutExpired: process.kill(); process.wait()


def concat(paths, target, seconds, size, control=None, durations=None):
    boundary(control)
    if not target.exists():
        durations = durations or [seconds / len(paths)] * len(paths)
        if len(durations) != len(paths) or abs(sum(durations)-seconds)>1/48: raise ValueError('拼接时长不匹配')
        inputs, filters, labels = [], [], []
        for i,(path,duration) in enumerate(zip(paths,durations,strict=True)):
            inputs.extend(['-threads','1','-i',path])
            filters.extend([f'[{i}:v]trim=end_frame={round(duration*24)},setpts=N/(24*TB)[v{i}]',
                f'[{i}:a]atrim=duration={duration:g},asetpts=PTS-STARTPTS,apad=whole_dur={duration:g},atrim=duration={duration:g}[a{i}]'])
            labels.extend([f'[v{i}]',f'[a{i}]'])
        filters.append(''.join(labels)+f'concat=n={len(paths)}:v=1:a=1[v][a]')
        temporary = target.with_name(target.stem + '-' + uuid.uuid4().hex + '.partial.mp4')
        try:
            command(['-n', *inputs, '-filter_complex_threads','1','-filter_complex',';'.join(filters),
                     '-map','[v]','-map','[a]','-r','24','-fps_mode','cfr','-c:v','libx264','-threads','2',
                     '-preset','fast','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-ac','2',
                     '-t', seconds, '-movflags', '+faststart', temporary], control)
            temporary.replace(target)
        finally: temporary.unlink(missing_ok=True)
    qa = inspect_video(target, seconds, size, require_audio=True)
    if qa['status'] != 'pass':
        raise ProductionNeedsDecision('合成媒体未通过技术检查', 'episode_assembly_failed', qa)
    return qa


def timestamp(seconds):
    millis = round(seconds * 1000)
    hours, rem = divmod(millis, 3600000)
    minutes, rem = divmod(rem, 60000)
    sec, ms = divmod(rem, 1000)
    return f'{hours:02d}:{minutes:02d}:{sec:02d},{ms:03d}'


def offset_cues(timelines, durations):
    cues, offset = [], 0.
    for timeline, seconds in zip(timelines, durations, strict=True):
        previous = 0.
        for cue in timeline.get('subtitles', []):
            start, end = cue.get('start'), cue.get('end')
            if (type(start) not in (int,float) or type(end) not in (int,float)
                    or not 0 <= start < end <= seconds + 1/24 or start < previous or not isinstance(cue.get('text'),str)):
                raise ValueError('单元字幕时间轴无效')
            cues.append({**cue, 'start': round(offset + start, 6), 'end': round(offset + end, 6)})
            previous = end
        offset += seconds
    return cues


def assemble(episode, units, control=None):
    if not units or any(u['status'] != 'unit_ready' or not u.get('delivery') for u in units):
        raise ValueError('全部制作单元完成审核后才能合成单集')
    size = tuple(units[0]['delivery']['resolution'])
    if any(tuple(u['delivery']['resolution']) != size or u['delivery'].get('fps') != 24 for u in units):
        raise ValueError('制作单元的尺寸和帧率须一致')
    durations = [float(u['delivery']['duration_seconds']) for u in units]
    if any(not 0 < d <= 90 for d in durations): raise ValueError('单元交付时长无效')
    if abs(sum(durations)-episode['duration_seconds']) > 1/24:
        raise ValueError('制作单元总时长不等于本集目标时长')
    closed, last = set(), None
    for unit in units:
        scene=unit['scene_id']
        if scene != last and scene in closed: raise ValueError('场景单元必须连续，不能错配合成和字幕')
        closed.add(scene);last=scene
    sources, timelines = [], []
    for u in units:
        boundary(control)
        sources.append({'project_id': u['id'], 'scene_id': u['scene_id'], 'prefix': u['global_prefix'],
            'source_signature': u['delivery']['source_signature'], 'artifacts': u['delivery']['artifacts']})
        timelines.append(read_json(verified_artifact(u, 'timeline.json')))
    signature = fingerprint({'version': 2, 'sources': sources, 'revision': episode['production_revision'], 'transition_review':episode.get('transition_review')})
    from .dramas import owned_folder
    folder = owned_folder(episode,ROOT) / 'release' / f"v{episode['production_revision']}-{signature[:12]}"
    manifest = folder / 'manifest.json'
    if manifest.exists():
        saved = read_json(manifest)
        if saved['source_signature'] != signature: raise ValueError('单集合成缓存版本不匹配')
        for artifact in saved['artifacts']:
            path = within(ROOT, artifact['path'])
            if sha256(path) != artifact['sha256']: raise ValueError('单集缓存产物校验失败')
        return saved
    folder.mkdir(parents=True, exist_ok=True)
    owner = folder.parent.parent / 'factory_owner.json'
    if owner.exists() and read_json(owner).get('project_id') != episode['id']: raise ValueError('单集目录归属不匹配')
    write_json(owner, {'project_id': episode['id'], 'schema': 'autonomous_factory.owner.v1'})
    scenes = []
    for scene_id in dict.fromkeys(u['scene_id'] for u in units):
        group = [u for u in units if u['scene_id'] == scene_id]
        total = sum(float(u['delivery']['duration_seconds']) for u in group)
        scene_folder = folder / scene_id
        scene_folder.mkdir(exist_ok=True)
        for filename in ('episode.mp4', 'episode_clean.mp4'):
            concat([verified_artifact(u, filename) for u in group], scene_folder / filename, total, size, control,
                   [float(u['delivery']['duration_seconds']) for u in group])
        scenes.append({'scene_id': scene_id, 'duration_seconds': total,
            'video': media_receipt(scene_folder / 'episode.mp4'), 'clean': media_receipt(scene_folder / 'episode_clean.mp4')})
    total = sum(durations)
    technical = concat([within(ROOT, s['video']['path']) for s in scenes], folder / 'episode.mp4', total, size, control,[s['duration_seconds'] for s in scenes])
    concat([within(ROOT, s['clean']['path']) for s in scenes], folder / 'episode_clean.mp4', total, size, control,[s['duration_seconds'] for s in scenes])
    cues = offset_cues(timelines, durations)
    subtitle = folder / 'subtitles.zh.srt'
    subtitle.write_text('\n\n'.join(f"{i}\n{timestamp(c['start'])} --> {timestamp(c['end'])}\n{c['text']}" for i,c in enumerate(cues,1))+'\n', encoding='utf-8')
    timeline = folder / 'timeline.json'
    write_json(timeline, {'fps':24,'duration_seconds':total,'units':sources,'scenes':scenes,'subtitles':cues,
        'shots': [{'id': u['global_prefix']+'-'+s['id'], 'unit_id':u['id'], 'local_id':s['id'], 'seconds':s['seconds']}
                  for u in units for s in (u.get('episode') or u.get('script') or {}).get('shots',[])]})
    write_json(folder / 'technical_qa.json', technical)
    paths = [folder / f for f in ('episode.mp4','episode_clean.mp4','subtitles.zh.srt','timeline.json','technical_qa.json')]
    def url(path): return '/media/' + path.relative_to(ROOT).as_posix()
    delivery = {'schema':'ai-studio.long-episode.delivery.v1','project_id':episode['id'], 'revision':episode['production_revision'],
        'source_signature':signature, 'title':episode['title'], 'status':'awaiting_producer_final_review',
        'path':str(paths[0].relative_to(ROOT)), 'video_url':url(paths[0]), 'clean_url':url(paths[1]),
        'subtitle_url':url(subtitle),'timeline_url':url(timeline),'manifest_url':url(manifest),
        'duration_seconds':total,'fps':24,'resolution':list(size),'technical_qa':technical,
        'scenes':scenes,'sources':sources,'artifacts':[media_receipt(p) for p in paths],
        'transition_review':episode.get('transition_review'),
        'semantic_status':'pass' if all(u['delivery'].get('semantic_status')=='pass' for u in units) else 'review_required',
        'limitations':{'lip_sync':'not_guaranteed','semantic_review':'sampled_unit_reviews_not_full_film_human_approval'}}
    boundary(control)
    write_json(manifest, delivery)
    return delivery
