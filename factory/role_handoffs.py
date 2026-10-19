"""Controller-owned, content-addressed handoffs; models cannot issue operations.

Each project's journal is independent of the work queue. Acceptance records the
controller receiving exact material, not model comprehension or human approval.
Old projects are read without creating a journal or inventing past transfers.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path
import re
import sqlite3
import time

from .creative_evidence_io import canonical, digest

SCHEMA = "ai-studio.role-handoffs.v1"
VERSIONS = ("workflow_version", "script_generation", "proposal_generation", "style_generation",
            "production_revision", "plan_version", "plan_cycle", "asset_version")
_bound = ContextVar("controlled_handoffs", default=())
_KINDS = {"episode_direction", "episode_review", "world_outline", "outline_review", "review_feedback", "visual_design"}
_IDENTITY = ('project_id', 'version', 'source', 'target', 'source_stage', 'target_stage',
             'contract', 'scope', 'content_sha256', 'approval_sha256', 'source_sha256', 'resolves')


def version(project):
    return {key: copy.deepcopy(project[key]) for key in VERSIONS if key in project}


def _path(folder):
    folder = Path(folder)
    if ".." in folder.parts or any(p.is_symlink() for p in (folder, *folder.parents)):
        raise ValueError("交接资料路径不能包含软链接或父目录跳转")
    path = folder / "handoffs.sqlite3"
    if path.is_symlink():
        raise ValueError("交接记录不能是软链接")
    return path


@contextmanager
def _db(folder, write=False):
    path = _path(folder)
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.is_file():
        yield None
        return
    connection = sqlite3.connect(str(path) if write else 'file:' + str(path) + '?mode=ro',
                                 uri=not write, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        if write:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("CREATE TABLE IF NOT EXISTS handoffs(id TEXT PRIMARY KEY, updated REAL NOT NULL, record TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, updated REAL NOT NULL, record TEXT NOT NULL)")
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
        yield connection
        if write:
            connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _load(db, table, ident):
    if not isinstance(ident, str) or not re.fullmatch(r"[a-f0-9]{32}", ident):
        raise ValueError("交接编号无效")
    row = db.execute(f"SELECT record FROM {table} WHERE id=?", (ident,)).fetchone() if db else None
    if row is None:
        raise KeyError("交接记录不存在")
    result = json.loads(row[0])
    if result.get('id') != ident:
        raise ValueError("交接记录身份校验失败")
    if table == 'handoffs':
        if (digest({k: result[k] for k in _IDENTITY})[:32] != ident
                or digest(result['value']) != result['content_sha256']
                or digest(result['approval']) != result['approval_sha256']
                or ('return_context' in result and digest(result['return_context']) != result['return_context_sha256'])):
            raise ValueError("交接内容校验失败，原资料已保留")
    elif digest({k: result[k] for k in ('project_id', 'version', 'scope', 'handoff_id', 'source', 'target', 'issues')})[:32] != ident:
        raise ValueError("补料请求校验失败，原资料已保留")
    return result


def _save(db, table, row):
    raw = canonical(row).decode()
    if len(raw.encode()) > 2 * 1024 * 1024:
        raise ValueError("交接材料过大，请拆分交付")
    db.execute(f"INSERT INTO {table}(id,updated,record) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,record=excluded.record",
               (row['id'], time.time(), raw))


def _event(row, action, **details):
    event = {'action': action, **details}
    if not any(all(item.get(k) == v for k, v in event.items()) for item in row['events']):
        row['events'].append({**event, 'at': time.time()})


def _check_current(project, row):
    if row['project_id'] != project.get('id') or row['version'] != version(project):
        raise ValueError("交接资料版本已变化，不能用于当前任务")
    # Reuse existing leases/locks when this is a real controlled worker run.
    from .execution_context import current_execution
    context = current_execution.get() or {}
    if context.get('project_id') == project.get('id'):
        from . import store
        live = store.get(project['id'], False)
        if version(live) != row['version']:
            raise ValueError("任务期间项目版本已变化，旧交接不能发布")
        if live.get('pause_requested') or live.get('status') == 'paused':
            from .scheduler import ProductionPaused
            raise ProductionPaused('交接已停在安全边界，等待恢复')
        if live.get('status') in {'cancelled', 'approved'}:
            raise ValueError('终态项目不能继续提交交接')
        task = context.get('task_id', '')
        if ':' in task:
            from .team_control import admit, assert_publishable
            kind, scope = task.split(':', 1)
            admit(project['id'], kind, scope)
            assert_publishable(project['id'], kind, scope)


def _issues(contract, value, scope, approval):
    result = []
    def problem(code, description, ids=()):
        result.append({'code': code, 'severity': 'blocking', 'description': description,
                       'suggestion': '仅补齐指定材料，保留范围外已批准内容，再提交分镜及独立复审。',
                       'shot_ids': list(ids), 'evidence': '控制层交接结构校验'})
    if not isinstance(value, dict):
        problem('handoff_structure', '交接内容必须是结构化对象')
        return result
    if contract in {'episode_direction', 'episode_review'}:
        shots = value.get('shots')
        if not isinstance(shots, list) or not shots or any(not isinstance(s, dict) for s in shots):
            problem('handoff_shots', '交接剧本缺少镜头数组')
            return result
        ids = [s.get('id') for s in shots]
        if any(not isinstance(i, str) or not re.fullmatch(r'S\d{2,3}', i) for i in ids) or len(set(ids)) != len(ids):
            problem('handoff_shot_ids', '镜头编号必须唯一且有效')
            return result
        if not set(scope) <= set(ids):
            problem('handoff_scope', '交接范围包含当前稿件不存在的镜头')
        if contract == 'episode_direction':
            for shot in shots:
                if (not scope or shot['id'] in scope) and not str(shot.get('action') or '').strip():
                    problem('missing_shot_action', '分镜缺少可依据的逐镜动作：' + shot['id'], [shot['id']])
    elif contract == 'world_outline':
        if not value.get('world') or not value.get('characters') or not value.get('visual_rules'):
            problem('handoff_world', '全剧设定缺少世界、角色或画风要求')
    elif contract == 'outline_review':
        if not isinstance(value.get('episodes'), list) or not value['episodes']:
            problem('handoff_outlines', '分集交付缺少本组大纲')
    elif contract == 'review_feedback':
        if not isinstance(value.get('issues'), list) or (not value['issues'] and not value.get('scores')):
            problem('handoff_feedback', '返修交付缺少具体问题')
    elif contract == 'visual_design':
        from .schemas import review_passes
        if not isinstance(approval, dict) or approval.get('passed') is not True or not review_passes(approval):
            problem('handoff_approval', '剧本尚未通过当前独立审核，不能交付视觉设计')
        if not value.get('title') or not value.get('cast'):
            problem('handoff_design', '视觉设计交付缺少剧名或角色设定')
    return result


def submit(folder, project, *, source, target, source_stage, target_stage, contract, value,
           scope=(), approval=None, source_sha256=None, resolves=None):
    from .config import ROLE_LABELS
    from .team_flow import ENGINE_LABELS
    roles = ROLE_LABELS.keys() | ENGINE_LABELS.keys()
    if source not in roles or target not in roles or contract not in _KINDS:
        raise ValueError("交接只能使用控制层已定义的岗位与合约")
    if (not isinstance(scope, (list, tuple)) or len(scope) > 150
            or any(not isinstance(s, str) or not re.fullmatch(r'S\d{2,3}', s) for s in scope)
            or len(set(scope)) != len(scope)):
        raise ValueError("交接范围必须是不重复的镜头编号")
    identity = {'project_id': project['id'], 'version': version(project), 'source': source,
                'target': target, 'source_stage': source_stage, 'target_stage': target_stage,
                'contract': contract, 'scope': list(scope), 'content_sha256': digest(value),
                'approval_sha256': digest(approval), 'source_sha256': source_sha256 or digest(value), 'resolves': resolves}
    ident = digest(identity)[:32]
    _check_current(project, identity)
    with _db(folder, True) as db:
        try:
            existing = _load(db, 'handoffs', ident)
            if any(existing.get(k) != v for k, v in identity.items()):
                raise ValueError("交接身份与冻结记录不一致")
            return existing
        except KeyError:
            pass
        if resolves:
            request = _load(db, 'requests', resolves)
            _check_current(project, request)
            if request['source'] != target or request['target'] != source or set(scope) != set(request['scope']):
                raise ValueError("补料只能回到原请求岗位及原镜头范围")
        record = {**identity, 'id': ident, 'schema': SCHEMA, 'state': 'submitted',
                  'source_sha256': source_sha256 or digest(value), 'value': copy.deepcopy(value),
                  'approval': copy.deepcopy(approval), 'events': [], 'created_at': time.time()}
        _event(record, 'submitted')
        _save(db, 'handoffs', record)
        return record


def _return(db, record, issues):
    identity = {key: record[key] for key in ('project_id', 'version', 'scope')}
    scope = list(dict.fromkeys(sid for issue in issues for sid in issue.get('shot_ids', []))) or record['scope']
    identity.update(handoff_id=record['id'], source=record['target'], target=record['source'],
                    scope=scope, issues=copy.deepcopy(issues))
    ident = digest(identity)[:32]
    try:
        request = _load(db, 'requests', ident)
    except KeyError:
        request = {**identity, 'id': ident, 'schema': SCHEMA, 'state': 'requested',
                   'events': [], 'created_at': time.time()}
        _event(request, 'requested')
        _save(db, 'requests', request)
    record.update(state='returned', request_id=ident)
    _event(record, 'returned', request_id=ident)
    _save(db, 'handoffs', record)
    return request


def receive(folder, project, ident, *, role, stage, value):
    with _db(folder, True) as db:
        record = _load(db, 'handoffs', ident)
        _check_current(project, record)
        if record['target'] != role or record['target_stage'] != stage or digest(value) != record['content_sha256']:
            raise ValueError("接收岗位、阶段或实际输入与交接单不一致")
        if record['state'] == 'returned':
            return {'accepted': False, 'record': record, 'request': _load(db, 'requests', record['request_id'])}
        problems = _issues(record['contract'], value, record['scope'], record['approval'])
        if problems:
            request = _return(db, record, problems)
            return {'accepted': False, 'record': record, 'request': request}
        record['state'] = 'accepted'
        _event(record, 'accepted', meaning='controller_received_exact_material_not_quality_approval')
        _save(db, 'handoffs', record)
        if record.get('resolves'):
            request = _load(db, 'requests', record['resolves'])
            request.update(state='supplemented', replacement_id=ident)
            _event(request, 'supplemented', replacement_id=ident)
            _save(db, 'requests', request)
        return {'accepted': True, 'record': record, 'request': None}


def return_for_supplement(folder, project, ident, issues):
    with _db(folder, True) as db:
        record = _load(db, 'handoffs', ident)
        _check_current(project, record)
        allowed = set(record['scope']) or {s['id'] for s in record['value'].get('shots', [])}
        if not issues or any(not set(i.get('shot_ids', [])) <= allowed for i in issues):
            raise ValueError("补料要求不能扩展交接范围")
        return _return(db, record, issues)


def remember_return(folder, project, ident, result):
    """Replay the same partial director result after interruption, without I/O to models."""
    with _db(folder, True) as db:
        record = _load(db, 'handoffs', ident)
        _check_current(project, record)
        if record['state'] != 'returned':
            raise ValueError("仅能记录真实退回结果")
        if 'return_context' in record and digest(result) != record['return_context_sha256']:
            raise ValueError("退回结果已冻结，不能覆盖")
        record.update(return_context=copy.deepcopy(result), return_context_sha256=digest(result))
        _save(db, 'handoffs', record)


@contextmanager
def bind(folder, record):
    token = _bound.set((*_bound.get(), (str(folder), record['id'])))
    try:
        yield
    finally:
        _bound.reset(token)


def current():
    return [ident for _, ident in _bound.get()]


def observe_call(receipt, *, reused=False):
    for folder, ident in _bound.get():
        with _db(folder, True) as db:
            record = _load(db, 'handoffs', ident)
            if reused:
                _event(record, 'cached_result_reused', origin_call_id=(receipt or {}).get('call_id'))
            elif receipt.get('call_id') and receipt.get('status') in {'succeeded', 'failed'}:
                _event(record, 'call_recorded', call_id=receipt['call_id'], status=receipt['status'],
                       delivery=(receipt.get('context_manifest') or {}).get('delivery', 'unknown'))
            _save(db, 'handoffs', record)


def _public(row, project, *, full=False):
    keys = ('id', 'schema', 'project_id', 'version', 'source', 'target', 'source_stage', 'target_stage',
            'contract', 'scope', 'content_sha256', 'source_sha256', 'approval_sha256', 'state',
            'resolves', 'request_id', 'handoff_id', 'replacement_id', 'issues', 'events', 'created_at')
    result = {k: copy.deepcopy(row[k]) for k in keys if k in row}
    result['current_version'] = row['version'] == version(project)
    if full:
        for key in ('value', 'approval'):
            if key in row:
                result[key] = copy.deepcopy(row[key])
    else:
        result['event_count'] = len(result.get('events', []))
        result['events'] = result.get('events', [])[-30:]
    return result


def snapshot(folder, project, *, offset=0, limit=30):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 60:
        raise ValueError("交接分页无效")
    with _db(folder) as db:
        if db is None:
            return {'schema': SCHEMA, 'history_state': 'not_recorded', 'handoffs': [], 'requests': [], 'total': 0}
        total = db.execute('SELECT count(*) FROM handoffs').fetchone()[0]
        ids = [r[0] for r in db.execute('SELECT id FROM handoffs ORDER BY updated DESC,id LIMIT ? OFFSET ?', (limit, offset))]
        records = [_load(db, 'handoffs', ident) for ident in ids]
        if any(r['project_id'] != project['id'] for r in records):
            raise ValueError("交接不属于当前项目")
        request_ids = {r.get('request_id') for r in records if r.get('request_id')} | {r.get('resolves') for r in records if r.get('resolves')}
        return {'schema': SCHEMA, 'history_state': 'recorded', 'total': total, 'offset': offset, 'limit': limit,
                'handoffs': [_public(r, project) for r in records],
                'requests': [_public(_load(db, 'requests', i), project) for i in sorted(request_ids)]}


def detail(folder, project, ident):
    with _db(folder) as db:
        row = _load(db, 'handoffs', ident)
        if row['project_id'] != project['id']:
            raise ValueError("交接不属于当前项目")
        return _public(row, project, full=True)
