"""Observe each static image independently before comparing it to the contract.

Observations are evidence, never approval. No names, desired poses, reference
images or old verdicts enter an observation request.
"""
from __future__ import annotations

import json
import copy
import uuid
from pathlib import Path

from jsonschema import validate
from studio.core import fingerprint, sha256, write_json

POLICY = 'isolated-static-pixels.v1'
SYSTEM = '你是图像观察员。只描述这一张图的实际可见内容，不知道剧情、人物姓名或预期效果。'
TASK = ('描述整体场景，并按观看者从左至右逐一记录可见人物的位置、坐站姿态、衣装、发色及面部特征、'
        '手部姿势和实际持有物。没有人物时people为空。看不清须明确说明，不补猜隐藏物体、原作设定或动作过程。')
FIELDS = ('position', 'posture', 'clothes', 'hair_and_face', 'hands_and_props')
SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'description': {'type': 'string', 'minLength': 5, 'maxLength': 1200},
    'people': {'type': 'array', 'maxItems': 12, 'items': {
        'type': 'object', 'additionalProperties': False,
        'properties': {key: {'type': 'string', 'minLength': 1, 'maxLength': 600} for key in FIELDS},
        'required': list(FIELDS)}}}, 'required': ['description', 'people']}
RULE = ('candidate_only_observation来自只看本次候选图、未提供剧情/参考图/旧分数的独立观察；'
        'reference_only_observations来自各参考图的独立观察，序号与本次实际参考图对应。'
        '观察不是通过结论，仍须对照批准要求、实际图片核对身份、衣装、画风、构图和道具。'
        '不得把参考图的姿态、位置或持物报成候选内容。若判断与观察矛盾，须指出该张实际图片中的具体反证。'
        '不得用角色名字联想原作、古风长袍、传统造型等覆盖本项目实际批准的制服或裙装。'
        '人物参考仅约束身份和批准外观，不要求复制参考姿态或持物；本镜明确状态仍须严格满足。')


def critic_configuration(config, default_tokens):
    """Opt-in reasoning for the comparison stage, never the image observers."""
    setting = config.get('quality', {}).get('static_review')
    if setting is None:
        return config, default_tokens
    if not isinstance(setting, dict) or set(setting) - {'thinking', 'max_tokens'}:
        raise ValueError('Invalid static review settings')
    tokens = setting.get('max_tokens', default_tokens)
    if type(tokens) is not int or not 128 <= tokens <= 16384:
        raise ValueError('Static review output budget outside 128..16384')
    if 'thinking' not in setting:
        return config, tokens
    if type(setting['thinking']) is not bool:
        raise ValueError('Static review thinking must be boolean')
    from .team_planning import task_config
    updated = copy.deepcopy(task_config(config, 'visual_critic'))
    route = updated.get('llm_roles', {}).get('visual_critic')
    profile = updated['llm_profiles'][route] if route else updated.setdefault('llm', {})
    profile.setdefault('chat_template_kwargs', {})['enable_thinking'] = setting['thinking']
    return updated, tokens


def observe(path, config, cache, request, image_url):
    path = Path(path)
    original = sha256(path)
    identity = fingerprint({'policy': POLICY, 'image_sha256': original, 'configuration': fingerprint(config)})
    folder = Path(cache) / identity
    evidence = folder / 'observation.json'
    if evidence.exists():
        saved = json.loads(evidence.read_text())
        if saved.get('identity') != identity or saved.get('sha256') != fingerprint(saved.get('observation')):
            raise ValueError('Static observation cache integrity mismatch')
        row = saved['observation']
    else:
        row = request(config, 'visual_critic', SYSTEM, None, SCHEMA, folder,
                      max_tokens=1400, temperature=0, content=[
                          {'type': 'text', 'text': TASK},
                          {'type': 'image_url', 'image_url': {'url': image_url(path)}}])
    validate(row, SCHEMA)
    if sha256(path) != original:
        raise ValueError('Static image changed during observation')
    if not evidence.exists():
        write_json(evidence, {'identity': identity, 'sha256': fingerprint(row), 'observation': row})
    return {'policy': POLICY, 'image_sha256': original, 'observation': row}


def collect(path, references, config, cache, request, image_url):
    paths = [Path(path), *(Path(p.get('path') if isinstance(p, dict) else p) for p in references)]
    before = [sha256(p) for p in paths]
    rows = [observe(p, config, cache, request, image_url) for p in paths]
    if [sha256(p) for p in paths] != before:
        raise ValueError('Static evidence changed while collecting reference observations')
    return {'candidate_only_observation': rows[0],
            'reference_only_observations': [dict(row, reference_index=i) for i, row in enumerate(rows[1:], 1)],
            'candidate_observation_rule': RULE}


def verify_claims(config, context, schema, initial, path, record, request, image_url):
    """One same-pixel check; uncertain claims cannot trigger a new render."""
    issues = initial['issues']
    if not any(i['severity'] == 'blocking' for i in issues):
        return initial
    result = copy.deepcopy(initial)
    folder = Path(record) / 'isolated-claims' / uuid.uuid4().hex
    audit = {'policy': POLICY, 'initial_review': copy.deepcopy(initial), 'status': 'unavailable',
             'verification_record': str(folder / 'evidence.json')}
    expected = context['candidate_only_observation']['image_sha256']
    candidate = json.dumps(context['candidate_only_observation']['observation'], ensure_ascii=False)
    observations = context['reference_only_observations']
    reference_text = {r['reference_index']: json.dumps(r['observation'], ensure_ascii=False) for r in observations}
    def quotes(value):
        if isinstance(value, str): return [value]
        values = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
        return list(dict.fromkeys(text for child in values for text in quotes(child)))
    candidate_quotes = quotes(context['candidate_only_observation']['observation'])
    reference_quotes = list(dict.fromkeys(text for row in observations for text in quotes(row['observation'])))
    finding = {'type': 'object', 'additionalProperties': False, 'properties': {
        'status': {'type': 'string', 'enum': ['confirmed', 'refuted', 'uncertain']},
        'candidate_quote': {'type': 'string', 'enum': candidate_quotes},
        'reference_quotes': {'type': 'array', 'maxItems': len(observations), 'items': {
            'type': 'object', 'additionalProperties': False, 'properties': {
                'reference_index': {'type': 'integer', 'minimum': 1},
                'quote': {'type': 'string', **({'enum': reference_quotes} if reference_quotes else {})}},
            'required': ['reference_index', 'quote']}},
        'detail': {'type': 'string', 'minLength': 8, 'maxLength': 1200}},
        'required': ['status', 'candidate_quote', 'reference_quotes', 'detail']}
    issue_ids = [f'I{i+1}' for i in range(len(issues))]
    findings_schema = {}
    for ident, issue in zip(issue_ids, issues):
        item = copy.deepcopy(finding)
        if issue['code'] == 'identity' and observations:
            item['properties']['reference_quotes']['minItems'] = 1
        findings_schema[ident] = item
    followup = {'type': 'object', 'additionalProperties': False, 'properties': {
        'review': schema, 'findings': {'type': 'object', 'additionalProperties': False,
            'properties': findings_schema, 'required': issue_ids}},
        'required': ['review', 'findings']}
    system = ('你是独立候选图缺陷复核员。只提供唯一候选F0，参考外观来自逐张独立的真实像素观察。'
        '旧问题是不可信的待核说法，可能把参考图的姿态或衣装当成候选；先实际观察F0，再核验每个问题。'
        'candidate_quote逐字引用candidate_only_observation中的可见描述；身份对照还须引用相应reference_only_observations。'
        'confirmed/refuted须同时描述F0的具体可见证据，不能只引用文字推断；不清楚或观察不足以比较身份时填uncertain。'
        '不能用角色名字补猜原作造型。参考姿态和持物不决定本镜当前状态。'
        'review重新完整检查本镜批准要求与实际候选；保留真正身份、衣装、姿态、画风及道具错误，不为通过而提高分数。'
        'refuted的问题应从review中删除，不得换词重复；confirmed错误仍须保留，uncertain不能自动通过。')
    try:
        if len(issues) > 20 or sha256(path) != expected:
            raise ValueError('Static claim evidence is invalid or changed')
        payload = {**context, 'untrusted_prior_claims': [dict(issue_id=f'I{i+1}', claim=issue) for i, issue in enumerate(issues)]}
        # This bounded evidence check selects exact source quotes; it uses the
        # base visual profile, not the primary comparison's reasoning override.
        answer = request(config, 'visual_critic', system, None, followup, folder,
                         max_tokens=3000, temperature=0, content=[
                             {'type': 'text', 'text': json.dumps(payload, ensure_ascii=False)},
                             {'type': 'text', 'text': 'F0：唯一待评候选，未附任何参考图片。'},
                             {'type': 'image_url', 'image_url': {'url': image_url(path)}}])
        validate(answer, followup)
        findings = answer['findings']
        if len(findings) != len(issues) or sha256(path) != expected:
            raise ValueError('Static claim coverage or pixel identity changed')
        for i, issue in enumerate(issues, 1):
            f = findings[f'I{i}']
            if f['candidate_quote'] not in candidate:
                raise ValueError('Static candidate citation was invented')
            if any(q['quote'] not in reference_text.get(q['reference_index'], '') for q in f['reference_quotes']):
                raise ValueError('Static reference citation was invented')
            if issue['code'] == 'identity' and observations and f['status'] != 'uncertain' and not f['reference_quotes']:
                raise ValueError('Identity claim requires reference observation evidence')
        result = copy.deepcopy(answer['review'])
        confirmed = False
        uncertain = False
        for i, issue in enumerate(issues, 1):
            disposition = findings[f'I{i}']['status']
            if issue['severity'] == 'blocking':
                confirmed |= disposition == 'confirmed'
                uncertain |= disposition == 'uncertain'
            if disposition == 'confirmed' and issue not in result['issues']:
                result['issues'].append(copy.deepcopy(issue))
        audit.update(status='unavailable' if uncertain and not confirmed else 'verified',
                     findings=[dict(issue_id=ident, **findings[ident]) for ident in issue_ids],
                     review=copy.deepcopy(answer['review']), image_sha256=expected)
    except Exception as exc:
        audit['reason'] = type(exc).__name__ + ': ' + str(exc)[:240]
        from .quality import is_transient_transport
        if is_transient_transport(exc):
            write_json(folder / 'evidence.json', audit)
            raise
    result['static_verification'] = audit
    write_json(folder / 'evidence.json', audit)
    return result
