#!/usr/bin/env python3
"""Run one image review in an owned loopback Qwen3.8 process, then release GPUs."""
from __future__ import annotations
import argparse
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from factory.scheduler import GpuLease
from factory.vision_components import verify_pair


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--image', type=Path, action='append', required=True)
    parser.add_argument('--media', type=Path, help='Also exercise the application visual_critic on this image/video')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    identity = verify_pair(config['model'], config['projector'])
    cards = config['gpu_cards']
    if not isinstance(cards, list) or len(cards) != 2 or len(set(cards)) != 2 or any(type(c) is not int or not 0 <= c < 32 for c in cards):
        raise ValueError('Select two distinct GPU indices')
    context = config.get('context_tokens', 16384)
    if type(context) is not int or not 4096 <= context <= 32768:
        raise ValueError('Bounded test context must be 4096..32768')
    if not 1 <= len(args.image) <= 8:
        raise ValueError('Review one to eight images')
    content = []
    for image in args.image:
        if image.suffix.lower() not in ('.png', '.jpg', '.jpeg') or not 0 < image.stat().st_size <= 5 * 1024 * 1024:
            raise ValueError('Use bounded PNG/JPEG test images')
        mime = 'image/png' if image.suffix.lower() == '.png' else 'image/jpeg'
        content.append({'type': 'image_url', 'image_url': {'url': 'data:' + mime + ';base64,' + base64.b64encode(image.read_bytes()).decode()}})
    content.append({'type': 'text', 'text': 'Inspect these ordered frames of a fictional adult actor. Describe visible person, clothing, setting, hands and cup. Assess identity/clothing continuity, facial and hand artifacts, motion and instruction adherence. State uncertainty, and do not infer unseen movement from one still. Return JSON with scene (string), identity_consistent (boolean), defects (array of strings), motion_evidence (string), usable (boolean). Only assess what is visible.'})
    schema = {'type': 'object', 'properties': {'scene': {'type': 'string'}, 'identity_consistent': {'type': 'boolean'}, 'defects': {'type': 'array', 'items': {'type': 'string'}}, 'motion_evidence': {'type': 'string'}, 'usable': {'type': 'boolean'}}, 'required': ['scene', 'identity_consistent', 'defects', 'motion_evidence', 'usable'], 'additionalProperties': False}
    args.output.mkdir(parents=True, exist_ok=True)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    alias = config.get('alias', 'ai-studio-qwen38-vision')
    argv = [config['server'], '--model', config['model'], '--mmproj', config['projector'],
            '--alias', alias, '--host', '127.0.0.1', '--port', str(port), '--device', 'CUDA0,CUDA1',
            '--split-mode', 'layer', '--tensor-split', '1,1', '--gpu-layers', '99', '--ctx-size', str(context),
            '--parallel', '1', '--batch-size', '256', '--ubatch-size', '128', '--flash-attn', 'auto',
            '--jinja', '--no-webui', '--fit', 'off', '--reasoning', 'off']
    started = time.monotonic()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = f'http://127.0.0.1:{port}'
    def request(path, payload=None, timeout=5):
        req = urllib.request.Request(base + path, data=None if payload is None else json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        with opener.open(req, timeout=timeout) as response:
            return json.load(response)
    with GpuLease(cards, min_free=15000), (args.output / 'server.log').open('w') as log:
        child = subprocess.Popen(argv, env={**os.environ, 'CUDA_VISIBLE_DEVICES': ','.join(map(str, cards))}, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 300
            while True:
                if child.poll() is not None:
                    raise RuntimeError('Owned vision test process exited; inspect its log')
                try:
                    if request('/health').get('status') == 'ok':
                        break
                except (OSError, ValueError):
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError('Owned vision process did not become ready')
                time.sleep(1)
            props, catalog = request('/props'), request('/v1/models')
            if props.get('modalities', {}).get('vision') is not True or [m['id'] for m in catalog['data']] != [alias]:
                raise ValueError('Actual vision capability/model identity mismatch')
            answer = request('/v1/chat/completions', {'model': alias, 'messages': [{'role': 'user', 'content': content}], 'max_tokens': 1400, 'temperature': 0.1, 'response_format': {'type': 'json_schema', 'json_schema': {'name': 'human_review', 'strict': True, 'schema': schema}}}, timeout=600)
            if answer.get('model') != alias or answer['choices'][0]['finish_reason'] != 'stop':
                raise ValueError('Incomplete review or wrong model')
            review = json.loads(answer['choices'][0]['message']['content'])
            import jsonschema
            jsonschema.validate(review, schema)
            receipt = {**identity, 'actual_model': alias, 'modalities': props['modalities'], 'review': review,
                       'usage': answer.get('usage'), 'elapsed_seconds': round(time.monotonic() - started, 3),
                       'gpu_cards': cards, 'context_tokens': context, 'production_services_changed': False}
            if args.media:
                from factory.llm import review_media
                from factory.config import ROLE_LABELS
                profile = {'provider': 'llama_cpp', 'enabled': True, 'network_mode': 'loopback',
                           'base_url': base + '/v1', 'model': alias, 'vision': True,
                           'context_tokens': context, 'image_token_budget': 1536,
                           'response_format': 'llama_json_schema', 'context_validation': 'conservative_estimate',
                           'request_timeout_seconds': 600, 'chat_template_kwargs': {'enable_thinking': False}}
                application = {'schema_version': 1, 'llm_profiles': {'vision_test': profile},
                               'llm_roles': {role: 'vision_test' for role in ROLE_LABELS},
                               'quality': {'semantic_enabled': True, 'temporal_samples': 4, 'minimum_score': 60}}
                project = {'slug': 'isolated-human-quality', 'format': 'live_action',
                           'creative_contract': {'schema_version': 1, 'visual_style': 'photorealistic',
                                                 'story_mode': 'standalone', 'pacing': 'short_drama', 'motion': 'generated_video'}}
                shot = {'id': 'S01', 'seconds': 5,
                        'prompt': '写实成年男子，深蓝衬衫，在窗边咖啡桌前双手围着白杯，保持人物身份和衣服。',
                        'action': '自然眨眼，从看窗外缓慢转向相机，露出轻微笑容；手和杯子留在桌上。',
                        'camera_motion': '固定机位，连续单镜头'}
                receipt['application_review'] = review_media(project, shot, args.media, args.image, config=application,
                                                            output_dir=args.output / 'application')
                receipt['elapsed_seconds'] = round(time.monotonic() - started, 3)
            (args.output / 'review.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps(receipt, ensure_ascii=False))
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)


if __name__ == '__main__':
    main()
