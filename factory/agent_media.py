"""A text role delegates real first-frame pixels to the configured visual role.

Full technical inspection and a first-frame observation are distinct evidence.
Neither can approve a movie or prove that an action completed over time.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import time

from jsonschema import validate
from studio.core import ROOT, media_receipt, sha256, write_json
from .creative_evidence_io import digest, read_json
from .generation_pipeline import FRAME_SCHEMA
from .scheduler import ProductionNeedsDecision


def inspect(loop,args):
    from . import llm
    from .quality import inspect_image,inspect_video
    from .config import resolve_llm
    from .team_planning import task_config
    from .prompt_workbench import _local_trial
    from .prompt_registry import load_prompt
    from .profile_creative import adapt_system
    from .review_contracts import starting_frame
    p=loop.project
    shots=p.get('shots') or (p.get('episode') or p.get('script') or {}).get('shots') or []
    shot=next((s for s in shots if s.get('id')==args['shot_id']),None)
    record=shot.get(args['kind']) if shot else None
    if not isinstance(record,dict) or not record.get('path') or not record.get('sha256'):
        raise ValueError('该镜头尚无已登记的实际媒体及字节哈希；文字不能作为像素证据')
    relative=Path(record['path'])
    project_root=Path(ROOT)/'production'/('factory-'+p['id'])
    target=Path(ROOT)/relative
    if relative.is_absolute() or '..' in relative.parts or not target.resolve().is_relative_to(project_root.resolve()) or any(x.is_symlink() for x in (target,*target.parents)):
        raise ValueError('只能观察当前项目已登记的实际媒体，不能读取任意路径')
    actual=media_receipt(target)
    if actual['sha256']!=record['sha256']:
        raise ProductionNeedsDecision('已登记媒体的实际字节已变化','agent_stale_media')
    directory=loop.directory/('inspection-'+digest({'shot':shot['id'],'kind':args['kind'],'sha256':actual['sha256']}))
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    result_path=directory/'result.json'
    def account(saved):
        if digest({'result':saved['result'],'charge':saved['charge']})!=saved['sha256']:
            raise ValueError('媒体观察记录校验失败')
        accounted=loop.row.setdefault('accounted_inspections',[])
        if directory.name not in accounted:
            charge=saved['charge']
            if type(charge) is not int or charge<0:raise ValueError('视觉用量记录无效')
            loop.row['spent_output_tokens']+=charge
            accounted.append(directory.name)
            loop.row['pending']=None
            loop.row.setdefault('observed_media',[]).append(actual)
            loop.event('visual_result',summary='实际像素观察与用量已保存，首帧不代表完整动作')
        return saved['result']
    if result_path.exists():
        return account(read_json(result_path))
    technical=inspect_video(target,shot.get('seconds')) if args['kind']=='video' else inspect_image(target)
    cfg=copy.deepcopy(task_config(loop.config,'visual_critic'))
    profile=resolve_llm(cfg,'visual_critic',require_vision=True)
    if not _local_trial(profile):raise ValueError('视觉补证暂限已配置内网视觉模型，未启动费用未知的请求')
    if not cfg.get('quality',{}).get('semantic_enabled',True):
        return {'state':'visual_unavailable','technical':technical,'reason':'视觉检查已停用；没有像素语义证据','quality_approval':False}
    frame=target
    if args['kind']=='video':
        import av
        frame=directory/'first-frame.png'
        if not frame.exists():
            with av.open(str(target)) as container:
                image=next(container.decode(video=0)).to_image();image.save(frame)
    original_sha=sha256(frame)
    parsed=list(directory.glob('calls/*/parsed.json'))
    pending=loop.row.get('pending')
    if parsed:
        observation=read_json(parsed[0]);validate(observation,FRAME_SCHEMA)
        receipts=list(directory.glob('calls/*/receipt.json'))
        receipt=read_json(receipts[0]) if receipts else {}
        usage=(receipt.get('attempts') or [{}])[-1].get('usage') or {}
        charge=usage.get('completion_tokens')
    else:
        if pending and pending.get('phase')=='tool_model':
            loop.stop('unknown','上次视觉补证请求结果未知，未重投模型')
        cap=min(2200,loop.settings['max_output_tokens'],loop.settings['total_output_tokens']-loop.row['spent_output_tokens'])
        remaining=loop.settings['max_wall_seconds']-(time.time()-loop.row['created_at'])
        if cap<128 or remaining<1:loop.stop('exhausted','视觉补证需要的输出或时限额度不足')
        selected=cfg.get('llm_profiles',{}).get(profile['profile']) if profile['profile']!='default' else cfg.get('llm')
        if selected is None:selected=cfg.setdefault('llm',{})
        selected.update(max_schema_attempts=1,request_timeout_seconds=max(1,min(profile.get('request_timeout_seconds',480),int(remaining))))
        loop.row['pending']={'phase':'tool_model','directory':directory.name,'cap':cap}
        loop.row['reserved_output_tokens']+=cap;loop.event('visual_delegate',summary='将实际像素交给视觉岗位，结果不作质量批准')
        context={'project':{'id':p['id']},'approved_shot':starting_frame(shot),'approved_action':shot.get('action'),
                 'reference_sha256':original_sha,'rule':'目标是当前真实首帧，不能以历史审核或参考图片冒充目标像素。只观察起始状态，不能证明视频完整动作。'}
        content=[{'type':'text','text':json.dumps(context,ensure_ascii=False)},
                 {'type':'image_url','image_url':{'url':llm._image_url(frame)}}]
        if sha256(frame)!=original_sha:raise ProductionNeedsDecision('观察前首帧变化','agent_stale_media')
        observation=llm._request(cfg,'visual_critic',adapt_system(load_prompt('visual_critic','1.0.0'),p),context,
                                 FRAME_SCHEMA,directory,max_tokens=cap,temperature=.1,content=content)
        receipts=list(directory.glob('calls/*/receipt.json'))
        usage=(read_json(receipts[0]).get('attempts') or [{}])[-1].get('usage') or {} if receipts else {}
        charge=usage.get('completion_tokens')
    if sha256(target)!=actual['sha256'] or sha256(frame)!=original_sha:
        raise ProductionNeedsDecision('视觉检查期间像素发生变化，旧结果不能用于提交','agent_stale_media')
    cap=(loop.row.get('pending') or {}).get('cap',2200)
    charge=charge if type(charge) is int and charge>=0 else cap
    result={'state':'observed','shot_id':shot['id'],'media_kind':args['kind'],'media':actual,
        'technical':technical,'visual_role':'visual_critic','visual_model':profile['model'],
        'frame_sha256':original_sha,'observation':observation,'scope':'first_frame_pixels_only',
        'motion_completion':'not_established','quality_approval':False}
    saved={'result':result,'charge':charge}
    write_json(result_path,{'sha256':digest(saved),**saved});result_path.chmod(0o600)
    return account(read_json(result_path))
