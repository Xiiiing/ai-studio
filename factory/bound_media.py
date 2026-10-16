"""Route configured media without changing the legacy renderer's checkpoints."""
from __future__ import annotations
from contextlib import nullcontext
import time
from pathlib import Path
from studio.core import media_receipt, read_json
from studio.graphs import flux_image, wan_video
from .media_profiles import identity
from .scheduler import ProductionNeedsDecision, ProductionPaused


def connection(engine, kind):
    return engine.config.get('image_connection', {'provider': 'legacy'}) if kind == 'image' else engine.video_connection


def uses_bound(engine, target):
    return connection(engine, target['kind']).get('provider') in {'comfyui_local', 'comfyui_external', 'dashscope_image'}


def render(engine, target, record, phase):
    profile = connection(engine, target['kind'])
    if not profile.get('enabled', True): raise ProductionNeedsDecision('制作连接已停用', 'media_disabled')
    if record.get('repair', {}).get('regenerate_keyframe'):
        # Reuse the configured image pipeline, including its reference semantics.
        refs = list(target['references']) + [engine.receipt_path(v) for v in engine.assets.get('characters', {}).values()]
        replacement = engine._target('repair_' + target['key'], target['folder'] / record['take'] / 'repair_keyframe',
                                     record['repair'].get('prompt_prefix', '') + '\n' + engine.style_prompt + ' ' + target['shot']['prompt'], refs[:4])
        engine._render_targets([replacement], 'keyframe_repair')
        path = engine.receipt_path(replacement['selected']) if replacement.get('selected') else None
        # _attach stores the selected receipt in the target's selection manifest.
        if path is None:
            selected = engine._valid_selection(replacement)
            if not selected: raise ValueError('Replacement keyframe missing')
            path = engine.receipt_path(selected)
        record['repair_keyframe'] = media_receipt(path)
        target = {**target, 'references': [path] + list(target['references'][1:])}
    external = profile['provider'] == 'comfyui_external'
    if not external and engine.config.get('gpu_service'):
        from .scheduler import Scheduler
        scheduler = Scheduler({k:v for k,v in engine.config.items() if k != 'gpu_service'}, engine.budget)
    else: scheduler = None if external else engine.scheduler
    scope = nullcontext(None) if external else scheduler.worker(engine.slug + '_' + phase, engine.control)
    with scope as owned:
        from .external_comfy import ExternalComfy
        selected = profile if external else {'provider': 'comfyui_external', 'base_url': owned.base, 'network_mode': 'loopback', 'enabled': True,
                                             **{k: v for k, v in profile.items() if k in {'workflow_template', 'reference_templates', 'request_timeout_seconds'}}}
        client = ExternalComfy(selected)
        refs = [client.upload(p) for p in target['references']]
        width, height = engine.image_size if target['kind'] == 'image' else engine.video_size
        values = {'prompt': record['prompt'], 'negative_prompt': 'blurry, distorted, text, watermark',
                  'seed': record['seed'], 'width': width, 'height': height,
                  'frames': int(round(target.get('seconds', 6)*24)) // 4 * 4 + 1, 'fps': 24}
        template_id = profile.get('reference_templates', {}).get(str(len(refs)), profile.get('workflow_template'))
        output = None
        if template_id:
            from .workflow_templates import bind, load_template
            template = load_template(template_id)
            graph = bind(template, values, refs); output = template['output']
        elif target['kind'] == 'image':
            graph = flux_image(record['prompt'], record['seed'], width, height, references=refs)
        else:
            if len(refs) != 1: raise ValueError('内置视频工作流需要一张首帧参考图')
            graph = wan_video(record['prompt'], record['seed'], refs[0], width, height, seconds=target['seconds'])
        for node in graph.values():
            for field, filename in profile.get('model_files', {}).items():
                if field in node['inputs']: node['inputs'][field] = filename
        path = client.execute(graph, target['folder'] / record['take'], target['kind'], output=output,
                              check=engine.boundary, timeout=engine.budget.timeout(1800 if target['kind'] == 'image' else 7200, 0 if external else 1))
        receipt = read_json(path.parent / 'result.json')
        receipt.update(provider=profile['provider'], model=profile['model'], connection=identity(profile), workflow_template=template_id)
        from studio.core import write_json
        write_json(path.parent / 'result.json', receipt)
        return receipt


def render_targets(engine, targets, phase):
    from . import cloud_media
    cloud_targets = [t for t in targets if connection(engine,t["kind"])["provider"] == "dashscope_image"]
    prepared = cloud_media.prepare_images(engine,cloud_targets) if cloud_targets else None
    ready = []
    for target in targets:
        engine.boundary()
        cloud = target in cloud_targets
        record = prepared[1][target['key']][0] if cloud else target.pop('resume_attempt', None) or engine._new_attempt(target)
        started = time.monotonic()
        try:
            receipt = cloud_media.render_image(engine,target,record,prepared) if cloud else render(engine, target, record, phase)
            record.update(state='generated', output=receipt, elapsed_seconds=round(time.monotonic()-started, 3))
            ready.append((target, record, receipt))
        except (ProductionNeedsDecision, ProductionPaused):
            raise
        except Exception as exc:
            record.update(state='failed', error=str(exc)[:500], qa={'status': 'fail', 'issues': [{'code': 'generation', 'detail': type(exc).__name__}]})
        finally:
            engine._save_attempt(target, record); engine.emit(phase)
    return ready
