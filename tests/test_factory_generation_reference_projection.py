"""Model-facing correspondence is compact; frozen evidence remains complete."""
import copy
import json

from PIL import Image
import pytest

from factory import generation_pipeline as g, schemas
from factory.image_generation import image_request
from test_factory_generation_pipeline import production, shot, transport
from test_factory_team_ledger import project


def test_prepare_projects_actual_reference_order_without_internal_provenance(production, monkeypatch):
    p, frame, config = production
    refs = [frame]
    for index, color in enumerate(('red', 'blue', 'green'), 2):
        path = frame.with_name(f'reference-{index}.png')
        Image.new('RGB', (64, 64), color).save(path)
        refs.append(path)
    internal = {'path': 'private/full/source.png', 'sha256': 'e' * 64,
                'composition_manifest': {'path': 'private/manifest.json', 'sha256': 'f' * 64},
                'purpose': 'arbitrary private instructions must not enter the image prompt'}
    bindings = [
        {**internal, 'kind': 'composition', 'entity_id': 'combined-cast', 'sha256': g.sha256(refs[0]),
         'entities': [
             {**internal, 'kind': 'character', 'entity_id': 'Alice'},
             {**internal, 'kind': 'composition', 'entity_id': 'inherited-pair', 'entities': [
                 {**internal, 'kind': 'character', 'entity_id': 'Bob'},
                 {**internal, 'kind': 'character', 'entity_id': 'Carol'}]}]},
        {**internal, 'kind': 'scene', 'entity_id': 'Hall', 'sha256': g.sha256(refs[1])},
        {**internal, 'kind': 'prop', 'entity_id': 'Cup', 'sha256': g.sha256(refs[2])},
    ]
    original = copy.deepcopy(bindings)
    calls = transport(monkeypatch)
    result = g.prepare(p, shot(), refs, config=config, phase='image',
                       reference_bindings=bindings, identities=[('female', refs[0])])
    prompt = result['parameters']['prompt']
    mapping = json.loads(prompt.split('Actual reference correspondence (1-based image order): ', 1)[1])
    assert mapping == [
        {'reference_index': 1, 'character_ids': ['female'], 'materials': [
            {'kind': 'composition', 'entity_id': 'combined-cast', 'entities': [
                {'kind': 'character', 'entity_id': 'Alice'},
                {'kind': 'composition', 'entity_id': 'inherited-pair', 'entities': [
                    {'kind': 'character', 'entity_id': 'Bob'}, {'kind': 'character', 'entity_id': 'Carol'}]}]}]},
        {'reference_index': 2, 'character_ids': [], 'materials': [{'kind': 'scene', 'entity_id': 'Hall'}]},
        {'reference_index': 3, 'character_ids': [], 'materials': [{'kind': 'prop', 'entity_id': 'Cup'}]},
        {'reference_index': 4, 'character_ids': [], 'materials': []},
    ]
    for forbidden in ('private/', 'sha256', 'composition_manifest', 'purpose', 'arbitrary private', str(frame)):
        assert forbidden not in prompt
    assert all(g.sha256(path) not in prompt for path in refs)
    assert result['source']['reference_bindings'] == original == bindings
    assert result['source']['reference_map'][0]['materials'][0] == original[0]
    assert result['source']['references'] == [g.media_receipt(path) for path in refs]
    assert result['parameters']['reference_sha256'] == [g.sha256(path) for path in refs]
    request = image_request({'provider': 'legacy'}, prompt, 7, 576, 1024, ['one', 'two', 'three', 'four'])
    assert request['4']['inputs']['text'] == prompt
    assert len([node for node in request.values() if node['class_type'] == 'LoadImage']) == 4
    # Pause/resume keeps the frozen compilation and all evidence, without a call.
    assert g.prepare(p, shot(), refs, config=config, phase='image',
                     reference_bindings=bindings, identities=[('female', refs[0])]) == result
    assert len(calls) == 1


@pytest.mark.parametrize('state', ['approved_plan', 'in_flight', 'approved_legacy'])
def test_existing_production_selections_and_inflight_plans_keep_original_prompt(production, monkeypatch, state):
    from factory import production as renderer
    from factory.gpu_client import GPUClient
    p, frame, config = production
    monkeypatch.setattr(renderer, 'ROOT', g.ROOT)
    calls = transport(monkeypatch)
    cfg = {**config, 'video_connection': {'provider': 'comfyui_wan', 'model': 'Wan2.2-TI2V-5B', 'enabled': True},
           'image_width': 288, 'image_height': 512}
    run = renderer._Run(p, cfg, None, None)
    folder = run.folder / 'assets/shots/S01'
    if state == 'approved_legacy':
        legacy = copy.deepcopy(run.project)
        legacy['generation_pipeline'] = None
        prior_run = renderer._Run(legacy, cfg, None, None)
        prior = prior_run._target('S01', folder, 'original request', [frame], shot=prior_run.shots[0])
        receipt = {**g.media_receipt(frame), 'source_signature': prior['signature'], 'qa': {'status': 'pass'}}
        g.write_json(folder / 'selected.json', receipt)
        frozen = None
    else:
        prior = run._target('S01', folder, 'original request', [frame], shot=run.shots[0])
        frozen = copy.deepcopy(prior['generation_plan'])
        # Emulate a pre-projection plan. Its full provenance remains unchanged.
        frozen['parameters']['prompt'] = 'Original frozen prompt with provenance ' + json.dumps(frozen['source']['reference_map'])
        frozen['signature'] = 'old-approved-compiler-signature'
        if state == 'approved_plan':
            receipt = {**g.media_receipt(frame), 'source_signature': prior['signature'],
                       'qa': {'status': 'pass'}, 'generation_plan': frozen}
            g.write_json(folder / 'selected.json', receipt)
        else:
            g.write_json(folder / 'take_001/attempt.json', {'state': 'waiting_remote', 'generation_plan': frozen})
    before = len(calls)
    monkeypatch.setattr(schemas, 'cached_json_call', lambda *args, **kwargs: pytest.fail('Must not recompile a frozen request'))
    monkeypatch.setattr(GPUClient, 'run', lambda *args, **kwargs: pytest.fail('Must not submit media while restoring a frozen target'))
    preserved = run._target('S01', folder, 'original request', [frame], shot=run.shots[0])
    assert len(calls) == before
    if frozen:
        assert preserved['generation_plan'] == frozen
        assert preserved['prompt'] == frozen['parameters']['prompt']
    else:
        assert 'generation_plan' not in preserved
    if state != 'in_flight':
        assert renderer.read_json(folder / 'selected.json') == receipt
