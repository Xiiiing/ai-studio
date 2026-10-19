"""Explicit execution snapshots for system-owned, bounded production repair."""
from __future__ import annotations

import copy
import math
import re

from . import store
from .schemas import fingerprint
from .creative_evidence_io import read_json
from studio import core

VERSION = 'ai-studio.production-recovery.v1'
DEFAULT_POLICY = {'version': VERSION, 'enabled': True, 'max_asset_cycles': 2, 'max_project_cycles': 8}


def enabled(configuration):
    policy = configuration.get('production_recovery', {})
    return isinstance(policy, dict) and policy.get('enabled') is True and policy.get('version') == VERSION


def apply_configuration(configuration, policy):
    result = copy.deepcopy(configuration)
    result.setdefault('production', {})['production_recovery'] = copy.deepcopy(policy)
    return result


def _verified_media(folder, record):
    try:
        path = core.within(core.ROOT, record['path'])
        return path.is_relative_to(folder) and core.sha256(path) == record['sha256']
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _verified_seconds(seconds):
    return (not isinstance(seconds, bool) and isinstance(seconds, (int, float))
            and math.isfinite(seconds) and 0 <= seconds <= 604800 * 8)


def _guard_remote_evidence(db, project):
    """Read only: a dead business lease is not proof of a terminal provider job."""
    message = '请先对账现有远端请求，不能跨未知提交切换执行版本'
    table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cloud_tasks'").fetchone()
    if table and db.execute("SELECT 1 FROM cloud_tasks WHERE project_id=? AND status NOT IN ('downloaded','failed','rejected')", (project['id'],)).fetchone():
        raise ValueError(message)
    slug = project.get('slug') or 'factory-' + project['id']
    if slug != 'factory-' + project['id'] or not re.fullmatch(r'factory-[A-Za-z0-9_-]{1,80}', slug):
        raise ValueError(message)
    raw_folder = core.ROOT / 'production' / slug
    if any(path.is_symlink() for path in (raw_folder, *raw_folder.parents)):
        raise ValueError(message)
    folder = core.within(core.ROOT / 'production', slug)
    if not folder.exists():
        journals = []
    else:
        journals = sorted(folder.rglob('submission.json')) + sorted(folder.rglob('remote_task.json'))
    verified_jobs = set()
    try:
        for journal in journals:
            if any(path.is_symlink() for path in (journal, *journal.parents)):
                raise ValueError(message)
            state = read_json(journal, maximum=32 * 1024 * 1024)
            if journal.name == 'remote_task.json':
                if state.get('status') not in {'downloaded', 'failed', 'rejected'}:
                    raise ValueError(message)
                if state.get('status') == 'downloaded':
                    result = read_json(journal.parent / 'result.json')
                    if not _verified_media(folder, result):
                        raise ValueError(message)
                continue
            ident = state.get('gpu_service_job_id')
            if ident:
                if not isinstance(ident, str) or not re.fullmatch(r'[a-f0-9]{64}', ident):
                    raise ValueError(message)
                terminal_path = journal.parent / 'remote_result.json'
                if terminal_path.exists():
                    terminal = read_json(terminal_path)
                    receipt = (terminal.get('result') or {}).get('receipt') or terminal.get('receipt') or {}
                    if (terminal.get('id') != ident or terminal.get('status') not in {'completed', 'failed'}
                            or receipt.get('usage_final') is False
                            or not _verified_seconds(receipt.get('gpu_seconds'))):
                        raise ValueError(message)
                else:
                    # A downloaded artifact receipt also proves completion;
                    # an old "rendering" display must not hide verified output.
                    result = read_json(journal.parent / 'result.json')
                    if (result.get('gpu_service_job_id') != ident or not _verified_seconds(result.get('gpu_seconds'))
                            or not _verified_media(folder, result)):
                        raise ValueError(message)
                verified_jobs.add(ident)
                continue
            # Legacy/external Comfy journals can retain "accepted" after the
            # exact output was downloaded. Verify its bytes and prompt identity.
            result_path = journal.parent / 'result.json'
            if result_path.exists():
                result = read_json(result_path)
                if (state.get('prompt_id') and result.get('prompt_id') == state['prompt_id']
                        and _verified_media(folder, result)):
                    continue
            history_path = journal.parent / 'history.json'
            if history_path.exists():
                history = read_json(history_path)
                if state.get('prompt_id') and history.get('status', {}).get('status_str') == 'error':
                    continue
            usage = (state.get('owned_usage') or {}).get('execution_seconds')
            if (state.get('state') == 'cancelled' and state.get('prompt_id')
                    and state.get('stop_confirmed_at') and state.get('owned_queue_state') == 'absent'
                    and _verified_seconds(usage)):
                continue
            raise ValueError(message)
        hint = (project.get('production') or {}).get('remote_gpu_execution') or {}
        if hint.get('status') in {'waiting', 'running', 'dispatching', 'unresolved', 'unknown'} and hint.get('job_id') not in verified_jobs:
            raise ValueError(message)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValueError(message) from exc


def activate(db, drama, request_id):
    if not drama.get('automation', {}).get('enabled'):
        raise ValueError('请先授权连续制作范围和预算')
    if not drama.get('pause_requested') or not drama['automation'].get('suspended'):
        raise ValueError('请先暂停制作，在安全边界应用自主返修版本')
    episodes = [store._read(db, eid) for eid in drama.get('episodes', [])]
    units = [store._read(db, uid) for episode in episodes for uid in episode.get('unit_ids', [])]
    records = [drama, *episodes, *units]
    if any(db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running','waiting_remote')", (p['id'],)).fetchone() for p in records):
        raise ValueError('请等待现有任务到达安全边界；升级不会中断或重投模型')
    for p in records:
        production = p.get('production') or {}
        if production.get('active_gpu_leases', 0) or (production.get('resource_wait') or {}).get('status') in {'checking', 'waiting'}:
            raise ValueError('请先对账现有远端请求，不能跨未知提交切换执行版本')
        _guard_remote_evidence(db, p)
    policy = copy.deepcopy(DEFAULT_POLICY)
    authorization = {'request_id': request_id, 'actor': 'producer', 'at': store.now(),
                     'policy': policy, 'scope': 'unfinished_authorized_episodes_and_units',
                     'budget_and_attempt_limits_preserved': True}
    selected = set(drama['automation']['episode_numbers'])
    selected_episodes = [p for p in episodes if p['episode_number'] in selected]
    selected_units = {uid for p in selected_episodes for uid in p.get('unit_ids', [])}
    for p in [*selected_episodes, *(u for u in units if u['id'] in selected_units)]:
        if p['status'] in {'awaiting_final', 'approved', 'unit_ready', 'cancelled'}:
            continue
        if p.get('execution_config'):
            before = fingerprint(p['execution_config'])
            p['execution_config'] = apply_configuration(p['execution_config'], policy)
            change = {**authorization, 'before_config_sha256': before,
                      'after_config_sha256': fingerprint(p['execution_config'])}
            p.setdefault('execution_config_changes', []).append(change)
            store._write(db, p)
    drama['automation']['production_recovery'] = policy
    drama.setdefault('execution_policy_authorizations', []).append(authorization)
    store._event(db, drama['id'], '已应用系统自主返修版本；保留预算、累计费用、尝试历史和有效成果，等待恢复制作。',
                 'execution_policy_applied', authorization)
