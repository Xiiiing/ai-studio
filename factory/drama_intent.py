"""Frozen, opt-in whole-drama execution and narrative contracts.

Existing projects retain their original contracts. A creation request grants a
finite production scope; splitting scenes never creates additional GPU time.
"""
from __future__ import annotations

import copy
import math
import json
import re

from studio.core import fingerprint

VERSION = 'ai-studio.one-sentence.v1'
INHERITED = ('production_mode', 'production_spec', 'agent_loop', 'generation_pipeline')


def enabled(project):
    return project.get('production_mode') == 'one_sentence' and (project.get('production_spec') or {}).get('version') == VERSION


def initialize(project, config, request_id):
    from .agent_policy import DEFAULTS, VERSION as AGENT_VERSION
    from .generation_pipeline import VERSION as PIPELINE_VERSION
    from .production_execution import DEFAULT_POLICY, apply_configuration
    count, seconds = project['episode_count'], project['duration_seconds']
    budget = project['automation']['budget']
    units = count * math.ceil(seconds / 90)
    project.update(production_mode='one_sentence',
        production_spec={'version': VERSION, 'episode_count': count, 'episode_seconds': seconds,
                         'format': project['format'], 'gpu_minutes': budget['gpu_minutes'] * units,
                         'budget_basis': 'fixed_at_creation_not_actual_scene_count',
                         'authorization_id': request_id, 'voice_mode': 'voice_over'},
        generation_pipeline={'version': PIPELINE_VERSION, 'selection_id': request_id},
        agent_loop={'version': AGENT_VERSION, 'selection_id': request_id,
                    'settings': {**DEFAULTS, 'protocol': 'structured'}},
        execution_config=apply_configuration(config, DEFAULT_POLICY))
    # Freeze explicit global mouth constraints independently of model paraphrasing.
    source = project['brief'] + '\n' + project.get('visual_style', '')
    source = re.sub(r'(?:不要求|无需|不必|不要).{0,8}(?:闭口|闭嘴|嘴(?:巴|唇|部)?闭合)|闭口不谈', '', source)
    if re.search(r'(?:全程|始终|所有|必须|要求).{0,12}(?:闭口|闭嘴|嘴(?:巴|唇|部)?闭合)|(?:mouths?|lips).{0,30}(?:closed|together).{0,20}(?:throughout|all frames)|closed[ -]mouth', source, re.I):
        project['production_spec']['visible_mouths_closed'] = True
    frozen = project['execution_config']
    routes = frozen.setdefault('llm_roles', {})
    if 'executive_producer' not in routes:
        from .config import resolve_llm
        try:
            resolved = resolve_llm(frozen, 'story_editor')
            source = frozen.get('llm_profiles', {}).get(resolved['profile']) if resolved.get('configured') else frozen.get('llm')
            if source:
                frozen.setdefault('llm_profiles', {})['one_sentence_producer'] = copy.deepcopy(source)
                routes['executive_producer'] = 'one_sentence_producer'
        except ValueError:
            pass  # The worker's static preflight reports the missing configuration.
    project['execution_config'].setdefault('creative', {})['separate_storyboard'] = True
    project['execution_config'].setdefault('production', {})['review_only_existing_videos'] = False
    project['production_spec']['config_signature'] = fingerprint(project['execution_config'])


def inherit(project, parent):
    if not enabled(parent):
        return
    for key in INHERITED:
        project.setdefault(key, copy.deepcopy(parent[key]))
    if 'execution_config' in parent:
        project.setdefault('execution_config', copy.deepcopy(parent['execution_config']))
    count = parent['production_spec']['episode_count']
    number = project.get('episode_number', parent.get('episode_number', 1))
    project['narrative_scope'] = {
        'kind': project['record_kind'], 'episode_number': number, 'episode_count': count,
        'series_finale': number == count,
        'unit_index': project.get('unit_index'),
        'unit_count': project.get('unit_brief', {}).get('unit_count'),
        'outline': copy.deepcopy(project.get('outline', {})),
    }



def closed_mouths(project):
    return not enabled(project) or project['production_spec'].get('visible_mouths_closed') is True

def instructions(project):
    if not enabled(project):
        return ''
    scope = project.get('narrative_scope') or {'kind': 'drama'}
    if scope['kind'] == 'drama':
        return ('\n整剧制作契约：先安排全剧因果与结局，再写各集独立的新事件。'
                '每集beats列出2至8个必要的可见事件、主动选择与结果，使用本集唯一稳定ID。'
                '末集兑现全剧主要冲突，前集不得提前解决全剧或重复同一高潮。')
    if scope['kind'] == 'scene_unit':
        ending = '只在本场景最后单元兑现场景结果。'
    elif scope['series_finale']:
        ending = '本集为末集，必须兑现全剧已铺垫的主要冲突与结局。'
    else:
        ending = '完成本集目标和结果，允许全剧主线继续；不得提前结束全剧或复制前集事件。'
    return ('\n分层叙事契约覆盖通用独立短片的闭环要求：' + ending +
            '沿用narrative_scope、批准大纲和当前dependency_context中的实际前集状态，旧反馈中的历史状态不能覆盖当前采用版本。每镜beat_ids只绑定该镜实际承载的必要事件；'
            '所有必要事件须有画面或声音承载，标签不能代替剧情。'
            '声音采用外部画外音，每镜可有多句，起点按顺序分配；总可发声字符按秒数估时，'
            '每句保留实际TTS测量余量，不用快读或静音代替剧情。'
            '视频须用可辨认的动作推动事件；不要求同步口型。闭口仅在明确的用户/镜头约束下要求。')


def budget_snapshot(db, drama):
    rows = [json.loads(r[0]) for r in db.execute(
        "SELECT snapshot FROM projects WHERE json_extract(snapshot,'$.drama_id')=?", (drama['id'],))]
    leaves = [p for p in rows if p.get('record_kind') == 'scene_unit' or
              p.get('record_kind') == 'episode' and not p.get('unit_ids')]
    def usage(project):
        from .scheduler import ProductionNeedsDecision
        values = [float(project.get('production', {}).get('gpu_seconds', 0)) / 60,
                  float(project.get('budget', {}).get('spent_gpu_minutes', 0))]
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ProductionNeedsDecision('整剧累计用量记录无效，未新增生成', 'whole_budget_unverified')
        return max(values)
    spent = sum(usage(p) for p in leaves)
    cap = drama['production_spec']['gpu_minutes']
    return {'gpu_minutes': cap, 'spent_gpu_minutes': round(spent, 6),
            'remaining_gpu_minutes': round(max(0, cap - spent), 6), 'scope': 'whole_drama'}


def bound_budget(project, config):
    if not enabled(project) or not project.get('drama_id'):
        return config
    from . import store
    from .scheduler import ProductionBudgetExceeded
    with store.connection() as db:
        drama = store._read(db, project['drama_id'])
        state = budget_snapshot(db, drama)
    own = float(project.get('production', {}).get('gpu_seconds', 0))
    ceiling = state['remaining_gpu_minutes'] * 60 + own
    if ceiling <= own:
        raise ProductionBudgetExceeded('整剧授权GPU额度已用尽，拆分或恢复不会增加额度', state)
    return {**config, 'max_gpu_seconds': min(config.get('max_gpu_seconds', 7200), ceiling)}
