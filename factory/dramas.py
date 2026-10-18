"""Whole-drama metadata on existing projects, revisions and durable jobs."""
from __future__ import annotations

import copy
import json
import re
import time
import uuid

from . import store
from studio.core import fingerprint

MAX_EPISODE_SECONDS = 90  # Extended by the dependent long-episode phase.


def integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label}须为{low}–{high}的整数")
    return value


def text(value, label, low=1, high=6000):
    if not isinstance(value, str) or not low <= len(value.strip()) <= high:
        raise ValueError(f"{label}须为{low}–{high}字")
    return value.strip()


def record(kind, title, brief, **values):
    pid = uuid.uuid4().hex[:12]
    return {"id": pid, "slug": "factory-" + pid, "record_kind": kind,
        "title": title, "brief": brief, "format": "motion_comic", "duration_seconds": 60,
        "status": "queued", "stage": "queued", "progress": 0, "revision": 1,
        "created_at": store.now(), "updated_at": store.now(), "workflow_version": 3,
        "team_mode": "delegated", "sources": [], "submitted_sources": [], "creation_mode": "original",
        "proposals": [], "selected_story": None, "styles": [], "selected_style": None,
        "script": None, "shots": [], "decisions": [], "answers": {}, "production": {},
        "delivery": None, "error": None, "pause_requested": False, "proposal_generation": 1,
        "script_generation": 1, "style_generation": 1, "production_revision": 1,
        "budget": {"gpu_minutes": 120., "spent_gpu_minutes": 0., "wall_minutes": 180., "max_attempts": 3},
        **values}


def insert(db, project):
    db.execute("INSERT INTO projects(id,updated,revision,snapshot) VALUES(?,?,?,?)",
               (project['id'], time.time(), project['revision'], store.encoded(project)))
    store._event(db, project['id'], '已建立' + {'drama': '整剧项目', 'episode': '剧集', 'scene_unit': '制作单元'}.get(project['record_kind'], '记录'), 'created')


def require(db, pid, kind='drama'):
    project = store._read(db, pid)
    if project.get('record_kind') != kind:
        raise ValueError('记录类型与操作不匹配')
    return project


def cas(project, payload):
    if payload.get('expected_revision') != project['revision']:
        raise ValueError('内容已更新，请刷新后提交')


def replay(project, payload):
    key = text(payload.get('request_id'), '请求标识', 1, 100)
    signature = fingerprint({k: v for k, v in payload.items() if k not in {'request_id', 'expected_revision'}})
    saved = project.setdefault('operation_requests', {}).get(key)
    if saved and saved['signature'] != signature:
        raise ValueError('请求标识已用于其他操作')
    return key, signature, saved


def create(payload):
    allowed = {'title', 'brief', 'source_text', 'source_name', 'episode_count', 'duration_seconds', 'format', 'genre', 'visual_style', 'request_id'}
    if set(payload) - allowed: raise ValueError('整剧输入包含未知字段')
    source = payload.get('source_text', '')
    if source:
        source = text(source, '剧本', 5, 60000)
        if '\x00' in source: raise ValueError('请导入UTF-8文本剧本')
    source_name = payload.get('source_name', '')
    if not isinstance(source_name, str) or len(source_name) > 150 or (source_name and not source_name.lower().endswith(('.txt', '.md', '.markdown'))):
        raise ValueError('导入支持TXT或Markdown文件')
    title = text(payload.get('title'), '剧名', 1, 100)
    brief = text(payload.get('brief') or source[:1000], '创作方向', 5, 6000)
    count = integer(payload.get('episode_count'), '总集数', 1, 100)
    duration = integer(payload.get('duration_seconds', 60), '每集秒数', 15, MAX_EPISODE_SECONDS)
    fmt = payload.get('format', 'motion_comic')
    if fmt not in {'motion_comic', 'animation', 'live_action'}: raise ValueError('请选择动态漫或H3视频路线')
    key = 'drama:' + text(payload.get('request_id'), '请求标识', 1, 80)
    signature = fingerprint(payload)
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        prior = db.execute('SELECT * FROM idempotency WHERE key=?', (key,)).fetchone()
        if prior:
            if prior['request_hash'] != signature: raise ValueError('请求标识已用于其他输入')
            return snapshot(prior['project_id'], db=db)
        p = record('drama', title, brief, format=fmt, duration_seconds=duration,
            episode_count=count, source_text=source, source_name=source_name,
            genre=text(payload.get('genre') or '剧情', '题材', 1, 100),
            visual_style=text(payload.get('visual_style') or '统一人物与场景画风', '画风', 1, 2000),
            plan_version=0, plans=[], episodes=[], batches=[], series_key='')
        p['series_key'] = 'drama_' + p['id']
        insert(db, p)
        db.execute('INSERT INTO idempotency VALUES(?,?,?)', (key, p['id'], signature))
        store._enqueue(db, p['id'], 'drama_plan')
        return snapshot(p['id'], db=db)


def list_dramas():
    with store.connection() as db:
        return {'dramas': [snapshot(row['id'], db=db, details=False) for row in db.execute(
            "SELECT id FROM projects WHERE json_extract(snapshot,'$.record_kind')='drama' ORDER BY updated DESC")]}


def snapshot(pid, *, db=None, details=True):
    if db is None:
        with store.connection() as connection:
            return snapshot(pid, db=connection, details=details)
    p = require(db, pid)
    episodes = [store._read(db, eid) for eid in p['episodes']]
    # Snapshot reads never dispatch, retry, poll a provider or mutate aggregation.
    for episode in episodes:
        episode.pop('operation_requests', None)
        episode.pop('execution_config', None)
    result = {k: copy.deepcopy(v) for k, v in p.items() if k not in {'operation_requests', 'source_text', 'planning_cache'}}
    result['episodes'] = episodes
    result['batches'] = [{**b, 'members': [{'id': e['id'], 'number': e['episode_number'], 'status': e['status'],
        'progress': e['progress'], 'error': e.get('error')} for e in episodes if e['id'] in b['episode_ids']]}
        for b in p['batches']]
    result['affected_episodes'] = [{'id': e['id'], 'number': e['episode_number'], 'pinned_version': e['plan_version']}
        for e in episodes if e['plan_version'] != p['plan_version']]
    if details: result['source_text'] = p['source_text']
    return result


def validate_plan(plan, project):
    from jsonschema import validate, ValidationError
    from .drama_planning import plan_schema
    try: validate(plan, plan_schema(project['episode_count']))
    except ValidationError as exc: raise ValueError('全剧规划字段无效：' + '.'.join(map(str, exc.absolute_path))) from exc
    ids = [c['character_id'] for c in plan['characters']]
    if len(set(ids)) != len(ids): raise ValueError('全剧角色ID须唯一')
    numbers = [e['number'] for e in plan['episodes']]
    if sorted(numbers) != list(range(1, project['episode_count'] + 1)): raise ValueError('分集大纲须覆盖全部集数且不重复')
    for e in plan['episodes']:
        if not set(e['characters']) <= set(ids) or not e['characters']: raise ValueError('剧集必须引用全剧已定义角色')
        if any(dep >= e['number'] or dep < 1 for dep in e['depends_on']): raise ValueError('剧集依赖只能引用前面的集数')
    return copy.deepcopy(plan)


def confirm(pid, payload):
    if set(payload) - {'expected_revision', 'request_id', 'plan'}: raise ValueError('规划确认包含未知字段')
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        p = require(db, pid)
        key, signature, saved = replay(p, payload)
        if saved: return snapshot(pid, db=db)
        cas(p, payload)
        plan = validate_plan(payload.get('plan', p.get('draft_plan')), p)
        if not p.get('plan_review', {}).get('passed') or fingerprint(plan) != fingerprint(p.get('draft_plan')):
            # Edited plans go back through independent review before confirmation.
            p.update(draft_plan=plan, plan_review=None, status='queued', stage='plan_review')
            store._enqueue(db, pid, 'drama_plan')
        else:
            p['plan_version'] += 1
            p['plans'].append({'version': p['plan_version'], 'document': plan, 'sha256': fingerprint(plan), 'confirmed_at': store.now(), 'actor': 'producer'})
            p.update(status='ready', stage='episodes', progress=100)
            if not p['episodes']:
                for outline in sorted(plan['episodes'], key=lambda e: e['number']):
                    e = record('episode', f"第{outline['number']}集 · {outline['title']}", outline['synopsis'],
                        drama_id=pid, parent_id=pid, series_key=p['series_key'], episode_number=outline['number'],
                        duration_seconds=p['duration_seconds'], format=p['format'], plan_version=p['plan_version'],
                        outline=outline, plan=plan, status='draft', unit_ids=[], batch_id=None)
                    insert(db, e)
                    p['episodes'].append(e['id'])
        p['operation_requests'][key] = {'signature': signature}
        store._write(db, p)
        store._event(db, pid, '全剧规划已确认' if p['status'] == 'ready' else '修改后的规划等待独立审核', 'drama_plan')
        return snapshot(pid, db=db)


def generate(pid, payload):
    if set(payload) - {'expected_revision', 'request_id', 'episode_numbers', 'budget'}: raise ValueError('生成批次包含未知字段')
    from .config import load_config
    config = load_config()
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        p = require(db, pid)
        key, signature, saved = replay(p, payload)
        if saved: return snapshot(pid, db=db)
        cas(p, payload)
        if not p['plan_version']: raise ValueError('请先确认全剧规划')
        numbers = payload.get('episode_numbers')
        if not isinstance(numbers, list) or not numbers or len(numbers) != len(set(numbers)):
            raise ValueError('请选择不重复的剧集')
        for number in numbers: integer(number, '集号', 1, p['episode_count'])
        budget = payload.get('budget', {'gpu_minutes': 120, 'wall_minutes': 180, 'max_attempts': 3})
        if not isinstance(budget, dict) or set(budget) - {'gpu_minutes', 'wall_minutes', 'max_attempts'}: raise ValueError('预算字段无效')
        for field, low, high in [('gpu_minutes', 5, 1440), ('wall_minutes', 10, 1440), ('max_attempts', 1, 5)]:
            integer(budget.get(field), field, low, high)
        chosen = [store._read(db, eid) for eid in p['episodes'] if store._read(db, eid)['episode_number'] in numbers]
        if any(e['status'] != 'draft' for e in chosen): raise ValueError('已开始的剧集请使用继续、返修或失败重试')
        batch = {'id': uuid.uuid4().hex[:12], 'episode_ids': [e['id'] for e in chosen], 'episode_numbers': sorted(numbers),
            'plan_version': p['plan_version'], 'budget': copy.deepcopy(budget), 'created_at': store.now(), 'status': 'queued',
            'config_signature': fingerprint(config)}
        # Private configuration remains a backend-only snapshot; never returned by the API.
        for e in chosen:
            outline = next(o for o in p['plans'][-1]['document']['episodes'] if o['number'] == e['episode_number'])
            e.update(outline=outline, plan=p['plans'][-1]['document'], plan_version=p['plan_version'],
                status='queued', stage='queued', budget=copy.deepcopy(budget), batch_id=batch['id'],
                execution_config=copy.deepcopy(config), pause_requested=False)
            store._write(db, e)
        p['batches'].append(batch)
        p['operation_requests'][key] = {'signature': signature, 'batch_id': batch['id']}
        p.update(status='producing', pause_requested=False)
        store._write(db, p)
        dispatch(db, p)
        return snapshot(pid, db=db)


def dispatch(db, drama):
    """Publish one eligible episode to the existing queue, in episode order."""
    if drama.get('pause_requested') or drama['status'] == 'cancelled': return
    episodes = [store._read(db, eid) for eid in drama['episodes']]
    if any(db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running')", (e['id'],)).fetchone() for e in episodes): return
    by_number = {e['episode_number']: e for e in episodes}
    for e in sorted(episodes, key=lambda e: e['episode_number']):
        if e['status'] not in {'queued', 'waiting_dependency'} or e.get('pause_requested'): continue
        deps = e['outline']['depends_on']
        if any(by_number[n]['status'] not in {'awaiting_final', 'approved'} for n in deps):
            if e['status'] != 'waiting_dependency':
                e.update(status='waiting_dependency', stage='dependency')
                store._write(db, e)
            continue
        e['status'] = 'queued'
        store._write(db, e)
        store._enqueue(db, e['id'], 'drama_episode')
        return


def after_episode(pid):
    e = store.get(pid, False)
    if not e.get('drama_id'): return
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        p = require(db, e['drama_id'])
        members = [store._read(db, eid) for eid in p['episodes']]
        states = {m['status'] for m in members}
        if not p.get('pause_requested') and p['status'] != 'cancelled':
            p['status'] = ('approved' if states == {'approved'} else 'producing' if states & {'queued','producing','scripting','waiting_remote'}
                else 'awaiting_final' if 'awaiting_final' in states else 'blocked' if states & {'blocked','failed','waiting_dependency'} else 'ready')
        p['budget']['spent_gpu_minutes'] = round(sum(m['budget'].get('spent_gpu_minutes',0) for m in members), 3)
        store._write(db, p)
        dispatch(db, p)


def control(pid, payload):
    allowed = {'expected_revision', 'request_id', 'action', 'episode_id', 'batch_id', 'feedback', 'unit_id', 'shot_ids'}
    if set(payload) - allowed: raise ValueError('控制请求包含未知字段')
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        p = require(db, pid)
        key, signature, saved = replay(p, payload)
        if saved: return snapshot(pid, db=db)
        cas(p, payload)
        action = payload.get('action')
        if action not in {'pause', 'resume', 'cancel', 'retry', 'approve', 'revise', 'reedit'}: raise ValueError('不支持的剧集操作')
        targets = [store._read(db, eid) for eid in p['episodes'] if (not payload.get('episode_id') or eid == payload['episode_id'])]
        if payload.get('batch_id'): targets = [e for e in targets if e.get('batch_id') == payload['batch_id']]
        if not targets: raise ValueError('未找到属于本剧的目标剧集')
        if action in {'approve', 'revise', 'reedit'} and len(targets) != 1: raise ValueError('请明确选择一集验收或返修')
        for e in targets:
            if action == 'pause':
                if e['status'] in {'approved', 'cancelled', 'awaiting_final', 'draft'}: continue
                e['pause_requested'] = True
                running = db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='running'", (e['id'],)).fetchone()
                if not running: e['status'] = 'paused'
            elif action == 'cancel':
                if e['status'] == 'approved': continue
                e.update(pause_requested=True, status='cancelled')
            elif action in {'resume', 'retry'}:
                if e['status'] not in {'paused', 'blocked', 'failed', 'waiting_dependency'}: raise ValueError('该集当前不支持继续或重试')
                if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='running'", (e['id'],)).fetchone(): raise ValueError('请等待当前任务到达安全边界')
                e.update(status='queued', pause_requested=False, error=None, needs_decision=None)
            else:
                if e['status'] != 'awaiting_final' or not e.get('delivery'): raise ValueError('请先等待该集成片进入待验收')
                if action == 'approve':
                    pending = next((g for g in reversed(e['decisions']) if g.get('type') == 'final' and g.get('status') == 'pending'), None)
                    if pending:
                        answer = {'gate_id': pending['id'], 'type': 'final', 'choice': 'approve', 'feedback': '', 'actor': 'producer', 'at': store.now()}
                        pending.update(status='answered', answer=answer)
                        e['answers'][pending['id']] = answer
                        e['status'] = 'queued'
                        store._enqueue(db, e['id'], 'drama_episode')
                    else:
                        e.update(status='approved', accepted_at=store.now(), accepted_by='producer', progress=100)
                else:
                    e['revision_feedback'] = text(payload.get('feedback'), '修改意见', 2, 4000)
                    e['revision_scope'] = {k: payload[k] for k in ('unit_id', 'shot_ids') if k in payload}
                    e.update(status='queued', revision_action=action, pause_requested=False)
            if action in {'pause', 'cancel'}:
                db.execute("UPDATE jobs SET status='cancelled',finished=? WHERE project_id=? AND status='queued'", (time.time(), e['id']))
            store._write(db, e)
        if not payload.get('episode_id') and not payload.get('batch_id'):
            p.update(pause_requested=action in {'pause','cancel'}, status='cancelled' if action == 'cancel' else 'paused' if action == 'pause' else 'producing')
        p['operation_requests'][key] = {'signature': signature}
        store._write(db, p)
        dispatch(db, p)
        return snapshot(pid, db=db)
