"""Four-input compositions keep every bound entity through independent review."""
import copy
import json
from pathlib import Path

import pytest
from PIL import Image

from factory import drama_assets, llm, production, reference_review
from factory.scheduler import ProductionNeedsDecision, ProductionPaused
from studio import core
from test_factory_drama_reference_prompts import case
from test_factory_dramas import isolated


@pytest.fixture
def composition(case):
    project = copy.deepcopy(case.episode)
    characters = [
        {'character_id': 'hero', 'name': '林青', 'identity_constraints': '成年男性，短黑发', 'default_wardrobe': '黑色合体外套', 'voice_id': 'male'},
        {'character_id': 'woman', 'name': '苏宁', 'identity_constraints': '成年女性，长发', 'default_wardrobe': '蓝色长裙', 'voice_id': 'female'},
        {'character_id': 'elder', 'name': '陈叔', 'identity_constraints': '年长男性，灰发', 'default_wardrobe': '灰色长袍', 'voice_id': 'male'}]
    project['plan'].update(characters=characters,
        scenes=[{'entity_id': 'court', 'name': '院落', 'description': '白墙红门的安静院落'}],
        props=[{'entity_id': 'ruler', 'name': '青铜重尺', 'description': '宽厚矩形尺体'}])
    project['episode'] = {'cast': {c['character_id']: c['identity_constraints'] for c in characters}, 'shots': []}
    project['styles'] = [{'id': 'approved', 'prompt': 'Approved watercolor'}]
    project['selected_style'] = 'approved'
    shared = {}
    def reference(kind, entity):
        ident = entity.get('character_id') or entity['entity_id']
        folder = case.folder / 'production' / 'approved-composition-inputs'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (kind + '-' + ident + '.png')
        Image.new('RGB', (288, 512), (20 + len(shared) * 20, 40, 130)).save(path)
        receipt = core.media_receipt(path, qa={'technical_passed': True, 'semantic': {'status': 'pass'}})
        record = {'kind': kind, 'entity_id': ident, 'plan_version': 1, 'receipt': receipt}
        shared[kind + ':' + ident] = record
        return path
    paths = [reference('character', c) for c in characters]
    for kind, entities in [('scene', project['plan']['scenes']), ('prop', project['plan']['props'])]:
        for entity in entities:
            reference(kind, entity)
    project['drama_references'] = shared
    engine = production._Run(project, case.render_config, None, None,
        evaluator=lambda p, s, path, refs: llm.review_media(p, s, path, refs, {}))
    shot = {'id': 'S01', 'characters': ['hero', 'woman', 'elder']}
    return case, engine, shot, paths, reference, shared


def test_first_batch_uses_exact_three_bound_characters_and_scene_not_unsupplied_prop(composition):
    case, engine, shot, paths, _, _ = composition
    before = copy.deepcopy(engine.project['drama_references'])
    result, note, provenance = drama_assets.shot_inputs(engine, shot, paths)
    assert len(case.wires) == 1 and len(result) == 2
    context = case.reviews[0]['shot']
    requirements = context['approved_reference_requirements']
    assert requirements['visible_character_count'] == 3 and context['characters'] == shot['characters']
    assert {(e['kind'], e['entity_id']) for e in requirements['entities']} == {
        ('character', 'hero'), ('character', 'woman'), ('character', 'elder'), ('scene', 'court')}
    assert not any(e['kind'] == 'prop' for e in requirements['entities'])
    bindings = context['reference_bindings']
    assert len(bindings) == 4
    assert [row['reference_index'] for row in bindings] == [1, 2, 3, 4]
    assert all(core.sha256(case.folder / row['path']) == row['sha256'] for row in bindings)
    for entity in requirements['entities'][:3]:
        original = next(c for c in engine.project['plan']['characters'] if c['character_id'] == entity['entity_id'])
        assert entity['approved_facts'] == original
    wire = json.dumps(case.wires[0], ensure_ascii=False)
    assert 'exactly 3 distinct people' in wire and 'Do not fuse people' in wire
    assert '林青' in wire and '苏宁' in wire and '陈叔' in wire
    assert '黑色合体外套' in wire and '蓝色长裙' in wire and '灰色长袍' in wire
    prompt = next(node['inputs']['text'] for node in case.wires[0].values() if node['class_type'] == 'CLIPTextEncode')
    assert len(prompt) < 3500
    assert all(token not in prompt for token in ('sha256', 'composition_manifest', 'approved-composition-inputs', str(case.folder)))
    assert all(token not in note for token in ('sha256', 'composition_manifest', 'approved-composition-inputs', str(case.folder)))
    for character in engine.project['plan']['characters']:
        assert prompt.count(character['name']) == 1
        assert prompt.count(character['identity_constraints']) == 1
        assert prompt.count(character['default_wardrobe']) == 1
    assert '青铜重尺' not in wire
    assert case.reviews[0]['reference_evidence']['supplied_image_count'] == 4
    facts = reference_review._approved_facts(case.reviews[0])
    assert any(item['field'] == 'visible_character_count' and item['text'] == '3' for item in facts)
    assert any(item['field'].endswith('.approved_facts.default_wardrobe') for item in facts)
    combined = next(item for item in provenance if item['kind'] == 'composition')
    manifest = core.read_json(case.folder / combined['composition_manifest']['path'])
    assert manifest['requirements'] == requirements
    assert manifest['output']['sha256'] == core.sha256(result[0])
    assert 'combined image' in note and engine.project['drama_references'] == before


def test_later_composition_inherits_all_prior_entities_and_correct_people_count(composition):
    case, engine, shot, paths, reference, shared = composition
    project = engine.project
    more_scene = {'entity_id': 'hall', 'name': '大厅', 'description': '灰色石柱大厅'}
    more_props = [{'entity_id': f'prop{i}', 'name': f'道具{i}', 'description': f'独立道具{i}'} for i in (2, 3)]
    project['plan']['scenes'].append(more_scene)
    project['plan']['props'].extend(more_props)
    reference('scene', more_scene)
    for prop in more_props:
        reference('prop', prop)
    project['drama_references'] = copy.deepcopy(shared)
    result, _, provenance = drama_assets.shot_inputs(engine, shot, paths)
    assert len(case.wires) == 2 and len(result) <= 4
    first, second = [c['shot']['approved_reference_requirements'] for c in case.reviews]
    assert first['visible_character_count'] == second['visible_character_count'] == 3
    inherited = second['reference_bindings'][0]
    assert inherited['composition_manifest'] and len(inherited['entities']) == 4
    assert {e['entity_id'] for e in inherited['entities']} == {'hero', 'woman', 'elder', 'court'}
    assert {e['entity_id'] for e in second['entities']} == {'hero', 'woman', 'elder', 'court', 'ruler', 'hall', 'prop2'}
    assert 'prop3' not in {e['entity_id'] for e in second['entities']}
    assert all(sum(node['class_type'] == 'LoadImage' for node in graph.values()) == 4 for graph in case.wires)
    assert len([item for item in provenance if item['kind'] == 'composition']) == 2
    for graph in case.wires:
        prompt = next(node['inputs']['text'] for node in graph.values() if node['class_type'] == 'CLIPTextEncode')
        assert len(prompt) <= 3500 and all(key not in prompt for key in ('sha256', 'composition_manifest', 'approved-composition-inputs'))
        for character in engine.project['plan']['characters']:
            assert prompt.count(character['identity_constraints']) == 1
            assert prompt.count(character['default_wardrobe']) == 1


def test_missing_or_merged_characters_still_block_without_publishing_manifest(composition, monkeypatch):
    case, engine, shot, paths, _, _ = composition
    def transport(cfg, role, system, payload, schema, output_dir, *args, **kwargs):
        context = json.loads(kwargs['content'][0]['text'])
        if 'candidate_reference_claims_untrusted' not in context:
            return {'status': 'fail', 'score': 30, 'summary': '缺少两位批准人物', 'issues': [
                {'code': 'identity', 'severity': 'blocking', 'detail': '只看到男主，缺少蓝裙女性和灰袍长者'}]}
        facts = context['approved_facts']
        criterion = next(item for item in facts if item['field'] == 'purpose')
        issue = context['candidate_reference_claims_untrusted'][0]['claim']
        return {'review': {'status': 'fail', 'score': 30, 'summary': '确实缺少两人', 'issues': [issue]},
            'findings': [{'issue_id': 'I1', 'status': 'confirmed', 'basis_status': 'supported_requirement',
                'fact_citations': [{'fact_id': criterion['fact_id'], 'quote': 'exactly 3 distinct people'}],
                'evidence_ids': ['F0'], 'detail': 'F0只有一位黑衣男主，批准要求是三个彼此分开的人物，蓝裙女性和灰袍长者确实缺失'}]}
    monkeypatch.setattr(llm, '_request', transport)
    with pytest.raises(ProductionNeedsDecision):
        drama_assets.shot_inputs(engine, shot, paths)
    assert len(case.wires) == 1
    assert not list((engine.folder / 'assets/compositions').glob('**/manifests/*.json'))
    attempt = core.read_json(engine.folder / 'assets/compositions/S01/1/take_001/attempt.json')
    assert attempt['qa']['semantic']['status'] == 'fail'
    assert (engine.folder / 'assets/compositions/S01/1/take_001/image.png').exists()


def test_unbound_original_image_cannot_invent_character_identity_or_submit_composition(composition):
    case, engine, shot, paths, _, _ = composition
    unknown = case.folder / 'unknown.png'
    Image.new('RGB', (288, 512), 'orange').save(unknown)
    with pytest.raises(ProductionNeedsDecision) as error:
        drama_assets.shot_inputs(engine, shot, [unknown, *paths[1:]])
    assert error.value.reason == 'drama_composition_binding_required' and not case.wires


def test_resume_reuses_approved_composition_pixels_and_same_manifest(composition, monkeypatch):
    case, engine, shot, paths, _, _ = composition
    first, _, first_provenance = drama_assets.shot_inputs(engine, shot, paths)
    original = engine._render_targets
    once = True
    def paused(targets, phase):
        nonlocal once
        if once:
            once = False
            raise ProductionPaused('模拟合成步骤边界暂停')
        return original(targets, phase)
    monkeypatch.setattr(engine, '_render_targets', paused)
    with pytest.raises(ProductionPaused):
        drama_assets.shot_inputs(engine, shot, paths)
    resumed, _, provenance = drama_assets.shot_inputs(engine, shot, paths)
    assert resumed == first and len(case.wires) == 1
    assert [v for v in provenance if v['kind'] == 'composition'] == [v for v in first_provenance if v['kind'] == 'composition']


@pytest.mark.parametrize('provider,provider_limit', [('legacy', 10000), ('comfyui_qwen_image21', 5000)])
def test_long_complete_facts_stop_before_gpu_instead_of_truncating_or_deferring_bad_retakes(composition, provider, provider_limit):
    case, engine, shot, paths, _, _ = composition
    long_identity = '批准的身份事实。' * 650 + '最后的身份要求不能丢弃'
    engine.project['plan']['characters'][2]['identity_constraints'] = long_identity
    engine.config['image_connection']['provider'] = provider
    before = copy.deepcopy(engine.project['plan'])
    with pytest.raises(ProductionNeedsDecision) as error:
        drama_assets.shot_inputs(engine, shot, paths)
    assert error.value.reason == 'drama_composition_prompt_capacity'
    assert error.value.details['provider_limit'] == provider_limit
    assert error.value.details['repairable_prompt_limit'] == 3500
    assert error.value.details['prompt_characters'] > 4000
    assert not case.wires and not case.reviews and engine.project['plan'] == before
    assert engine.project['plan']['characters'][2]['identity_constraints'].endswith('最后的身份要求不能丢弃')
