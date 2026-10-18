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
    'scope', 'boundaries', 'from_unit', 'to_unit'}


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
        outline_match = re.fullmatch(r'drama-outline-(\d+)-(\d+)', match[1])
        if outline_match:
            title += f' · 第{outline_match[2]}集（第{outline_match[1]}轮）'
        elif (review_match := re.fullmatch(r'drama-plan-review-(\d+)-(\d+)', match[1])):
            first = int(review_match[2]) + 1
            last = min(first + 2, project.get('episode_count', first + 2))
            title += f' · 第{first}–{last}集（第{review_match[1]}轮）'
        elif (world_match := re.fullmatch(r'drama-world-(\d+)', match[1])):
            title += f' · 第{world_match[1]}轮'
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
        confirmed = bool(project.get('plan_version'))
        has_plan = bool(plan)
        stages = [
            _row('planning', '全剧设定与分集大纲', 'story_editor', 'succeeded' if has_plan or confirmed else current,
                {'brief': project.get('brief', '')[:1200], 'episode_count': project.get('episode_count'),
                 'duration_seconds': project.get('duration_seconds')},
                {'storyline': plan.get('storyline'), 'characters': len(plan.get('characters', [])), 'outlines': len(plan.get('episodes', []))}),
            _row('review', '全剧规划独立审核', 'story_critic', 'succeeded' if review.get('passed') else 'blocked' if review else current if has_plan else 'pending',
                '全剧主线、人物设定与相邻分集大纲', _summary(review) if review else '尚无已保存的独立审核结果', issues=_summary(review.get('issues', []))),
            _row('confirmation', '出品人确认全剧规划', 'producer', 'succeeded' if confirmed else 'waiting' if review.get('passed') else 'pending',
                '经独立审稿的全剧规划', {'plan_version': project.get('plan_version', 0)}),
            _row('production', '按集制作与团队协作', 'orchestrator',
                'running' if any(e.get('status') in _ACTIVE for e in episodes) else 'blocked' if any(e.get('status') in {'blocked', 'failed', 'waiting_dependency'} for e in episodes)
                else 'succeeded' if episodes and all(e.get('status') in _GOOD for e in episodes) else 'pending',
                '已确认大纲、共享资产版本与生成批次', {'batches': len(batches), 'episodes': len(episodes)}),
            _row('acceptance', '逐集验收与成果', 'producer', 'succeeded' if episodes and all(e.get('status') == 'approved' for e in episodes)
                else 'waiting' if any(e.get('status') == 'awaiting_final' for e in episodes) else 'pending',
                '每集成片、字幕与制作清单', {'awaiting': sum(e.get('status') == 'awaiting_final' for e in episodes),
                'approved': sum(e.get('status') == 'approved' for e in episodes)})]
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
        tasks.extend(_child_task(u, unit=True) for u in units)
    return {'kind': 'drama_lifecycle' if project.get('record_kind') == 'drama' else 'episode_lifecycle',
        'stages': stages, 'tasks': tasks, 'evidence': evidence, 'active_job': bool(active_job),
        'note': '阶段为制作状态汇总；角色记录来自已有缓存或调用回执。编剧与审稿是独立任务，可共享同一模型。'}
