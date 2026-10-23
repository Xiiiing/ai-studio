"""Explicit, one-time asset continuation; historical quotas never reset."""
from __future__ import annotations

import copy
import re

from . import store
from .creative_evidence_io import digest, read_json
from .production_execution import attempt_limit, strategy_enabled, _guard_remote_evidence
from studio import core

VERSION = 'ai-studio.asset-continuation.v1'


def grant(project, config, target):
    """Return an applicable producer grant, never infer one from retry/resume."""
    if not target or not strategy_enabled(config):
        return None
    saved = (project.get('production_continuations') or {}).get(target['kind'] + ':' + target['key'])
    if not saved:
        return None
    row = saved.get('authorization', {})
    budget = {k: project.get('budget', {}).get(k) for k in ('gpu_minutes', 'wall_minutes', 'max_attempts')}
    if (saved.get('sha256') != digest(row) or row.get('schema') != VERSION
            or row.get('actor') != 'producer' or row.get('project_id') != project['id']
            or row.get('asset') != target['key'] or row.get('stage') != target['kind']
            or row.get('source_signature') != target.get('signature')
            or row.get('budget') != budget or row.get('base_attempt_limit') != attempt_limit(config)
            or type(row.get('extra_attempts')) is not int or row['extra_attempts'] != 2
            or row.get('base_attempt_limit') != 5 or row.get('lifetime_attempt_limit') != 7):
        raise ValueError('镜头追加授权与当前来源、预算或执行版本不符')
    return copy.deepcopy(row)


def ceiling(project, config, target):
    authorized = grant(project, config, target)
    return authorized['lifetime_attempt_limit'] if authorized else attempt_limit(config)


def authorize(db, drama, payload, request_id):
    """Authorize exactly two new strategies for one exhausted video asset.

    Leaves all snapshots, budgets, usage, media, attempts and jobs unchanged.
    The normal resume action is still required after this transaction.
    """
    ids = payload.get('shot_ids')
    uid = payload.get('unit_id')
    if not isinstance(uid, str) or not re.fullmatch(r'[a-f0-9]{12}', uid):
        raise ValueError('追加返修授权必须指定有效制作单元')
    if (not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', ids[0])):
        raise ValueError('追加返修授权必须指定一个镜头')
    episodes = [store._read(db, eid) for eid in drama.get('episodes', [])]
    selected = set((drama.get('automation') or {}).get('episode_numbers', []))
    owners = [e for e in episodes if e.get('episode_number') in selected
              and uid in [e['id'], *e.get('unit_ids', [])]]
    if len(owners) != 1 or not drama.get('automation', {}).get('enabled'):
        raise ValueError('制作单元不在已授权的整剧范围内')
    project = store._read(db, uid)
    key = 'video:' + ids[0]
    if key in (project.get('production_continuations') or {}):
        raise ValueError('该镜头已经追加过授权，不能刷新尝试次数')
    if drama['status'] != 'blocked' or project['status'] != 'blocked':
        raise ValueError('仅可在制作停止后追加镜头授权')
    for item in {p['id']: p for p in [drama, owners[0], project]}.values():
        if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running','waiting_remote')", (item['id'],)).fetchone():
            raise ValueError('请等待现有制作任务到达安全边界')
        _guard_remote_evidence(db, item)
    need = project.get('needs_decision') or {}
    detail = need.get('details') or {}
    if need.get('code') != 'retry_limit' or detail.get('asset') != ids[0] or detail.get('stage') != 'video':
        raise ValueError('当前阻塞不是该视频镜头的尝试额度')
    from .config import production_config
    config = production_config(project, project.get('execution_config') or {})
    if not strategy_enabled(config) or attempt_limit(config) != 5:
        raise ValueError('追加授权仅适用于已用完五次的自主返修镜头')
    folder = core.ROOT / 'production' / ('factory-' + uid) / 'renders' / ids[0]
    if any(p.is_symlink() for p in [folder, *folder.parents]):
        raise ValueError('不能使用链接目录中的返修证据')
    records = [(p, read_json(p)) for p in sorted(folder.glob('take_*/attempt.json'))]
    if not records:
        raise ValueError('缺少原尝试证据')
    latest_path, latest = records[-1]
    signature = latest.get('source_signature')
    lineage = (project.get('production', {}).get('recovery_lineage') or {}).get(key, [])
    counted = [r for _, r in records if r.get('stage') == 'video' and r.get('state') != 'prompt_preparing'
               and (r.get('source_signature') == signature or r.get('source_signature') in lineage or r.get('production_strategy'))]
    if (len(counted) != 5 or latest.get('attempt') != 5 or latest.get('state') != 'failed'
            or latest.get('qa', {}).get('status') != 'fail' or not isinstance(signature, str) or not signature):
        raise ValueError('原五次尝试及独立失败审核无法核对')
    from .production_recovery import _gate, _CLOSED
    shot = next((s for s in project.get('episode', {}).get('shots', []) if s.get('id') == ids[0]), None)
    if not shot or not _CLOSED.search(latest.get('prompt', '')):
        raise ValueError('追加定向策略要求原有闭口动作契约')
    # Check the remaining budget without pretending the old attempt was unused.
    reason = _gate(project, config, {**latest, 'attempt': 0})
    if reason:
        raise ValueError('当前预算或提交状态不能继续：' + reason)
    budget = {k: project['budget'][k] for k in ('gpu_minutes', 'wall_minutes', 'max_attempts')}
    row = {'schema': VERSION, 'request_id': request_id, 'actor': 'producer', 'at': store.now(),
           'drama_id': drama['id'], 'project_id': uid, 'asset': ids[0], 'stage': 'video',
           'source_signature': signature, 'budget': budget, 'base_attempt_limit': 5,
           'extra_attempts': 2, 'lifetime_attempt_limit': 7,
           'failed_attempt_path': str(latest_path.relative_to(core.ROOT)),
           'failed_attempt_sha256': core.sha256(latest_path),
           'preserves': ['story', 'dialogue', 'closed_mouth', 'quality_review', 'media', 'usage', 'attempts']}
    saved = {'sha256': digest(row), 'authorization': row}
    project.setdefault('production_continuations', {})[key] = saved
    store._write(db, project)
    drama.setdefault('asset_continuation_authorizations', []).append(copy.deepcopy(saved))
    store._event(db, drama['id'], '已为指定镜头追加最多两次定向返修；累计七次，原 GPU/时限/质量要求不变，等待恢复。',
                 'asset_continuation_authorized', row)
