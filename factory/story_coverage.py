"""Original-source and approved-event coverage, separate from semantic evidence."""
from __future__ import annotations
import copy
import hashlib
import re
from studio.core import fingerprint
from .creative_contracts import enabled as editorial_enabled

VERSION = 'ai-studio.story-coverage.v1'


def enabled(project):
    return editorial_enabled(project) and (project.get('creative_quality_contract') or {}).get('coverage_version') == VERSION


def freeze_source(project, parent=None):
    marker = project.get('creative_quality_contract')
    if not enabled(project): return
    if parent and (parent.get('creative_quality_contract') or {}).get('source_document'):
        marker.setdefault('source_document', copy.deepcopy(parent['creative_quality_contract']['source_document']))
    elif project.get('source_text'):
        text = project['source_text']
        sha = hashlib.sha256(text.encode('utf-8')).hexdigest()
        project.setdefault('creative_source_document', {'version': VERSION, 'text': text, 'sha256': sha})
        marker.setdefault('source_document', {'project_id': project['id'], 'sha256': sha})


def ordinal(value):
    if value.isdecimal(): return int(value)
    digits = dict(zip('零〇一二两三四五六七八九', [0,0,1,2,2,3,4,5,6,7,8,9]))
    if '百' in value:
        left, right = value.split('百', 1)
        return digits.get(left, 1) * 100 + (ordinal(right) if right else 0)
    if '十' in value:
        left, right = value.split('十', 1)
        return digits.get(left, 1) * 10 + (digits.get(right, 0) if right else 0)
    return int(''.join(str(digits[c]) for c in value))


def _document(project):
    value = project.get('creative_source_document')
    ref = (project.get('creative_quality_contract') or {}).get('source_document')
    if ref and ref.get('project_id') != project.get('id'):
        from . import store
        value = store.get(ref['project_id'], False).get('creative_source_document')
    if value:
        text = value.get('text', '')
        sha = hashlib.sha256(text.encode('utf-8')).hexdigest()
        if sha != value.get('sha256') or ref and sha != ref.get('sha256'):
            raise ValueError('原始剧本文档与冻结来源哈希不符，未使用当前文本冒充原稿')
        return text, sha
    # Explicit adoption snapshots the original input; reads never fabricate it.
    return '', None


def source_catalog(project):
    if not enabled(project): return []
    text, document_sha = _document(project)
    result, narrative, number = [], True, None
    for match in re.finditer(r'[^\n]+', text):
        value = match.group().strip()
        if not value: continue
        heading = value.lstrip('#').strip()
        if re.fullmatch(r'(?:角色|人物|场景|道具|风格)(?:设定|介绍|表)?[:：]?', heading): narrative = False
        elif re.fullmatch(r'(?:正文|剧本正文|故事正文|正片)[:：]?', heading): narrative = True
        episode = re.match(r'(?:第\s*([零〇一二两三四五六七八九十百0-9]+)\s*集|(?:Episode|EP)\s*([0-9]+)(?![0-9]))', heading, re.I)
        if episode: number = ordinal(episode[1] or episode[2]); narrative = True
        scene_heading = bool(re.fullmatch(r'(?:第[零〇一二两三四五六七八九十百0-9]+场.*|(?:INT\.|EXT\.|内景|外景).*)', heading, re.I))
        # Bound individual semantic chunks without dropping any source bytes.
        start, end = match.start(), match.end()
        while start < end:
            stop = min(end, start + 3000)
            if stop < end:
                boundary = max(text.rfind('。', start, stop), text.rfind('\n', start, stop))
                if boundary > start: stop = boundary + 1
            segment = text[start:stop]
            result.append({'id': 'src_' + hashlib.sha256((str(start) + ':' + segment).encode()).hexdigest()[:16],
                'text': segment, 'start': start, 'end': stop, 'sha256': hashlib.sha256(segment.encode()).hexdigest(),
                'document_sha256': document_sha, 'required': narrative and not value.startswith('#') and episode is None and not scene_heading and heading not in {'正文', '剧本正文', '故事正文', '正片'},
                'episode_number': number})
            start = stop
    return result


def scoped_sources(project):
    number = project.get('episode_number')
    values = source_catalog(project)
    values = [v for v in values if v['episode_number'] in (None, number)] if number else values
    if project.get('record_kind') == 'scene_unit' and 'beats' in (project.get('outline') or {}):
        refs = {ref for event in project['outline']['beats'] for ref in event.get('source_refs', [])}
        values = [v for v in values if v['id'] in refs or not v['required'] and v['episode_number'] is None]
    return values


def required_events(project):
    outline = project.get('outline') or {}
    beats = outline.get('beats')
    if beats is not None:
        return [{'id': b['id'], 'description': b['description'], 'source_refs': list(b.get('source_refs', []))} for b in beats]
    resolved = project.get('resolved_story_brief') or {}
    if resolved.get('causal_beats'):
        return [{'id': 'event_' + hashlib.sha256(v.encode()).hexdigest()[:12], 'description': v, 'source_refs': []} for v in resolved['causal_beats']]
    selected = project.get('selected_story')
    if isinstance(selected, dict): selected = selected.get('id')
    proposal = next((p for p in project.get('proposals', []) if p.get('id') == selected), None)
    if proposal and proposal.get('synopsis'):
        return [{'id': 'approved_story', 'description': proposal['synopsis'], 'source_refs': []}]
    return []


def attach_schema(schema, project):
    if not enabled(project): return schema
    schema = copy.deepcopy(schema)
    shots = schema.get('properties', {}).get('shots', {}).get('items')
    if not isinstance(shots, dict) or 'action' not in shots.get('properties', {}): return schema
    ids = [b['id'] for b in required_events(project)]
    shots['properties']['beat_ids'] = {'type': 'array', 'items': {'type': 'string', 'enum': ids} if ids else {'type': 'string'}, 'maxItems': 32, 'uniqueItems': True}
    shots['required'] = list(dict.fromkeys(shots['required'] + ['beat_ids']))
    return schema


def inspect(project, episode=None):
    episode = episode or project.get('episode') or project.get('script') or {}
    if not enabled(project): return {'version': VERSION, 'status': 'legacy', 'semantic_status': 'not_recorded', 'events': [], 'mapping': []}
    events = required_events(project)
    ids = [b['id'] for b in events]
    known = set(ids)
    source_ids = {s['id'] for s in scoped_sources(project) if s['required']}
    invalid, mapping, covered = [], [], set()
    if len(ids) != len(known): invalid.append('批准事件ID重复')
    shot_ids = [s.get('id') for s in episode.get('shots', [])]
    if len(shot_ids) != len(set(shot_ids)): invalid.append('镜头ID重复，无法确定覆盖位置')
    for event in events:
        if len(event['source_refs']) != len(set(event['source_refs'])) or set(event['source_refs']) - source_ids:
            invalid.append('事件 ' + event['id'] + ' 引用无效或重复的原文片段')
        if source_ids and not event['source_refs']: invalid.append('事件 ' + event['id'] + ' 缺少原文来源')
    for shot in episode.get('shots', []):
        refs = shot.get('beat_ids', [])
        if not isinstance(refs, list) or any(not isinstance(v, str) for v in refs):
            invalid.append(str(shot.get('id')) + ' 事件引用格式无效'); continue
        if len(refs) != len(set(refs)) or set(refs) - known: invalid.append(str(shot.get('id')) + ' 引用无效或重复事件')
        covered.update(refs)
        mapping.append({'shot_id': shot.get('id'), 'event_ids': refs, 'source_signature': fingerprint(shot)})
    missing = sorted(known - covered)
    review = project.get('draft_review') or (project.get('script_review') or {}).get('review') or {}
    checks = review.get('coverage_checks', {})
    current = review.get('source_signature') == fingerprint(episode) and set(checks) == known
    semantic = ('text_pass' if all(c.get('status') == 'pass' for c in checks.values()) else 'text_fail' if any(c.get('status') == 'fail' for c in checks.values()) else 'uncertain') if current and checks else 'not_reviewed'
    return {'version': VERSION, 'status': 'fail' if missing or invalid else 'mapped' if events else 'not_applicable',
        'semantic_status': semantic, 'source_signature': fingerprint({'events': events, 'shots': episode.get('shots', [])}),
        'events': events, 'mapping': mapping, 'missing': missing, 'invalid': invalid,
        'rule': '映射完整只证明引用关系有效，语义落实仍须当前独立审核与实际媒体证据。'}


def structural_issues(project, episode):
    result = inspect(project, episode)
    if result['status'] != 'fail': return []
    return [{'severity': 'blocking', 'code': 'story_coverage', 'description': '必要剧情覆盖不完整或来源无效',
        'evidence': '缺少事件：' + '、'.join(result['missing']) + '；' + '；'.join(result['invalid']),
        'suggestion': '只补齐对应事件或修正引用；标签不能代替正文，保留其他镜头并重新独立审核。',
        'shot_ids': [m['shot_id'] for m in result['mapping'] if m['event_ids'] and any(v in result['invalid'] for v in m['event_ids'])]}]


def request_context(project):
    if not enabled(project): return {}
    return {'version': VERSION, 'events': required_events(project), 'source_catalog': scoped_sources(project),
        'rule': '必要事件须由动作、对白或画面承载；逐镜beat_ids只作来源映射，不代表语义或媒体已通过。'}


def outline_schema(schema, project):
    if not enabled(project): return schema
    schema = copy.deepcopy(schema)
    sources = [s['id'] for s in scoped_sources(project) if s['required']]
    beat = {'type': 'object', 'additionalProperties': False, 'properties': {
        'id': {'type': 'string', 'pattern': '^[A-Za-z][A-Za-z0-9_-]{0,63}$'},
        'description': {'type': 'string', 'minLength': 4, 'maxLength': 800},
        'source_refs': {'type': 'array', 'items': {'type': 'string', 'enum': sources} if sources else {'type': 'string'}, 'minItems': 1 if sources else 0, 'maxItems': 128, 'uniqueItems': True}},
        'required': ['id', 'description', 'source_refs']}
    schema['properties']['beats'] = {'type': 'array', 'items': beat, 'minItems': 2, 'maxItems': 8}
    schema['required'] = list(dict.fromkeys(schema['required'] + ['beats']))
    return schema


def plan_issues(project, plan):
    if not enabled(project): return []
    required = {s['id'] for s in source_catalog(project) if s['required']}
    references, invalid = [], []
    for outline in plan.get('episodes', []):
        beats = outline.get('beats', [])
        if len({b['id'] for b in beats}) != len(beats): invalid.append('第%d集必要事件ID重复' % outline['number'])
        for beat in beats:
            refs = beat.get('source_refs', [])
            references.extend(refs)
            if set(refs) - required or len(set(refs)) != len(refs): invalid.append('第%d集原文来源无效或重复' % outline['number'])
    missing = required - set(references)
    if missing: invalid.append('原始正文片段未被任何分集事件承载：' + '、'.join(sorted(missing)))
    return invalid



def unit_source_excerpt(episode, outline):
    """Only approved event sources enter the unit's narrative excerpt."""
    if not enabled(episode): return episode.get('source_excerpt', '')
    refs = {ref for event in outline.get('beats', []) for ref in event.get('source_refs', [])}
    return '\n'.join(source['text'] for source in scoped_sources(episode) if source['id'] in refs)
