"""Blind camera observation, separated from narrative and requested movement."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from jsonschema import validate
from studio.core import sha256, write_json

from .review_contracts import CAMERA_EXECUTION, camera_observation_schema, verify_camera


def verify(config, shot, path, samples, sampling, initial, output, expected_sha, request, image_url):
    target = Path(output) / 'camera-observation'
    result = copy.deepcopy(initial)
    try:
        if sha256(path) != expected_sha:
            raise ValueError('Candidate pixels changed before camera observation')
        if not 4 <= len(samples) <= 16 or len(samples) != len(sampling['timestamps']):
            raise ValueError('Camera observation requires actual timestamped samples')
        content = [{'type': 'text', 'text': json.dumps({
            'task': '只从候选实际画面判断主要相机运动，不判断是否符合任何未提供的要求。'
                    '追踪固定背景参照物相对于画面的横向位置与尺度；人物自身移动不代表相机运动。'
                    '细微运动也应按可见证据记录，不要求大幅变化。无法确定用uncertain，明确多种运动混合用mixed。',
            'definitions': CAMERA_EXECUTION}, ensure_ascii=False)}]
        for i, image in enumerate(samples, 1):
            content.extend([{'type': 'text', 'text': f'Sample {i}, {sampling["timestamps"][i-1]:.6f}s'},
                            {'type': 'image_url', 'image_url': {'url': image_url(image)}}])
        row = request(config, 'visual_critic',
            '你是独立的相机运动观察员，只根据候选画面描述实际运动与固定背景证据。',
            None, camera_observation_schema(), target, max_tokens=800, temperature=0, content=content)
        validate(row, camera_observation_schema())
        if sha256(path) != expected_sha:
            raise ValueError('Candidate pixels changed during camera observation')
        result['camera_observation'] = row
        result = verify_camera(result, shot, sampling, expected_sha)
        result['camera_verification']['observation_input'] = 'candidate_pixels_only_without_expected_motion_or_prior_verdict'
    except Exception as exc:
        result['_constraint_unavailable'] = True
        result['camera_verification'] = {'policy': 'ai-studio.camera-contract.v1', 'status': 'unavailable',
            'media_sha256': expected_sha, 'reason': f'{type(exc).__name__}: {str(exc)[:240]}'}
        from .quality import is_transient_transport
        if is_transient_transport(exc):
            write_json(target / 'evidence.json', result['camera_verification'])
            raise
    write_json(target / 'evidence.json', result['camera_verification'])
    return result
