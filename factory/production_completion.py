"""Explicit producer authorization to finish a fixed set of existing episodes.

Removes cumulative quotas only. Accounting, pause, submission reconciliation,
per-request timeouts and independent quality review remain mandatory.
"""
import copy

from . import store
from .creative_evidence_io import digest
from .production_execution import _guard_remote_evidence

VERSION = 'ai-studio.production-completion.v1'
FIELD = 'production_completion_authorization'


def authorization(project):
    saved = project.get(FIELD)
    if saved is None:
        return None
    row = saved.get('authorization', {})
    episodes = row.get('episode_ids')
    root = project.get('drama_id') or project['id']
    episode = project.get('episode_id') or project['id']
    if (saved.get('sha256') != digest(row) or row.get('schema') != VERSION
            or row.get('actor') != 'producer' or row.get('mode') != 'until_complete'
            or row.get('drama_id') != root or not isinstance(episodes, list) or not episodes
            or any(not isinstance(e, str) or not e for e in episodes)
            or (project['id'] != root and episode not in episodes)):
        raise ValueError('持续制作授权与当前整剧及剧集范围不符')
    return copy.deepcopy(row)


def inherit(parent, child):
    if authorization(parent):
        candidate = {**child, FIELD: copy.deepcopy(parent[FIELD])}
        authorization(candidate)
        child[FIELD] = candidate[FIELD]


def authorize(db, drama, request_id):
    if drama['status'] not in {'blocked', 'paused', 'failed'}:
        raise ValueError('请等待当前制作到达安全边界')
    automation = drama.get('automation') or {}
    numbers = automation.get('episode_numbers') or []
    episodes = [store._read(db, eid) for eid in drama.get('episodes', [])]
    episodes = [e for e in episodes if e.get('episode_number') in numbers]
    if not automation.get('enabled') or not episodes or len(episodes) != len(set(numbers)):
        raise ValueError('持续制作授权必须限定到已选定的原剧集')
    if authorization(drama):
        raise ValueError('这些剧集已经获得持续制作授权')
    items = {p['id']: p for p in [drama, *episodes,
        *(store._read(db, uid) for e in episodes for uid in e.get('unit_ids', []))]}
    for p in items.values():
        if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running','waiting_remote')", (p['id'],)).fetchone():
            raise ValueError('请等待现有制作任务到达安全边界')
        _guard_remote_evidence(db, p)
    row = {'schema': VERSION, 'actor': 'producer', 'request_id': request_id,
           'at': store.now(), 'mode': 'until_complete', 'drama_id': drama['id'],
           'episode_ids': [e['id'] for e in episodes], 'episode_numbers': sorted(numbers),
           'limits': {'gpu_minutes': None, 'wall_minutes': None, 'attempts': None, 'repair_plans': None},
           'preserves': ['story', 'dialogue', 'closed_mouth', 'quality_review', 'media', 'usage', 'attempts']}
    saved = {'authorization': row, 'sha256': digest(row)}
    for p in items.values():
        p[FIELD] = copy.deepcopy(saved)
        authorization(p)
        if p is not drama:
            store._write(db, p)
    store._event(db, drama['id'], '已授权原选定剧集持续制作：不限制累计 GPU、制作时长、尝试次数和返修方案；保留剧情、质量审核、历史素材与真实用量。',
                 'production_completion_authorized', row)
