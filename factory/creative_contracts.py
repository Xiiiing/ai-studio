"""Versioned, source-bound editorial decisions; suggestions never grant authority."""
from __future__ import annotations

import copy
import json

from studio.core import fingerprint

VERSION = 'ai-studio.creative-quality.v1'
NATURES = ('STRUCTURAL', 'OBJECTIVE', 'CRAFT', 'AESTHETIC')
INSTRUCTION = (
    '\n审核契约：逐项区分STRUCTURAL结构错误、OBJECTIVE可复核事实问题、CRAFT创作建议、AESTHETIC审美选择。'
    '每项注明正文位置、来源证据ID、实际影响及最小修订目标。只引用project.review_evidence_catalog中的当前证据；'
    '证据缺失不能编造。审美和创作建议不得作为重大事实错误，不得单独触发自动返修；'
    '有两种会改变作者意图的合理方向时requires_choice=true，描述具体选择。'
    '必须保留未命中内容、批准事实和已明确的用户要求；数字评分继续使用原门槛。'
)


def enabled(project):
    return (project.get('creative_quality_contract') or {}).get('version') == VERSION


def initialize(project, parent=None):
    if parent is not None and not enabled(parent):
        return project
    project.setdefault('creative_quality_contract', copy.deepcopy((parent or {}).get(
        'creative_quality_contract') or {'version': VERSION}))
    from .story_coverage import freeze_source
    freeze_source(project, parent)
    return project


def evidence_catalog(payload):
    """Catalog current supplied narrative, never inferred historical evidence."""
    result = []
    episode = payload.get('episode') or payload.get('current_episode')
    if isinstance(episode, dict):
        for shot in episode.get('shots', []):
            ident = shot.get('id')
            if isinstance(ident, str):
                text = json.dumps({k: shot.get(k) for k in ('action', 'dialogue', 'continuity', 'dramatic_beat', 'character_states')}, ensure_ascii=False)
                result.append({'id': ident, 'location': '镜头 ' + ident, 'text': text, 'sha256': fingerprint(text)})
    for proposal in payload.get('proposals', []):
        ident = proposal.get('id')
        if ident:
            text = json.dumps({k: v for k, v in proposal.items() if k not in ('review', 'scores')}, ensure_ascii=False)
            result.append({'id': 'proposal:' + ident, 'location': '方案 ' + ident, 'text': text, 'sha256': fingerprint(text)})
    return result


def review_call(project, role, system, payload, schema):
    if not enabled(project) or role not in {'independent_critic', 'story_critic'}:
        return system, payload, schema
    catalog = evidence_catalog(payload)
    if not catalog:
        return system, payload, schema
    schema = copy.deepcopy(schema)
    changed = False
    from .story_coverage import required_events
    event_project = {**project, **({'resolved_story_brief': payload['resolved_story_brief']} if payload.get('resolved_story_brief') else {})}
    events = required_events(event_project) if isinstance(payload.get('episode'), dict) else []
    if events:
        schema['properties']['coverage_checks'] = {'type': 'object', 'additionalProperties': False,
            'properties': {event['id']: {'type': 'object', 'additionalProperties': False, 'properties': {
                'status': {'type': 'string', 'enum': ['pass', 'fail', 'uncertain']},
                'evidence_refs': {'type': 'array', 'items': {'type': 'string', 'enum': [r['id'] for r in catalog]}, 'minItems': 1, 'maxItems': 12, 'uniqueItems': True},
                'reason': {'type': 'string', 'minLength': 4, 'maxLength': 1000}}, 'required': ['status', 'evidence_refs', 'reason']} for event in events},
            'required': [event['id'] for event in events]}
        schema['required'] = list(dict.fromkeys(schema.get('required', []) + ['coverage_checks']))
        payload = {**payload, 'project': {**(payload.get('project') or {}), 'required_story_events': events}}
        changed = True

    def upgrade(node):
        nonlocal changed
        if not isinstance(node, dict):
            return
        props = node.get('properties', {})
        if {'severity', 'description', 'suggestion', 'shot_ids'} <= set(props):
            props.update(nature={'type': 'string', 'enum': list(NATURES)},
                location={'type': 'string', 'minLength': 1, 'maxLength': 300},
                impact={'type': 'string', 'minLength': 1, 'maxLength': 800},
                repair_target={'type': 'string', 'minLength': 1, 'maxLength': 800},
                evidence_refs={'type': 'array', 'items': {'type': 'string', 'enum': [r['id'] for r in catalog]}, 'minItems': 1, 'maxItems': 12, 'uniqueItems': True},
                requires_choice={'type': 'boolean'})
            node['required'] = list(dict.fromkeys(node.get('required', []) + ['nature', 'location', 'impact', 'repair_target', 'evidence_refs', 'requires_choice']))
            changed = True
        for value in list(node.values()):
            if isinstance(value, dict): upgrade(value)
            elif isinstance(value, list):
                for entry in value: upgrade(entry)
    upgrade(schema)
    if not changed:
        return system, payload, schema
    from .prompt_registry import derive_prompt
    return (derive_prompt(system, str(system) + INSTRUCTION, 'creative_quality_v1'),
            {**payload, 'project': {**(payload.get('project') or {}), 'review_contract': VERSION, 'review_evidence_catalog': catalog}}, schema)


def normalize(project, review, episode, structural=()):
    if not enabled(project):
        return {**review, 'issues': list(structural) + review.get('issues', [])}
    value = copy.deepcopy(review)
    source = fingerprint(episode)
    catalog = {r['id']: r for r in evidence_catalog({'episode': episode})}
    issues = []
    for issue in structural:
        entry = copy.deepcopy(issue)
        ids = entry.get('shot_ids') or list(catalog)
        entry.update(nature='STRUCTURAL', location='、'.join(ids) or '当前制作规格',
            impact=entry.get('description', ''), repair_target=entry.get('suggestion', ''),
            evidence_refs=[sid for sid in ids if sid in catalog], requires_choice=False,
            source_signature=source, evidence_origin='deterministic_validator', auto_repair=True)
        issues.append(entry)
    for issue in value.get('issues', []):
        entry = copy.deepcopy(issue)
        refs = entry.get('evidence_refs', [])
        valid = bool(refs and len(refs) == len(set(refs)) and all(r in catalog for r in refs))
        entry['source_signature'] = source
        entry['evidence_origin'] = 'independent_review'
        entry['evidence_sources'] = [copy.deepcopy(catalog[r]) for r in refs if r in catalog]
        entry['auto_repair'] = bool(valid and entry.get('nature') in {'STRUCTURAL', 'OBJECTIVE'}
            and entry.get('severity') in {'blocking', 'major'} and not entry.get('requires_choice')
            and entry.get('impact', '').strip() and entry.get('repair_target', '').strip())
        if entry.get('nature') in {'CRAFT', 'AESTHETIC'}:
            entry['reported_severity'] = entry.get('severity')
            entry['severity'] = 'minor'
        if not valid:
            entry['auto_repair'] = False
            entry['evidence_status'] = 'unverified'
        issues.append(entry)
    for ident, check in value.get('coverage_checks', {}).items():
        if check.get('status') != 'pass':
            refs = check.get('evidence_refs', [])
            valid = bool(refs and all(r in catalog for r in refs))
            issues.append({'severity': 'major', 'code': 'story_coverage_' + check.get('status', 'uncertain'),
                'nature': 'OBJECTIVE', 'description': '必要事件 ' + ident + ' 尚未在正文中得到有效落实',
                'location': '、'.join(refs), 'evidence': check.get('reason', ''), 'impact': '必要剧情可能缺失',
                'suggestion': '补齐相应事件并重新独立审核，保留其他内容', 'repair_target': check.get('reason', ''),
                'shot_ids': [r for r in refs if r in catalog], 'evidence_refs': refs,
                'evidence_sources': [copy.deepcopy(catalog[r]) for r in refs if r in catalog],
                'source_signature': source, 'requires_choice': False,
                'auto_repair': valid and check.get('status') == 'fail', 'evidence_status': check.get('status')})
    value.update(issues=issues, review_contract=VERSION, source_signature=source,
        repair_shot_ids=sorted({sid for i in issues if i.get('auto_repair') for sid in i.get('shot_ids', []) if sid in catalog}))
    return value


def can_repair(project, review):
    if not enabled(project): return True
    return bool(any(i.get('auto_repair') for i in review.get('issues', []))
                and not any(i.get('requires_choice') for i in review.get('issues', [])))


def repair_feedback(project, review):
    if not enabled(project): return copy.deepcopy(review)
    return {**copy.deepcopy(review), 'issues': [copy.deepcopy(i) for i in review.get('issues', []) if i.get('auto_repair')],
        'instruction': '只修改有证据的修订目标及必要衔接；其他已批准内容保持原样。'}


def adopt(pid, payload):
    from . import store
    from .production_edits import require_idle
    from .review_advice import ensure_no_gpu_intents
    from .dramas import cas, replay
    if set(payload) != {'expected_revision', 'request_id', 'confirmed'} or payload.get('confirmed') is not True:
        raise ValueError('启用新审核契约须明确确认当前项目版本')
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        project = store._read(db, pid)
        key, signature, saved = replay(project, payload)
        if saved: return project
        cas(project, payload)
        require_idle(db, project)
        ensure_no_gpu_intents(project)
        initialize(project)
        project['creative_quality_review_required'] = True
        project.setdefault('creative_quality_adoptions', []).append({'at': store.now(), 'source_revision': project['revision'], 'version': VERSION})
        project.setdefault('operation_requests', {})[key] = {'signature': signature}
        store._write(db, project)
        return project


def snapshot(project):
    review = project.get('draft_review') or (project.get('script_review') or {}).get('review') or {}
    from .story_coverage import inspect
    return {'coverage': inspect(project), 'enabled': enabled(project), 'version': (project.get('creative_quality_contract') or {}).get('version'),
        'requires_review': project.get('creative_quality_review_required', False),
        'review': copy.deepcopy(review), 'history_preserved': True}
