"""Approved adjacent-shot state snapshots and scoped observations of actual cuts."""
from __future__ import annotations
import copy
import json
from pathlib import Path
from studio import core
from .creative_contracts import enabled
from .schemas import object_schema, text_schema, array_schema
from .scheduler import ProductionNeedsDecision

VERSION = 'ai-studio.shot-continuity.v1'
STATE_SCHEMA = object_schema({'mode': {'type': 'string', 'enum': ['continuous', 'scene_cut', 'time_skip']},
    'opening_state': text_schema(1, 1200), 'ending_state': text_schema(1, 1200),
    'allowed_changes': array_schema(text_schema(1, 500), 0, 12)})
REVIEW_SCHEMA = object_schema({'status': {'type': 'string', 'enum': ['pass', 'fail', 'uncertain']},
    'previous_observed': text_schema(1, 1800), 'current_observed': text_schema(1, 1800),
    'issues': array_schema(text_schema(1, 1200), 0, 12)})
RULE = ('连续镜头只保留批准的人物身份、衣装、持物和空间关系；scene_cut/time_skip允许明确列出的场景与时间变化。'
        'opening_state和ending_state是计划，不是已观察事实。图像是当前镜头开始瞬间；后续动作不得提前发生在首帧。')


def attach_schema(schema, project):
    if not enabled(project): return schema
    schema = copy.deepcopy(schema)
    shot = schema.get('properties', {}).get('shots', {}).get('items')
    if not isinstance(shot, dict) or 'action' not in shot.get('properties', {}): return schema
    shot['properties']['continuity_state'] = copy.deepcopy(STATE_SCHEMA)
    shot['required'] = list(dict.fromkeys(shot['required'] + ['continuity_state']))
    return schema


def state(shot):
    value = shot.get('continuity_state')
    if not isinstance(value, dict): return None
    from jsonschema import validate, ValidationError
    try: validate(value, STATE_SCHEMA)
    except ValidationError: return None
    return copy.deepcopy(value)


def request_context(project, shot, episode=None):
    if not enabled(project): return None
    episode = episode or project.get('episode') or project.get('script') or project.get('screenplay_draft') or {}
    shots = episode.get('shots', [])
    index = next((i for i, s in enumerate(shots) if s.get('id') == shot.get('id')), None)
    if index is None: raise ProductionNeedsDecision('当前镜头不在批准顺序中，保留已有素材', 'shot_continuity_stale')
    previous = shots[index - 1] if index else None
    current_state = state(shot)
    previous_state = state(previous) if previous else None
    if current_state is None or previous and previous_state is None:
        raise ProductionNeedsDecision('当前稿件缺少明确的跨镜状态，请先完成分镜审核', 'shot_continuity_missing', {'shot_id': shot.get('id')})
    current = {'shot_id': shot['id'], 'mode': current_state['mode'], 'opening_state': current_state['opening_state'],
        'ending_state': current_state['ending_state'], 'allowed_changes': current_state['allowed_changes'],
        'character_states': copy.deepcopy(shot.get('character_states', {}))}
    context = {'version': VERSION, 'previous': None if previous is None else {
        'shot_id': previous['id'], 'planned_ending_state': previous_state['ending_state'],
        'character_states': copy.deepcopy(previous.get('character_states', {})),
        'source_signature': core.fingerprint(previous)}, 'current': current, 'rule': RULE,
        'observed_state': 'not_observed'}
    context['source_signature'] = core.fingerprint(context)
    return context


def structural_issues(project, episode):
    if not enabled(project): return []
    result = []
    for shot in episode.get('shots', []):
        if state(shot) is None:
            result.append({'severity': 'blocking', 'code': 'shot_continuity_missing', 'description': '镜头缺少有效的开始/结束状态与允许变化',
                'evidence': str(shot.get('id')) + ' 缺少或不符合当前跨镜状态合约',
                'suggestion': '仅补齐该镜计划状态，不改写批准剧情；随后重新独立审核', 'shot_ids': [shot.get('id')]})
    return result


def snapshot(project):
    if not enabled(project): return {'version': VERSION, 'status': 'legacy', 'links': []}
    episode = project.get('episode') or project.get('script') or project.get('screenplay_draft') or {}
    links, invalid = [], []
    for shot in episode.get('shots', []):
        try: links.append(request_context(project, shot, episode))
        except ProductionNeedsDecision as exc: invalid.append({'shot_id': shot.get('id'), 'message': str(exc)})
    return {'version': VERSION, 'status': 'needs_review' if invalid else 'planned', 'links': links, 'invalid': invalid,
        'media_review': copy.deepcopy(project.get('shot_continuity_review') or ((project.get('needs_decision') or {}).get('details') or {}).get('review')), 'rule': RULE}


def verify_delivery(project, delivery, config, control=None):
    """Observe only actual adjacent endpoints; never certify an unobserved action."""
    if not enabled(project): return None
    episode = project.get('episode') or project.get('script') or {}
    shots = episode.get('shots', [])
    if len(shots) < 2: return {'version': VERSION, 'status': 'not_applicable', 'scope': 'adjacent_endpoint_frames'}
    from .episode_assembly import verified_artifact, boundary
    from .drama_continuity import frame
    from .config import llm_identity
    from .llm import _request, _image_url
    from .drama_resources import ensure
    from jsonschema import validate
    path = verified_artifact({**project, 'delivery': delivery}, 'episode_clean.mp4')
    media_sha = core.sha256(path)
    from .config import resolve_llm
    from .prompt_workbench import _local_trial
    if not _local_trial(resolve_llm(config, 'visual_critic', require_vision=True)):
        raise ProductionNeedsDecision('跨镜复核仅使用已有授权的内网视觉模型；供应商金额授权尚未接入', 'shot_continuity_budget_required')
    model = llm_identity(config, 'visual_critic')
    results, offset = [], 0.0
    for index, (previous, current) in enumerate(zip(shots, shots[1:]), 1):
        offset += float(previous['seconds'])
        if project.get('continuity_boundary_indices') is not None and index not in project['continuity_boundary_indices']:
            continue
        context = request_context(project, current, episode)
        timestamps = [max(0, offset - 1/24), offset]
        identity = {'version': VERSION, 'context': context['source_signature'], 'media_sha256': media_sha,
            'timestamps': timestamps, 'model': model, 'scope': 'adjacent_endpoint_frames'}
        signature = core.fingerprint(identity)
        folder = core.within(core.ROOT / 'production' / project['slug'], 'quality/shot-transitions/' + signature)
        result_file = folder / 'review.json'
        boundary(control)
        if result_file.exists():
            record = core.read_json(result_file)
            if record.get('signature') != signature or record.get('identity') != identity: raise ValueError('跨镜证据版本不符')
            answer = record['answer']; validate(answer, REVIEW_SCHEMA)
        else:
            ensure(project, config, role='visual_critic')
            images = [frame(path, timestamp) for timestamp in timestamps]
            folder.mkdir(parents=True, exist_ok=True)
            intent = folder / 'intent.json'
            parsed = list((folder / 'call').glob('calls/*/parsed.json'))
            if parsed:
                answer = core.read_json(parsed[-1]); validate(answer, REVIEW_SCHEMA)
            elif intent.exists():
                raise ProductionNeedsDecision('跨镜复核结果未知，保留成片等待核对，不重复提交', 'shot_continuity_unknown', identity)
            else:
                payload = {'approved_transition': context, 'actual_media': identity,
                    'evidence_scope': '只有前镜末帧与本镜首帧，不能据此证明全过程动作、说话或口型同步。'}
                content = [{'type': 'text', 'text': json.dumps(payload, ensure_ascii=False)}]
                for label, timestamp, image in zip(('previous final frame', 'current first frame'), timestamps, images):
                    content += [{'type': 'text', 'text': label + ' @ ' + str(timestamp)}, {'type': 'image_url', 'image_url': {'url': _image_url(image)}}]
                core.write_json(intent, identity)
                answer = _request(config, 'visual_critic',
                    '只审核所给真实交界帧。描述各帧实际可见人物、衣装、持物和位置，检查批准的跨镜承接。'
                    '连续、换场和省略时间分别按mode与allowed_changes判断；不因正常换场的构图/光照改变判错。'
                    '明确可见且破坏剧情的矛盾fail；证据不能确定时uncertain；pass时issues为空。'
                    '不得把计划描述当作实际结果，也不推断未采样画面或音轨。',
                    payload, REVIEW_SCHEMA, folder / 'call', max_tokens=2400, temperature=.2, content=content)
                validate(answer, REVIEW_SCHEMA)
            core.write_json(result_file, {'signature': signature, 'identity': identity, 'answer': answer})
        result = {'from_shot': previous['id'], 'to_shot': current['id'], 'identity': identity, 'signature': signature, **answer}
        results.append(result)
        if answer['status'] != 'pass' or answer['issues']:
            raise ProductionNeedsDecision('跨镜状态需要复核或定向修复，原成片已保留', 'shot_continuity',
                {'type': 'episode_continuity', 'unit_id': current.get('unit_id'), 'shot_ids': [current.get('local_shot_id', current['id'])], 'review': result, 'retained_delivery': copy.deepcopy(delivery)})
    return {'version': VERSION, 'status': 'pass', 'scope': 'adjacent_endpoint_frames', 'media_sha256': media_sha,
        'transitions': results, 'limitations': '不证明未采样动作、整段声音或人工终审'}



def verify_episode(episode, units, delivery, config, control=None):
    """Long-unit internals were checked by the graph; inspect only new cut joins."""
    if not enabled(episode): return None
    shots, boundaries = [], []
    for unit in units:
        if shots: boundaries.append(len(shots))
        for shot in (unit.get('episode') or unit.get('script') or {}).get('shots', []):
            shots.append({**copy.deepcopy(shot), 'id': unit['global_prefix'] + '-' + shot['id'],
                'unit_id': unit['id'], 'local_shot_id': shot['id']})
    if not shots: raise ProductionNeedsDecision('单集合成缺少逐镜来源，未生成跨镜通过结论', 'shot_continuity_missing')
    current = {**episode, 'script': {'shots': shots}, 'episode': {'shots': shots}, 'continuity_boundary_indices': boundaries}
    return verify_delivery(current, delivery, config, control)
