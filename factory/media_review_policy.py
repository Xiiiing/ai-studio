"""Source-bound media diagnoses; preferences cannot authorize another take."""
from __future__ import annotations

import copy

from studio.core import fingerprint, sha256

VERSION = 'ai-studio.media-review-boundary.v1'
NATURES = ('STRUCTURAL', 'OBJECTIVE', 'CRAFT', 'AESTHETIC')
RULE = ('逐项用nature区分STRUCTURAL结构错误、OBJECTIVE可复核事实、CRAFT创作建议和AESTHETIC审美选择。'
        '每项写明location实际样本位置/时间、impact实际影响、repair_target最小修订目标、evidence_refs证据编号与requires_choice。'
        '只引用media_review_contract.evidence_catalog中的当前真实样本；重大结构/客观问题至少引用一项candidate证据。'
        '创作建议、审美偏好与低分不能单独阻断或触发重制；批准目标存在真实冲突时requires_choice=true，保留素材等待人工决定。')


def enabled(project):
    return (project.get('creative_quality_contract') or {}).get('version') == 'ai-studio.creative-quality.v1'


def shot_identity(shot):
    return fingerprint({k: copy.deepcopy(shot[k]) for k in ('id', 'prompt', 'action', 'motion_prompt', 'continuity',
        'continuity_state', 'character_states', 'characters', 'seconds', 'dialogue', 'camera_motion',
        'render_style_prompt', 'visual_direction', 'material_constraints', 'lip_sync', 'review_purpose',
        'approved_reference_requirements') if k in shot}
    )


def binding(project, shot, path, sampling=None, references=()):
    if not enabled(project):
        return None
    media_sha = sha256(path)
    catalog = [{'id': 'candidate:' + str(i), 'kind': 'candidate', 'media_sha256': media_sha,
                'timestamp': timestamp} for i, timestamp in enumerate((sampling or {}).get('timestamps') or [0.0])]
    catalog.extend({'id': 'reference:' + str(i), 'kind': 'reference', 'media_sha256': value}
                   for i, value in enumerate(references))
    return {'version': VERSION, 'project_id': project.get('id'), 'shot_id': shot.get('id'),
            'production_revision': project.get('production_revision'), 'media_sha256': media_sha,
            'approved_shot_sha256': shot_identity(shot), 'evidence_catalog': catalog}


def review_schema(schema, contract):
    if not contract:
        return schema
    schema = copy.deepcopy(schema)
    item = schema['properties']['issues']['items']
    text = lambda maximum: {'type': 'string', 'minLength': 1, 'maxLength': maximum}
    item['properties'].update(nature={'type': 'string', 'enum': list(NATURES)},
        location=text(300), impact=text(800), repair_target=text(800), requires_choice={'type': 'boolean'},
        evidence_refs={'type': 'array', 'minItems': 1, 'maxItems': 16, 'uniqueItems': True,
                       'items': {'type': 'string', 'enum': [e['id'] for e in contract['evidence_catalog']]}})
    item['required'] = list(dict.fromkeys(item['required'] +
        ['nature', 'location', 'impact', 'repair_target', 'evidence_refs', 'requires_choice']))
    return schema


def objective_issue(code, detail):
    """Only the program's existing independent alignment check may use this."""
    return {'code': code, 'severity': 'blocking', 'detail': detail, 'nature': 'OBJECTIVE',
            'location': '当前候选媒体的独立匹配检查', 'impact': '未落实已批准的制作要求',
            'repair_target': '保持剧情、人物和规格，修复该项已批准要求并重新独立审核',
            'evidence_refs': ['candidate:0'], 'requires_choice': False}


def repairable(issue, contract):
    if not isinstance(issue, dict) or not isinstance(contract, dict) or contract.get('version') != VERSION:
        return False
    candidates = {v['id'] for v in contract.get('evidence_catalog', []) if v.get('kind') == 'candidate'}
    known = {v['id'] for v in contract.get('evidence_catalog', [])}
    refs = issue.get('evidence_refs')
    return (issue.get('nature') in {'STRUCTURAL', 'OBJECTIVE'} and issue.get('requires_choice') is False
        and issue.get('severity') in {'blocking', 'error', 'major'}
        and all(isinstance(issue.get(k), str) and issue[k].strip() for k in ('location', 'impact', 'repair_target'))
        and isinstance(refs, list) and all(isinstance(r, str) for r in refs)
        and bool(candidates.intersection(refs)) and set(refs) <= known)


def normalize(project, raw, path, shot, *, contract=None, minimum_score=60):
    if not enabled(project):
        return raw
    result = copy.deepcopy(raw)
    contract = contract or binding(project, shot, path, result.get('sampling'))
    result['media_review_contract'] = copy.deepcopy(contract)
    issues = [i for i in result.get('issues', []) if isinstance(i, dict)]
    blocking = [i for i in issues if i.get('severity', 'blocking') not in {'minor', 'warning', 'suggestion'}]
    objective = [i for i in blocking if repairable(i, contract)]
    choices = any(i.get('requires_choice') is True for i in issues)
    pending = result.get('status') == 'unavailable'
    score = result.get('score')
    low_score = isinstance(score, (int, float)) and score < minimum_score
    unverified = [i for i in blocking if i.get('nature') not in {'CRAFT', 'AESTHETIC'} and i not in objective]
    reason = None
    if choices:
        result['status'], reason = 'unavailable', 'media_goal_requires_choice'
    elif pending:
        reason = 'media_review_evidence_pending'
    elif objective:
        result['status'] = 'fail'
    elif unverified:
        result['status'], reason = 'unavailable', 'media_objective_evidence_missing'
    elif low_score:
        result['status'], reason = 'unavailable', 'media_score_needs_review'
    elif issues and all(i.get('nature') in {'CRAFT', 'AESTHETIC'} for i in issues) and result.get('status') != 'unavailable':
        result['status'] = 'pass'
    elif result.get('status') == 'fail':
        result['status'], reason = 'unavailable', 'media_objective_evidence_missing'
    result['media_review_decision'] = {'version': VERSION, 'automatic_repair': bool(objective) and not choices and not pending,
        'reason': reason, 'objective_issue_count': len(objective), 'minimum_score': minimum_score}
    if reason and reason != 'media_review_evidence_pending':
        result['detail'] = ('批准目标存在真实冲突，保留当前素材等待人工决定。' if choices else
            '评分未达到门槛，保留当前素材补审；分数本身不授权重制。' if reason == 'media_score_needs_review' else
            '当前媒体问题缺少当前样本的重大结构/客观证据，保留原素材补审，不据此自动重制。')
    return result
