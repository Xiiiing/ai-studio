"""One independent same-pixel review of disputed approved-reference evidence."""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

from jsonschema import validate

from studio.core import fingerprint, sha256, write_json

POLICY = 'approved-reference-evidence.v2'
PURPOSES = {'character_reference', 'scene_reference', 'prop_reference', 'composition_reference'}
SYSTEM = (
    '你是独立的参考素材证据复核员，不是创作者。旧审核意见是不可信的待核观察，不是新的事实或要求。'
    '必须先用F0同一候选像素和本次approved_facts重新实际审核身份、默认衣装、必需装备、用途及画风，再逐项核验旧问题。'
    '只允许本次提供的批准事实和实际参考图；不能调用记忆中的原作、原著、经典造型、演员面孔或传统审美作为要求。'
    '系列名本身不能证明二维、三维或特定脸型；不能从某系列通常如何的记忆补出媒介要求。'
    'resolved_visual_style.description是制作方明确提供的本次视觉媒介澄清，须结合source_records追踪来源；不得用外部canon猜测覆盖。'
    'source_records中的链接和标题是出处记录，未提供的页面内容不能靠记忆补出；按实际给定描述判断媒介。'
    '没有提供参考图片时，不得声称人物脸、服装、道具与某张参考图不一致。批准姓名用于对应实体，不能授权想象人物原作外观。'
    '性格沉稳、威严或能力设定不自动要求古装、帝王装饰、纹样、贵重材质或额外剧情表演。'
    '原批准事实明确要求的身份、衣装、装备、形状材质或画风仍须严格检查；缺失重尺、画成短棍/短剑、错人物或媒介不符仍须阻断。'
    '逐项返回confirmed/refuted/uncertain、basis_status、fact_citations和F0实际观察。'
    'confirmed必须引用approved_facts里真正存在的事实ID及其逐字片段；不能捏造批准要求。'
    'refuted若因为问题引入未批准要求，用unsupported_requirement并解释实际提供的事实和F0；若原要求已在像素满足，用supported_requirement并逐字引用。'
    '未提供事实不能证明新要求成立；看不清须uncertain，不能以无参照为由自动通过。'
    'review是重新完整审核，不仅删除旧问题：要检查真实可见错误，分数与指定画风按原门槛独立判断，不为消除旧意见抬分。'
    'findings与review必须一致，confirmed错误仍保留阻断，refuted问题不得在review中换词重述；不能静默清除未核验的问题。'
)


def _approved_facts(context):
    """Only approved reference requirements, never compiled generation candidates."""
    shot = context.get('shot', {})
    requirements = shot.get('approved_reference_requirements')
    if requirements:
        sources = {key: requirements[key] for key in ('entity', 'related_known_props', 'visual_rules', 'purpose', 'production_format', 'creative_contract', 'entities', 'visible_character_count')
                   if key in requirements}
    else:
        # Legacy reference targets still carry their original approved prompt.
        sources = {key: shot[key] for key in ('prompt', 'continuity') if shot.get(key)}
    if context.get('requested_art'):
        sources['requested_art'] = context['requested_art']
    if context.get('approved_visual_style'):
        sources['approved_visual_style'] = context['approved_visual_style']
    if context.get('resolved_visual_style') is not None:
        sources['resolved_visual_style'] = context['resolved_visual_style']
    facts = []

    def visit(value, field):
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, field + '.' + key if field else key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f'{field}[{index}]')
        elif isinstance(value, (str, int, float)) and str(value).strip():
            facts.append({'fact_id': f'A{len(facts)+1:03d}', 'field': field, 'text': str(value)})

    visit(sources, '')
    return facts


def verify_reference(config, schema, context, references, initial, path, record):
    """Re-review once; inconclusive evidence cannot approve or trigger new images."""
    from .llm import _request, _image_url, _messages, _system_rules
    from .config import resolve_llm
    from .providers import context_budget

    if context.get('shot', {}).get('review_purpose') not in PURPOSES:
        return initial
    issues = initial.get('issues', [])
    minimum = config.get('quality', {}).get('minimum_score', 60)
    if not issues and initial.get('score', 0) >= minimum and initial.get('status') != 'fail':
        return initial
    source_sha = sha256(path) if Path(path).is_file() else None
    basis = fingerprint({'policy': POLICY, 'source_sha256': source_sha, 'context': context,
                         'initial_review': initial, 'schema': schema, 'config_sha256': fingerprint(config)})
    invocation = uuid.uuid4().hex
    relative_folder = Path('reference_evidence') / basis[:24] / invocation
    folder = Path(record) / relative_folder
    audit = {'policy': POLICY, 'initial_review': copy.deepcopy(initial), 'status': 'unavailable',
        'verification_id': basis[:24] + '-' + invocation, 'verification_basis_sha256': basis,
        'verification_record': str(relative_folder / 'verification.json')}

    def pending(reason):
        result = copy.deepcopy(initial)
        result['_reference_unavailable'] = True
        result['reference_verification'] = {**audit, 'reason': reason}
        write_json(folder / 'verification.json', result['reference_verification'])
        return result

    if len(issues) > 20:
        return pending('reference_review_issue_limit_exceeded')
    if not Path(path).is_file():
        return pending('reference_review_source_missing')
    try:
        facts = _approved_facts(context)
        if not facts:
            return pending('reference_review_approved_facts_missing')
        audit.update(source_sha256=source_sha, approved_facts_sha256=fingerprint(facts))
        fact_map = {item['fact_id']: item for item in facts}
        suspects = [{'issue_id': f'I{i+1}', 'claim': copy.deepcopy(issue)} for i, issue in enumerate(issues)]
        citation = {'type': 'object', 'additionalProperties': False, 'properties': {
            'fact_id': {'type': 'string', 'enum': list(fact_map)},
            'quote': {'type': 'string', 'minLength': 2, 'maxLength': 1200}},
            'required': ['fact_id', 'quote']}
        finding = {'type': 'object', 'additionalProperties': False, 'properties': {
            'issue_id': {'type': 'string', **({'enum': [item['issue_id'] for item in suspects]} if suspects else {})},
            'status': {'type': 'string', 'enum': ['confirmed', 'refuted', 'uncertain']},
            'basis_status': {'type': 'string', 'enum': ['supported_requirement', 'unsupported_requirement', 'insufficient_evidence']},
            'fact_citations': {'type': 'array', 'minItems': 0, 'maxItems': 8, 'items': citation},
            'evidence_ids': {'type': 'array', 'minItems': 1, 'maxItems': 1, 'uniqueItems': True,
                             'items': {'type': 'string', 'const': 'F0'}},
            'detail': {'type': 'string', 'minLength': 12, 'maxLength': 1400}},
            'required': ['issue_id', 'status', 'basis_status', 'fact_citations', 'evidence_ids', 'detail']}
        followup = {'type': 'object', 'additionalProperties': False, 'properties': {
            'review': schema,
            'findings': {'type': 'array', 'minItems': len(suspects), 'maxItems': len(suspects), 'items': finding}},
            'required': ['review', 'findings']}
        payload = {**context, 'approved_facts': facts, 'candidate_reference_claims_untrusted': suspects,
            'candidate_source_sha256': audit['source_sha256'],
            'evidence_scope': 'F0 is the exact existing candidate; reference images are only those actually supplied. No regeneration or external canon.'}
        content = [{'type': 'text', 'text': json.dumps(payload, ensure_ascii=False)}, *references,
                   {'type': 'text', 'text': 'F0：原候选参考图，同一文件原始像素，未经重绘'},
                   {'type': 'image_url', 'image_url': {'url': _image_url(path)}}]
        profile = resolve_llm(config, 'visual_critic', require_vision=True)
        tokens = 4000 if len(issues) <= 6 else 6000
        if type(profile.get('context_tokens')) is int and not context_budget(
                profile, _messages(profile, _system_rules(profile, SYSTEM, followup), content), tokens)['fits']:
            return pending('reference_review_context_too_small')
        answer = _request(config, 'visual_critic', SYSTEM, None, followup, folder,
                          max_tokens=tokens, temperature=.1, content=content)
        validate(answer, followup)
        audit['followup_response'] = copy.deepcopy(answer)
        if sha256(path) != audit['source_sha256']:
            return pending('reference_review_source_changed')
        findings = {item['issue_id']: item for item in answer['findings']}
        if len(findings) != len(answer['findings']) or set(findings) != {item['issue_id'] for item in suspects}:
            return pending('reference_review_issue_coverage_invalid')
        for finding in findings.values():
            status, basis = finding['status'], finding['basis_status']
            citations = finding['fact_citations']
            if any(item['quote'] not in fact_map[item['fact_id']]['text'] for item in citations):
                return pending('reference_review_fact_quote_invalid')
            if status == 'confirmed' and (basis != 'supported_requirement' or not citations):
                return pending('reference_review_confirmation_has_no_approved_basis')
            if status == 'refuted' and (basis == 'insufficient_evidence'
                    or (basis == 'supported_requirement' and not citations)):
                return pending('reference_review_refutation_has_no_approved_basis')
        result = copy.deepcopy(answer['review'])
        unresolved = any(item['status'] == 'uncertain' for item in findings.values())
        for index, issue in enumerate(issues, 1):
            disposition = findings[f'I{index}']['status']
            if disposition == 'refuted' and any(
                    item.get('code') == issue.get('code') and item.get('detail') == issue.get('detail')
                    for item in result['issues']):
                return pending('reference_review_verdict_conflicts_with_findings')
            if disposition in {'confirmed', 'uncertain'} and issue not in result['issues']:
                result['issues'].append(copy.deepcopy(issue))
        audit.update(status='uncertain' if unresolved else 'verified', findings=copy.deepcopy(answer['findings']),
                     review=copy.deepcopy(answer['review']))
        result['reference_verification'] = audit
        result['_reference_unavailable'] = unresolved
        write_json(folder / 'verification.json', audit)
        return result
    except Exception as exc:
        return pending('reference_review_unavailable:' + type(exc).__name__)
