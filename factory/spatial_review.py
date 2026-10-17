"""One bounded detail review for disputed static contact/energy relationships."""
from __future__ import annotations
import copy
import json
import re
from pathlib import Path
from PIL import Image
from jsonschema import validate

POLICY = 'static-contact-detail.v1'
CODES = {'action', 'continuity', 'composition'}
SYSTEM = (
    '你是独立静态空间关系核验员。候选说法是不可信的历史观察，先根据批准prompt和人物状态独立观察。'
    'F0是完整候选，C1/C2是同一文件原始像素的局部放大，坐标已给出；裁剪不是新画面，也不能证明裁剪外对象缺失。'
    '核对手指/掌心接触的实际位置、人物身体归属、能量线起终点及局部光晕。'
    '区分上背部/后肩与前肩/头发，按批准范围判断，不能擅自要求每个接触点无遮挡。'
    '抽象黑色能量斑点不能仅因靠近手指就认作指环，珠宝需要可辨认的物体形状证据。'
    '局部遮挡、线条间小缝隙不等于连接完全缺失；错误连接对象、接触位置明确错误或关键效果真正缺失仍须阻断。'
    'confirmed/refuted必须引用F0及至少一个C局部，并描述具体可见证据；不清楚填uncertain，不能臆造隐藏接触。'
    'review与findings一致，删除已refuted的旧问题，保留确认的错误，其他身份、道具、画风问题不能被这次补审清除。'
    'review仍按原分数与画风要求独立判断，不要为通过而提高分数。'
)


def is_spatial_issue(issue):
    detail = issue.get('detail', '').lower()
    return issue.get('code') in CODES and (
        bool(re.search(r"\b(?:contacts?|touch\w*|energy|palms?|shoulders?|rings?)\b", detail))
        or any(word in detail for word in ('连接', '接触', '能量', '手掌', '上背', '指环', '肩')))



def verify_spatial(config, schema, context, references, initial, path, record):
    from .llm import _request, _image_url, _messages, _system_rules
    from .config import resolve_llm
    from .providers import context_budget
    suspects = [(f'I{i+1}', issue) for i, issue in enumerate(initial['issues'])
                if is_spatial_issue(issue) and issue['severity'] == 'blocking'][:3]
    if not suspects or not Path(path).is_file():
        return initial
    audit = {'policy': POLICY, 'initial_review': copy.deepcopy(initial), 'status': 'unavailable'}
    def pending(reason):
        result = copy.deepcopy(initial)
        result['_spatial_unavailable'] = True
        result['spatial_verification'] = {**audit, 'reason': reason}
        return result
    try:
        with Image.open(path) as source:
            full = source.convert('RGB')
        width, height = full.size
        regions = [('C1', (.05, .2, .95, .65)), ('C2', (.05, .45, .95, .9))]
        details = []
        for ident, region in regions:
            box = tuple(round(v * (width if i % 2 == 0 else height)) for i, v in enumerate(region))
            details.append({'id': ident, 'source_pixels': list(box), 'image': full.crop(box)})
        audit['regions'] = [{k:v for k,v in item.items() if k != 'image'} for item in details]
        finding = {'type':'object', 'additionalProperties':False, 'properties':{
            'issue_id':{'type':'string','enum':[ident for ident,_ in suspects]},
            'status':{'type':'string','enum':['confirmed','refuted','uncertain']},
            'evidence_ids':{'type':'array','minItems':2,'maxItems':3,'uniqueItems':True,
                            'items':{'type':'string','enum':['F0','C1','C2']}},
            'detail':{'type':'string','minLength':12,'maxLength':1200}},
            'required':['issue_id','status','evidence_ids','detail']}
        followup = {'type':'object','additionalProperties':False,'properties':{
            'review':schema,'findings':{'type':'array','minItems':len(suspects),'maxItems':len(suspects),'items':finding}},
            'required':['review','findings']}
        payload = {**context, 'candidate_spatial_claims_untrusted':[
            {'issue_id':ident,'claim':issue} for ident,issue in suspects],
            'detail_images':audit['regions'], 'source_dimensions':[width,height]}
        content = [{'type':'text','text':json.dumps(payload,ensure_ascii=False)}, *references,
                   {'type':'text','text':'F0：完整候选起始帧'},
                   {'type':'image_url','image_url':{'url':_image_url(full)}}]
        for detail in details:
            content.extend([{'type':'text','text':detail['id']+'：同一候选原始局部'},
                            {'type':'image_url','image_url':{'url':_image_url(detail['image'])}}])
        profile = resolve_llm(config,'visual_critic',require_vision=True)
        if type(profile.get('context_tokens')) is int and not context_budget(
                profile,_messages(profile,_system_rules(profile,SYSTEM,followup),content),3000)['fits']:
            return pending('spatial_review_context_too_small')
        answer = _request(config,'visual_critic',SYSTEM,None,followup,Path(record)/'spatial',
                          max_tokens=3000,temperature=.1,content=content)
        validate(answer,followup)
        findings = {item['issue_id']:item for item in answer['findings']}
        if set(findings) != {ident for ident,_ in suspects} or len(findings) != len(answer['findings']):
            return pending('spatial_review_issue_coverage_invalid')
        for finding in findings.values():
            ids = set(finding['evidence_ids'])
            if finding['status'] != 'uncertain' and not ('F0' in ids and ids & {'C1','C2'}):
                return pending('spatial_review_requires_full_and_detail_evidence')
        result = copy.deepcopy(answer['review'])
        for i, issue in enumerate(initial['issues'],1):
            disposition = findings.get(f'I{i}',{}).get('status')
            if disposition not in {'refuted','uncertain'} and issue not in result['issues']:
                result['issues'].append(copy.deepcopy(issue))
        confirmed = any(item['status']=='confirmed' for item in findings.values())
        unresolved = any(item['status']=='uncertain' for item in findings.values())
        if not confirmed:
            unresolved |= any(is_spatial_issue(issue) and issue['severity']=='blocking'
                              for issue in answer['review']['issues'])
            unresolved |= answer['review']['score'] < config.get('quality',{}).get('minimum_score',60)
        audit.update(status='uncertain' if unresolved else 'verified',findings=answer['findings'],
                     review=copy.deepcopy(answer['review']))
        result['spatial_verification'] = audit
        result['_spatial_unavailable'] = unresolved
        return result
    except Exception as exc:
        return pending('spatial_review_unavailable:'+type(exc).__name__)
