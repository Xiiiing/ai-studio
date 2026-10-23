"""Observed camera motion must survive the general-score normalization gate."""
import copy
import json

import pytest

from factory import llm
from factory.review_contracts import camera_execution
from studio.core import sha256


@pytest.mark.parametrize('observed,expected', [
    ('pan_right', 'pass'), ('push_in', 'fail'), ('pan_left', 'fail'),
    ('hold', 'fail'), ('mixed', 'unavailable'), ('uncertain', 'unavailable')])
def test_actual_review_enforces_camera_even_when_narrative_score_passes(tmp_path, monkeypatch, observed, expected):
    path = tmp_path / 'video.mp4'; path.write_bytes(b'actual sampled candidate')
    sampling = {'timestamps': [0., 1., 2., 3.]}
    shot = {'id': 'S03', 'camera_motion': 'pan_right', 'characters': ['Yun_Yun'],
            'action': 'She stands beside her companion.', 'dramatic_beat': '推进：补充情报'}
    initial = {'status': 'pass', 'score': 99, 'summary': '剧情推进，因此前推通过', 'issues': [],
               'camera_observation': {'motion': 'pan_right', 'from_sample': 1, 'to_sample': 4,
                                      'evidence': '0秒与3秒的固定立柱及人物景别尺度发生变化。'}}
    seen = []
    def request(*args, **kwargs):
        seen.append((args[4], json.loads(kwargs['content'][0]['text'])))
        return copy.deepcopy(initial if len(seen) == 1 else {**initial['camera_observation'], 'motion': observed})
    monkeypatch.setattr(llm, '_request', request)
    monkeypatch.setattr(llm, '_image_url', lambda _: 'data:image/png;base64,ZmFrZQ==')
    monkeypatch.setattr('factory.evaluation_sampling.sample_video', lambda *a, **k:
                        ([{'image': str(i)} for i in range(4)], sampling))
    config = {'llm': {'model': 'fixture'}, 'production': {
        'production_recovery': {'version': 'ai-studio.production-recovery.v1', 'enabled': True}}}
    result = llm.review_media({'slug': 'factory-camera', 'format': 'animation'}, shot,
                             path, [], config, output_dir=tmp_path / 'review')
    assert result['status'] == expected
    audit = result['camera_verification']
    assert audit['status'] == expected and audit['media_sha256'] == sha256(path)
    assert audit['expected_motion'] == 'pan_right' and audit['timestamps'] == sampling['timestamps']
    assert len(seen) == 2
    assert '剧情推进' not in str(seen[1][1]) and 'Yun_Yun' not in str(seen[1][1])
    assert 'expected' not in str(seen[1][1]) and '99' not in str(seen[1][1])
    assert '剧情推进，不是push_in' in seen[0][1]['camera_review_rule']
    assert '明显男性' in seen[0][1]['visible_cast_rule']
    assert initial['issues'] == []
    if expected == 'fail':
        assert result['issues'][-1]['code'] == 'action'  # handled by the existing repair route
        assert result['issues'][-1]['severity'] == 'blocking'


@pytest.mark.parametrize('change_pixels,malformed', [(True, False), (False, True)])
def test_camera_observation_never_certifies_changed_pixels_or_invalid_response(tmp_path, change_pixels, malformed):
    from factory.camera_review import verify
    path = tmp_path / 'candidate.mp4'; path.write_bytes(b'original')
    def request(*a, **k):
        if change_pixels:
            path.write_bytes(b'changed')
        return {} if malformed else {'motion': 'pan_right', 'from_sample': 1, 'to_sample': 4,
                                     'evidence': 'Fixed columns move left.'}
    result = verify({}, {'camera_motion': 'pan_right'}, path, ['a', 'b', 'c', 'd'],
                    {'timestamps': [0., 1., 2., 3.]}, {'status': 'pass', 'issues': []},
                    tmp_path / 'review', sha256(path), request, lambda s: 'pixel:' + s)
    assert result['_constraint_unavailable'] and result['camera_verification']['status'] == 'unavailable'
    assert not result['issues']


@pytest.mark.parametrize('first,last', [(1, 1), (2, 1), (1, 9), (False, 3)])
def test_fabricated_or_non_temporal_camera_evidence_cannot_pass(first, last):
    from factory.review_contracts import verify_camera
    result = verify_camera({'issues': [], 'camera_observation': {
        'motion': 'pan_right', 'from_sample': first, 'to_sample': last, 'evidence': 'Background moves left.'}},
        {'camera_motion': 'pan_right'}, {'timestamps': [0., 1., 2.]}, 'candidate-sha')
    assert result['camera_verification']['status'] == 'unavailable'
    assert result['_constraint_unavailable'] and not result['issues']


def test_camera_enum_translation_preserves_direction_and_unknown_explicit_description():
    assert 'background landmarks travel left' in camera_execution('pan_right')
    assert 'background landmarks travel right' in camera_execution('pan_left')
    assert 'framing widens' in camera_execution('pull_out')
    assert camera_execution('custom', 'Approved orbit with fixed height') == 'Approved orbit with fixed height'
