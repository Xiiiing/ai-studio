"""Execute drama episodes using the existing durable graph and production engine."""
from __future__ import annotations

import copy
import math
from . import store, dramas
from .config import config_snapshot
from .schemas import fingerprint


def capture_dependencies(episode):
    if 'dependency_context' in episode:
        from .drama_intent import enabled
        if enabled(episode):
            siblings = [store.get(eid, False) for eid in store.get(episode['drama_id'], False)['episodes']]
            if stale_dependencies(episode, siblings):
                from .scheduler import ProductionNeedsDecision
                raise ProductionNeedsDecision('前集采用版本已变化，必须重新检查本集承接', 'dependency_version_changed')
        return True
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        root=dramas.require(db,episode['drama_id'])
        siblings=[store._read(db,eid) for eid in root['episodes']]
        context=[]
        from .drama_intent import enabled as whole_drama
        numbers = set(episode['outline']['depends_on'])
        if whole_drama(episode) and episode.get('episode_number', 1) > 1:
            numbers.add(episode['episode_number'] - 1)
        for number in sorted(numbers):
            dependency=next(e for e in siblings if e['episode_number']==number)
            if dependency['status'] not in {'awaiting_final','approved'}:
                current=store._read(db,episode['id'])
                current.update(status='waiting_dependency',stage='dependency')
                store._write(db,current)
                return False
            state=dependency.get('script') or {}
            order=dependency.get('edit_unit_order') or dependency.get('unit_ids') or []
            if order: state=store._read(db,order[-1]).get('script') or {}
            from .drama_intent import enabled as whole_drama
            actual = {}
            if whole_drama(episode):
                from .drama_delivery import adopted_end
                actual = {'observed_end': adopted_end(dependency)}
            context.append({**actual, 'episode_id':dependency['id'],'number':number,'plan_version':dependency['plan_version'],
                'delivery_signature':(dependency.get('delivery') or {}).get('source_signature'),
                'script_ending':state.get('ending'),'planned_ending':dependency['outline']['ending_state'],
                'basis':'pinned_script_and_delivery_receipt_not_full_film_observation'})
        current=store._read(db,episode['id'])
        current['dependency_context']=context
        store._write(db,current)
    return True



def stale_dependencies(episode, siblings):
    by_id = {e['id']: e for e in siblings}
    return [c for c in episode.get('dependency_context', [])
            if c['episode_id'] not in by_id or c.get('delivery_signature') !=
            (by_id[c['episode_id']].get('delivery') or {}).get('source_signature')]

def setup_episode(episode):
    if episode.get('production_profile'): return
    plan, outline = episode['plan'], episode['outline']
    characters = [c for c in plan['characters'] if c['character_id'] in outline['characters']]
    seconds = episode['duration_seconds']
    profile = {'preset': 'compact6', 'shot_count': max(3, math.ceil(seconds / 6)),
        'duration_seconds': seconds, 'characters': [{k: c[k] for k in ('character_id', 'name', 'voice_id')} for c in characters]}
    from .production_profiles import normalize
    profile = normalize(profile)
    bible = {k: copy.deepcopy(plan[k]) for k in ('world', 'visual_rules', 'characters', 'scenes', 'props')}
    bible['schema_version'] = 1
    proposal = {'id': 'confirmed_episode', 'title': outline['title'], 'synopsis': outline['synopsis'],
        'logline': outline['synopsis'][:220], 'ending': outline['ending_state'], 'eligible': True,
        'hook': outline['opening_state'], 'characters': characters,
        'basis': {'drama_id': episode['drama_id'], 'plan_version': episode['plan_version']}}
    store.update(episode['id'], {'production_profile': profile, 'proposals': [proposal],
        'selected_story': proposal['id'], 'proposals_complete_generation': 1,
        'series_bible': {'version': episode['plan_version'], 'document': bible, 'sha256': fingerprint(bible)},
        'story_feedback': '严格制作已确认的本集大纲，不重新构思无关故事。人物身份、声线、画风与开场/结尾状态固定。\n' + store.encoded(outline) +
            '\n固定人物关系：' + store.encoded(plan.get('relationships',[])) +
            '\n原剧本文本片段（非完整源文）：' + episode.get('source_excerpt','') +
            '\n连续制作单元要求：' + store.encoded(episode.get('unit_brief',{})) +
            '\n上一单元已制作结尾：' + store.encoded(episode.get('previous_unit_state',{})) +
            '\n已固定的前集剧本与成片版本：' + store.encoded(episode.get('dependency_context',[])),
        'team_control_version': 1})


def execute_episode(pid):
    from .workflows import advance, ReviewBlocked
    episode = store.get(pid, False)
    if not capture_dependencies(episode): return {'waiting_dependency':True}
    episode = store.get(pid,False)
    with config_snapshot(episode['execution_config']):
        if episode['duration_seconds'] > 90 or episode.get('unit_ids'):
            from .long_episodes import run
            return run(episode, episode['execution_config'])
        setup_episode(episode)
        from .drama_assets import prepare
        prepare(pid, episode['execution_config'], lambda:bool(store.get(pid,False).get('pause_requested')))
        episode = store.get(pid, False)
        if episode.get('revision_action'):
            action = episode['revision_action']
            gate = next((g for g in reversed(episode['decisions']) if g.get('type') == 'final' and g.get('status') == 'pending'), None)
            if not gate: raise ReviewBlocked('原终片审核记录缺失，不能创建返修')
            store.decide(pid, {'type': 'final', 'decision_id': gate['id'], 'choice': action, 'feedback': episode['revision_feedback'],
                               'expected_revision': episode['revision']})
            store.update(pid, {'revision_action': None})
        result = advance(pid)
        current = store.get(pid, False)
        if current['status'].startswith('awaiting_') and current['status'] != 'awaiting_final':
            raise ReviewBlocked('该集自动创作尚未完成，请检查总制片模型和审核问题', {'stage': current['stage']})
        return result


def run_plan(pid):
    from .config import load_config
    from .drama_planning import run
    project = store.get(pid, False)
    with config_snapshot(project.get('execution_config')):
        run(pid, load_config())
