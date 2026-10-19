"""Independent, per-sample evidence for explicit visual requirements.

A general score cannot discharge an explicit constraint. This check uses only
the actual candidate samples, without prior verdicts or identity-reference poses.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import re

from jsonschema import Draft202012Validator
from studio.core import sha256, write_json

POLICY = 'ai-studio.explicit-visual-constraints.v1'
_CLOSED = re.compile(
    r'\bclosed[ -](?:lips|mouths?)\b|\b(?:lips|mouths?)\s+(?:(?:stay|remain|are|is|keep)\s+)?'
    r'(?:(?:naturally|relaxed|fully)\s+)?closed\b|闭口|闭嘴|嘴(?:巴|唇|部)?(?:保持|始终|全程)?闭合', re.I)


def required(shot):
    fields = ('prompt', 'action', 'motion_prompt', 'continuity')
    sources = [{'field': field, 'text': shot[field]} for field in fields
               if isinstance(shot.get(field), str) and _CLOSED.search(shot[field])]
    if shot.get('execution_constraints', {}).get('visible_mouths_closed') is True:
        sources.append({'field': 'execution_constraints.visible_mouths_closed',
                        'text': 'Existing external-voiceover adapter requires closed visible mouths.'})
    return ([{'id': 'visible_mouths_closed', 'sources': sources,
              'requirement': 'Every visible mouth stays closed, with touching lips.'}] if sources else [])


def certified(assessment, path):
    audit = assessment.get('explicit_constraint_verification') or {}
    try:
        actual_sha = sha256(path)
    except OSError:
        return False
    if (not isinstance(audit, dict) or audit.get('policy') != POLICY or audit.get('status') != 'pass'
            or audit.get('media_sha256') != actual_sha):
        return False
    rows, times = audit.get('observations'), audit.get('timestamps')
    if not isinstance(rows, list) or not isinstance(times, list) or not 4 <= len(rows) <= 16 or len(rows) != len(times):
        return False
    if any(type(t) not in (int, float) or not math.isfinite(t) or t < 0 for t in times):
        return False
    if not all(isinstance(row, dict) and type(row.get('sample')) is int for row in rows):
        return False
    return (sorted(row['sample'] for row in rows) == list(range(1, len(rows)+1))
            and all(isinstance(row, dict) and row.get('mouth') in {'closed', 'not_visible'}
                    and isinstance(row.get('evidence'), str) and len(row['evidence']) >= 4 for row in rows))


def enabled(config):
    policy = config.get('production', {}).get('production_recovery', {})
    return isinstance(policy, dict) and policy.get('enabled') is True and policy.get('version') == 'ai-studio.production-recovery.v1'


def verify(config, shot, path, samples, sampling, initial, output, expected_sha, request, image_url):
    constraints = required(shot)
    if not enabled(config) or not constraints:
        return initial
    result = copy.deepcopy(initial)
    audit = {'policy': POLICY, 'constraints': constraints, 'media_sha256': expected_sha,
             'timestamps': list(sampling['timestamps']), 'scope': 'actual_sampled_frames_not_exhaustive_video'}
    target = Path(output) / 'explicit-constraints'
    try:
        if sha256(path) != expected_sha:
            raise ValueError('Candidate pixels changed before explicit-constraint review')
        n = len(samples)
        if not n or n != len(sampling['timestamps']):
            raise ValueError('Explicit-constraint review requires actual timestamped samples')
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['observations'], 'properties': {
            'observations': {'type': 'array', 'minItems': n, 'maxItems': n, 'items': {
                'type': 'object', 'additionalProperties': False, 'required': ['sample', 'mouth', 'evidence'],
                'properties': {'sample': {'type': 'integer', 'minimum': 1, 'maximum': n},
                               'mouth': {'enum': ['closed', 'open', 'not_visible', 'uncertain']},
                               'evidence': {'type': 'string', 'minLength': 4, 'maxLength': 240}}}}}}
        content = [{'type': 'text', 'text': json.dumps({
            'task': '逐张检查候选图中所有可见人物的嘴唇状态。只描述实际像素，不评价剧情，不参考其他审核分数。',
            'requirement': constraints[0]['requirement'],
            'rules': '嘴唇接触且看不到口内间隙才是closed；任何人物上下唇分开、可见口腔或牙齿都是open。'
                     '没有可见嘴部才是not_visible；分辨不清用uncertain。每个样本恰好一项，sample使用实际序号。'
                     '即使只在一帧张嘴，也不能声称所有可见嘴部保持闭合。'}, ensure_ascii=False)}]
        for index, image in enumerate(samples, 1):
            content.extend([{'type': 'text', 'text': f'Sample {index}, {sampling["timestamps"][index-1]:.6f}s'},
                            {'type': 'image_url', 'image_url': {'url': image_url(image)}}])
        followup = request(config, 'visual_critic', '你是独立的可见状态检查员。逐样本记录嘴唇接触或分开的实际证据。',
                           None, schema, target, max_tokens=2400, temperature=0, content=content)
        errors = list(Draft202012Validator(schema).iter_errors(followup))
        if errors:
            raise ValueError('Explicit-constraint observations do not match their contract')
        rows = followup['observations']
        if sorted(row['sample'] for row in rows) != list(range(1, n + 1)):
            raise ValueError('Missing or duplicate actual sample evidence')
        if sha256(path) != expected_sha:
            raise ValueError('Candidate pixels changed during explicit-constraint review')
        audit['observations'] = rows
        opened = [row for row in rows if row['mouth'] == 'open']
        uncertain = [row for row in rows if row['mouth'] == 'uncertain']
        audit['status'] = 'fail' if opened else 'unavailable' if uncertain else 'pass'
        if opened:
            result.setdefault('issues', []).append({'code': 'action', 'severity': 'blocking',
                'detail': '明确的全程闭口要求未满足：' + '；'.join(
                    f'样本{row["sample"]}（{sampling["timestamps"][row["sample"]-1]:.3f}秒）：{row["evidence"]}' for row in opened)})
        if uncertain:
            result['_constraint_unavailable'] = True
    except Exception as exc:
        audit.update(status='unavailable', reason=f'{type(exc).__name__}: {str(exc)[:240]}')
        result['_constraint_unavailable'] = True
    result['explicit_constraint_verification'] = audit
    write_json(target / 'evidence.json', audit)
    return result
