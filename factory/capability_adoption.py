"""Explicit project capability adoption under existing mutation guards."""
from __future__ import annotations
from . import store


def snapshot(pid):
    import copy
    from .production_edits import require_idle, require_resolved_cloud
    from .review_advice import ensure_no_gpu_intents
    with store.connection() as db:
        project = store._read(db, pid)
        reason = None
        try:
            require_idle(db, project)
            shots = project.get('episode') or project.get('script') or {}
            require_resolved_cloud(db, pid, [s['id'] for s in shots.get('shots', [])])
            ensure_no_gpu_intents(project)
        except (ValueError, OSError) as exc:
            reason = str(exc)
    return {'revision': project['revision'], 'enabled': project.get('model_capability_snapshot') is not None,
            'snapshot': copy.deepcopy(project.get('model_capability_snapshot')),
            'history': copy.deepcopy(project.get('model_capability_history', [])),
            'can_adopt': reason is None, 'blocked_reason': reason}


def adopt(pid, payload):
    from .config import load_config
    from .model_capabilities import adopt as apply
    from .production_edits import require_idle, require_resolved_cloud
    from .review_advice import ensure_no_gpu_intents
    from .dramas import cas, replay
    if set(payload) != {'expected_revision', 'request_id', 'confirmed'} or payload.get('confirmed') is not True:
        raise ValueError('更新模型能力定义须明确确认当前项目版本')
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        project = store._read(db, pid)
        key, signature, previous = replay(project, payload)
        if previous:
            return project
        cas(project, payload)
        require_idle(db, project)
        shots = project.get('episode') or project.get('script') or {}
        require_resolved_cloud(db, pid, [s['id'] for s in shots.get('shots', [])])
        ensure_no_gpu_intents(project)
        apply(project, project.get('execution_config') or load_config())
        project.setdefault('operation_requests', {})[key] = {'signature': signature}
        store._write(db, project)
        return project
