import copy
import subprocess
import pytest
from factory import creative_contracts, shot_continuity as continuity, series_bindings, generation_pipeline
from studio import core


def shot(ident, mode='continuous'):
    return {'id': ident, 'seconds': 1, 'action': '甲递出钥匙', 'prompt': 'Adult gives a key', 'motion_prompt': 'A hand moves gently.',
        'continuity_state': {'mode': mode, 'opening_state': '甲持有钥匙', 'ending_state': '乙拿到钥匙', 'allowed_changes': []}}


def project():
    return creative_contracts.initialize({'id': '111122223333', 'slug': 'factory-111122223333', 'format': 'animation',
        'script': {'shots': [shot('S01'), shot('S02')], 'cast': {}}})


def test_adjacent_planned_state_is_explicit_and_never_claimed_observed():
    p = project(); context = continuity.request_context(p, p['script']['shots'][1])
    assert context['previous']['shot_id'] == 'S01'
    assert context['previous']['planned_ending_state'] == '乙拿到钥匙'
    assert context['observed_state'] == 'not_observed'
    old = context['source_signature']
    p['script']['shots'][0]['continuity_state']['ending_state'] = '钥匙留在桌上'
    assert continuity.request_context(p, p['script']['shots'][1])['source_signature'] != old


def test_generation_request_source_binds_the_adjacent_state_snapshot():
    p = project(); p['script']['shots'][1]['seconds'] = 5
    result = generation_pipeline.source(p, p['script']['shots'][1])
    assert result['shot_continuity']['previous']['shot_id'] == 'S01'
    assert result['approved_shot']['continuity_state']['mode'] == 'continuous'


def test_invalidation_propagates_only_continuous_or_explicit_references():
    p = project(); p['script']['shots'] += [shot('S03', 'scene_cut'), shot('S04'), shot('S05', 'time_skip')]
    assert series_bindings.dependent_closure(p, ['S01']) == ['S01', 'S02']
    p['script']['shots'][2]['reference_shots'] = ['S01']
    assert series_bindings.dependent_closure(p, ['S01']) == ['S01', 'S02', 'S03', 'S04']
    p.pop('creative_quality_contract')
    assert series_bindings.dependent_closure(p, ['S01']) == ['S01', 'S03']


def test_missing_state_and_stale_order_do_not_fabricate_a_context():
    p = project(); p['script']['shots'][1].pop('continuity_state')
    with pytest.raises(continuity.ProductionNeedsDecision, match='缺少明确'):
        continuity.request_context(p, p['script']['shots'][1])
    with pytest.raises(continuity.ProductionNeedsDecision, match='批准顺序'):
        continuity.request_context(p, shot('S99'))


@pytest.fixture
def actual_cut(tmp_path, monkeypatch):
    from studio.media import FFMPEG
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    from factory import config, drama_resources, llm
    monkeypatch.setattr(config, 'llm_identity', lambda *a: {'model': 'synthetic-visual-fixture'})
    monkeypatch.setattr(drama_resources, 'ensure', lambda *a, **kw: None)
    p = project(); folder = tmp_path / 'production' / p['slug']; folder.mkdir(parents=True)
    path = folder / 'episode_clean.mp4'
    subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'color=red:size=64x64:rate=24:duration=1', '-f', 'lavfi', '-i', 'color=blue:size=64x64:rate=24:duration=1', '-filter_complex_threads', '1', '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]', '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', str(path)], check=True, capture_output=True)
    delivery = {'artifacts': [core.media_receipt(path)]}
    colors = []
    def image_url(image):
        colors.append(image.getpixel((0, 0)))
        return 'data:image/png;base64,synthetic'
    monkeypatch.setattr(llm, '_image_url', image_url)
    return p, delivery, colors


def test_actual_adjacent_frames_are_used_and_success_is_reused(actual_cut, monkeypatch):
    from factory import llm
    p, delivery, colors = actual_cut
    calls = []
    def request(*args, **kwargs):
        calls.append(kwargs['content'])
        return {'status': 'pass', 'previous_observed': '红色测试帧', 'current_observed': '蓝色测试帧', 'issues': []}
    monkeypatch.setattr(llm, '_request', request)
    result = continuity.verify_delivery(p, delivery, {})
    assert result['scope'] == 'adjacent_endpoint_frames'
    assert colors[0][0] > 200 and colors[1][2] > 200
    assert len(calls) == 1
    assert continuity.verify_delivery(p, delivery, {}) == result
    assert len(calls) == 1


@pytest.mark.parametrize('status', ['fail', 'uncertain'])
def test_failed_or_insufficient_frames_retain_delivery_and_cannot_pass(actual_cut, monkeypatch, status):
    from factory import llm
    p, delivery, _ = actual_cut
    monkeypatch.setattr(llm, '_request', lambda *a, **kw: {'status': status, 'previous_observed': '前镜可见人物持物', 'current_observed': '后镜无法匹配', 'issues': ['测试矛盾或证据不足']})
    with pytest.raises(continuity.ProductionNeedsDecision) as caught:
        continuity.verify_delivery(p, delivery, {})
    assert caught.value.details['shot_ids'] == ['S02']
    assert caught.value.details['retained_delivery'] == delivery
    assert caught.value.details['review']['status'] == status


def test_unknown_endpoint_review_is_not_reposted(actual_cut, monkeypatch):
    from factory import llm
    p, delivery, _ = actual_cut
    count = []
    def unknown(*a, **kw): count.append(1); raise ConnectionError('result unknown')
    monkeypatch.setattr(llm, '_request', unknown)
    with pytest.raises(ConnectionError): continuity.verify_delivery(p, delivery, {})
    with pytest.raises(continuity.ProductionNeedsDecision, match='结果未知'):
        continuity.verify_delivery(p, delivery, {})
    assert len(count) == 1
