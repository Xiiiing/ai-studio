"""Version-pinned shared references, generated and visually reviewed by the existing engine."""
from __future__ import annotations

import copy
import shutil
from studio.core import ROOT, within, sha256, read_json, write_json
from . import store
from .scheduler import ProductionNeedsDecision


def valid(receipt):
    if (receipt.get('qa',{}).get('semantic',{}).get('status') != 'pass'
            or receipt.get('qa',{}).get('technical_passed') is not True):
        raise ProductionNeedsDecision('全剧共享素材缺少通过的视觉审核', 'drama_reference_review_required')
    path=within(ROOT,receipt['path'])
    if not path.is_file() or sha256(path)!=receipt['sha256']:
        raise ProductionNeedsDecision('全剧共享素材与固定回执不匹配','cache_integrity')
    return path


def prepare(pid,config,control=None):
    from .production import _Run
    from .config import production_config
    from .llm import review_media
    p=store.get(pid,False)
    root=store.get(p['drama_id'],False)
    from .dramas import owned_folder
    owned_folder(root,ROOT)
    version=str(p['plan_version'])
    shared=copy.deepcopy(root.get('shared_assets',{}).get(version,{}))
    characters={c['character_id']:c for c in p['plan']['characters'] if c['character_id'] in p['outline']['characters']}
    needed=[('character',key,c['identity_constraints']+' '+c['default_wardrobe']) for key,c in characters.items()]
    needed += [('scene',v['entity_id'],v['description']) for v in p['plan']['scenes'] if v['entity_id']==p.get('location_id')]
    needed += [('prop',v['entity_id'],v['description']) for v in p['plan']['props'] if v['entity_id'] in p.get('prop_ids',[])]
    # No generation occurs until the pinned profile, owner and visual reviewer exist.
    prototype=copy.deepcopy(p)
    prototype['episode']={'cast':{key:c['identity_constraints'] for key,c in characters.items()},'shots':[]}
    prototype['styles']=[{'id':'drama_style','name':'全剧定稿画风','prompt':p['plan']['visual_rules']}]
    prototype['selected_style']='drama_style'
    engine=None
    for kind,key,description in needed:
        ident=kind+':'+key
        library_folder=ROOT/'production'/root['slug']/'shared'/('v'+version)/kind/key
        library_record=library_folder/'reference.json'
        if ident not in shared and library_record.exists():
            recovered=read_json(library_record)
            if recovered.get('kind')!=kind or recovered.get('entity_id')!=key or recovered.get('plan_version')!=p['plan_version']:
                raise ProductionNeedsDecision('共享参考图恢复版本不匹配','cache_integrity')
            valid(recovered['receipt'])
            shared[ident]=recovered
            with store.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                current=store._read(db,root['id'])
                current.setdefault('shared_assets',{}).setdefault(version,{})[ident]=recovered
                store._write(db,current)
        if ident in shared:
            valid(shared[ident]['receipt'])
            continue
        if engine is None:
            engine=_Run(prototype,production_config(prototype,config),lambda patch:store.update(pid,patch),control,
                evaluator=lambda proj,shot,path,refs:review_media(proj,shot,path,refs,config))
        prompt=(p['plan']['visual_rules']+'\n'+description+'\n'+
            {'character':'Single adult character identity reference, neutral background, medium shot, no lettering.',
             'scene':'Environment reference, no people, establish stable spatial layout, no lettering.',
             'prop':'Single prop reference, neutral background, preserve shape and material, no lettering.'}[kind])
        if len(prompt)>4500:
            from .drama_planning import call, obj, txt
            compiled=call(p,config,'prompt_style_compiler','shared-prompt-'+kind+'-'+key,
                '将完整参考图要求编译为不超过4000字的图像提示词，保持身份、默认服装、媒介画风和必要结构，不添加剧情。',
                {'requirements':prompt},obj({'prompt':txt(1,4000)}),3000)
            prompt=compiled['prompt']
        target=engine._target('shared_'+kind+'_'+key,
            ROOT/'production'/p['slug']/'assets/shared_references'/('v'+version)/kind/key,prompt,
            review=True,review_context={'id':'shared_'+key,'prompt':prompt,'review_purpose':kind+'_reference',
                'continuity':description,'characters':[key] if kind=='character' else []})
        engine._render_targets([target],'shared_references')
        receipt=copy.deepcopy(target['selected'])
        source=valid(receipt)
        library_folder.mkdir(parents=True,exist_ok=True)
        copied=library_folder/'image.png'
        if copied.exists() and sha256(copied)!=receipt['sha256']:
            raise ProductionNeedsDecision('共享参考图版本不能覆盖','cache_integrity')
        if not copied.exists():
            temporary=copied.with_suffix('.partial.png')
            shutil.copyfile(source,temporary)
            temporary.replace(copied)
        if sha256(copied)!=receipt['sha256']: raise ProductionNeedsDecision('共享参考图复制校验失败','cache_integrity')
        receipt={**receipt,'path':str(copied.relative_to(ROOT)),'origin_receipt':copy.deepcopy(receipt)}
        shared[ident]={'kind':kind,'entity_id':key,'plan_version':p['plan_version'],'receipt':receipt,
                       'reviewed_by':'visual_critic','selection':'automatic_reviewed_reference'}
        write_json(library_record,shared[ident])
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current=store._read(db,root['id'])
            current.setdefault('shared_assets',{}).setdefault(version,{})[ident]=shared[ident]
            store._write(db,current)
    selected={k:shared[k] for kind,key,_ in needed for k in [kind+':'+key]}
    store.update(pid,{'drama_references':selected})


def shot_inputs(engine,shot,references):
    refs=engine.project.get('drama_references',{})
    extras=[valid(value['receipt']) for value in refs.values() if value['kind']!='character']
    paths=list(references)+extras
    # Keep all inputs by composing in bounded stages, rather than dropping identities.
    index=0
    while len(paths)>4:
        index+=1
        prompt=engine.style_prompt+'\nCreate a coherent reference composition preserving every supplied identity, scene layout and prop. No text.'
        target=engine._target('composition_'+shot['id']+'_'+str(index),
            engine.folder/'assets/compositions'/shot['id']/str(index),prompt,paths[:4],
            review=True,review_context={'id':shot['id'],'prompt':prompt,'review_purpose':'composition_reference','characters':shot.get('characters',[])})
        engine._render_targets([target],'composition_references')
        paths=[engine.receipt_path(target['selected'])]+paths[4:]
    roles=set(engine._shot_render_roles(shot))
    provenance=[{'kind':v['kind'],'entity_id':v['entity_id'],'version':v['plan_version'],
                 'path':v['receipt']['path'],'sha256':v['receipt']['sha256']} for v in refs.values()
                 if v['kind']!='character' or v['entity_id'] in roles]
    return paths,' Preserve the pinned whole-drama reference identities, scene geometry and props. ',provenance
