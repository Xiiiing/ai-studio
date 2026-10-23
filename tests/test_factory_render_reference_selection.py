"""Contract-verified per-shot render inputs; original review evidence stays intact."""
import copy

import pytest
from PIL import Image

from factory import llm, production_strategy as strategy
from factory.scheduler import ProductionNeedsDecision
from studio import core
from tests.test_factory_production_strategy import case, prepare, compile_, candidate


@pytest.fixture
def props(case):
    project, config, target, qa, previous, root = case
    target['kind'] = 'image'
    target['approved_render_style'] = 'Unified 3D cinematic animation with volumetric lighting.'
    target['reference_roles'] = ['woman']
    prop, scene = root / 'production/prop.png', root / 'production/scene.png'
    Image.new('RGB', (64, 64), 'red').save(prop)
    Image.new('RGB', (64, 64), 'blue').save(scene)
    target['references'] += [prop, scene]
    target['asset_references'] = [
        {'kind': 'prop', 'entity_id': 'cup', 'sha256': core.sha256(prop)},
        {'kind': 'scene', 'entity_id': 'room', 'sha256': core.sha256(scene)}]
    return case


@pytest.mark.parametrize('approved', [True, False])
def test_prop_omission_requires_independent_contract_and_keeps_original_source(props, monkeypatch, approved):
    project, config, target, qa, previous, root = props
    before = copy.deepcopy(target)
    qa['issues'].append({'code': 'action', 'severity': 'minor', 'detail': 'Optional fixture motion suggestion.'})
    calls = []
    def request(config, role, system, payload, schema, output, **kwargs):
        calls.append(copy.deepcopy(payload))
        if 'compiled_visual_prompt' in payload:
            assert payload['approved_render_style'] == target['approved_render_style']
            assert payload['excluded_prop_references'][0]['entity_id'] == 'cup'
            assert len(payload['approved_reference_bindings']) == 3
            return {'passed': approved, 'reason': 'Fixture: off-screen prop.' if approved else 'Approved shot requires the cup.'}
        assert all(i['severity'] != 'minor' for i in payload['project']['diagnostic_summary']['issues'])
        result = candidate(target['shot'], payload['project']['strategy'])
        result.pop('performance')
        result['plan'] = {k: v for k, v in result['plan'].items() if k in {'approved_action', 'camera_motion', 'image'}}
        result['offscreen_prop_reference_indices'] = [2]
        return result
    monkeypatch.setattr(llm, '_request', request)
    record = prepare(props)
    if not approved:
        with pytest.raises(ProductionNeedsDecision) as failure:
            compile_(props, record)
        assert failure.value.reason == 'production_strategy_contract_conflict'
    else:
        result = compile_(props, record)
        assert result['parameters']['prompt'].startswith('Approved rendering style for every person and the environment: ' + target['approved_render_style'])
        assert result['parameters']['reference_indices'] == [1, 3]
        assert result['parameters']['reference_sha256'] == [core.sha256(target['references'][i]) for i in (0, 2)]
        assert 'Reference image 2 (room)' in result['parameters']['prompt']
        assert 'Reference image 2 (cup)' not in result['parameters']['prompt']
        assert strategy.render_references(target, result['parameters']) == [target['references'][0], target['references'][2]]
        assert compile_(props, record) == result  # No replay after restart.
    assert len(calls) == 2
    assert target == before and len(record['source']['references']) == 3


def test_identity_and_scene_cannot_be_omitted_and_changed_pixels_cannot_be_submitted(props):
    record = prepare(props)
    for index in (1, 3):
        with pytest.raises(ProductionNeedsDecision):
            strategy._render_source(record['source'], [index])
    target = props[2]
    params = {'reference_indices': [1, 3], 'reference_sha256': [core.sha256(target['references'][i]) for i in (0, 2)]}
    target['references'][2].write_bytes(b'changed after compilation')
    with pytest.raises(ProductionNeedsDecision) as failure:
        strategy.render_references(target, params)
    assert failure.value.reason == 'production_strategy_source_changed'


def test_adapter_preflight_uses_selected_count_instead_of_original_count(props, monkeypatch):
    from factory import image_generation
    target = props[2]
    params = {'reference_indices': [1, 3], 'reference_sha256': [core.sha256(target['references'][i]) for i in (0, 2)],
              'prompt': 'Fixture shot', 'width': 64, 'height': 64}
    calls = []
    original = image_generation.image_request
    def build(*args, **kwargs):
        calls.append(args[5])
        return original(*args, **kwargs)
    monkeypatch.setattr(image_generation, 'image_request', build)
    strategy._adapter_preflight(props[1], target, params)
    assert len(calls[0]) == 2


from tests.test_factory_autonomous_execution import autonomous, h3_setup


def test_renderer_submits_subset_but_independent_reviewer_gets_all_references(autonomous, monkeypatch):
    from factory import production
    from factory.production_execution import DEFAULT_POLICY
    project, cfg, _, events, compiles, executions, frame = autonomous
    cfg['production_recovery'] = copy.deepcopy(DEFAULT_POLICY)
    from tests.test_factory_role_routing import routing_config
    cfg['generation_pipeline_config'] = routing_config()
    cfg['generation_pipeline_config']['llm_roles']['storyboard_director'] = 'qwen'
    prop, scene = frame.parent / 'prop.png', frame.parent / 'scene.png'
    Image.new('RGB', (64, 64), 'red').save(prop)
    Image.new('RGB', (64, 64), 'blue').save(scene)
    reviewed = []
    def evaluate(project, shot, path, refs):
        reviewed.append(list(refs))
        return {'status': 'pass', 'score': 94, 'issues': [], 'scope': 'Synthetic CPU fixture, not model quality'}
    run = production._Run(project, cfg, None, None, evaluator=evaluate)
    shot = run.shots[0]
    target = run._target(shot['id'], run.folder / 'assets/shots' / shot['id'], shot['prompt'],
        [frame, prop, scene], shot=shot, review=True, reference_roles=['lead'],
        asset_references=[{'kind': 'prop', 'entity_id': 'cup', 'sha256': core.sha256(prop)},
                          {'kind': 'scene', 'entity_id': 'room', 'sha256': core.sha256(scene)}])
    core.write_json(target['folder'] / 'take_001/attempt.json', {
        'take': 'take_001', 'attempt': 1, 'stage': 'image', 'state': 'failed', 'source_signature': target['signature'],
        'qa': {'status': 'fail', 'issues': [{'code': 'action', 'severity': 'blocking', 'detail': 'Synthetic extra prop.'}]}})
    def request(config, role, system, payload, schema, output, **kwargs):
        if 'compiled_visual_prompt' in payload:
            return {'passed': True, 'reason': 'Synthetic off-screen prop selection.'}
        result = candidate(payload['project']['approved_contract'], payload['project']['strategy'])
        result.pop('performance')
        result['plan'] = {k: v for k, v in result['plan'].items() if k in {'approved_action', 'camera_motion', 'image'}}
        result['offscreen_prop_reference_indices'] = [2]
        return result
    monkeypatch.setattr(llm, '_request', request)
    selected = run._render_targets([target], 'image')[0]
    assert selected['take'] == 'take_002' and len(executions) == 1
    graph = executions[0][1]
    loads = [node['inputs']['image'] for node in graph.values() if node['class_type'] == 'LoadImage']
    assert len(loads) == 2 and not any('prop.png' in name for name in loads)
    saved = core.read_json(target['folder'] / 'take_002/attempt.json')
    assert len(saved['references']) == 3 and len(saved['render_references']) == 2
    assert saved['prompt'].startswith('Approved rendering style for every person and the environment: ' + run.style_prompt)
    assert reviewed == [[str(frame), str(prop), str(scene)]]


def test_changed_approved_style_invalidates_frozen_repair_source(props):
    from factory import production_recovery as recovery
    project, config, target, qa, previous, root = props
    record = prepare(props)
    assert record['source']['approved_render_style'] == target['approved_render_style']
    target['approved_render_style'] = 'A newly approved watercolor style.'
    assert not recovery.applicable(record, project, config, target, qa, previous_attempt=previous, root=root)
