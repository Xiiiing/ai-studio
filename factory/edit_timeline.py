"""Frame-exact immutable edit documents and source ownership checks (24 fps).

The editor references existing receipts, never client paths or provider URLs.
Planning references are not evidence that a trimmed movie depicts an event.
"""
from __future__ import annotations

import copy
import math
import re
from pathlib import Path

import av
from studio import core
from . import store

FPS = 24
SCHEMA = 'ai-studio.edit.v1'
ID = re.compile(r'^[A-Za-z0-9_:-]{1,120}$')


def number(value, label, low, high, integer=False):
    if type(value) not in ((int,) if integer else (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{label}须在{low}–{high}范围内')
    return int(value) if integer else float(value)


def verified(receipt, owner, checksum=True):
    if not isinstance(receipt, dict) or not receipt.get('path') or not receipt.get('sha256'):
        raise ValueError('剪辑素材缺少可信回执')
    path = core.within(core.ROOT, receipt['path'])
    base = core.within(core.ROOT / 'production', owner['slug'])
    if not path.is_relative_to(base) or not path.is_file():
        raise ValueError('剪辑素材不属于本项目')
    marker = base / 'factory_owner.json'
    if not marker.is_file() or core.read_json(marker).get('project_id') != owner['id']:
        raise ValueError('剪辑素材目录归属不符')
    if path.stat().st_size != receipt.get('bytes') or (checksum and core.sha256(path) != receipt['sha256']):
        raise ValueError('剪辑源素材与回执不符')
    return path


def metadata(path):
    with av.open(str(path)) as container:
        video = next(iter(container.streams.video), None)
        audio = next(iter(container.streams.audio), None)
        duration = float(container.duration / av.time_base) if container.duration else 0
        if video and video.duration is not None:
            duration = float(video.duration * video.time_base)
        return {'frames': round(duration * FPS), 'has_audio': bool(audio),
                'resolution': [video.width, video.height] if video else None}


def owners(project):
    if project.get('record_kind') == 'drama':
        raise ValueError('请打开具体剧集进行剪辑，不能跨集混剪')
    if project.get('record_kind') == 'episode' and project.get('unit_ids'):
        result = []
        for uid in project.get('edit_unit_order', project['unit_ids']):
            unit = store.get(uid, False)
            if unit.get('episode_id') != project['id'] or uid not in project['unit_ids']:
                raise ValueError('制作单元不属于当前剧集')
            result.append(unit)
        return result
    return [project]


def source_delivery(project):
    current = project.get('delivery') or {}
    return ((project.get('timeline_edits') or {}).get('base_delivery') or {}) if current.get('edit_version_id') else current


def catalog(project, checksum=True):
    sources, clips, audio, subtitles = {}, [], [], []
    cursor = 0
    for owner in owners(project):
        delivery = source_delivery(owner)
        artifacts = delivery.get('artifacts') or []
        timeline_receipt = next((r for r in artifacts if Path(r.get('path', '')).name == 'timeline.json'), None)
        if not timeline_receipt:
            raise ValueError('请先完成原始成片，剪辑需要已交付的时间线和素材')
        timeline_path = verified(timeline_receipt, owner, checksum)
        timeline = core.read_json(timeline_path)
        plan = owner.get('episode') or owner.get('script') or {}
        runtime = {s['id']: s for s in owner.get('shots', [])}
        local_cursor = 0
        for shot in plan.get('shots', []):
            sid = shot['id']
            data = runtime.get(sid, shot)
            receipt = data.get('video') or next((s.get('video') for s in timeline.get('shots', []) if s.get('shot') == sid), None)
            path = verified(receipt, owner, checksum)
            info = metadata(path)
            frames = number(round(shot['seconds'] * FPS), '镜头帧数', 1, 21600, True)
            if info['frames'] < frames:
                raise ValueError('原视频帧数少于批准的镜头时长')
            key = owner['id'] + ':' + sid
            sources[key] = {'id': key, 'kind': 'video', 'owner_id': owner['id'], 'owner_slug': owner['slug'],
                'shot_id': sid, 'global_shot_id': (owner.get('global_prefix', '') + '-' + sid).lstrip('-'),
                'receipt': copy.deepcopy(receipt), **info, 'approved_frames': frames,
                'beat_ids': copy.deepcopy(shot.get('beat_ids', [])), 'action': shot.get('action', ''),
                'continuity': shot.get('continuity', ''), 'media_url': '/media/' + receipt['path']}
            clips.append({'id': 'c' + str(len(clips) + 1), 'source_id': key, 'in_frame': 0,
                'out_frame': frames, 'transition': {'kind': 'cut', 'frames': 0}, 'source_gain': 1.0})
            local_cursor += frames
        # Delivery-owned independent stems exist separately from the mixed soundtrack.
        audio_receipt = next((r for r in artifacts if Path(r.get('path', '')).name == 'audio_edit.json'), None)
        if audio_receipt:
            audio_path = verified(audio_receipt, owner, checksum)
            audio_edit = core.read_json(audio_path)
            for line in audio_edit.get('dialogue', []):
                receipt = line.get('processed') or line.get('source')
                path = verified(receipt, owner, checksum)
                key = owner['id'] + ':voice:' + line['id']
                info = metadata(path)
                source_shot = next((c for c in clips if c['source_id'] == owner['id'] + ':' + line.get('shot', '')), None)
                sources[key] = {'id': key, 'kind': 'voice', 'owner_id': owner['id'], 'owner_slug': owner['slug'],
                    'receipt': copy.deepcopy(receipt), **info, 'media_url': '/media/' + receipt['path']}
                audio.append({'id': 'a' + str(len(audio)+1), 'source_id': key, 'start_frame': cursor + round(line['start'] * FPS),
                    'in_frame': 0, 'out_frame': info['frames'], 'gain': 1.0, 'anchor_clip_id': source_shot['id'] if source_shot else ''})
            music = audio_path.parent / 'music.wav'
            if music.is_file():
                receipt = core.media_receipt(music)
                verified(receipt, owner, checksum)
                key = owner['id'] + ':music'
                info = metadata(music)
                sources[key] = {'id': key, 'kind': 'music', 'owner_id': owner['id'], 'owner_slug': owner['slug'],
                    'receipt': receipt, **info, 'media_url': '/media/' + receipt['path']}
                audio.append({'id': 'a' + str(len(audio)+1), 'source_id': key, 'start_frame': cursor,
                    'in_frame': 0, 'out_frame': min(info['frames'], local_cursor), 'gain': 1.0, 'anchor_clip_id': ''})
        for cue in timeline.get('subtitles', []):
            subtitles.append({'id': 's' + str(len(subtitles)+1), 'start_frame': cursor + round(cue['start'] * FPS),
                'end_frame': cursor + round(cue['end'] * FPS), 'text': cue['text'], 'source_text': cue['text'],
                'anchor_clip_id': next((c['id'] for c in clips if c['source_id'] == owner['id'] + ':' + cue.get('shot','')), '')})
        cursor += local_cursor
    if not clips:
        raise ValueError('当前交付没有可剪辑的镜头')
    size = (project.get('delivery') or {}).get('resolution') or sources[clips[0]['source_id']]['resolution']
    return {'integrity_checked':checksum,'sources': sources, 'default': {'schema': SCHEMA, 'fps': FPS, 'resolution': size, 'clips': clips,
        'audio': audio, 'subtitles': subtitles, 'confirmed_duration_frames': cursor, 'caption_text_confirmed': False},
        'signature': core.fingerprint({'sources': sources, 'delivery': source_delivery(project).get('source_signature'),
            'approved_context':{k:project.get(k) for k in ('outline','script','episode','resolved_story_brief','creative_quality_contract','execution_config')}})}


def positions(clips):
    result, cursor = [], 0
    for index, clip in enumerate(clips):
        overlap = clip['transition']['frames'] if index else 0
        start = cursor - overlap
        result.append({'clip_id': clip['id'], 'source_id': clip['source_id'], 'start_frame': start,
                       'end_frame': start + clip['out_frame'] - clip['in_frame']})
        cursor = result[-1]['end_frame']
    return result


def normalize(value, sources):
    if not isinstance(value, dict) or set(value) - {'schema','fps','resolution','clips','audio','subtitles','confirmed_duration_frames','caption_text_confirmed'}:
        raise ValueError('剪辑文档包含未知字段')
    result = copy.deepcopy(value)
    if result.get('schema') != SCHEMA or result.get('fps') != FPS:
        raise ValueError('剪辑文档须使用24fps版本合约')
    if (not isinstance(result.get('resolution'), list) or len(result['resolution']) != 2):
        raise ValueError('剪辑分辨率无效')
    for v in result['resolution']:
        number(v, '分辨率', 64, 4096, True)
        if v % 2:
            raise ValueError('剪辑分辨率须为偶数')
    clips = result.get('clips')
    if not isinstance(clips, list) or not 1 <= len(clips) <= 256:
        raise ValueError('剪辑须包含1–256段镜头')
    ids = set()
    for index, clip in enumerate(clips):
        if not isinstance(clip, dict) or set(clip) != {'id','source_id','in_frame','out_frame','transition','source_gain'}:
            raise ValueError('镜头剪辑字段不完整')
        if not isinstance(clip['id'], str) or not ID.fullmatch(clip['id']) or clip['id'] in ids:
            raise ValueError('剪辑片段编号重复或无效')
        ids.add(clip['id'])
        source = sources.get(clip['source_id'])
        if not source or source['kind'] != 'video':
            raise ValueError('剪辑引用了无效的视频来源')
        start = number(clip['in_frame'], '入点', 0, source['frames']-1, True)
        stop = number(clip['out_frame'], '出点', start+1, source['frames'], True)
        number(clip['source_gain'], '原声音量', 0, 2)
        transition = clip['transition']
        if not isinstance(transition, dict) or set(transition) != {'kind','frames'} or transition['kind'] not in {'cut','dissolve','fadeblack'}:
            raise ValueError('不支持的转场')
        frames = number(transition['frames'], '转场帧数', 0, min(48, stop-start-1), True)
        if transition['kind'] == 'cut' and frames != 0 or transition['kind'] != 'cut' and frames < 1:
            raise ValueError('转场时长无效')
        if index == 0 and frames:
            raise ValueError('首镜不能引用前镜转场')
        if index and frames >= clips[index-1]['out_frame'] - clips[index-1]['in_frame']:
            raise ValueError('转场超过相邻镜头可用范围')
    span = positions(clips)
    duration = span[-1]['end_frame']
    number(duration, '成片帧数', 2, 21600, True)
    number(result.get('confirmed_duration_frames'), '确认片长', 2, 21600, True)
    if type(result.get('caption_text_confirmed', False)) is not bool:
        raise ValueError('字幕文字确认须为布尔值')
    if not isinstance(result.get('audio'), list) or len(result['audio']) > 1024:
        raise ValueError('音轨数量无效')
    ids = set()
    for track in result['audio']:
        if not isinstance(track, dict) or set(track) != {'id','source_id','start_frame','in_frame','out_frame','gain','anchor_clip_id'}:
            raise ValueError('音轨字段不完整')
        if not isinstance(track['id'], str) or not ID.fullmatch(track['id']) or track['id'] in ids:
            raise ValueError('音轨编号重复或无效')
        ids.add(track['id'])
        source = sources.get(track['source_id'])
        if not source or source['kind'] not in {'voice','music'}:
            raise ValueError('音轨引用不属于本项目')
        start = number(track['start_frame'], '音轨起点', 0, duration, True)
        first = number(track['in_frame'], '音频入点', 0, source['frames']-1, True)
        last = number(track['out_frame'], '音频出点', first+1, source['frames'], True)
        if start + last-first > duration:
            raise ValueError('音轨超出成片，不能截断对白尾音；请调整音轨或片长')
        number(track['gain'], '音量', 0, 2)
        if track['anchor_clip_id'] and track['anchor_clip_id'] not in {c['id'] for c in clips}:
            raise ValueError('音轨关联的镜头不存在')
    subs = result.get('subtitles')
    if not isinstance(subs, list) or len(subs) > 2048:
        raise ValueError('字幕数量无效')
    previous, ids = 0, set()
    for cue in subs:
        if not isinstance(cue, dict) or set(cue) != {'id','start_frame','end_frame','text','source_text','anchor_clip_id'}:
            raise ValueError('字幕字段不完整')
        if not isinstance(cue['id'],str) or not ID.fullmatch(cue['id']) or cue['id'] in ids:
            raise ValueError('字幕编号重复或无效')
        ids.add(cue['id'])
        start = number(cue['start_frame'], '字幕起点', previous, duration-1, True)
        previous = number(cue['end_frame'], '字幕终点', start+1, duration, True)
        if cue['anchor_clip_id'] and cue['anchor_clip_id'] not in {c['id'] for c in clips}:
            raise ValueError('字幕关联的镜头不存在')
        if not isinstance(cue['text'],str) or not 1 <= len(cue['text']) <= 1000 or not isinstance(cue['source_text'],str):
            raise ValueError('字幕文字无效')
    return result


def check_document(project, document, sources):
    spans = positions(document['clips'])
    issues = []
    duration = spans[-1]['end_frame']
    if document['confirmed_duration_frames'] != duration:
        issues.append({'code':'duration_confirmation', 'message':'剪辑片长已变化，请明确确认新交付规格'})
    if any(c['text'] != c['source_text'] for c in document['subtitles']) and not document.get('caption_text_confirmed'):
        issues.append({'code':'caption_text_changed','message':'字幕改字不会重新配音，请确认字幕与声音的关系'})
    try:
        from .story_coverage import required_events
        required = required_events(project)
    except ImportError:
        required = (project.get('outline') or {}).get('beats') or []
    if not required:
        issues.append({'code':'coverage_contract_missing','message':'当前项目尚未建立批准事件映射；请在空闲边界显式启用新创作合约并核对映射，原素材和草稿保留'})
    represented = set()
    for clip in document['clips']:
        represented.update(sources[clip['source_id']].get('beat_ids', []))
    for event in required:
        if event['id'] not in represented:
            issues.append({'code':'missing_event','event_id':event['id'],'message':'剪辑缺少必要事件：' + event.get('description',event['id'])})
    return {'status':'fail' if issues else 'pass','issues':issues, 'duration_frames':duration, 'positions':spans,
            'semantic_status':'not_reviewed','required_event_ids':[e['id'] for e in required],
            'scope':'structure_and_declared_mapping_not_observed_media'}
