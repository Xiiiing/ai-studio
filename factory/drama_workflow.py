"""Read-only production overview from drama state and recorded role outputs.

Planning is not a LangGraph run. These stages describe the actual drama lifecycle;
cache files and provider receipts are evidence, not a synthesized team ledger.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re

from .config import ROLE_LABELS
from .creative_evidence_io import digest, read_json
from .team_flow import _open_directory

MAX_FILES = 512
MAX_TASKS = 160
MAX_BYTES = 256 * 1024
_ACTIVE = {'queued', 'creating', 'scripting', 'producing', 'waiting_remote', 'visual_development'}
_GOOD = {'approved', 'awaiting_final', 'unit_ready'}
_FIELDS = {'summary', 'world', 'storyline', 'visual_rules', 'relationships', 'characters',
    'character_id', 'name', 'identity_constraints', 'default_wardrobe', 'voice_id', 'scenes',
    'props', 'entity_id', 'description', 'episodes', 'number', 'title', 'synopsis',
    'opening_state', 'ending_state', 'depends_on', 'uncertainties', 'scene_id', 'seconds',
    'location_id', 'prop_ids', 'passed', 'issues', 'status', 'reason', 'unit_ids', 'unit_order',
    'brief', 'genre', 'visual_style', 'episode_count', 'episode_seconds', 'review_feedback',
    'previous', 'outline', 'feedback', 'producer_feedback', 'unit_count', 'unit_index',
    'scope', 'boundaries', 'from_unit', 'to_unit', 'previous_world', 'previous_outline'}


def _summary(value, depth=0, budget=None):
    """Only product fields, with a shared size bound for each input/output."""
    budget = [4000] if budget is None else budget
    if depth > 5 or budget[0] <= 0: return '…'
    budget[0] -= 12
    if isinstance(value, str):
        result = value[:max(0, min(1200, budget[0]))]
        budget[0] -= len(result)
        return result
    if value is None or type(value) in (bool, int, float): return value
    if isinstance(value, list):
        result = []
        for item in value[:16]:
            if budget[0] <= 0: break
            result.append(_summary(item, depth+1, budget))
        return result
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if budget[0] <= 0: break
            if key in _FIELDS: result[key] = _summary(item, depth+1, budget)
        return result
    return None


def _state(record):
    state = record.get('status', 'pending')
    if state in _GOOD: return 'succeeded' if state != 'awaiting_final' else 'waiting'
    if state in {'failed', 'blocked', 'waiting_dependency'}: return 'blocked'
    if state in _ACTIVE: return 'running' if state != 'queued' else 'pending'
    if state in {'paused', 'cancelled'}: return state
    return 'pending'


def _row(ident, title, role, status, input, output, **extra):
    return {'id': ident, 'title': title, 'role': role, 'status': status,
        'input': input, 'output': output, **extra}


def _files(root, parts, directories, evidence):
    """Bound traversal and reject symlinks, including intermediate components."""
    descriptor = None
    result = []
    try:
        descriptor = _open_directory(root, parts)
        with os.scandir(descriptor) as entries:
            for number, entry in enumerate(entries):
                if number >= MAX_FILES:
                    evidence['truncated'] = True
                    break
                valid = entry.is_dir(follow_symlinks=False) if directories else entry.is_file(follow_symlinks=False)
                if valid: result.append(Path(*parts) / entry.name)
    except OSError:
        pass
    finally:
        if descriptor is not None: os.close(descriptor)
    return result


def _read(root, relative, evidence):
    try:
        return read_json(root / relative, maximum=MAX_BYTES)
    except (OSError, ValueError):
        evidence['unreadable'] += 1
        return None


def _cache_kind(stage):
    if stage.startswith('drama-source-'): return '剧本资料整理', 'story_editor', '来源剧本片段与集号'
    if stage.startswith('drama-world-'): return '全剧设定与主线', 'story_editor', '创作方向、题材、画风、总集数及上轮审稿意见'
    if stage.startswith('drama-outline-'): return '分集大纲创作', 'screenwriter', '全剧设定、指定集号、前集状态与来源摘录'
    if stage.startswith('drama-plan-review-'): return '全剧规划独立审核', 'story_critic', '全剧设定、分集大纲、相邻集状态与来源依据'
    if stage.startswith('episode-scenes-review-'): return '单集场景独立审核', 'independent_critic', '已确认单集大纲、场景方案及人物设定'
    if stage.startswith('episode-scenes-'): return '单集场景创作', 'screenwriter', '已确认单集大纲、目标时长及共享设定'
    if stage.startswith('episode-reedit-'): return '单集重剪建议', 'edit_director', '用户重剪意见与已有制作单元'
    if stage.startswith('episode-revision-scope-'): return '局部返修范围', 'edit_director', '用户修改意见与已有制作单元'
    return None


def _evidence(project):
    from studio import core
    evidence = {'source': 'recorded_planning_outputs', 'truncated': False, 'unreadable': 0}
    ident = str(project.get('id', ''))
    if not re.fullmatch(r'[a-f0-9]{12}', ident) or project.get('slug') != 'factory-' + ident:
        return [], evidence
    root = core.ROOT / 'production' / project['slug']
    tasks, hashes = [], {}
    for path in _files(root, ('planning', 'cache'), False, evidence):
        match = re.fullmatch(r'(.+)-([a-f0-9]{24})\.json', path.name)
        kind = _cache_kind(match[1]) if match else None
        if not kind: continue
        saved = _read(root, path, evidence)
        if not saved or not isinstance(saved.get('value'), dict): continue
        if not isinstance(saved.get('signature'), str) or saved['signature'][:24] != match[2]: continue
        value = saved['value']
        title, role, inputs = kind
        outline_match = re.fullmatch(r'drama-outline-(?:(\d+)-)?(\d+)-(\d+)', match[1])
        if outline_match:
            title += f' · 第{outline_match[3]}集（第{outline_match[2]}轮）'
        elif (review_match := re.fullmatch(r'drama-plan-review-(?:(\d+)-)?(\d+)-(\d+)', match[1])):
            first = int(review_match[3]) + 1
            last = min(first + 2, project.get('episode_count', first + 2))
            title += f' · 第{first}–{last}集（第{review_match[2]}轮）'
        elif (world_match := re.fullmatch(r'drama-world-(?:(\d+)-)?(\d+)', match[1])):
            title += f' · 第{world_match[2]}轮'
        status = 'blocked' if value.get('passed') is False or value.get('issues') else 'succeeded'
        task = _row('cache:' + path.stem, title, role, status,
            {'basis': inputs, 'scope': '任务依据说明；旧缓存未保存输入正文'}, _summary(value),
            stage=match[1], generated_at=saved.get('generated_at'), evidence='validated_cache',
            execution_status='succeeded')
        tasks.append(task)
        hashes.setdefault((role, digest(value)), []).append(task)
    for path in sorted(_files(root, ('planning', 'llm', 'calls'), True, evidence), reverse=True):
        saved = _read(root, path / 'receipt.json', evidence)
        if not saved or not isinstance(saved.get('role'), str) or saved['role'] not in ROLE_LABELS: continue
        output_hash = saved.get('output_sha256')
        matches = [t for t in hashes.get((saved['role'], output_hash), []) if not t.get('call_id')] if isinstance(output_hash, str) else []
        task = matches[0] if len(matches) == 1 else None
        if len(matches) > 1 and isinstance(saved.get('finished_at'), (int, float)):
            timed = []
            for candidate in matches:
                try:
                    distance = abs(datetime.fromisoformat(candidate['generated_at']).timestamp() - saved['finished_at'])
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
                if distance <= 5: timed.append((distance, candidate))
            if timed: task = min(timed, key=lambda pair: pair[0])[1]
        # Equal critic outputs from different stages are not enough to infer
        # which historical model call produced a cache; retain separate evidence.

        if task is None:
            value = _read(root, path / 'parsed.json', evidence) if saved.get('status') == 'succeeded' else None
            task = _row('call:' + path.name, ROLE_LABELS[saved['role']] + '调用记录', saved['role'],
                {'succeeded': 'succeeded', 'failed': 'blocked', 'starting': 'running'}.get(saved.get('status'), 'pending'),
                {'basis': '当次角色任务资料', 'scope': '输入原文未在此摘要展示'}, _summary(value) if value else '尚无已验证输出',
                evidence='model_receipt')
            tasks.append(task)
        task.update(call_id=path.name, model=str(saved.get('response_model') or saved.get('configured_model') or saved.get('model') or '')[:200],
            started_at=saved.get('started_at'), finished_at=saved.get('finished_at'),
            execution_status=saved.get('status'))
        request = _read(root, path / 'request-01.json', evidence)
        # In user-prefix mode this field includes system rules. Only a complete
        # JSON user message may be treated as product input; never split prompts.
        if request and isinstance(request.get('messages'), list):
            for message in request['messages'][:8]:
                if not isinstance(message, dict) or message.get('role') != 'user' or not isinstance(message.get('content'), str):
                    continue
                try:
                    payload = json.loads(message['content'])
                except (ValueError, RecursionError):
                    continue
                if isinstance(payload, dict) and (summary := _summary(payload)):
                    task['input'] = {'summary': summary, 'scope': '实际调用输入的内容摘要；省略原剧本与模型提示词'}
                    break
        if saved.get('status') == 'failed': task['issues'] = ['模型调用失败，详细诊断见运行记录']
    tasks.sort(key=lambda item: str(item.get('generated_at') or item.get('finished_at') or item.get('started_at') or ''), reverse=True)
    if len(tasks) > MAX_TASKS: evidence['truncated'] = True
    return tasks[:MAX_TASKS], evidence


def _child_task(child, *, unit=False):
    return _row(('unit:' if unit else 'episode:') + child['id'], child['title'], 'orchestrator', _state(child),
        {'plan_version': child.get('plan_version'), 'duration_seconds': child.get('duration_seconds'),
         'outline': _summary(child.get('outline', {}))},
        {'stage': child.get('stage'), 'progress': child.get('progress'), 'delivery_ready': bool(child.get('delivery'))},
        project_id=child['id'], record_kind=child.get('record_kind'),
        issues=[str(child['error'])[:1200]] if child.get('error') else [])


_CONTRACTS = {
    'planning': ('创作需求与来源资料', '全剧设定、人物关系和分集大纲',
        '编剧依据创作需求编写全剧规划；收到审核问题后带原稿返修。', ['story_editor', 'screenwriter']),
    'review': ('全剧设定与分集大纲', '审核结论与具体修改要求',
        '审稿检查剧情、人物和集间承接；未通过时退回编剧，修改后重新审核。', ['story_critic']),
    'confirmation': ('审核通过的全剧规划', '已确认的规划版本',
        '出品人确认规划，后续剧集固定引用该版本。', ['producer']),
    'production': ('已确认大纲、共享设定与选集批次', '逐集制作结果和待处理问题',
        '按选集批次推进剧本、分镜、素材、配音与剪辑；打开剧集查看具体任务。', ['orchestrator']),
    'scenes': ('已确认单集大纲、时长与共享设定', '经审核的场景方案',
        '编剧按单集大纲拆分场景，并交由独立审稿检查。', ['screenwriter', 'independent_critic']),
    'units': ('场景方案、人物身份与声线', '制作单元成片、配音与字幕',
        '按场景分块制作；打开制作单元查看分镜、生成与剪辑任务。', ['orchestrator']),
    'assembly': ('已完成单元与跨镜连续性证据', '完整单集与连续性检查结果',
        '检查跨单元连续性并合成单集，发现问题时退回对应单元返修。', ['editor', 'visual_critic']),
    'acceptance': ('完整成片、字幕与制作清单', '验收决定或局部修改要求',
        '出品人观看成片后验收，或指定局部修改；内部制作完成不等于人工通过。', ['producer']),
}


def _task_stage(task, drama):
    """Assign saved evidence to its lifecycle step, without inventing runs."""
    ident, stage, role = task['id'], task.get('stage', ''), task['role']
    if drama:
        if ident.startswith('plan-review:') or stage.startswith('drama-plan-review-'):
            return 'review'
        if ident.startswith(('episode:', 'batch:')):
            return 'production'
        return 'review' if role in {'story_critic', 'independent_critic'} else 'planning'
    if ident.startswith('unit:'):
        return 'units'
    if ident.startswith('continuity-return:') or stage.startswith(('episode-reedit-', 'episode-revision-scope-')):
        return 'assembly'
    return 'assembly' if role in {'editor', 'edit_director', 'visual_critic'} else 'scenes'


def _current_plan_confirmed(project):
    """An older pinned version does not confirm a new draft under review."""
    return bool(project.get('plan_version')) and project.get('plan_phase') not in {
        'writing', 'reviewing', 'returned', 'exhausted'} and project.get('status') != 'awaiting_plan'


def _projection(project, stages, tasks, active_job):
    """The shared workflow reader displays lifecycle state, not a LangGraph run."""
    drama = project.get('record_kind') == 'drama'
    ids = [stage['id'] for stage in stages]
    phase = project.get('plan_phase')
    if drama and not _current_plan_confirmed(project):
        active = ('planning' if phase in {'writing', 'returned'} else
            'review' if phase == 'reviewing' else
            'confirmation' if (project.get('plan_review') or {}).get('passed') else
            'review' if project.get('plan_review') else 'planning')
    else:
        remaining = stages[3:] if drama else stages
        active = next((stage['id'] for stage in remaining
            if stage['status'] in {'running', 'blocked', 'failed', 'waiting', 'paused', 'cancelled'}),
            next((stage['id'] for stage in remaining if stage['status'] != 'succeeded'), ids[-1]))
    nodes, steps = [], []
    for stage in stages:
        ident = stage['id']
        input_label, output_label, description, members = _CONTRACTS[ident]
        status = stage['status']
        if status == 'returned':
            status = 'running' if active_job and ident == active else 'blocked'
        if ident == active and not active_job and project.get('status') in {'paused', 'cancelled', 'failed', 'blocked'}:
            status = _state(project)
        # Waiting is a human decision only where that decision is actually due.
        if status == 'waiting' and ident in {'confirmation', 'acceptance'}:
            status = 'human'
        node = {'id': ident, 'label': stage['title'], 'status': status,
            'owner': stage['role'], 'members': members, 'description': description,
            'input': input_label, 'output': output_label, 'evidence': 'current_project_state'}
        if stage.get('issues'):
            node['issues'] = stage['issues']
        nodes.append(node)
        steps.append({'id': ident,
            'inputs': [{'label': input_label, 'kind': 'json', 'value': stage['input']}],
            'outputs': [{'label': output_label, 'kind': 'json', 'value': stage['output']}]})
    edges = [{'source': source, 'target': target, 'label': '交付下一阶段'}
        for source, target in zip(ids, ids[1:])]
    edges.append({'source': 'review' if drama else 'assembly',
        'target': 'planning' if drama else 'units', 'label': '审核未通过，退回返修', 'loop': True})
    edges.append({'source': 'acceptance', 'target': 'production' if drama else 'units',
        'label': '按验收意见局部返修', 'loop': True})
    for task in tasks:
        task['stage_id'] = _task_stage(task, drama)
    from .team_blueprint import collaboration_departments
    identity = {'project_id': project['id'], 'revision': project.get('revision', 0)}
    # Declared handoffs mirror planning calls, not observed packet transfers.
    role_handoffs = ([
        {'source': 'story_editor', 'target': 'screenwriter', 'label': '全剧设定、人物与画风要求'},
        {'source': 'screenwriter', 'target': 'story_critic', 'label': '分集大纲与集间承接'},
        {'source': 'story_critic', 'target': 'story_editor', 'label': '设定修改要求', 'loop': True},
        {'source': 'story_critic', 'target': 'screenwriter', 'label': '大纲修改要求', 'loop': True},
    ] if drama else [
        {'source': 'screenwriter', 'target': 'independent_critic', 'label': '单集场景方案'},
        {'source': 'independent_critic', 'target': 'screenwriter', 'label': '场景修改意见', 'loop': True},
    ])
    return ({**identity, 'active_node': active, 'nodes': nodes, 'edges': edges,
        'departments': collaboration_departments(),
        'presentation': {'stages': [{'id': row['id'], 'label': row['title'], 'node_ids': [row['id']]}
            for row in stages], 'branches': []}},
        {**identity, 'steps': steps, 'nodes': [], 'edges': role_handoffs})


def build(project, *, episodes=(), batches=(), units=(), active_job=False):
    """No DB writes, mkdir, provider polls or config loads are permitted here."""
    tasks, evidence = _evidence(project)
    for task in tasks:
        if task['status'] == 'running' and not active_job:
            task.update(status='waiting', issues=['调用回执未完结；当前没有排队或执行中的制作任务'])
    status = project.get('status')
    waiting = status in {'blocked', 'failed', 'paused', 'cancelled'}
    current = _state(project) if waiting else 'running' if active_job and status != 'queued' else 'pending'
    if project.get('record_kind') == 'drama':
        plan = project.get('draft_plan') or {}
        review = project.get('plan_review') or {}
        confirmed = _current_plan_confirmed(project)
        has_plan = bool(plan)
        repairing = active_job and project.get('plan_phase') in {'writing','returned'}
        stages = [
            _row('planning', '全剧设定与分集大纲', 'story_editor', 'running' if repairing else 'succeeded' if has_plan or confirmed else current,
                {'brief': project.get('brief', '')[:1200], 'episode_count': project.get('episode_count'),
                 'duration_seconds': project.get('duration_seconds')},
                {'storyline': plan.get('storyline'), 'characters': len(plan.get('characters', [])), 'outlines': len(plan.get('episodes', []))}),
            _row('review', '全剧规划独立审核', 'story_critic', 'succeeded' if review.get('passed') else 'returned' if repairing and review else 'running' if active_job and project.get('plan_phase') == 'reviewing' else 'blocked' if review else current if has_plan else 'pending',
                '全剧主线、人物设定与相邻分集大纲', _summary(review) if review else '尚无已保存的独立审核结果', issues=_summary(review.get('issues', []))),
            _row('confirmation', '出品人确认全剧规划', 'producer', 'succeeded' if confirmed else 'waiting' if review.get('passed') else 'pending',
                '经独立审稿的全剧规划', {'plan_version': project.get('plan_version', 0) if confirmed else 0,
                    **({'previous_plan_version': project['plan_version']} if not confirmed and project.get('plan_version') else {})}),
            _row('production', '按集制作与团队协作', 'orchestrator',
                'running' if any(e.get('status') in _ACTIVE for e in episodes) else 'blocked' if any(e.get('status') in {'blocked', 'failed', 'waiting_dependency'} for e in episodes)
                else 'succeeded' if episodes and all(e.get('status') in _GOOD for e in episodes) else 'pending',
                '已确认大纲、共享资产版本与生成批次', {'batches': len(batches), 'episodes': len(episodes)}),
            _row('acceptance', '逐集验收与成果', 'producer', 'succeeded' if episodes and all(e.get('status') == 'approved' for e in episodes)
                else 'waiting' if any(e.get('status') == 'awaiting_final' for e in episodes) else 'pending',
                '每集成片、字幕与制作清单', {'awaiting': sum(e.get('status') == 'awaiting_final' for e in episodes),
                'approved': sum(e.get('status') == 'approved' for e in episodes)})]
        for entry in project.get('plan_history', []):
            tasks.append(_row(f"plan-review:{entry.get('cycle',0)}:{entry.get('attempt',0)}", '规划审核与退回记录',
                'story_critic', 'succeeded' if (entry.get('review') or {}).get('passed') else 'returned',
                {'规划次数':entry.get('cycle'), '审核轮次':entry.get('attempt')}, _summary(entry.get('review', {})),
                issues=_summary((entry.get('review') or {}).get('issues', [])), generated_at=entry.get('at'),
                returned_to=entry.get('returned_to', [])))
        tasks.extend(_child_task(e) for e in episodes)
        for batch in batches:
            tasks.append(_row('batch:' + batch['id'], '生成批次 · ' + '、'.join(map(str, batch.get('episode_numbers', []))),
                'orchestrator', _state(batch), {'plan_version': batch.get('plan_version'), 'episode_numbers': batch.get('episode_numbers', [])},
                {'progress': batch.get('progress'), 'members': [{k: m.get(k) for k in ('id','number','status','progress')} for m in batch.get('members', [])]}))
    else:
        scenes = project.get('scene_plan') or []
        stages = [
            _row('scenes', '单集场景创作与独立审核', 'screenwriter', 'succeeded' if units else current,
                _summary(project.get('outline', {})), {'scenes': len(scenes), 'review': _summary(project.get('scene_review'))}),
            _row('units', '分镜与制作单元', 'orchestrator', 'succeeded' if units and all(u.get('status') == 'unit_ready' for u in units)
                else 'blocked' if any(u.get('status') in {'blocked','failed'} for u in units) else current if units else 'pending',
                '场景方案、共享人物身份和声线', {'units': len(units), 'completed': sum(u.get('status') == 'unit_ready' for u in units)}),
            _row('assembly', '连续性检查与单集合成', 'editor', 'succeeded' if project.get('delivery') else current if project.get('stage') == 'assembly' else 'pending',
                '逐单元成片、配音、字幕与连续性证据', {'delivery_ready': bool(project.get('delivery')), 'continuity': _summary(project.get('transition_review'))}),
            _row('acceptance', '出品人验收', 'producer', 'succeeded' if status == 'approved' else 'waiting' if status == 'awaiting_final' else 'pending',
                '完整单集与制作清单', {'status': status})]
        for entry in project.get('continuity_repair_history', []):
            tasks.append(_row(f"continuity-return:{entry.get('cycle',1)}:{entry.get('from_unit')}:{entry.get('unit_id')}:{entry['round']}",
                '连续性审核退回', 'visual_critic', 'blocked' if entry['status'] == 'exhausted' else 'returned',
                {'审核轮次': entry['round'], '最多轮次': entry['max_rounds']}, _summary(entry.get('review', {})),
                issues=_summary(entry.get('issues', [])), returned_to=entry.get('returned_to', []), project_id=entry['unit_id']))
        tasks.extend(_child_task(u, unit=True) for u in units)
    graph, flow = _projection(project, stages, tasks, active_job)
    flow['evidence'] = evidence
    return {'kind': 'drama_lifecycle' if project.get('record_kind') == 'drama' else 'episode_lifecycle',
        'graph': graph, 'flow': flow,
        'stages': stages, 'tasks': tasks, 'evidence': evidence, 'active_job': bool(active_job),
        'note': '阶段为制作状态汇总；角色记录来自已有缓存或调用回执。编剧与审稿是独立任务，可共享同一模型。'}
