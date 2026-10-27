"""Versioned contracts for the media adapters this application actually executes.

These are application/adapter limits, not proof of vendor availability, model
quality, or support for every feature advertised under the same model name.
No discovery, model request, credential lookup or graph execution happens here.
"""
from __future__ import annotations

import copy
import math

from studio.core import fingerprint

VERSION = 'ai-studio.model-capabilities.v1'


class CapabilityError(ValueError):
    def __init__(self, message, field='request', details=None):
        super().__init__(message)
        self.code = 'model_capability'
        self.details = {'field': field, **(details or {})}


def _profile_identity(profile, kind):
    # Connection/secret edits cannot redefine a model's declared capabilities.
    return fingerprint({'kind': kind, **{key: profile.get(key) for key in
        ('provider', 'model', 'revision', 'workflow_template', 'reference_templates', 'model_files')}})


def _templates(profile, kind):
    from .workflow_templates import load_template
    result = {}
    for count in range(17):
        ident = profile.get('reference_templates', {}).get(str(count), profile.get('workflow_template'))
        if not ident:
            # An explicit reference template map is a closed mapping.
            if profile.get('reference_templates'):
                continue
            continue
        template = load_template(ident)
        sources = {m['source'] for m in template['mappings']}
        if template['kind'] != kind or sum(s.startswith('reference:') for s in sources) != count:
            continue
        result[str(count)] = {'id': ident, 'parameters': sorted(s for s in sources if not s.startswith('reference:'))}
    return result


def describe(profile, kind, *, procedural=False):
    """Return a public, deterministic projection of the executable adapter."""
    profile = profile or {}
    provider = profile.get('provider', 'legacy' if kind != 'video' else 'comfyui_wan')
    model = profile.get('model', 'FLUX.2-klein-4B' if kind == 'image' else 'Qwen3-TTS' if kind == 'audio' else 'Wan2.2-TI2V-5B')
    cap = {'schema': VERSION, 'kind': kind, 'provider': provider, 'model': model,
           'identity': _profile_identity(profile, kind), 'enabled': profile.get('enabled', True),
           'status': 'declared', 'source': 'repository_adapter_contract', 'output_count': 1,
           'verification': 'adapter_contract_only_not_generation_or_vendor_verification',
           'inputs': ['text'], 'modes': [], 'reference_counts': [0],
           'first_frame': False, 'last_frame': False, 'reference_video': False,
           'reference_audio': False, 'native_audio': False, 'lip_sync': False,
           'dimensions': None, 'duration': None, 'fps': 24 if kind == 'video' else None, 'unknown': []}
    if ((provider == 'comfyui_qwen_image21' and model != 'Qwen-Image-2.1')
            or (provider == 'comfyui_h3' and model != 'MiniMax-H3')
            or (provider == 'comfyui_wan' and model != 'Wan2.2-TI2V-5B')):
        return {**cap, 'status': 'unknown', 'unknown': ['adapter_model_mismatch']}
    if kind == 'audio':
        if provider not in {'legacy', 'qwen_tts_local', 'dashscope_tts'}:
            return {**cap, 'status': 'unknown', 'unknown': ['adapter']}
        cap.update(modes=['text_to_speech'], voice_ids=sorted(profile.get('voices', {})),
                   voice_selection='configured_voice_mapping_or_explicit_default', default_voice=profile.get('default_voice'),
                   unknown=['measured_output_duration'])
        return cap
    if kind not in {'image', 'video'}:
        return {**cap, 'status': 'unknown', 'unknown': ['adapter']}
    cap['dimensions'] = {'min': 128, 'max': 2048, 'multiple': 32,
                         'source': 'application_canvas_bounds', 'provider_verified': False}
    if procedural:
        return {**cap, 'provider': 'procedural_camera', 'model': 'ffmpeg_zoompan_v1', 'enabled': True,
                'identity': fingerprint({'kind': 'video', 'adapter': 'ffmpeg_zoompan_v1'}),
                'modes': ['still_camera'], 'inputs': ['image'], 'reference_counts': [1],
                'first_frame': True, 'duration': {'min': .5, 'max': 15}, 'continuous_action': False}
    cap['continuous_action'] = kind == 'video'
    if provider in {'comfyui_local', 'comfyui_external'} and (profile.get('workflow_template') or profile.get('reference_templates')):
        templates = _templates(profile, kind)
        return {**cap, 'status': 'partial', 'source': 'content_addressed_workflow_mappings',
                'inputs': ['text', 'images'], 'modes': ['workflow'], 'templates': templates,
                'reference_counts': sorted(int(c) for c in templates),
                'duration': {'min': .5, 'max': 15} if kind == 'video' else None,
                'unknown': ['vendor_model_limits', 'custom_workflow_semantics']}
    if kind == 'image':
        cap.update(inputs=['text', 'images'], modes=['text_to_image', 'image_to_image'])
        if provider == 'comfyui_qwen_image21':
            cap.update(reference_counts=list(range(5)), max_prompt_characters=5000,
                       dimensions={'min': 256, 'max': 1536, 'multiple': 32,
                                   'max_pixels': 1024 * 1536, 'source': 'qwen_image21.validate_request', 'provider_verified': False})
        elif provider == 'dashscope_image':
            cap.update(reference_counts=list(range(4 if str(model).startswith('qwen-image-3.') else 11)),
                       dimensions={'min': 128, 'max': 2048, 'multiple': 32,
                                   'min_pixels': 512 * 512, 'source': 'cloud_media.image_body', 'provider_verified': False},
                       reference_media={'max_bytes': 10 * 1024 * 1024, 'max_side': 8000, 'max_ratio': 10})
        elif provider in {'legacy', 'comfyui_local', 'comfyui_external'}:
            cap['reference_counts'] = list(range(5))
            cap['source'] = 'bounded_flux_reference_contract'
        else:
            cap.update(status='unknown', modes=[], reference_counts=[], unknown=['adapter'])
    else:
        cap.update(inputs=['text', 'images'], modes=['image_to_video'], reference_counts=[1], first_frame=True,
                   duration={'min': .5, 'max': 15})
        if provider == 'comfyui_h3':
            cap.update(duration={'min': 1, 'max': 15},
                       dimensions={'min': 128, 'max': 1536, 'multiple': 32,
                                   'max_pixels': 768 * 1344, 'source': 'studio.graphs.h3_video', 'provider_verified': False})
        elif provider == 'minimax':
            resolutions = ['768P', '1080P'] if str(model).lower().startswith('minimax-hailuo-2.3') else ['512P', '768P', '1080P']
            cap.update(duration={'min': .5, 'max': profile.get('duration', 6)},
                       duration_options=[6, 10], resolution_options=resolutions,
                       invalid_combinations=[{'duration': 10, 'resolution': '1080P'}], max_prompt_characters=2000,
                       reference_media={'max_bytes': 5 * 1024 * 1024, 'min_short_side_exclusive': 300,
                                        'min_ratio': .4, 'max_ratio': 2.5},
                       unknown=['upstream_model_availability'])
        elif provider not in {'comfyui_wan', 'comfyui_local', 'comfyui_external'}:
            cap.update(status='unknown', modes=[], reference_counts=[], unknown=['adapter'])
    return cap


def selected_profiles(config):
    """Accept either the public configuration or the worker's frozen flat config."""
    from .media_profiles import resolve
    from .video_providers import resolve_video
    result = {}
    for kind in ('image', 'audio', 'video'):
        direct = config.get(kind + '_connection') or config.get('production', {}).get(kind + '_connection')
        result[kind] = copy.deepcopy(direct or (resolve_video(config, require_enabled=False) if kind == 'video' else resolve(config, kind)))
    return result


def pin(project, config):
    """Set once for a new project; callers guard explicit adoption of old ones."""
    if 'model_capability_snapshot' not in project:
        profiles = selected_profiles(config)
        project['model_capability_snapshot'] = {'version': VERSION,
            'profiles': {kind: describe(profile, kind, procedural=kind == 'video' and project.get('format') == 'motion_comic')
                         for kind, profile in profiles.items()}}
    return project['model_capability_snapshot']


def adopt(project, config):
    """Caller must hold its existing CAS/idle/unresolved-submission guards."""
    candidate = copy.deepcopy(project)
    previous = candidate.pop('model_capability_snapshot', None)
    replacement = pin(candidate, config)
    if previous and previous != replacement:
        project.setdefault('model_capability_history', []).append(copy.deepcopy(previous))
    project['model_capability_snapshot'] = replacement
    return replacement


def enabled(project):
    snapshot = project.get('model_capability_snapshot')
    if snapshot is None:
        return False
    if not isinstance(snapshot, dict) or snapshot.get('version') != VERSION:
        raise CapabilityError('模型能力快照版本不受当前程序支持，原记录保留', 'snapshot')
    return True


def active(project, config, kind):
    profiles = selected_profiles(config)
    current = describe(profiles[kind], kind, procedural=kind == 'video' and project.get('format') == 'motion_comic')
    if not enabled(project):
        return current
    saved = project['model_capability_snapshot'].get('profiles', {}).get(kind)
    if not saved or saved != current:
        raise CapabilityError('模型能力或连接已变化，请暂停并明确应用当前能力版本', 'snapshot', {'kind': kind})
    return copy.deepcopy(saved)


def validate(cap, request):
    """Reject a complete request before any upload, model call, or paid intent."""
    def fail(message, field):
        raise CapabilityError(message, field, {'provider': cap.get('provider'), 'kind': cap.get('kind')})
    if cap.get('enabled') is not True:
        fail('所选制作连接已停用', 'enabled')
    if cap.get('status') == 'unknown':
        fail('所选适配器能力尚未明确，不能提交生成', 'adapter')
    if not isinstance(request, dict):
        fail('生成参数必须为对象', 'request')
    if request.get('output_count', 1) != 1:
        fail('当前适配器每次请求只生成一个产物', 'output_count')
    for field in ('first_frame', 'last_frame', 'reference_video', 'reference_audio', 'native_audio', 'lip_sync'):
        if request.get(field) and not cap.get(field):
            fail('当前适配器不支持此输入组合：' + field, field)
    refs = request.get('references', [])
    if not isinstance(refs, (list, tuple)) or len(refs) not in cap['reference_counts']:
        fail('参考图数量与当前模型能力不匹配，可用数量：' + ', '.join(map(str, cap['reference_counts'])), 'references')
    if cap['kind'] != 'audio' and 'text' in cap['inputs']:
        prompt = request.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip():
            fail('生成提示词不能为空', 'prompt')
        if cap.get('max_prompt_characters') and len(prompt) > cap['max_prompt_characters']:
            fail('提示词长度超过当前模型能力', 'prompt')
    if cap['kind'] == 'audio':
        text = request.get('text')
        if not isinstance(text, str) or not text.strip():
            fail('配音正文不能为空', 'text')
        if request.get('voice_id') is not None and cap.get('voice_ids') and request['voice_id'] not in cap['voice_ids'] and not cap.get('default_voice'):
            fail('配音声线没有有效映射', 'voice_id')
    dims = cap.get('dimensions')
    if dims and ('width' in request or 'height' in request):
        w, h = request.get('width'), request.get('height')
        if any(type(v) is not int or not dims['min'] <= v <= dims['max'] or v % dims['multiple'] for v in (w, h)):
            fail('画布尺寸超出当前适配器范围或未对齐像素步长', 'dimensions')
        if w * h < dims.get('min_pixels', 0) or w * h > dims.get('max_pixels', math.inf):
            fail('画布像素总数超出当前适配器范围；不会自动缩放请求规格', 'dimensions')
    if 'fps' in request and cap.get('fps') != request['fps']:
        fail('当前适配器仅支持声明的帧率', 'fps')
    if cap.get('duration') and 'seconds' in request:
        s = request['seconds']
        if isinstance(s, bool) or not isinstance(s, (int, float)) or not math.isfinite(s) or not cap['duration']['min'] <= s <= cap['duration']['max']:
            fail('镜头时长超出当前模型能力；请修改镜头或明确更换配置', 'seconds')
    for field, options in (('duration', 'duration_options'), ('resolution', 'resolution_options')):
        if field in request and request[field] not in cap.get(options, []):
            fail('所选参数不受当前适配器支持：' + field, field)
    if any(all(request.get(k) == v for k, v in row.items()) for row in cap.get('invalid_combinations', [])):
        fail('该时长与清晰度组合不受当前适配器支持', 'duration_resolution')
    template = cap.get('templates', {}).get(str(len(refs)))
    if template:
        required = {'width', 'height'} | ({'frames', 'fps'} if cap['kind'] == 'video' else set())
        missing = required - set(template['parameters'])
        if missing:
            fail('自定义工作流未映射制作参数，不能假定请求会生效：' + ', '.join(sorted(missing)), 'workflow_mappings')
    # Check actual reference bytes when paths are supplied. Uploaded Comfy names
    # are checked later by their existing transport; never fetch arbitrary URLs.
    media = cap.get('reference_media')
    if media and request.get('inspect_files', False):
        from pathlib import Path
        from PIL import Image
        for value in refs:
            path = Path(value)
            if not path.is_file() or path.stat().st_size > media['max_bytes']:
                fail('参考图缺失或文件大小超限', 'references')
            with Image.open(path) as image:
                w, h = image.size
                ratio = w / h
                if (min(w, h) <= media.get('min_short_side_exclusive', 0)
                        or max(w, h) > media.get('max_side', math.inf)
                        or not media.get('min_ratio', 0) <= ratio <= media.get('max_ratio', math.inf)
                        or max(w, h) / min(w, h) > media.get('max_ratio', math.inf)):
                    fail('参考图尺寸或比例超出当前模型能力', 'references')
                if image.format not in {'PNG', 'JPEG', 'WEBP'}:
                    fail('参考图格式不受当前适配器支持', 'references')
                image.verify()
    return {'schema': VERSION, 'identity': cap['identity'], 'status': 'valid'}


def validate_target(project, config, target, width, height, *, references=None, prompt=None):
    if not enabled(project):
        return None
    kind = target['kind']
    cap = active(project, config, kind)
    request = {'prompt': target['prompt'] if prompt is None else prompt,
               'references': target.get('references', []) if references is None else references,
               'width': width, 'height': height, 'inspect_files': True}
    if kind == 'video':
        request['seconds'] = target.get('seconds')
        request['fps'] = 24
        profile = selected_profiles(config)['video']
        if profile.get('provider') == 'minimax' and project.get('format') != 'motion_comic':
            request.update(duration=profile.get('duration', 6), resolution=profile.get('resolution', '768P'))
    return validate(cap, request)


def inventory(config):
    from .media_profiles import registry, inventory as media_inventory
    from .video_providers import video_registry, video_presets
    catalogs, presets = {}, {}
    media_presets = media_inventory(config)['media_presets']
    for kind in ('image', 'audio', 'video'):
        profiles, _ = video_registry(config, require_enabled=False) if kind == 'video' else registry(config, kind)
        catalogs[kind] = {name: describe(p, kind) for name, p in profiles.items()}
        options = {**media_presets[kind], **(video_presets() if kind == 'video' else {})}
        presets[kind] = {name: describe(p, kind) for name, p in options.items()}
    return {'model_capabilities': catalogs, 'model_capability_presets': presets,
            'model_capability_schema': VERSION}
