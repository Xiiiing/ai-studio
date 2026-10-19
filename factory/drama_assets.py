"""Version-pinned shared references, generated and visually reviewed by the existing engine."""
from __future__ import annotations

import copy
import re
import shutil
from studio.core import ROOT, within, sha256, read_json, write_json, fingerprint
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


REFERENCE_PURPOSES = {
    'character': 'Single adult character identity reference, neutral background; show required wardrobe and carried equipment together in one visible static instant. Choose framing that keeps required equipment visible. No lettering, action sequence or extra people.',
    'scene': 'Environment reference without people; show the approved spatial layout in one static instant. No lettering or invented story action.',
    'prop': 'Single prop reference on neutral background; show the approved object shape, material and distinctive visible details in one static instant. No people, lettering or action sequence.',
}


REFERENCE_COMPILER_SYSTEM = (
    '将完整批准实体资料编译为图像适配器可直接使用的静态参考图提示词，输出prompt。'
    '优先使用适合FLUX等图像模型的清晰English视觉描述，中文原名须保留以固定实体对应。'
    '实体原名仅作身份对应资料，不得写在画面或要求角色姓名、道具名标字；角色、道具和环境均不生成可读文字或标记。'
    '场景有牌匾、旗帜、碑面等表面时明确编译为blank/unlettered surfaces，保留批准的金色装饰和建筑结构。'
    '不要使用Chinese calligraphy或ornate lettering等诱导文字的正向措辞；装饰应为不含文字的图案，不把原名变成招牌或题字。'
    'production_format是本次选定制作媒介；animation必须明确绘制或CG动画参考，不能生成真人实拍照片，但animation本身不强制二维或三维。'
    '已有creative_contract和visual_rules明确二维、三维或其他画风时须遵循；其他production_format只落实已有媒介合约，不擅自改写。'
    '只描绘purpose指定的单一可见瞬间；角色参考展示本人、默认衣装、明确随身装备，取景须容纳必需装备，不能默认中景裁掉它。'
    '环境只描绘批准布局，道具只描绘批准外观；不可增加人物、剧情、动作终态或更改画风。'
    'entity.name、identity_constraints、default_wardrobe、related_known_props和visual_rules均为事实来源。'
    '衣装须落实原要求可见的版型：劲装应编译为合体利落的fitted combat attire，不得泛化为宽松练功服或道服；原要求为长裙或宽袍时仍保留原版型。'
    '可将重尺等名词解释成准确可见形状，不能替换为权杖、短剑等不同物品；不得捏造未给定的身份关系或道具归属。'
    '不要把能力、性格写成必须在静帧完成的动作。不输出权限、审核结论或生成操作。'
    '完整保留可见必需事实；输入很长也不能静默丢弃身份、衣装、道具或媒介要求。模型提示词只是待视觉审核的候选。'
)


def _reference_requirements(project, kind, entity):
    """Keep approved facts complete; associations need an explicit source mention."""
    related = []
    if kind == 'character':
        description = entity['name'] + ' ' + entity['identity_constraints'] + ' ' + entity['default_wardrobe']
        for prop in project['plan']['props']:
            named_equipment = prop['name'] in description
            owner_mention = (len(entity['name']) >= 2 and re.search(
                re.escape(entity['name']) + r'(?:的)?(?:随身携带|随身佩戴|随身装备|所携带|所持有|佩戴|持有)', prop['description']))
            if named_equipment or owner_mention:
                related.append(copy.deepcopy(prop))
    return {'plan_version': project['plan_version'], 'kind': kind, 'entity': copy.deepcopy(entity),
        'production_format': project.get('format'), 'creative_contract': copy.deepcopy(project.get('creative_contract')),
        'related_known_props': related, 'visual_rules': project['plan']['visual_rules'],
        'purpose': REFERENCE_PURPOSES[kind],
        'source_rule': 'Approved entity facts only. Related prop descriptions clarify explicitly mentioned equipment; do not invent ownership, relationships, plot or additional people.'}


def _compile_reference(project, config, requirements, image_profile):
    from .config import llm_identity
    from .media_profiles import identity
    from .drama_planning import call, obj, txt
    adapter = identity(image_profile)
    source = {'requirements': requirements, 'image_adapter': adapter,
        'compiler_system_sha256': fingerprint(REFERENCE_COMPILER_SYSTEM),
        'compiler_model': llm_identity(config, 'prompt_style_compiler'),
        'prompt_snapshot_sha256': fingerprint(project.get('prompt_snapshot'))}
    signature = fingerprint({'reference_compiler_version': 1, **source})
    compiled = call(project, config, 'prompt_style_compiler',
        'shared-prompt-' + requirements['kind'] + '-' + signature[:24],
        REFERENCE_COMPILER_SYSTEM,
        source, obj({'prompt': txt(1, 4000)}), 3500)
    document = {'schema_version': 1, 'signature': signature, 'plan_version': project['plan_version'], **copy.deepcopy(source),
        'requirements_sha256': fingerprint(requirements), 'compiled_prompt': compiled['prompt'],
        'compiled_prompt_sha256': fingerprint(compiled['prompt'])}
    folder = ROOT / 'production' / project['slug'] / 'planning' / 'shared_reference_prompts'
    path = folder / (signature + '.json')
    if path.is_file() and read_json(path) != document:
        raise ProductionNeedsDecision('参考图编译结果与固定版本不匹配，已保留旧结果', 'cache_integrity')
    write_json(path, document)
    return compiled['prompt'], {'path': str(path.relative_to(ROOT)), 'sha256': sha256(path),
        'signature': signature, 'plan_version': project['plan_version'],
        'requirements_sha256': document['requirements_sha256'],
        'compiler_system_sha256': source['compiler_system_sha256'],
        'compiled_prompt_sha256': document['compiled_prompt_sha256'], 'image_adapter': adapter}


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
    needed=[('character',key,c) for key,c in characters.items()]
    unit = p.get('record_kind') == 'scene_unit'
    needed += [('scene',v['entity_id'],v) for v in p['plan']['scenes']
        if not unit or v['entity_id']==p.get('location_id')]
    prop_ids = set(p.get('prop_ids', []))
    for character in characters.values():
        prop_ids.update(v['entity_id'] for v in _reference_requirements(p, 'character', character)['related_known_props'])
    needed += [('prop',v['entity_id'],v) for v in p['plan']['props']
        if not unit or v['entity_id'] in prop_ids]
    # No generation occurs until the pinned profile, owner and visual reviewer exist.
    prototype=copy.deepcopy(p)
    prototype['episode']={'cast':{key:c['identity_constraints'] for key,c in characters.items()},'shots':[]}
    prototype['styles']=[{'id':'drama_style','name':'全剧定稿画风','prompt':p['plan']['visual_rules']}]
    prototype['selected_style']='drama_style'
    engine=None
    render_config=None
    for kind,key,entity in needed:
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
            render_config=production_config(prototype,config)
            engine=_Run(prototype,render_config,lambda patch:store.update(pid,patch),control,
                evaluator=lambda proj,shot,path,refs:review_media(proj,shot,path,refs,config))
        requirements = _reference_requirements(p, kind, entity)
        prompt, compilation = _compile_reference(p, config, requirements,
            render_config.get('image_connection', {'provider': 'legacy', 'model': 'FLUX.2-klein-4B'}))
        approved_facts = store.encoded(requirements)
        target=engine._target('shared_'+kind+'_'+key,
            ROOT/'production'/p['slug']/'assets/shared_references'/('v'+version)/kind/key,prompt,
            review=True,review_context={'id':'shared_'+key,'prompt':approved_facts,'review_purpose':kind+'_reference',
                'approved_reference_requirements':requirements, 'reference_compilation':compilation,
                'continuity':store.encoded(entity),'characters':[key] if kind=='character' else []})
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
                       'reviewed_by':'visual_critic','selection':'automatic_reviewed_reference',
                       'prompt_compilation':compilation}
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
