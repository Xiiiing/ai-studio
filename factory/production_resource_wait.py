"""Authorized, read-only resource polling before any workflow/model execution.

The worker never starts, stops, swaps or warms a model here. Discovery uses GET
and the frozen configuration; resource waiting grants no production budget.
"""
from __future__ import annotations

import copy
import ssl
import time
import urllib.error

from . import store
from .config import resolve_llm
from .providers import ProviderError, check_model
from .scheduler import ProductionNeedsDecision, ProductionPaused
from .remote_tasks import ProductionDeferred, defer_job

POLL_SECONDS = 300
_REASON = 'model_resources_unavailable'
_MESSAGE = '等待 Wkp 制作资源，恢复后系统自动继续。'
_ROLES = {'screenwriter', 'storyboard_director', 'visual_critic', 'independent_critic', 'producer', 'edit_director'}


def _temporary(exc):
    if isinstance(exc, ssl.SSLCertVerificationError):
        return False
    if isinstance(exc, ProviderError):
        return exc.code in {'model_missing', 'transport_failed', 'dns_busy', 'http_429', 'http_500', 'http_502', 'http_503', 'http_504'}
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {429, 500, 502, 503, 504}
    if isinstance(exc, urllib.error.URLError):
        return _temporary(exc.reason)
    if isinstance(exc, ProductionPaused) and exc.reason == 'remote_gpu_unavailable':
        return exc.__cause__ is None or _temporary(exc.__cause__)
    return isinstance(exc, (TimeoutError, ConnectionError, OSError)) and not isinstance(exc, ssl.SSLError)


def probe(configuration):
    """Return capability availability using only bounded, authenticated GETs."""
    from .gpu_client import GPUClient
    from .video_providers import resolve_video
    from studio.h3 import backend_ready
    if not isinstance(configuration, dict) or not configuration:
        raise ProductionNeedsDecision('制作资源等待缺少冻结配置，未调用模型', 'resource_wait_configuration')
    checked, missing = set(), []
    routes = configuration.get('llm_roles') or {}
    if not isinstance(routes, dict):
        raise ProductionNeedsDecision('制作岗位路由配置无效，资源等待已停止', 'resource_wait_configuration')
    try:
        for role in sorted(_ROLES | set(routes)):
            model = resolve_llm(configuration, role, require_vision=role == 'visual_critic')
            if (model.get('enabled') is False or not model.get('base_url') or not model.get('model')
                    or model.get('model_discovery') == 'skip'):
                raise ValueError('Frozen model discovery must verify an explicit served model')
            from .schemas import fingerprint
            key = fingerprint(model)
            if key in checked:
                continue
            checked.add(key)
            try:
                check_model(model, timeout=5)
            except Exception as exc:
                if not _temporary(exc):
                    raise
                missing.append({'kind': 'model', 'role': role, 'reason': getattr(exc, 'code', 'unavailable')})
        production = configuration.get('production') or {}
        gpu = production.get('gpu_service')
        if not gpu:
            raise ValueError('Resource waiting requires the configured Wkp inference service')
        try:
            health = GPUClient(gpu).request('/health', timeout=5)
            if (health.get('service') != 'gpu_inference' or health.get('workflow_execution') is not False
                    or health.get('ok') is not True):
                raise ValueError('Unexpected inference health contract')
            from .media_profiles import resolve
            image = resolve(configuration, 'image')
            audio = resolve(configuration, 'audio')
            if image.get('provider') == 'legacy':
                if health.get('models', {}).get('image') is not True:
                    missing.append({'kind': 'image', 'reason': 'configured_image_backend_unavailable'})
            elif image.get('provider') == 'comfyui_qwen_image21':
                if health.get('image_backends', {}).get('comfyui_qwen_image21', {}).get('installed') is not True:
                    missing.append({'kind': 'image', 'reason': 'configured_image_backend_unavailable'})
            else:
                raise ValueError('Resource waiting cannot verify the configured image backend')
            if audio.get('provider') in {'legacy', 'qwen_tts_local'} and health.get('models', {}).get('audio') is not True:
                missing.append({'kind': 'audio', 'reason': 'configured_audio_backend_unavailable'})
            video = resolve_video(configuration)
            if video.get('provider') == 'comfyui_h3':
                backend = health.get('video_backends', {}).get('comfyui_h3', {})
                if backend.get('execution') != 'resident_comfy' or not backend_ready(backend, health.get('gpu_cards', []), video):
                    missing.append({'kind': 'video', 'reason': 'resident_h3_unavailable'})
            elif video.get('provider') not in {'minimax'}:
                raise ValueError('Resource waiting cannot verify this video execution backend without starting models')
        except Exception as exc:
            if not _temporary(exc):
                raise
            missing.append({'kind': 'gpu_service', 'reason': 'unavailable'})
    except (ProductionNeedsDecision, ProductionPaused):
        raise
    except Exception as exc:
        # No private endpoint, certificate paths or provider error text is copied
        # into public status. Invalid credentials/configuration must be repaired.
        raise ProductionNeedsDecision('制作资源预检的配置、鉴权或协议无效，等待已停止；请核对冻结连接配置。',
                                      'resource_wait_configuration', {'error_type': type(exc).__name__}) from exc
    return {'ready': not missing, 'missing': missing, 'checked_models': len(checked),
            'scope': 'served_model_catalog_and_resident_backend_capabilities_not_inference'}


def _records(db, project):
    records = [project]
    if project.get('episode_id'):
        records.append(store._read(db, project['episode_id']))
    if project.get('drama_id'):
        records.append(store._read(db, project['drama_id']))
    return records


def before_execution(job):
    """Defer the same owned job, or resolve the grant and execute normally."""
    with store.connection() as db:
        owner = db.execute('SELECT status,lease FROM jobs WHERE id=?', (job['id'],)).fetchone()
        if not owner or owner['status'] != 'running' or owner['lease'] != job.get('lease'):
            return True
        project = store._read(db, job['project_id'])
        records = _records(db, project)
        holder = next((p for p in records if isinstance((p.get('production') or {}).get('resource_wait'), dict)
            and p['production']['resource_wait'].get('enabled') is True
            and p['production']['resource_wait'].get('status') in {'waiting', 'resolved'}), None)
        if holder is None:
            return False
        if any(p.get('pause_requested') or p.get('status') in {'paused', 'cancelled'}
               or p.get('automation', {}).get('suspended') for p in records):
            raise ProductionPaused('制作已暂停，资源等待未触发任何模型操作')
        marker = copy.deepcopy(holder['production']['resource_wait'])
        if (marker.get('authorized_by') != 'producer' or not isinstance(marker.get('authorization_id'), str)
                or not marker['authorization_id'] or marker.get('reason') != _REASON
                or marker.get('poll_seconds') != POLL_SECONDS):
            raise ProductionNeedsDecision('资源等待授权记录无效，未调用模型', 'resource_wait_configuration')
        configuration = copy.deepcopy(project.get('execution_config') or holder.get('execution_config'))
    observation = probe(configuration)
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        owner = db.execute('SELECT status,lease FROM jobs WHERE id=?', (job['id'],)).fetchone()
        if not owner or owner['status'] != 'running' or owner['lease'] != job.get('lease'):
            return True
        project = store._read(db, job['project_id'])
        records = _records(db, project)
        if any(p.get('pause_requested') or p.get('status') in {'paused', 'cancelled'}
               or p.get('automation', {}).get('suspended') for p in records):
            raise ProductionPaused('制作已暂停，资源等待未触发任何模型操作')
        fresh = store._read(db, holder['id'])
        current = (fresh.get('production') or {}).get('resource_wait') or {}
        if (current.get('enabled') is not True or current.get('status') != marker.get('status')
                or current.get('authorization_id') != marker['authorization_id']
                or (project.get('execution_config') or fresh.get('execution_config')) != configuration):
            raise ProductionPaused('资源等待授权或冻结配置已变化，旧预检未应用')
        current.update(status='waiting', last_checked_at=time.time(), checks=current.get('checks', 0)+1,
                       observation=observation, message=_MESSAGE)
        if observation['ready']:
            current.update(status='resolved', resolved_at=time.time(), message='Wkp 制作资源已恢复，系统继续原任务。')
            prior_recovery = fresh['production'].get('recovery')
            if prior_recovery and prior_recovery.get('status') == 'stopped':
                current['prior_recovery'] = copy.deepcopy(prior_recovery)
                fresh['production']['recovery'] = {'status': 'diagnosing', 'owner': 'resource_scheduler',
                    'message': '资源已恢复，系统重新核对原任务与持久返修记录。'}
        fresh['production']['resource_wait'] = current
        store._write(db, fresh)
        if fresh['id'] != project['id']:
            project.setdefault('production', {})['resource_wait'] = copy.deepcopy(current)
            prior = project['production'].get('recovery')
            if observation['ready'] and prior and prior.get('status') == 'stopped':
                project['production']['resource_wait']['prior_recovery'] = copy.deepcopy(prior)
                project['production']['recovery'] = {'status': 'diagnosing', 'owner': 'resource_scheduler',
                    'message': '资源已恢复，系统重新核对原任务与持久返修记录。'}
            store._write(db, project)
        # Keep the owning episode and its units consistent for independently
        # queued unit jobs as well as the normal parent-driven execution path.
        def basis(config):
            value = copy.deepcopy(config or {})
            for key in ('max_gpu_seconds', 'max_wall_seconds', 'max_attempts'):
                value.get('production', {}).pop(key, None)
            return value
        related = {p['id']: p for p in records if p.get('record_kind') != 'drama'}
        for root in records:
            if root.get('record_kind') == 'drama':
                for eid in root.get('episodes', []):
                    related[eid] = store._read(db, eid)
        for parent in list(related.values()):
            for uid in parent.get('unit_ids', []):
                related[uid] = store._read(db, uid)
        for candidate in related.values():
            if candidate['id'] in {fresh['id'], project['id']}:
                continue
            waiting = (candidate.get('production') or {}).get('resource_wait') or {}
            if (waiting.get('enabled') is True and waiting.get('authorization_id') == marker['authorization_id']
                    and basis(candidate.get('execution_config')) == basis(configuration)):
                waiting.update(status=current['status'], message=current['message'],
                               last_checked_at=current['last_checked_at'], observation=copy.deepcopy(observation))
                if observation['ready']:
                    waiting['resolved_at'] = current['resolved_at']
                    prior = candidate['production'].get('recovery')
                    if prior and prior.get('status') == 'stopped':
                        waiting['prior_recovery'] = copy.deepcopy(prior)
                        candidate['production']['recovery'] = {'status': 'diagnosing', 'owner': 'resource_scheduler',
                            'message': '资源已恢复，系统重新核对原任务与持久返修记录。'}
                store._write(db, candidate)
        store._event(db, project['id'], current['message'], 'resource_wait',
                     {'status': current['status'], 'authorization_id': marker['authorization_id']})
    if observation['ready']:
        return False
    defer_job(job, ProductionDeferred(time.time()+POLL_SECONDS, 'local_render', {
        'wait_kind': 'resources', 'reason': _REASON,
        'authorization_id': marker['authorization_id'], 'resume_action': 'automatic_resource_probe'}))
    return True
