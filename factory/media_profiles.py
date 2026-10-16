"""Image/speech connections, with explicit legacy interpretation and no I/O."""
from __future__ import annotations
import copy
from pathlib import Path
from .providers import ENV_RE, ID_RE, _reject_secret_fields, _valid_text, validate_endpoint

KINDS = {'image': 'image_generator', 'audio': 'speech_generator'}
MEDIA_PROVIDERS = {
    'legacy': {'label': '沿用现有制作环境'},
    'comfyui_local': {'label': '应用管理的 ComfyUI'},
    'comfyui_external': {'label': '自己的 ComfyUI 服务'},
    'dashscope_image': {'label': '百炼图像 API'},
    'dashscope_tts': {'label': '百炼语音 API'},
    'qwen_tts_local': {'label': '本地 Qwen3-TTS'},
}
COMMON = {'provider', 'enabled', 'model', 'revision', 'base_url', 'network_mode', 'allowed_hosts',
          'api_key_env', 'tls_ca_file_env', 'tls_client_cert_env', 'tls_client_key_env',
          'workflow_template', 'reference_templates', 'model_files', 'python_path', 'model_dir', 'voices',
          'default_voice', 'language', 'request_timeout_seconds', 'pricing', 'download_hosts', 'max_download_bytes'}


def normalize_media(value, kind):
    if not isinstance(value, dict) or set(value) - COMMON:
        raise ValueError('制作连接包含不支持的字段')
    _reject_secret_fields(value)
    p = copy.deepcopy(value)
    valid = {'legacy', 'comfyui_local', 'comfyui_external'} if kind in {'image', 'video'} else {'legacy', 'qwen_tts_local', 'dashscope_tts'}
    if kind == 'image': valid.add('dashscope_image')
    if p.get('provider') not in valid:
        raise ValueError('不支持的制作供应商')
    p.setdefault('enabled', True)
    if type(p['enabled']) is not bool: raise ValueError('enabled 必须为布尔值')
    p['model'] = _valid_text(p.get('model'), 'model')
    p.setdefault('request_timeout_seconds', 120)
    if type(p['request_timeout_seconds']) is not int or not 1 <= p['request_timeout_seconds'] <= 600:
        raise ValueError('制作接口超时应为1至600秒')
    if p['provider'] in {'comfyui_external', 'dashscope_image', 'dashscope_tts'}:
        p.setdefault('network_mode', 'remote' if p['provider'].startswith('dashscope_') else 'loopback'); p.setdefault('allowed_hosts', [])
        p['base_url'] = validate_endpoint(p)
    env = p.get('api_key_env')
    if env and (not isinstance(env, str) or not ENV_RE.fullmatch(env)):
        raise ValueError('请填写密钥环境变量名称')
    if p['provider'].startswith('dashscope_'):
        if not env: raise ValueError('云端制作需要密钥环境变量名称')
        from .cloud_budget import pricing
        quote = pricing(p.get('pricing'))
        p['pricing'] = {k:quote[k] for k in ('unit_cost_micros','currency','source')}
        p.setdefault('download_hosts', [])
        if not isinstance(p['download_hosts'],list) or len(p['download_hosts'])>16: raise ValueError('下载主机列表无效')
        from .providers import _canonical_host
        p['download_hosts'] = [_canonical_host(host) for host in p['download_hosts']]
        p.setdefault('max_download_bytes',256*1024*1024)
        if type(p['max_download_bytes']) is not int or not 1 <= p['max_download_bytes'] <= 1024*1024*1024: raise ValueError('下载大小限制无效')
        if p['provider']=='dashscope_image' and p['model'] not in {'qwen-image-3.0-pro','qwen-image-3.0','qwen-image-2.1-pro','qwen-image-2.1-turbo'}: raise ValueError('该图像适配器支持百炼 Qwen Image 2.1／3.0 系列')
        if p['provider']=='dashscope_tts':
            p.setdefault('default_voice','Cherry'); p.setdefault('language','Chinese')
            _valid_text(p['default_voice'],'voice',128); _valid_text(p['language'],'language',64)
    for field in ('python_path', 'model_dir'):
        if field in p and (not isinstance(p[field], str) or not Path(p[field]).is_absolute()):
            raise ValueError(field + ' 必须是绝对路径')
    if 'workflow_template' in p:
        from .workflow_templates import load_template
        template = load_template(p['workflow_template'])
        if template['kind'] != kind: raise ValueError('工作流输出类型与连接不匹配')
    if 'reference_templates' in p:
        templates = p['reference_templates']
        if not isinstance(templates, dict) or len(templates) > 16: raise ValueError('参考图数量与模板映射无效')
        from .workflow_templates import load_template
        for count, ident in templates.items():
            if not isinstance(count, str) or not count.isdecimal() or int(count) > 15: raise ValueError('参考图数量无效')
            if load_template(ident)['kind'] != kind: raise ValueError('工作流输出类型不匹配')
    files = p.get('model_files', {})
    if not isinstance(files, dict) or set(files) - {'unet_name', 'clip_name', 'vae_name'}:
        raise ValueError('模型文件仅支持 unet_name、clip_name、vae_name')
    for name in files.values():
        if not isinstance(name, str) or not name or Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('模型文件必须是模型目录内的相对路径')
    if 'voices' in p and (not isinstance(p['voices'], dict) or len(p['voices']) > 32):
        raise ValueError('声线映射应为至多32项的对象')
    return p


def registry(config, kind):
    role = KINDS[kind]
    defaults = {'legacy': {'provider': 'legacy', 'enabled': True,
                          'model': 'FLUX.2-klein-4B' if kind == 'image' else 'Qwen3-TTS'}}
    profiles = config.get(kind + '_profiles', defaults)
    assignments = config.get(kind + '_roles', {role: next(iter(profiles), '')})
    if not isinstance(profiles, dict) or not 1 <= len(profiles) <= 32:
        raise ValueError('制作连接数量须为1至32')
    if any(not isinstance(k, str) or not ID_RE.fullmatch(k) for k in profiles):
        raise ValueError('制作连接编号无效')
    normalized = {k: normalize_media(v, kind) for k, v in profiles.items()}
    if not isinstance(assignments, dict) or set(assignments) != {role} or assignments[role] not in normalized:
        raise ValueError('请选择有效的制作连接')
    return normalized, copy.deepcopy(assignments)


def resolve(config, kind):
    profiles, roles = registry(config, kind)
    ident = roles[KINDS[kind]]
    return {'profile': ident, **profiles[ident]}


def identity(profile):
    return {k: copy.deepcopy(v) for k, v in profile.items() if k not in {
        'api_key_env', 'tls_ca_file_env', 'tls_client_cert_env', 'tls_client_key_env', 'pricing'}}


def inventory(config):
    result = {'media_providers': MEDIA_PROVIDERS, 'media_presets': {
        'image': {'comfyui_local': {'provider': 'comfyui_local', 'model': 'FLUX.2-klein-4B', 'enabled': True},
                  'comfyui_external': {'provider': 'comfyui_external', 'model': 'FLUX.2-klein-4B', 'enabled': True, 'base_url': 'http://127.0.0.1:8188', 'network_mode': 'loopback', 'allowed_hosts': []}},
        'audio': {'qwen_tts_local': {'provider': 'qwen_tts_local', 'model': 'Qwen3-TTS', 'enabled': True}},
        'video': {'comfyui_local': {'provider': 'comfyui_local', 'model': 'Wan2.2-TI2V-5B', 'enabled': True},
                  'comfyui_external': {'provider': 'comfyui_external', 'model': 'Wan2.2-TI2V-5B', 'enabled': True, 'base_url': 'http://127.0.0.1:8188', 'network_mode': 'loopback', 'allowed_hosts': []}}}}
    result['media_presets']['image']['dashscope_image'] = {'provider':'dashscope_image','model':'qwen-image-2.1-pro','enabled':True,'network_mode':'remote','allowed_hosts':['dashscope.aliyuncs.com'],'base_url':'https://dashscope.aliyuncs.com/compatible-mode/v1','api_key_env':'DASHSCOPE_API_KEY'}
    result['media_presets']['audio']['dashscope_tts'] = {'provider':'dashscope_tts','model':'qwen3-tts-flash','enabled':True,'network_mode':'remote','allowed_hosts':['dashscope.aliyuncs.com'],'base_url':'https://dashscope.aliyuncs.com/api/v1','api_key_env':'DASHSCOPE_API_KEY','default_voice':'Cherry','language':'Chinese','voices':{'female':'Cherry','male':'Ethan','narrator':'Cherry'}}
    for kind in KINDS:
        profiles, roles = registry(config, kind)
        result.update({kind + '_profiles': profiles, kind + '_roles': roles})
    result["readiness"] = readiness(config)
    return result


def validate_registry(config, updates):
    out = copy.deepcopy(config)
    for kind in KINDS:
        keys = {kind + '_profiles', kind + '_roles'}
        if keys & updates.keys():
            if not keys <= updates.keys(): raise ValueError('制作连接与分工必须一起保存')
            out.update({key: updates[key] for key in keys})
            profiles, roles = registry(out, kind)
            out.update({kind + '_profiles': profiles, kind + '_roles': roles})
    return out


def probe(profile, kind):
    import os
    result = {'kind': kind, 'generation': False, 'network_checked': False, 'model_match': False,
              'status': 'configured', 'message': '配置检查通过；实际生成尚未验证'}
    if not profile['enabled']:
        result.update(status='disabled'); return result
    if profile.get('api_key_env') and not os.environ.get(profile['api_key_env']):
        result.update(status='error', message='密钥环境变量尚未设置'); return result
    if profile['provider'] == 'comfyui_external':
        from .external_comfy import ExternalComfy
        info = ExternalComfy(profile).check()
        result.update(network_checked=True, nodes_checked=len(info))
    elif profile['provider'] == 'qwen_tts_local':
        from studio.core import ROOT
        python = Path(profile.get('python_path', ROOT / 'envs/tts/bin/python'))
        model = Path(profile.get('model_dir', ROOT / 'models/tts/Qwen3-TTS-12Hz-1.7B-CustomVoice'))
        if not python.is_file() or not (model / 'download_receipt.json').is_file():
            result.update(status='error', message='缺少本地 TTS 运行环境或模型来源回执')
    return result


def local_kinds(config):
    """Only selected, enabled local engines need local model/GPU discovery."""
    production = config.get('production', config)
    image = config.get('image_connection') or resolve(config, 'image')
    audio = config.get('audio_connection') or resolve(config, 'audio')
    from .video_providers import resolve_video
    video = config.get('video_connection') or resolve_video(config, require_enabled=False)
    remote = bool(production.get('gpu_service'))
    return {kind: bool(p.get('enabled', True)) and (p['provider'] in {'comfyui_local', 'qwen_tts_local'} or
             (p['provider'] in {'legacy', 'comfyui_wan', 'comfyui_h3'} and not remote))
            for kind, p in [('image', image), ('audio', audio), ('video', video)]}


def readiness(config):
    import os
    from .config import ROLE_LABELS, OPTIONAL_ROLES, resolve_llm
    missing=[]
    for role,label in ROLE_LABELS.items():
        if role in OPTIONAL_ROLES: continue
        try:
            p=resolve_llm(config,role)
            if not p.get("enabled",True): missing.append(label+"连接已停用")
            elif p.get("api_key_env") and not os.environ.get(p["api_key_env"]): missing.append(label+"缺少密钥环境变量 "+p["api_key_env"])
        except ValueError: missing.append(label+"尚未配置")
    for kind,label in (("image","图像"),("audio","配音")):
        p=resolve(config,kind)
        if not p["enabled"]: missing.append(label+"连接尚未启用")
        elif p.get("api_key_env") and not os.environ.get(p["api_key_env"]): missing.append(label+"缺少密钥环境变量 "+p["api_key_env"])
    return {"missing":list(dict.fromkeys(missing)),"generation_verified":False,"message":"配置检查不代表生成能力验收；真人／动画路线还需要视频连接"}
