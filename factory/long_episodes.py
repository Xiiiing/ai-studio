"""Scene planning and bounded units; the existing worker/graph remains authoritative."""
from __future__ import annotations

import copy
import math

from . import store, dramas
from .drama_planning import call, obj, txt, arr
from .scheduler import ProductionPaused


def unit_durations(seconds):
    dramas.integer(seconds, '场景时长', 15, 900)
    count = math.ceil(seconds / 90)
    base, remainder = divmod(seconds, count)
    return [base + (i < remainder) for i in range(count)]


def scene_schema(characters):
    return obj({'scenes': arr(obj({'scene_id': {'type':'string','pattern':'^C[0-9]{2,3}$'},
        'title': txt(1,100), 'synopsis':txt(10,3000), 'seconds':{'type':'integer','minimum':15,'maximum':900},
        'characters':{'type':'array','items':{'type':'string','enum':characters},'minItems':1,'maxItems':4,'uniqueItems':True},
        'location_id':txt(0,64), 'prop_ids':arr(txt(1,64),0,16),
        'opening_state':txt(1,1200),'ending_state':txt(1,1200)}),1,60)})


def plan_units(episode, config):
    if episode['unit_ids']: return
    from .workflows import ReviewBlocked
    feedback = []
    plan = episode['plan']
    ids = episode['outline']['characters']
    schema = scene_schema(ids)
    for attempt in range(1,4):
        generation=episode.get('scene_generation',1)
        result = call(episode,config,'screenwriter',f'episode-scenes-{generation}-{attempt}',
            '将已确认的单集大纲展开成顺序场景，写明每场动作因果、开场与结尾状态。每场聚焦最多4位角色；'
            '更多人物用相邻场景切换焦点，不改变身份。所有seconds之和必须等于目标时长，每场至少15秒。'
            'location_id、prop_ids只引用给出的全剧场景和道具；没有则留空。保持全剧画风。',
            {'outline':episode['outline'],'seconds':episode['duration_seconds'],'world':plan['world'],
             'storyline':plan['storyline'], 'relationships':plan.get('relationships',[]),
             'characters':[c for c in plan['characters'] if c['character_id'] in ids],
             'scenes':plan['scenes'],'props':plan['props'],'feedback':feedback,'producer_feedback':episode.get('scene_feedback'),
             'source_excerpt':episode.get('source_excerpt',''),
             'dependency_context':episode.get('dependency_context',[])},schema,11000)
        scenes = result['scenes']
        feedback = []
        if sum(s['seconds'] for s in scenes) != episode['duration_seconds']: feedback.append('场景秒数总和不等于目标时长')
        if len({s['scene_id'] for s in scenes}) != len(scenes): feedback.append('场景ID重复')
        if any(s['location_id'] and s['location_id'] not in {v['entity_id'] for v in plan['scenes']} for s in scenes): feedback.append('场景引用了未定义地点')
        if any(not set(s['prop_ids']) <= {v['entity_id'] for v in plan['props']} for s in scenes): feedback.append('场景引用了未定义道具')
        if feedback: continue
        review = call(episode,config,'independent_critic',f'episode-scenes-review-{generation}-{attempt}',
            '独立审核单集场景的因果、跨场景人物状态、对白空间、与已确认大纲的一致性及目标时长可行性。'
            '只提出影响成片目标的实际问题；passed=true时issues为空。',
            {'outline':episode['outline'],'scenes':scenes,'characters':[c for c in plan['characters'] if c['character_id'] in ids]},
            obj({'passed':{'type':'boolean'},'issues':arr(txt(1,1200),0,20)}),3500)
        if not review['passed'] or review['issues']:
            feedback=review['issues'] or ['场景审核未通过']
            continue
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            e=store._read(db,episode['id'])
            if e['unit_ids']: return
            if e.get('pause_requested'): raise ProductionPaused()
            sequence=0
            for scene in scenes:
                durations=unit_durations(scene['seconds'])
                for index, seconds in enumerate(durations,1):
                    sequence+=1
                    prefix=f"E{e['episode_number']:03d}-{scene['scene_id']}-U{index:03d}"
                    outline={'number':e['episode_number'],'title':scene['title'],
                        'synopsis':scene['synopsis'], 'characters':scene['characters'], 'depends_on':[],
                        'opening_state':scene['opening_state'],'ending_state':scene['ending_state']}
                    unit=dramas.record('scene_unit',prefix+' · '+scene['title'], scene['synopsis'],
                        drama_id=e['drama_id'],episode_id=e['id'],parent_id=e['id'],scene_id=scene['scene_id'],
                        unit_index=index,sequence=sequence,global_prefix=prefix, location_id=scene['location_id'],prop_ids=scene['prop_ids'],
                        duration_seconds=seconds,format=e['format'], plan_version=e['plan_version'],plan=plan,outline=outline,
                        source_excerpt=e.get('source_excerpt',''),
                        dependency_context=copy.deepcopy(e.get('dependency_context',[])),
                        series_key=e['series_key'], budget=copy.deepcopy(e['budget']),execution_config=copy.deepcopy(config),
                        unit_brief={'scene':scene,'unit_index':index,'unit_count':len(durations),'seconds':seconds,
                                    'allocation':'按顺序连续展开场景，勿在每个单元重复整个场景；最后单元才到达场景结尾'})
                    dramas.insert(db,unit)
                    e['unit_ids'].append(unit['id'])
            e.update(scene_plan=scenes,scene_review=review,status='producing',stage='scene_units',progress=10,
                budget_scope='per_unit',total_authorized_gpu_minutes=e['budget']['gpu_minutes']*sequence,
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
        current.update(revision_action=None,production_revision=current['production_revision']+1)
        store._write(db,current)


def run(episode, config):
    from .drama_runtime import setup_episode
    from .workflows import advance, ReviewBlocked
    from studio.core import ROOT
    check_parent(episode['id'])
    dramas.owned_folder(episode,ROOT)
    plan_units(episode,config)
    episode=store.get(episode['id'],False)
    if episode.get('revision_action'): revise_units(episode,config)
    episode=store.get(episode['id'],False)
    for i, uid in enumerate(episode['unit_ids']):
        check_parent(episode['id'])
        unit=store.get(uid,False)
        if unit['status']=='unit_ready': continue
        if unit['status'] in {'blocked','paused','failed'}:
            store.update(uid,{'status':'queued','pause_requested':False,'error':None})
        if i and not unit.get('production_profile'):
            previous=store.get(episode['unit_ids'][i-1],False)
            script=previous.get('script') or {}
            store.update(uid,{'previous_unit_state':{'unit':previous['global_prefix'],'ending':script.get('ending'),
                'last_shot_continuity':(script.get('shots') or [{}])[-1].get('continuity')}})
        setup_episode(store.get(uid,False))
        from .drama_assets import prepare
        try:
            prepare(uid,config,lambda:bool(store.get(episode['id'],False).get('pause_requested')))
            advance(uid)
        except Exception:
            # Surface the actual failed unit on its parent without discarding state.
            store.update(episode['id'],{'active_unit_id':uid,'stage':'scene_units'})
            raise
        finally:
            units=[store.get(value,False) for value in episode['unit_ids']]
            store.update(episode['id'],{'production':{'gpu_seconds':sum(u.get('production',{}).get('gpu_seconds',0) for u in units)}})
        unit=store.get(uid,False)
        if unit['status']!='unit_ready':
            raise ReviewBlocked('制作单元尚未自动完成，已保留审核与交接记录',{'unit_id':uid,'status':unit['status']})
        units=[store.get(value,False) for value in episode['unit_ids']]
        store.update(episode['id'],{'active_unit_id':uid,'progress':10+math.floor(75*(i+1)/len(units)),
            'production':{'gpu_seconds':sum(u.get('production',{}).get('gpu_seconds',0) for u in units)}})
    from .episode_assembly import assemble
    episode=store.get(episode['id'],False)
    order=episode.get('edit_unit_order',episode['unit_ids'])
    units=[store.get(uid,False) for uid in order]
    from .drama_continuity import review
    transition_review=review(episode,units,config,lambda:bool(store.get(episode['id'],False).get('pause_requested')))
    store.update(episode['id'],{'transition_review':transition_review})
    episode['transition_review']=transition_review
    store.update(episode['id'],{'stage':'assembly','status':'producing'})
    delivery=assemble(episode,units,lambda:bool(store.get(episode['id'],False).get('pause_requested')))
    check_parent(episode['id'])
    history=episode.get('delivery_history',[])
    if episode.get('delivery') and episode['delivery'].get('source_signature')!=delivery['source_signature']:
        history=[*history,episode['delivery']]
    store.update(episode['id'],{'delivery':delivery,'delivery_history':history,'delivery_complete_revision':episode['production_revision'],
        'status':'awaiting_final','stage':'final_decision','progress':95,'error':None,'needs_decision':None})
