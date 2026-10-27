"""Exercise actual plain adapter graphs; model/GPU transport remains synthetic."""
import copy
import json

import pytest

from factory import creative_contracts, shot_continuity
from studio import core
from tests.test_factory_remote_execution import runtime, transport


def enabled_run(run):
    creative_contracts.initialize(run.project)
    first = run.shots[0]
    first['motion_prompt'] = 'Adult returns the key.'
    first['continuity_state'] = {'mode': 'continuous', 'opening_state': '甲持有钥匙',
        'ending_state': '钥匙仍在甲手中', 'allowed_changes': []}
    second = copy.deepcopy(first)
    second.update(id='S02', continuity_state={'mode': 'continuous', 'opening_state': '钥匙仍在甲手中',
        'ending_state': '乙收下钥匙', 'allowed_changes': ['在批准动作中交接钥匙']})
    run.shots.append(second)
    run.episode['shots'] = copy.deepcopy(run.shots)
    run.project['episode'] = copy.deepcopy(run.episode)
    return second


@pytest.mark.parametrize('kind', ['image', 'video'])
def test_plain_graph_and_durable_attempt_bind_current_approved_adjacent_state(runtime, transport, kind):
    run = runtime
    shot = enabled_run(run)
    frame = run.folder / 'approved-frame.png'
    frame.write_bytes(b'synthetic reference bytes')
    target = run._target(shot['id'], run.folder / kind / shot['id'], 'Approved staging and motion.',
        [frame] if kind == 'video' else [], kind=kind, shot=shot, seconds=4)
    assert not run.project.get('generation_pipeline')
    snapshot = target['shot_continuity']
    assert snapshot['previous']['planned_ending_state'] == '钥匙仍在甲手中'
    assert snapshot['observed_state'] == 'not_observed'
    run._render_targets([target], 'synthetic_plain_adapter')
    record = core.read_json(target['folder'] / 'take_001/attempt.json')
    assert record['shot_continuity'] == snapshot
    graph = json.dumps(transport['posts'][0]['graph'], ensure_ascii=False)
    assert 'Approved adjacent-shot state' in graph
    assert '钥匙仍在甲手中' in graph and '乙收下钥匙' in graph
    signature = target['signature']
    run.shots[0]['continuity_state']['ending_state'] = '钥匙留在桌面'
    changed = run._target(shot['id'], target['folder'], 'Approved staging and motion.',
        [frame] if kind == 'video' else [], kind=kind, shot=shot, seconds=4)
    assert changed['signature'] != signature
    assert changed['shot_continuity']['previous']['planned_ending_state'] == '钥匙留在桌面'


def test_replacement_frame_context_is_bound_without_impersonating_an_approved_shot(runtime):
    run = runtime
    shot = enabled_run(run)
    target = run._target('repair_S02', run.folder / 'repair-frame', 'Repair only this frame.', review_context=shot)
    assert target['key'] == 'repair_S02'
    assert target['shot_continuity']['current']['shot_id'] == 'S02'
    assert '乙收下钥匙' in target['prompt']
    assert shot_continuity.bind_prompt(target['prompt'], target['shot_continuity']) == target['prompt']


def test_cloud_repair_keyframe_actual_graph_preserves_adjacent_state(runtime, transport, monkeypatch):
    from factory import video_production
    monkeypatch.setattr(video_production, 'inspect_image', lambda *args: {'status': 'pass', 'issues': []})
    run = runtime
    shot = enabled_run(run)
    frame = run.folder / 'approved-frame.png'
    frame.write_bytes(b'synthetic reference bytes')
    target = run._target('S02', run.folder / 'cloud-video', 'Approved motion', [frame], kind='video', shot=shot, seconds=4)
    record = {'take': 'take_001', 'repair': {'regenerate_keyframe': True}, 'seed': 1}
    run._remote_first_frame(target, record)
    graph = json.dumps(transport['posts'][0]['graph'], ensure_ascii=False)
    assert 'Approved adjacent-shot state' in graph and '钥匙仍在甲手中' in graph
    assert record['repair_keyframe']['path']


def test_legacy_plain_target_does_not_require_or_invent_state(runtime):
    shot = runtime.shots[0]
    target = runtime._target(shot['id'], runtime.folder / 'plain', shot['prompt'], shot=shot)
    assert target['prompt'] == shot['prompt']
    assert 'shot_continuity' not in target


def test_runtime_receipts_do_not_invalidate_planned_snapshot_and_unrelated_scene_prompt_is_not_a_dependency(runtime):
    run = runtime
    shot = enabled_run(run)
    context = shot_continuity.request_context(run.project, shot, {'shots': run.shots})
    run.shots[0].update(status='passed', image={'path': 'media.png', 'qa': {'status': 'pass'}}, video={'path': 'render.mp4'})
    assert shot_continuity.request_context(run.project, shot, {'shots': run.shots}) == context
    shot['continuity_state']['mode'] = 'scene_cut'
    scene = shot_continuity.request_context(run.project, shot, {'shots': run.shots})
    run.shots[0]['prompt'] = 'Different prior scene composition'
    assert shot_continuity.request_context(run.project, shot, {'shots': run.shots}) == scene
    shot['continuity_state']['mode'] = 'continuous'
    assert shot_continuity.request_context(run.project, shot, {'shots': run.shots})['source_signature'] != context['source_signature']
