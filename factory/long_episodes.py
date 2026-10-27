"""Scene planning and bounded units; the existing worker/graph remains authoritative."""
from __future__ import annotations

import copy
import math

from . import store, dramas
from .drama_planning import call, obj, txt, arr
from .scheduler import ProductionPaused
from .drama_intent import enabled as whole_drama


def unit_durations(seconds):
    dramas.integer(seconds, '场景时长', 15, 900)
    count = math.ceil(seconds / 90)
    base, remainder = divmod(seconds, count)
    return [base + (i < remainder) for i in range(count)]


def scene_schema(characters, seconds=None):
    return obj({'scenes': arr(obj({'scene_id': {'type':'string','pattern':'^C[0-9]{2,3}$'},
        'title': txt(1,100), 'synopsis':txt(10,3000), 'seconds':{'type':'integer','minimum':15,'maximum':min(900, seconds) if seconds is not None else 900},
        'characters':{'type':'array','items':{'type':'string','enum':characters},'minItems':1,'maxItems':4,'uniqueItems':True},
        'location_id':txt(0,64), 'prop_ids':arr(txt(1,64),0,16),
        'opening_state':txt(1,1200),'ending_state':txt(1,1200)}),1,min(60, seconds // 15) if seconds is not None else 60)})


def plan_units(episode, config):
    if episode['unit_ids']: return
    from .workflows import ReviewBlocked
    from studio.core import fingerprint
    feedback, technical_feedback, previous_candidate = [], [], None
    plan = episode['plan']
    ids = episode['outline']['characters']
    seconds = episode['duration_seconds']
    generation = episode.get('scene_generation', 1)
    schema = scene_schema(ids, seconds)
    from .story_coverage import enabled as quality_contract
    if whole_drama(episode) or quality_contract(episode):
        scene = schema['properties']['scenes']['items']
        scene['properties']['beats'] = arr(obj({'id': {'type': 'string', 'enum': [b['id'] for b in episode['outline']['beats']]}, 'description': txt(4, 800)}), 0, 8)
        scene['required'].append('beats')
    duration_contract = {'scope': 'single_episode', 'episode_number': episode['episode_number'],
        'series_episode_count': len(plan.get('episodes', [])), 'target_seconds': seconds,
        'minimum_scene_seconds': 15, 'maximum_scene_count': min(60, seconds // 15),
        'policy': '固定本集总时长；返修须在片长内简化或重排，不能扩大授权时长'}
    shared = {'outline': episode['outline'], 'seconds': seconds, 'duration_contract': duration_contract,
        'world': plan['world'], 'storyline': plan['storyline'], 'visual_rules': plan.get('visual_rules', ''),
        'relationships': plan.get('relationships', []),
        'characters': [c for c in plan['characters'] if c['character_id'] in ids],
        'known_locations': plan['scenes'], 'props': plan['props'],
        'producer_feedback': episode.get('scene_feedback'), 'source_excerpt': episode.get('source_excerpt', ''),
        'dependency_context': episode.get('dependency_context', []),
        'required_states': {'opening': episode['outline']['opening_state'], 'ending': episode['outline']['ending_state']},
        'downstream_context': [{'number': item['number'], 'opening_state': item['opening_state']}
            for item in plan.get('episodes', []) if episode['episode_number'] in item.get('depends_on', [])]}
    # Scope checkpoints to actual planning inputs, preserving earlier generations.
    basis = fingerprint({'contract_version': 2, 'inputs': shared, 'schema': schema,
        'config': config, 'prompt_snapshot': episode.get('prompt_snapshot'), 'agent_loop': episode.get('agent_loop')})

    def remember(entry):
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current = store._read(db, episode['id'])
            if current.get('scene_generation', 1) != generation:
                raise ProductionPaused('场景规划版本已改变，保留旧调用记录')
            history = current.get('scene_plan_history', [])
            matches = lambda item: (item.get('generation'), item.get('attempt'), item.get('basis_sha256')) == (generation, entry['attempt'], basis)
            history = [copy.deepcopy(entry) if matches(item) else item for item in history]
            if not any(matches(item) for item in history):
                history.append(copy.deepcopy(entry))
            current['scene_plan_history'] = history
            store._write(db, current)

    for attempt in range(1, 4):
        check_parent(episode['id'])
        history = store.get(episode['id'], False).get('scene_plan_history', [])
        saved = next((item for item in history if (item.get('generation'), item.get('attempt'), item.get('basis_sha256')) == (generation, attempt, basis)), None)
        if saved and saved['status'] in {'technical_rejected', 'returned'}:
            previous_candidate = saved['candidate']
            feedback = saved['issues']
            technical_feedback = saved['technical_issues']
            continue
        if saved:
            result = saved['candidate']
        else:
            result = call(episode, config, 'screenwriter', f'episode-scenes-{generation}-{attempt}',
                '将已确认的单集大纲展开成顺序场景，写明每场动作因果、开场与结尾状态。每场聚焦最多4位角色；'
                '更多人物用相邻场景切换焦点，不改变身份。duration_contract是单集硬约束，所有seconds之和必须精确等于target_seconds，每场至少15秒。'
                'location_id、prop_ids只引用给出的全剧场景和道具；没有则留空。保持全剧画风。'
                'previous_candidate是待定向返修的上一稿，feedback和technical_feedback是检查依据。保留正确场景和已确认开结尾，'
                '按具体问题局部修复；不得通过增加总时长落实审核建议。空间不足时在片长内简化动作或重排，不能添加未要求的对白或剧情。',
                {**shared, 'scenes': plan['scenes'], 'previous_candidate': previous_candidate,
                 'feedback': feedback, 'technical_feedback': technical_feedback}, schema, 11000)
        prior_feedback = copy.deepcopy(feedback)
        scenes = result['scenes']
        if quality_contract(episode):
            origins = {b['id']: b for b in episode['outline'].get('beats', [])}
            for scene in scenes:
                for beat in scene.get('beats', []):
                    approved = origins.get(beat['id'])
                    if approved:
                        beat['description'] = approved['description']
                        beat['source_refs'] = copy.deepcopy(approved.get('source_refs', []))
        actual = sum(s['seconds'] for s in scenes)
        technical_feedback = []
        if whole_drama(episode) or quality_contract(episode):
            assigned = [b['id'] for scene in scenes for b in scene.get('beats', [])]
            expected = [b['id'] for b in episode['outline']['beats']]
            if sorted(assigned) != sorted(expected):
                technical_feedback.append({'code': 'beat_allocation', 'message': '各场景须完整且不重复地分配本集必要事件'})
        if actual != seconds:
            technical_feedback.append({'code': 'duration_total', 'expected_seconds': seconds,
                'actual_seconds': actual, 'difference_seconds': actual - seconds,
                'message': f'场景秒数总和{actual}秒不等于本集目标{seconds}秒，差额{actual - seconds:+d}秒；须在固定片长内修复'})
        if len({s['scene_id'] for s in scenes}) != len(scenes):
            technical_feedback.append({'code': 'scene_ids', 'message': '场景ID重复'})
        if any(s['location_id'] and s['location_id'] not in {v['entity_id'] for v in plan['scenes']} for s in scenes):
            technical_feedback.append({'code': 'location_reference', 'message': '场景引用了未定义地点'})
        if any(not set(s['prop_ids']) <= {v['entity_id'] for v in plan['props']} for s in scenes):
            technical_feedback.append({'code': 'prop_reference', 'message': '场景引用了未定义道具'})
        feedback = [issue['message'] for issue in technical_feedback]
        entry = saved or {'generation': generation, 'attempt': attempt, 'basis_sha256': basis,
            'candidate': copy.deepcopy(result), 'candidate_sha256': fingerprint(result), 'created_at': store.now()}
        entry.update(technical_issues=copy.deepcopy(technical_feedback), issues=feedback,
            prior_feedback=entry.get('prior_feedback', prior_feedback),
            duration_contract={**duration_contract, 'actual_total_seconds': actual,
                               'scene_seconds': [s['seconds'] for s in scenes]})
        if feedback:
            entry.update(status='technical_rejected', review=None)
            remember(entry)
            previous_candidate = result
            continue
        if saved and saved['status'] == 'approved':
            review = saved['review']
        else:
            entry['status'] = 'reviewing'
            remember(entry)
            review = call(episode, config, 'independent_critic', f'episode-scenes-review-{generation}-{attempt}',
                '独立审核当前版本的单集场景大纲：因果、跨场景人物与道具状态、已确认大纲及制作可行性。'
                'duration_contract是本集固定片长，不是全剧时长；scene_seconds和actual_total_seconds由系统计算。'
                '以本集已确认的开结尾为边界，不要求一集完成全剧主线；上下游承接须符合给定状态，不自行扩写本集范围。'
                '这是场景大纲，不是完整对白、逐镜剧本或实际媒体。仅依据输入中存在的动作、设定和状态审核，'
                '不得假设未提供的完整对白、额外停顿、表情表演或新剧情，再以其挤占时间判不通过。'
                '真实必要动作过密仍须指出具体文本证据，并建议在固定target_seconds内简化或重排；不得要求扩展授权时长。'
                '每个阻断issues必须标明scene_id、具体字段、逐字引文、违反的已确认要求或实际矛盾以及片长内可落实的修改。'
                '审美偏好、可选表演和仅可优化节奏不能成为阻断；不能为通过门槛而忽略实际因果缺口、身份道具冲突或不可执行动作。'
                '前次意见只作为返修背景，须重新核对本稿证据，不得复述已修复的问题。passed=true时issues为空。',
                {**shared, 'scenes': scenes, 'duration_contract': entry['duration_contract'],
                 'previous_review_feedback': entry['prior_feedback'],
                 'review_scope': 'current_scene_outline_not_unprovided_dialogue_or_finished_media'},
                obj({'passed': {'type': 'boolean'}, 'issues': arr(txt(1, 1200), 0, 20)}), 3500)
            entry.update(review=copy.deepcopy(review), reviewed_at=store.now(),
                status='approved' if review['passed'] and not review['issues'] else 'returned',
                issues=review['issues'] or ([] if review['passed'] else ['场景审核未通过']))
            remember(entry)
        if not review['passed'] or review['issues']:
            feedback = entry['issues']
            previous_candidate = result
            continue
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            e=store._read(db,episode['id'])
            if e['unit_ids']: return
            if e.get('pause_requested') or e['status'] == 'cancelled': raise ProductionPaused()
            if e.get('scene_generation', 1) != generation:
                raise ProductionPaused('场景规划版本已改变，不能提交旧候选')
            sequence=0
            for scene in scenes:
                durations=unit_durations(scene['seconds'])
                for index, seconds in enumerate(durations,1):
                    sequence+=1
                    prefix=f"E{e['episode_number']:03d}-{scene['scene_id']}-U{index:03d}"
                    outline={'number':e['episode_number'],'title':scene['title'],
                        'synopsis':scene['synopsis'], 'characters':scene['characters'], 'depends_on':[],
                        'opening_state':scene['opening_state'],'ending_state':scene['ending_state'], **({'beats': scene['beats']} if 'beats' in scene else {})}
                    if whole_drama(episode) or quality_contract(episode):
                        beats = scene['beats']
                        outline['beats'] = beats[(index-1)*len(beats)//len(durations):index*len(beats)//len(durations)]
                    from .story_coverage import unit_source_excerpt
                    unit=dramas.record('scene_unit',prefix+' · '+scene['title'], scene['synopsis'],
                        drama_id=e['drama_id'],episode_id=e['id'],parent_id=e['id'],scene_id=scene['scene_id'],
                        unit_index=index,sequence=sequence,global_prefix=prefix, location_id=scene['location_id'],prop_ids=scene['prop_ids'],
                        duration_seconds=seconds,format=e['format'], plan_version=e['plan_version'],plan=plan,outline=outline,
                        source_excerpt=unit_source_excerpt(e, outline),
                        dependency_context=copy.deepcopy(e.get('dependency_context',[])),
                        series_key=e['series_key'], budget=copy.deepcopy(e['budget']),execution_config=copy.deepcopy(config),
                        unit_brief={'scene':scene,'unit_index':index,'unit_count':len(durations),'seconds':seconds,
                                    'allocation':'按顺序连续展开场景，勿在每个单元重复整个场景；最后单元才到达场景结尾'})
                    from .production_completion import inherit
                    inherit(e, unit)
                    from .creative_contracts import initialize
                    initialize(unit, parent=e)
                    dramas.insert(db,unit)
                    e['unit_ids'].append(unit['id'])
            e.update(scene_plan=scenes,scene_review=review,status='producing',stage='scene_units',progress=10,
                budget_scope='per_unit',total_authorized_gpu_minutes=e['production_spec']['gpu_minutes'] if whole_drama(e) else e['budget']['gpu_minutes']*sequence,
                total_authorized_wall_minutes=e['budget']['wall_minutes']*sequence)
            store._write(db,e)
        return
    raise ReviewBlocked('单集场景规划未通过三次有界检查',{'type':'episode_scenes','issues':feedback})


def check_parent(pid):
    e=store.get(pid,False)
    if e.get('pause_requested') or e['status']=='cancelled': raise ProductionPaused('已在单元边界暂停')


def revise_units(episode, config):
    from .workflows import ReviewBlocked
    units=[store.get(uid,False) for uid in episode['unit_ids']]
    scope=episode.get('revision_scope',{})
    selected=scope.get('unit_id')
    if selected and selected not in episode['unit_ids']: raise ValueError('返修单元不属于本集')
    feedback=episode['revision_feedback']
    if episode['revision_action']=='reedit' and not selected:
        schema=obj({'unit_order':{'type':'array','items':{'type':'string','enum':episode['unit_ids']},'minItems':len(units),'maxItems':len(units),'uniqueItems':True},
                    'supported':{'type':'boolean'},'reason':txt(1,1500)})
        result=call(episode,config,'edit_director',f"episode-reedit-{episode['production_revision']}",
            '根据用户意见重排已有制作单元。只能更改顺序，不增删镜头、不改对白、不承诺新动作。'
            '同场景的单元须保持连续。无法通过重排落实用户意见则supported=false并解释。',
            {'feedback':feedback,'units':[{'id':u['id'],'scene_id':u['scene_id'],'title':u['title'],'synopsis':u['brief'][:300]} for u in units]},schema,3000)
        order=result['unit_order']
        if not result['supported'] or order==episode.get('edit_unit_order',episode['unit_ids']):
            raise ReviewBlocked('重剪意见尚不能落实：'+result['reason'],{'type':'edit_feedback'})
        seen=set(); last=None
        for uid in order:
            scene=next(u['scene_id'] for u in units if u['id']==uid)
            if scene!=last and scene in seen: raise ReviewBlocked('同场景单元必须连续，不能错配场景和字幕')
            seen.add(scene);last=scene
        store.update(episode['id'],{'edit_unit_order':order,'revision_action':None,'production_revision':episode['production_revision']+1})
        return
    chosen=[selected] if selected else []
    if not chosen:
        result=call(episode,config,'edit_director',f"episode-revision-scope-{episode['production_revision']}",
            '将用户局部修改意见定位到明确制作单元。只选择受影响单元，保留其他单元。意见无法定位则supported=false。',
            {'feedback':feedback,'units':[{'id':u['id'],'prefix':u['global_prefix'],'title':u['title'],'brief':u['brief'][:300],
                'shot_ids':[s['id'] for s in (u.get('script') or {}).get('shots',[])]} for u in units]},
            obj({'supported':{'type':'boolean'},'reason':txt(1,1200),
                 'unit_ids':{'type':'array','items':{'type':'string','enum':episode['unit_ids']},'minItems':0,'maxItems':len(units),'uniqueItems':True}}),4000)
        if not result['supported'] or not result['unit_ids']: raise ReviewBlocked('返修范围未确定：'+result['reason'],{'type':'edit_feedback'})
        chosen=result['unit_ids']
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        current=store._read(db,episode['id'])
        for uid in chosen:
            unit=store._read(db,uid)
            if unit.get('status')=='cancelled': raise ValueError('已取消单元不能返修')
            notes={'notes':feedback,'shot_ids':scope['shot_ids']} if scope.get('shot_ids') else feedback
            unit.update(status='queued',pause_requested=False,error=None,needs_decision=None,
                revision_request={'choice':episode['revision_action'],'feedback':notes},
                production_revision=unit['production_revision']+1,delivery=None,
                workflow_restart_from='revision',workflow_restart_revision=unit['revision']+1)
            if episode['revision_action']=='revise': unit['script_generation']+=1
            if not unit.get('script'):
                unit.update(revision_request=None,workflow_restart_from='story_resolution',
                    story_feedback=unit.get('story_feedback','')+'\n出品人追加修改：'+feedback)
            store._write(db,unit)
        current.update(revision_action=None,production_revision=current['production_revision']+1,
            continuity_repair_cycle=current.get('continuity_repair_cycle',1)+1,continuity_repair=None)
        store._write(db,current)


def return_continuity_issue(episode, failure):
    """Route a failed boundary back to its owner, keeping completed work and caps."""
    details = failure.details
    if details.get('type') != 'episode_continuity': return False
    uid = details.get('unit_id')
    order = episode.get('edit_unit_order', episode['unit_ids'])
    if uid not in order or order.index(uid) == 0: return False
    previous_id = order[order.index(uid)-1]
    review = details.get('review') or {}
    if review.get('from_unit', previous_id) != previous_id or review.get('to_unit', uid) != uid: return False
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        current = store._read(db, episode['id'])
        if current.get('pause_requested') or current['status'] == 'cancelled':
            raise ProductionPaused('已在连续性返修边界暂停')
        history = current.get('continuity_repair_history', [])
        cycle = current.get('continuity_repair_cycle', 1)
        prior = [entry for entry in history if entry['cycle'] == cycle
            and entry['from_unit'] == previous_id and entry['unit_id'] == uid]
        from .production_completion import authorization
        maximum = None if authorization(current) else current.get('budget', {}).get('max_attempts', 3)
        round_index = len(prior)+1 if maximum is None else min(len(prior)+1, maximum)
        state = {'unit_id': uid, 'from_unit': previous_id, 'round': round_index,
            'max_rounds': maximum, 'status': 'repairing', 'issues': review.get('issues', []), 'cycle': cycle}
        if maximum is not None and round_index >= maximum:
            state['status'] = 'exhausted'
            if not prior or prior[-1].get('status') != 'exhausted':
                history = [*history, {**state, 'at': store.now(), 'review': copy.deepcopy(review), 'returned_to': []}]
            current.update(continuity_repair=state, transition_review=review,continuity_repair_history=history)
            store._write(db, current)
        else:
            unit = store._read(db, uid)
            if unit.get('episode_id') != episode['id']: raise ValueError('连续性返修单元不属于本集')
            if unit.get('delivery'):
                unit['delivery_history'] = [*unit.get('delivery_history', []), copy.deepcopy(unit['delivery'])]
            issues = review.get('issues') or [review.get('summary') or '交界帧人物与承接状态存在矛盾']
            notes = ('连续性审核退回，请根据前一单元已完成的结尾修复本单元开场，保留其他已通过内容。'
                + '\n审核意见：' + '\n'.join(str(v) for v in issues))
            shots = (unit.get('script') or {}).get('shots') or []
            previous = store._read(db, previous_id)
            notes += '\n前单元实际结尾：' + store.encoded({'ending':(previous.get('script') or {}).get('ending'),
                'last_shot':((previous.get('script') or {}).get('shots') or [{}])[-1].get('continuity')})
            feedback = {'notes': notes, 'shot_ids': [shots[0]['id']]} if shots else notes
            unit['previous_unit_state'] = {'unit': previous.get('global_prefix'),
                'ending': (previous.get('script') or {}).get('ending'),
                'last_shot_continuity': ((previous.get('script') or {}).get('shots') or [{}])[-1].get('continuity')}
            unit.update(status='queued', pause_requested=False, error=None, needs_decision=None,
                revision_request={'choice': 'revise', 'feedback': feedback},
                production_revision=unit['production_revision']+1, delivery=None,
                script_generation=unit['script_generation']+1,
                workflow_restart_from='revision', workflow_restart_revision=unit['revision']+1)
            if not unit.get('script'):
                unit.update(revision_request=None, workflow_restart_from='story_resolution',
                    story_feedback=unit.get('story_feedback', '')+'\n'+notes)
            unit['continuity_video_repair'] = {'shot_ids': [shots[0]['id']] if shots else [],
                'production_revision': unit['production_revision'], 'source': 'episode_continuity_review',
                'episode_id': current['id'], 'cycle': cycle, 'round': round_index}
            store._write(db, unit)
            history = [*history, {**state, 'at': store.now(), 'review': copy.deepcopy(review),
                'returned_to': ['screenwriter', 'storyboard_director']}]
            current.update(continuity_repair=state, continuity_repair_history=history,
                transition_review=review, stage='continuity_repair', status='producing',
                active_unit_id=uid, production_revision=current['production_revision']+1,
                error=None, needs_decision=None)
            store._write(db, current)
            store._event(db, current['id'], '连续性审核已退回对应单元自动返修', 'review_return',
                {'unit_id': uid, 'round': round_index, 'max_rounds': maximum})
    if state['status'] == 'exhausted':
        from .workflows import ReviewBlocked
        raise ReviewBlocked(f'跨单元连续性已审核{maximum}轮，自动返修额度用尽，原素材与问题已保留',
            {**details, 'automation': state})
    return True


def _reference_stage(episode_id, unit_id, *, finished=False):
    """Publish actual reference work without overwriting a gate or a later stage."""
    from .execution_context import current_job
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        episode = store._read(db, episode_id)
        unit = store._read(db, unit_id)
        job = current_job.get()
        if job:
            owner = db.execute('SELECT project_id,status,lease FROM jobs WHERE id=?', (job['id'],)).fetchone()
            if (not owner or owner['project_id'] != episode_id or owner['status'] != 'running'
                    or owner['lease'] != job.get('lease')):
                raise ProductionPaused('执行租约已失效，保留原任务和素材', 'stale_job')
        if (unit.get('episode_id') != episode_id or unit_id not in episode.get('unit_ids', [])):
            raise ValueError('制作单元不属于本集')
        if any(p.get('pause_requested') or p['status'] in {'paused', 'cancelled'} for p in (episode, unit)):
            raise ProductionPaused('已在共享参考素材边界暂停')
        if unit['status'] == 'unit_ready':
            return False
        preparing = unit.get('stage') in {'queued', 'shared_references'} and unit['status'] in {
            'queued', 'producing', 'waiting_remote'}
        stage = 'shared_references' if preparing and not finished else 'scene_units'
        if preparing and not finished:
            unit.update(status='producing', stage='shared_references')
            store._write(db, unit)
        episode.update(status='producing', stage=stage, active_unit_id=unit_id)
        store._write(db, episode)
        return True


def run(episode, config):
    from .quality_recheck import require_current, handle_final as handle_quality_final
    if (episode.get('delivery') or {}).get('quality_recheck_id') and not episode.get('revision_action'):
        require_current(episode)
    quality_handled = handle_quality_final(episode, long_episode=True)
    if quality_handled is not None: return quality_handled
    episode = store.get(episode['id'], False)
    from .drama_runtime import setup_episode
    from .workflows import advance, ReviewBlocked
    from studio.core import ROOT
    check_parent(episode['id'])
    dramas.owned_folder(episode,ROOT)
    plan_units(episode,config)
    episode=store.get(episode['id'],False)
    if episode.get('revision_action'): revise_units(episode,config)
    while True:
        episode=store.get(episode['id'],False)
        for i, uid in enumerate(episode['unit_ids']):
            check_parent(episode['id'])
            unit=store.get(uid,False)
            if unit['status']=='unit_ready':
                if whole_drama(unit):
                    from .drama_delivery import review_episode
                    review_episode(unit, config)
                continue
            if whole_drama(unit) and unit.get('dependency_context') != episode.get('dependency_context', []):
                unit = store.update(uid, {'dependency_context': copy.deepcopy(episode.get('dependency_context', []))})
            if unit.get('pause_requested') or unit['status'] in {'paused','cancelled'}:
                raise ProductionPaused('已在制作单元边界暂停')
            if unit['status'] in {'blocked','failed'}:
                store.update(uid,{'status':'queued','pause_requested':False,'error':None})
            if i and (not unit.get('production_profile') or whole_drama(unit)):
                previous=store.get(episode['unit_ids'][i-1],False)
                script=previous.get('script') or {}
                actual = {}
                if whole_drama(unit):
                    from .drama_delivery import adopted_end
                    actual = {'observed_end': adopted_end(previous)}
                store.update(uid,{'previous_unit_state':{**actual, 'unit':previous['global_prefix'],'ending':script.get('ending'),
                    'last_shot_continuity':(script.get('shots') or [{}])[-1].get('continuity')}})
            setup_episode(store.get(uid,False))
            from .drama_assets import prepare
            if not _reference_stage(episode['id'], uid): continue
            try:
                prepare(uid,config,lambda:bool(store.get(episode['id'],False).get('pause_requested')))
                if _reference_stage(episode['id'], uid, finished=True):
                    advance(uid)
            finally:
                from .execution_context import current_job
                from .remote_tasks import owns_job
                job = current_job.get()
                if not job or owns_job(job):
                    units=[store.get(value,False) for value in episode['unit_ids']]
                    store.update(episode['id'],{'production':{'gpu_seconds':sum(u.get('production',{}).get('gpu_seconds',0) for u in units)}})
            unit=store.get(uid,False)
            if unit['status']!='unit_ready':
                raise ReviewBlocked('制作单元尚未自动完成，已保留审核与交接记录',{'unit_id':uid,'status':unit['status']})
            if whole_drama(unit):
                from .drama_delivery import review_episode
                review_episode(unit, config)
            units=[store.get(value,False) for value in episode['unit_ids']]
            store.update(episode['id'],{'active_unit_id':uid,'progress':10+math.floor(75*(i+1)/len(units)),
                'production':{'gpu_seconds':sum(u.get('production',{}).get('gpu_seconds',0) for u in units)}})
        from .episode_assembly import assemble
        episode=store.get(episode['id'],False)
        order=episode.get('edit_unit_order',episode['unit_ids'])
        units=[store.get(uid,False) for uid in order]
        from .drama_continuity import review
        try:
            transition_review=review(episode,units,config,lambda:bool(store.get(episode['id'],False).get('pause_requested')))
        except ReviewBlocked as failure:
            if not return_continuity_issue(episode, failure): raise
            continue
        repair = episode.get('continuity_repair')
        if repair:
            store.update(episode['id'], {'continuity_repair': {**repair, 'status': 'passed',
                'round': repair['round']+1}})
        break
    store.update(episode['id'],{'transition_review':transition_review})
    episode['transition_review']=transition_review
    store.update(episode['id'],{'stage':'assembly','status':'producing'})
    delivery=assemble(episode,units,lambda:bool(store.get(episode['id'],False).get('pause_requested')))
    check_parent(episode['id'])
    history=episode.get('delivery_history',[])
    if episode.get('delivery') and episode['delivery'].get('source_signature')!=delivery['source_signature']:
        history=[*history,episode['delivery']]
    if whole_drama(episode):
        from .drama_delivery import review_episode
        current = store.update(episode['id'], {'delivery': delivery, 'status': 'producing'})
        review_episode(current, config)
    store.update(episode['id'],{'delivery':delivery,'delivery_history':history,'delivery_complete_revision':episode['production_revision'],
        'status':'awaiting_final','stage':'final_decision','progress':95,'error':None,'needs_decision':None})
