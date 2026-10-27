"""Scoped, durable project automation on the existing transactional job queue.

Snapshots are read-only. Only the worker and explicit user operations reconcile
an authorization; a browser is never required to advance reviewed planning.
"""
from __future__ import annotations

import copy
import math
import re

from studio.core import fingerprint
from . import store

DEFAULT_BUDGET = {'gpu_minutes': 120, 'wall_minutes': 180, 'max_attempts': 3}
FINAL = {'awaiting_final', 'approved'}


def validate_numbers(value, count):
    from .dramas import integer
    if not isinstance(value, list) or not value:
        raise ValueError('请选择不重复的剧集')
    for number in value:
        integer(number, '集号', 1, count)
    if len(value) != len(set(value)):
        raise ValueError('请选择不重复的剧集')
    return sorted(value)


def validate_budget(value):
    from .dramas import integer
    if not isinstance(value, dict) or set(value) != set(DEFAULT_BUDGET):
        raise ValueError('预算字段无效')
    for field, low, high in [('gpu_minutes', 5, 1440), ('wall_minutes', 10, 1440), ('max_attempts', 1, 5)]:
        integer(value.get(field), field, low, high)
    return copy.deepcopy(value)


def normalize(value, count):
    if value is None:
        return {'enabled': False}
    if not isinstance(value, dict) or set(value) - {'enabled', 'episode_numbers', 'budget'}:
        raise ValueError('自动制作配置包含未知字段')
    if type(value.get('enabled')) is not bool:
        raise ValueError('自动制作 enabled 须为布尔值')
    if not value['enabled']:
        return {'enabled': False}
    return {'enabled': True, 'episode_numbers': validate_numbers(value.get('episode_numbers'), count),
        'budget': validate_budget(value.get('budget', DEFAULT_BUDGET)), 'budget_scope': 'per_unit'}


def authorize(config, request_id):
    result = copy.deepcopy(config)
    if result['enabled']:
        result.update(authorization={'request_id': request_id, 'actor': 'producer', 'at': store.now()}, suspended=False)
        from .production_execution import DEFAULT_POLICY
        result['production_recovery'] = copy.deepcopy(DEFAULT_POLICY)
    return result


def _active(db, pid):
    return db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running')", (pid,)).fetchone() is not None


def _review_matches(p):
    review = p.get('plan_review') or {}
    return (review.get('passed') is True and not review.get('issues') and p.get('draft_plan') is not None
        and p.get('plan_review_sha256') == fingerprint(p['draft_plan']) and p.get('plan_phase') == 'approved')


def _plan_changed(p):
    return not p.get('plans') or fingerprint(p.get('draft_plan')) != p['plans'][-1]['sha256']


def _reauthorize_budget(db, p, normalized, request_id, *, suspended, unit_id=None):
    """Explicit per-unit cap increases at a global safe boundary, never a retry."""
    budget = normalized['budget']
    selected = [store._read(db, eid) for eid in p['episodes']]
    selected = [e for e in selected if e['episode_number'] in normalized['episode_numbers']
        and e['status'] not in FINAL | {'draft', 'cancelled'}]
    targets = []
    for episode in selected:
        units = [store._read(db, uid) for uid in episode.get('unit_ids', [])]
        targets.append((episode, bool(units)))
        targets.extend((unit, False) for unit in units if unit['status'] not in FINAL | {'unit_ready', 'cancelled'})
    if unit_id is not None:
        targets = [(item, aggregate) for item, aggregate in targets if item['id'] == unit_id and not aggregate]
        if len(targets) != 1:
            raise ValueError('制作单元不在已授权且未完成的整剧范围内')
    changed = [(item, aggregate) for item, aggregate in targets
        if any(item.get('budget', {}).get(field) != budget[field] for field in DEFAULT_BUDGET)]
    if not changed:
        return
    if not suspended:
        raise ValueError('调整已开始剧集的预算前，请先暂停全剧连续制作')
    # The parent episode drives units in its own job; also fence independently
    # queued unit work and a planning review before altering captured limits.
    ids = {p['id'], *(item['id'] for item, _ in targets)}
    if unit_id is not None:
        from .production_execution import _guard_remote_evidence
        ids.update(e['id'] for e in selected)
        for ident in ids:
            if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running','waiting_remote')", (ident,)).fetchone():
                raise ValueError('请等待当前制作任务到达安全边界，再调整预算')
            item = store._read(db, ident)
            if (item.get('production') or {}).get('active_gpu_leases', 0):
                raise ValueError('请先结算现有 GPU 执行')
            _guard_remote_evidence(db, item)
    if any(db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='running'", (pid,)).fetchone() for pid in ids):
        raise ValueError('请等待当前制作任务到达安全边界，再调整预算')
    for item, aggregate in changed:
        current = item.get('budget', {})
        if any(budget[field] < current.get(field, budget[field]) for field in DEFAULT_BUDGET):
            raise ValueError('已开始制作的预算与尝试次数上限不能降低；原有用量和素材将保留')
        production = item.get('production') or {}
        if not aggregate:
            measured_gpu = float(production.get('gpu_seconds', 0))
            recorded_gpu = float(current.get('spent_gpu_minutes', 0)) * 60
            wall = float(production.get('elapsed_seconds', 0))
            if any(not math.isfinite(value) or value < 0 for value in (measured_gpu, recorded_gpu, wall)):
                raise ValueError('已有制作预算用量记录无效，请先核对用量')
            gpu = max(measured_gpu, recorded_gpu)
            if budget['gpu_minutes'] * 60 <= gpu or budget['wall_minutes'] * 60 <= wall:
                raise ValueError('新的预算上限必须大于累计已用时间；不能清零或覆盖已发生用量')
    authorization = {'request_id': request_id, 'actor': 'producer', 'at': store.now(),
        'budget': copy.deepcopy(budget), 'budget_scope': 'single_unit' if unit_id else 'per_unit',
        **({'unit_id': unit_id} if unit_id else {})}
    for item, _ in changed:
        # Explicit budget changes may rebind an existing grant but can never
        # add attempts. Retain its exact prior signed authorization for audit.
        from .production_continuation import grant
        from .config import production_config
        from .creative_evidence_io import digest
        for key, saved in (item.get('production_continuations') or {}).items():
            row = saved.get('authorization', {})
            checked = grant(item, production_config(item, item.get('execution_config') or {}),
                {'kind': row.get('stage'), 'key': row.get('asset'), 'signature': row.get('source_signature')})
            if checked is None or budget['max_attempts'] != item['budget']['max_attempts']:
                raise ValueError('已有镜头授权不能随预算修改增加尝试次数')
            item.setdefault('production_continuation_history', []).append(copy.deepcopy(saved))
            row = {**checked, 'budget': copy.deepcopy(budget), 'budget_authorization_id': request_id}
            item['production_continuations'][key] = {'authorization': row, 'sha256': digest(row)}
        item.setdefault('budget', {}).update(copy.deepcopy(budget))
        limits = {'max_gpu_seconds': budget['gpu_minutes'] * 60,
            'max_wall_seconds': budget['wall_minutes'] * 60, 'max_attempts': budget['max_attempts']}
        item.setdefault('production', {}).update(limits)
        execution = item.setdefault('execution_config', {})
        execution.setdefault('production', {}).update(limits)
        execution.setdefault('creative', {})['max_review_rounds'] = budget['max_attempts']
        item.setdefault('budget_authorizations', []).append(copy.deepcopy(authorization))
        # No status, receipts, attempt ledgers, plan/model identity or use
        # counters change; explicit resume is the only release of suspension.
        store._write(db, item)
    for episode in selected:
        if episode.get('unit_ids'):
            units = [store._read(db, uid) for uid in episode['unit_ids']]
            episode['total_authorized_gpu_minutes'] = sum(u['budget']['gpu_minutes'] for u in units)
            episode['total_authorized_wall_minutes'] = sum(u['budget']['wall_minutes'] for u in units)
            store._write(db, episode)
    store._event(db, p['id'], '已重新授权未完成制作单元的有界预算，保留累计用量和成功素材，等待明确恢复。',
        'budget_authorization', {'authorization': authorization, 'project_ids': [item['id'] for item, _ in changed]})


def authorize_unit_budget(db, p, payload, request_id):
    if set(payload) != {'action', 'request_id', 'expected_revision', 'unit_id', 'gpu_minutes', 'wall_minutes'}:
        raise ValueError('单元预算授权字段无效')
    if not isinstance(payload['unit_id'], str) or not re.fullmatch(r'[a-f0-9]{12}', payload['unit_id']):
        raise ValueError('请指定有效制作单元')
    automation = p.get('automation') or {}
    if not automation.get('enabled') or not automation.get('suspended') or p['status'] not in {'paused', 'blocked'}:
        raise ValueError('请先暂停已授权的全剧连续制作')
    unit = store._read(db, payload['unit_id'])
    budget = validate_budget({'gpu_minutes': payload['gpu_minutes'], 'wall_minutes': payload['wall_minutes'],
                              'max_attempts': unit['budget']['max_attempts']})
    _reauthorize_budget(db, p, {**automation, 'budget': budget}, request_id,
                       suspended=True, unit_id=unit['id'])


def enable(db, p, config, request_id):
    normalized = normalize(config, p['episode_count'])
    if not normalized['enabled']:
        raise ValueError('启用连续制作需要 enabled=true')
    if p['status'] == 'cancelled':
        raise ValueError('项目已取消，请建立新项目')
    # Retain a global pause, and all existing episode states/config/receipts.
    suspended = p.get('pause_requested', False) or p.get('automation', {}).get('suspended', False)
    _reauthorize_budget(db, p, normalized, request_id, suspended=suspended)
    p['automation'] = authorize(normalized, request_id)
    p['automation']['suspended'] = suspended
    if not _active(db, p['id']) and _plan_changed(p) and not _review_matches(p) and p.get('draft_plan') and not suspended:
        # Legacy "passed" has no binding to the current draft; actually review it.
        from .drama_planning import start_cycle
        start_cycle(p, edited=True)
        p.update(plan_review=None, plan_review_sha256=None, status='queued', stage='plan_review', error=None)
        store._enqueue(db, p['id'], 'drama_plan')
    store._event(db, p['id'], '已授权 AI 连续制作所选剧集，预算按制作单元计算，成片仍等待人工验收。',
        'automation', {'episode_numbers': normalized['episode_numbers'], 'authorization': p['automation']['authorization']})


def _dependency_attention(p, episodes):
    config = p.get('automation') or {}
    numbers = set(config.get('episode_numbers', []))
    indexed = {e['episode_number']: e for e in episodes}
    plan = (p.get('plans') or [{}])[-1].get('document') or p.get('draft_plan') or {}
    attention = []
    for planned_outline in plan.get('episodes', []):
        if planned_outline['number'] not in numbers:
            continue
        episode = indexed.get(planned_outline['number'], {})
        # Begun episodes keep their pinned inputs when the shared plan changes.
        outline = episode.get('outline', planned_outline) if episode.get('status') != 'draft' else planned_outline
        if episode.get('status') in FINAL:
            continue
        for dependency in outline['depends_on']:
            if dependency not in numbers and indexed.get(dependency, {}).get('status') not in FINAL:
                attention.append({'code': 'dependency_scope', 'episode_number': outline['number'],
                    'message': f'第{outline["number"]}集依赖第{dependency}集；该依赖未完成且不在本次授权范围，请扩展范围或先完成依赖。'})
    return attention


def state(p, episodes, *, db=None):
    config = p.get('automation') or {}
    result = {'status': 'disabled', 'stage': 'planning', 'reason': '连续制作未启用',
        'episode_numbers': config.get('episode_numbers', []), 'budget_scope': 'per_unit', 'attention': []}
    if not config.get('enabled'):
        return result
    if p['status'] == 'cancelled':
        return {**result, 'status': 'cancelled', 'reason': '项目已取消'}
    if p.get('pause_requested') or config.get('suspended'):
        return {**result, 'status': 'paused', 'reason': '连续制作已暂停，可修改要求或继续'}
    from .drama_intent import enabled as whole_drama
    if whole_drama(p):
        waiting = next((e for e in [p, *episodes] if e.get('status') == 'waiting_remote' and e.get('production', {}).get('remote_wait', {}).get('wait_kind') == 'resources' and e.get('production', {}).get('next_poll_at')), None)
        if waiting:
            return {**result, 'status': 'waiting_resources', 'stage': 'planning' if not p.get('plan_version') else 'production', 'reason': '等待当前阶段所需资源，恢复后后台自动继续', 'recovery': copy.deepcopy(waiting['production']['remote_wait'])}
    if config.get('last_error'):
        return {**result, 'status': 'blocked', 'reason': config['last_error'],
            'attention': [{'code': 'automation_error', 'message': config['last_error']}]}
    if db is not None and _active(db, p['id']):
        reviewing = p.get('plan_phase') == 'reviewing' or p.get('stage') == 'plan_review'
        return {**result, 'status': 'reviewing' if reviewing else 'planning',
            'stage': 'plan_review' if reviewing else 'planning',
            'reason': 'AI 正在独立审核规划' if reviewing else 'AI 正在编写和修订整剧规划'}
    if _plan_changed(p):
        if p['status'] in {'blocked', 'failed'} or p.get('plan_phase') == 'exhausted':
            reason = p.get('error') or '规划自动返修次数已用尽，请补充要求后重试'
            return {**result, 'status': 'blocked', 'reason': reason,
                'attention': [{'code': 'planning_blocked', 'message': reason}]}
        if not _review_matches(p):
            if not p.get('draft_plan'):
                return {**result, 'status': 'planning', 'reason': '等待 AI 生成并独立审核规划'}
            reason = '当前规划需要独立审核，尚未获得与此稿件匹配的通过记录'
            return {**result, 'status': 'blocked', 'stage': 'plan_review', 'reason': reason,
                'attention': [{'code': 'review_required', 'message': reason}]}
        if p['draft_plan'].get('uncertainties'):
            issues = [{'code': 'plan_uncertainty', 'message': issue} for issue in p['draft_plan']['uncertainties']]
            return {**result, 'status': 'blocked', 'stage': 'plan_review', 'reason': '规划存在需要确认的歧义，请补充或修改规划', 'attention': issues}
        return {**result, 'status': 'planning', 'reason': '规划审核已通过，等待执行器固化并开工'}
    selected = [e for e in episodes if e['episode_number'] in config['episode_numbers']]
    attention = _dependency_attention(p, episodes)
    for e in selected:
        if e['status'] in {'blocked', 'failed', 'cancelled'}:
            attention.append({'code': 'episode_blocked', 'episode_number': e['episode_number'],
                'message': e.get('error') or f'第{e["episode_number"]}集需要处理后继续'})
        elif e['status'] == 'paused' or e.get('pause_requested'):
            attention.append({'code': 'episode_paused', 'episode_number': e['episode_number'],
                'message': f'第{e["episode_number"]}集已暂停，可保留现有成果继续'})
    states = {e['status'] for e in selected}
    if states == {'approved'}:
        return {**result, 'status': 'completed', 'stage': 'final_review', 'reason': '所选剧集已全部人工验收'}
    if selected and states <= FINAL:
        from .drama_intent import enabled as whole_drama
        if whole_drama(p) and (p.get('delivery_bundle') or {}).get('source_signature') != fingerprint([(e.get('episode_review') or {}).get('signature') for e in selected]):
            return {**result, 'status': 'reviewing', 'stage': 'final_review', 'reason': '正在核对整剧证据并生成固定版本交付包'}
        return {**result, 'status': 'awaiting_final', 'stage': 'final_review', 'reason': '成片已就绪，等待人工验收'}
    if attention:
        return {**result, 'status': 'paused' if all(item['code'] == 'episode_paused' for item in attention) else 'blocked',
            'stage': 'production', 'reason': attention[0]['message'], 'attention': attention}
    if db is not None:
        for episode in selected:
            resource_wait = (episode.get('production') or {}).get('resource_wait') or {}
            if resource_wait.get('enabled') and resource_wait.get('status') == 'waiting' and episode['status'] in {'queued', 'waiting_remote', 'producing'}:
                return {**result, 'status': 'waiting_resources', 'stage': 'production',
                    'reason': resource_wait.get('message') or '等待制作资源，恢复后系统自动继续。',
                    'recovery': copy.deepcopy(resource_wait)}
            waiting = (episode.get('production') or {}).get('review_transport_recovery') or {}
            if episode['status'] == 'waiting_remote' and waiting.get('status') == 'waiting':
                return {**result, 'status': 'reviewing', 'stage': 'production',
                    'reason': waiting.get('message') or '审核服务暂时连接失败，系统将自动复核已保存素材',
                    'recovery': copy.deepcopy(waiting)}
            if episode.get('active_unit_id'):
                unit = store._read(db, episode['active_unit_id'])
                repair = (unit.get('production') or {}).get('recovery') or {}
                if unit['status'] != 'unit_ready' and (unit['status'] in {'blocked','failed'} or repair.get('status') == 'stopped'):
                    reason = unit.get('error') or repair.get('message') or '当前制作单元已停止，原素材和问题证据已保留'
                    return {**result, 'status': 'blocked', 'stage': 'production', 'reason': reason,
                        'recovery': copy.deepcopy(repair), 'attention': [{'code': 'unit_blocked',
                            'episode_number': episode['episode_number'], 'unit_id': unit['id'], 'message': reason}]}
                if repair.get('status') in {'diagnosing','planning','handing_off','repairing','reviewing'}:
                    return {**result, 'status': repair['status'], 'stage': 'production',
                            'reason': repair.get('message') or f'AI 正在处理第{episode["episode_number"]}集的{repair.get("asset", "镜头")}，完成后自动复核并继续',
                            'recovery': copy.deepcopy(repair)}
    return {**result, 'status': 'producing', 'stage': 'production', 'reason': 'AI 正在连续制作所选剧集，成片将等待人工验收'}


def reconcile(db, p):
    """Atomically persist reviewed confirmation and draft dispatch; never retry media.

    BEGIN IMMEDIATE belongs to the caller. Once a batch is saved, its members
    are no longer draft, making both restart recovery and repeated calls no-ops.
    """
    config = p.get('automation') or {}
    if (not config.get('enabled') or config.get('suspended') or config.get('last_error')
            or p.get('pause_requested') or p['status'] == 'cancelled' or _active(db, p['id'])):
        return
    from . import dramas
    changed = False
    if _plan_changed(p):
        if p['status'] in {'blocked', 'failed'} or not _review_matches(p) or p['draft_plan'].get('uncertainties'):
            return
        plan = dramas.validate_plan(p['draft_plan'], p)
        dramas._confirm_reviewed(db, p, plan, actor='automation', authorization=config['authorization'])
        store._event(db, p['id'], 'AI 已按启动授权固化独立审核通过的规划。', 'automation',
            {'plan_version': p['plan_version'], 'actor': 'automation', 'authorization': config['authorization']})
        changed = True
    if not p['plan_version']:
        return
    episodes = [store._read(db, eid) for eid in p['episodes']]
    drafts = [e['episode_number'] for e in episodes
        if e['episode_number'] in config['episode_numbers'] and e['status'] == 'draft' and not e.get('pause_requested')]
    if drafts:
        from .config import load_config
        batch = dramas._generate_batch(db, p, drafts, config['budget'], load_config())
        batch.update(actor='automation', authorization=copy.deepcopy(config['authorization']))
        p.update(status='producing', stage='episodes', progress=0)
        store._event(db, p['id'], '已按连续制作授权安排未开始的剧集。', 'automation',
            {'batch_id': batch['id'], 'episode_numbers': drafts})
        changed = True
    if changed:
        store._write(db, p)
    # Persist aggregation during scheduler/restart recovery, never during reads.
    if p.get('episodes'):
        previous = copy.deepcopy(p)
        dramas.aggregate(db, p)
        if p != previous:
            store._write(db, p)
    dramas.dispatch(db, p)
    from .drama_delivery import maybe_enqueue
    maybe_enqueue(db, p)


def reconcile_project(pid):
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        from .dramas import require
        p = require(db, pid)
        try:
            # Roll back a partial confirmation/batch if config validation fails.
            db.execute('SAVEPOINT automation')
            reconcile(db, p)
            db.execute('RELEASE automation')
        except Exception as exc:
            db.execute('ROLLBACK TO automation')
            db.execute('RELEASE automation')
            p = require(db, pid)
            if not p.get('automation', {}).get('enabled'):
                raise
            message = '连续制作调度暂停，请检查配置后重新启用：' + str(exc)[:500]
            p['automation']['last_error'] = message
            store._write(db, p)
            store._event(db, pid, message, 'automation_error', {'exception_type': type(exc).__name__})


def recover():
    """Run under worker.lock after queue recovery; no provider calls or retries."""
    with store.connection() as db:
        ids = [r['id'] for r in db.execute("SELECT id FROM projects WHERE json_extract(snapshot,'$.record_kind')='drama' AND json_extract(snapshot,'$.automation.enabled')=1")]
    for pid in ids:
        reconcile_project(pid)
    return len(ids)
