"""Execute drama episodes using the existing durable graph and production engine."""
from __future__ import annotations

import copy
import math
from . import store, dramas
from .config import config_snapshot
from .schemas import fingerprint


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
        'story_feedback': '严格制作已确认的本集大纲，不重新构思无关故事。人物身份、声线、画风与开场/结尾状态固定。\n' + store.encoded(outline),
        'team_control_version': 1})


def execute_episode(pid):
    from .workflows import advance, ReviewBlocked
    episode = store.get(pid, False)
    with config_snapshot(episode['execution_config']):
        setup_episode(episode)
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
    with config_snapshot():
        run(pid, load_config())
