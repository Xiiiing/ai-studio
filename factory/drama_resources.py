"""Stage-local availability checks before an opt-in production submission.

Only discovery GETs are retried. Unknown model/generation submissions retain
their original journals and are never turned into automatic reposts.
"""
from __future__ import annotations

import time

from .drama_intent import enabled
from .remote_tasks import ProductionDeferred
from .scheduler import ProductionNeedsDecision


def ensure(project, config, *, role=None, media=None):
    if not enabled(project):
        return
    from .production_resource_wait import _temporary
    try:
        if role:
            from .config import resolve_llm
            from .providers import check_model
            from .agent_protocol import protocol
            from .prompt_workbench import _local_trial
            model = resolve_llm(config, role, require_vision=role == 'visual_critic')
            if not _local_trial(model):
                raise ProductionNeedsDecision('当前岗位需要已授权的内网模型和受支持的动作协议', 'one_sentence_model_configuration', {'role': role})
            protocol(model, project['agent_loop']['settings']['protocol'])
            check_model(model, timeout=5)
        if media:
            from .gpu_client import GPUClient
            connection = config.get(media + '_connection') or {}
            provider = connection.get('provider', 'legacy')
            # This mode does not grant unpriced cloud generation or failover.
            if provider not in {'legacy', 'comfyui_h3', 'comfyui_wan', 'comfyui_qwen_image21', 'qwen_tts_local', 'comfyui_local', 'comfyui_external'}:
                raise ProductionNeedsDecision('一句话模式未获得该付费供应商的金额授权', 'paid_production_authorization_unverified')
            if not config.get('gpu_service'):
                # Non-service installations retain their real local scheduler's checks.
                return
            health = GPUClient(config['gpu_service']).request('/health', timeout=5)
            if health.get('service') != 'gpu_inference' or health.get('ok') is not True:
                raise ProductionNeedsDecision('推理服务健康协议不符', 'resource_wait_configuration')
            available = True
            if media == 'video' and provider == 'comfyui_h3':
                from studio.h3 import backend_ready
                backend = health.get('video_backends', {}).get('comfyui_h3', {})
                available = backend_ready(backend, health.get('gpu_cards', []), connection)
            elif media == 'image' and provider == 'comfyui_qwen_image21':
                available = health.get('image_backends', {}).get(provider, {}).get('installed') is True
            elif provider in {'legacy', 'qwen_tts_local'}:
                available = health.get('models', {}).get(media) is True
            if not available:
                raise ProductionDeferred(time.time() + 300, 'remote_poll',
                    {'wait_kind': 'resources', 'required': media, 'reason': 'model_resources_unavailable'})
    except (ProductionDeferred, ProductionNeedsDecision):
        raise
    except Exception as exc:
        if _temporary(exc):
            raise ProductionDeferred(time.time() + 300, 'remote_poll',
                {'wait_kind': 'resources', 'required': role or media, 'reason': 'model_resources_unavailable'}) from exc
        raise ProductionNeedsDecision('当前阶段的连接、鉴权或能力配置需要处理', 'resource_wait_configuration',
                                      {'role': role, 'media': media, 'error_type': type(exc).__name__}) from exc
