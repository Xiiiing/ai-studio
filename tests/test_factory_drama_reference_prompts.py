"""Shared-reference prompts compile approved facts; QA receives independent facts."""
import copy
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from factory import config, drama_assets, drama_planning, llm, production, scheduler, store
from factory.scheduler import ProductionNeedsDecision, ProductionPaused
from studio import core
from test_factory_dramas import isolated, ready


@pytest.fixture
def case(isolated, monkeypatch):
    for module in (drama_assets, llm, production, scheduler):
        monkeypatch.setattr(module, 'ROOT', isolated)
    root = ready()
    current = store.get(root['episodes'][0]['id'], False)
    document = copy.deepcopy(current['plan'])
    document['characters'][0].update(name='林青', identity_constraints='成年女性，稳重，短发',
                                     default_wardrobe='蓝色外套，左侧携带青铜重尺')
    document['props'] = [{'entity_id': 'heavy_ruler', 'name': '青铜重尺',
                           'description': '林青随身携带的重尺，厚实宽大的直长矩形尺体，可承载能量'}]
    document['visual_rules'] = '水彩画风，保持蓝色外套和青铜装备的颜色'
    current = store.update(current['id'], {'plan': document})
    render_config = {'max_gpu_seconds': 300, 'max_wall_seconds': 300, 'max_attempts': 1,
        'image_width': 288, 'image_height': 512, 'output_width': 288, 'output_height': 512,
        'semantic_enabled': True, 'semantic_review_revision': 'reference-facts-test',
        'image_connection': {'provider': 'legacy', 'model': 'FLUX.2-klein-4B'}}
    monkeypatch.setattr(config, 'production_config', lambda *args: copy.deepcopy(render_config))
    monkeypatch.setattr(config, 'llm_identity', lambda cfg, role: {'model': cfg.get('compiler_model', 'fixture-compiler'), 'role': role})
    monkeypatch.setattr(llm, 'resolve_llm', lambda *args, **kwargs: {'model': 'fixture-vision', 'profile': 'vision'})
    calls, wires, reviews = [], [], []
    state = {'review_fail': False, 'pause_compiler': False,
             'compiled': 'One adult woman 林青 in a blue coat; 青铜重尺, a broad thick straight rectangular bronze ruler carried at her left side. Watercolor, single static instant, neutral background.'}

    def request(cfg, role, system, payload, schema, output_dir, *args, **kwargs):
        if role == 'prompt_style_compiler':
            calls.append(copy.deepcopy(payload))
            if state['pause_compiler']:
                state['pause_compiler'] = False
                store.update(current['id'], {'pause_requested': True})
            return {'prompt': state['compiled']}
        assert role == 'visual_critic'
        context = json.loads(kwargs['content'][0]['text'])
        reviews.append(context)
        return {'status': 'fail' if state['review_fail'] else 'pass', 'score': 35 if state['review_fail'] else 95,
                'summary': '独立检查原批准参考事实', 'issues': [
                    {'code': 'off_prompt', 'severity': 'blocking', 'detail': '候选像素中的短棍缺少批准重尺宽大矩形尺体'}] if state['review_fail'] else []}

    monkeypatch.setattr(llm, '_request', request)

    class Worker:
        def copy_input(self, path):
            return str(path)

        def execute(self, graph, destination, kind, **kwargs):
            assert kind == 'image'
            wires.append(copy.deepcopy(graph))
            folder = Path(destination)
            folder.mkdir(parents=True, exist_ok=True)
            image = Image.new('RGB', (288, 512), 'navy')
            draw = ImageDraw.Draw(image)
            for x in range(0, 288, 20):
                draw.rectangle((x, 0, x + 8, 512), fill=(100, 150, 180))
            draw.ellipse((72, 102, 216, 307), fill=(213, 177, 142))
            path = folder / 'image.png'
            image.save(path)
            core.write_json(folder / 'result.json', core.media_receipt(path, origin='test_fixture_not_model'))
            return path

    @contextmanager
    def worker(self, *args, **kwargs):
        self.budget.check()
        yield Worker()

    monkeypatch.setattr(scheduler.Scheduler, 'worker', worker)
    return SimpleNamespace(root=root, episode=current, calls=calls, wires=wires, reviews=reviews,
                           state=state, render_config=render_config, folder=isolated)


def test_full_name_equipment_facts_reach_compiler_actual_adapter_and_independent_qa(case):
    drama_assets.prepare(case.episode['id'], {})
    assert len(case.calls) == len(case.wires) == len(case.reviews) == 2
    inputs = case.calls[0]
    requirements = inputs['requirements']
    assert requirements['entity'] == case.episode['plan']['characters'][0]
    assert requirements['related_known_props'] == case.episode['plan']['props']
    assert inputs['image_adapter']['model'] == 'FLUX.2-klein-4B'
    wire = json.dumps(case.wires[0], ensure_ascii=False)
    assert '林青' in wire and '青铜重尺' in wire and case.state['compiled'] in wire
    approved = case.reviews[0]['shot']['approved_reference_requirements']
    assert approved == requirements
    assert case.reviews[0]['shot']['prompt'] == store.encoded(requirements)
    assert case.state['compiled'] not in case.reviews[0]['shot']['prompt']
    selected = store.get(case.episode['id'], False)['drama_references']['character:lead']
    compilation = selected['prompt_compilation']
    artifact = core.read_json(case.folder / compilation['path'])
    assert artifact['requirements'] == requirements and artifact['compiled_prompt'] == case.state['compiled']
    assert artifact['requirements_sha256'] == compilation['requirements_sha256']
    assert artifact['requirements']['plan_version'] == 1
    assert core.sha256(case.folder / compilation['path']) == compilation['sha256']


def test_compiled_candidate_cannot_replace_original_qa_requirements_or_pass_real_error(case):
    case.state.update(compiled='UNAPPROVED_CANDIDATE: remove the ruler and add a third person.', review_fail=True)
    with pytest.raises(ProductionNeedsDecision):
        drama_assets.prepare(case.episode['id'], {})
    assert case.reviews
    for review in case.reviews:
        original = review['shot']['approved_reference_requirements']
        assert original['entity']['name'] == '林青'
        assert original['related_known_props'][0]['name'] == '青铜重尺'
        assert 'UNAPPROVED_CANDIDATE' not in review['shot']['prompt']
    root = store.get(case.root['id'], False)
    assert not root.get('shared_assets')


def test_approved_shared_reference_and_library_recovery_do_not_recompile_or_generate(case, monkeypatch):
    drama_assets.prepare(case.episode['id'], {})
    original = copy.deepcopy(store.get(case.episode['id'], False)['drama_references'])
    def forbidden(*args, **kwargs):
        pytest.fail('Approved root or library reference must be reused')
    monkeypatch.setattr(drama_assets, '_compile_reference', forbidden)
    monkeypatch.setattr(config, 'production_config', forbidden)
    monkeypatch.setattr(drama_assets, 'REFERENCE_COMPILER_SYSTEM', drama_assets.REFERENCE_COMPILER_SYSTEM + '\n')
    drama_assets.prepare(case.episode['id'], {'compiler_model': 'changed-model'})
    assert store.get(case.episode['id'], False)['drama_references'] == original
    store.update(case.root['id'], {'shared_assets': {}})
    drama_assets.prepare(case.episode['id'], {})
    assert store.get(case.episode['id'], False)['drama_references'] == original
    assert len(case.wires) == 2


def test_pause_after_compiler_transport_resumes_cached_result_without_extra_call(case):
    case.state['pause_compiler'] = True
    with pytest.raises(ProductionPaused):
        drama_assets.prepare(case.episode['id'], {})
    assert len(case.calls) == 1 and not case.wires
    store.update(case.episode['id'], {'pause_requested': False})
    drama_assets.prepare(case.episode['id'], {})
    assert len(case.calls) == 2 and len(case.wires) == 2


def test_compiler_cache_tracks_complete_requirements_plan_adapter_model_and_snapshot(case):
    current = case.episode
    requirements = drama_assets._reference_requirements(current, 'character', current['plan']['characters'][0])
    profile = case.render_config['image_connection']
    first = drama_assets._compile_reference(current, {}, requirements, profile)
    assert drama_assets._compile_reference(current, {}, requirements, profile) == first
    assert len(case.calls) == 1
    changed = copy.deepcopy(requirements)
    changed['visual_rules'] += ' 使用暖色背景'
    altered_plan = {**current, 'plan_version': 2}
    changed_plan = {**requirements, 'plan_version': 2}
    altered_snapshot = {**current, 'prompt_snapshot': {'versions': {}, 'overrides': {'project': '私有项目事实'}}}
    versions = [
        drama_assets._compile_reference(current, {}, changed, profile),
        drama_assets._compile_reference(altered_plan, {}, changed_plan, profile),
        drama_assets._compile_reference(current, {}, requirements, {**profile, 'model': 'changed-adapter'}),
        drama_assets._compile_reference(current, {'compiler_model': 'changed-model'}, requirements, profile),
        drama_assets._compile_reference(altered_snapshot, {}, requirements, profile),
    ]
    assert len(case.calls) == 6
    assert len({item[1]['signature'] for item in [first, *versions]}) == 6
    assert (case.folder / first[1]['path']).is_file()


def test_long_approved_facts_are_not_silently_truncated(case):
    current = copy.deepcopy(case.episode)
    current['plan']['visual_rules'] = '批准颜色纹理；' * 700 + '末尾必须保留的风格事实'
    requirements = drama_assets._reference_requirements(current, 'character', current['plan']['characters'][0])
    drama_assets._compile_reference(current, {}, requirements, case.render_config['image_connection'])
    assert case.calls[0]['requirements']['visual_rules'].endswith('末尾必须保留的风格事实')
    assert len(store.encoded(case.calls[0]['requirements'])) > 4500


@pytest.mark.parametrize('kind', ['character', 'scene', 'prop', 'composition'])
def test_reference_review_uses_actual_zero_image_basis_and_not_story_action(case, kind):
    path = case.folder / 'candidate.png'
    Image.new('RGB', (288, 512), 'navy').save(path)
    project = {**case.episode, 'format': 'animation', 'creative_contract': {
        'schema_version': 1, 'visual_style': 'anime_2d', 'story_mode': 'standalone',
        'pacing': 'short_drama', 'motion': 'generated_video'}}
    shot = {'id': 'reference', 'review_purpose': kind + '_reference', 'prompt': '原批准事实',
            'action': 'UNRELATED_FUTURE_ACTION', 'approved_reference_requirements': {'entity': {'name': '林青'}}}
    calls = []
    def transport(*args, **kwargs):
        context = json.loads(kwargs['content'][0]['text'])
        calls.append(context)
        assert context['reference_evidence'] == {'supplied_image_count': 0, 'comparison_available': False}
        assert '不得声称脸、服装、道具与某张参考图不一致' in context['reference_evidence_rule']
        assert not context['approved_sequence'] and 'keyframe_rule' not in context
        assert '不是故事镜头' in context['任务']
        return {'status': 'pass', 'score': 95, 'summary': '参考用途', 'issues': [],
                'style_alignment': {'status': 'match', 'evidence': '候选符合明确批准的二维动画画风要求'}}
    from unittest.mock import patch
    with patch.object(llm, '_request', transport), patch('factory.spatial_review.verify_spatial', side_effect=AssertionError('Reference is not an action starting frame')):
        result = llm.review_media(project, shot, path, [case.folder / 'missing-reference.png'], {})
    assert result['status'] == 'pass' and result['reference_count'] == 0 and len(calls) == 1


@pytest.mark.parametrize('unit', [False, True])
def test_short_episode_keeps_known_assets_while_unit_keeps_required_equipment(case, unit):
    current = copy.deepcopy(case.episode)
    document = copy.deepcopy(current['plan'])
    document['scenes'] = [
        {'entity_id': 'courtyard', 'name': '院子', 'description': '红门石院'},
        {'entity_id': 'station', 'name': '车站', 'description': '灰色站台'}]
    document['props'].append({'entity_id': 'key', 'name': '钥匙', 'description': '另一处使用的青铜钥匙'})
    store.update(current['id'], {'plan': document, 'record_kind': 'scene_unit' if unit else 'episode',
        **({'location_id': 'courtyard', 'prop_ids': []} if unit else {})})
    drama_assets.prepare(current['id'], {})
    selected = store.get(current['id'], False)['drama_references']
    assert 'character:lead' in selected and 'prop:heavy_ruler' in selected
    if unit:
        assert set(selected) == {'character:lead', 'scene:courtyard', 'prop:heavy_ruler'}
    else:
        assert set(selected) == {'character:lead', 'scene:courtyard', 'scene:station', 'prop:heavy_ruler', 'prop:key'}


def test_prop_association_needs_explicit_equipment_or_ownership_not_single_character_or_mention(case):
    current = copy.deepcopy(case.episode)
    entity = current['plan']['characters'][0]
    entity.update(name='林', default_wardrobe='蓝色外套')
    current['plan']['props'] = [{'entity_id': 'stick', 'name': '木棍', 'description': '森林里的木棍'}]
    assert drama_assets._reference_requirements(current, 'character', entity)['related_known_props'] == []
    entity['name'] = '林青'
    current['plan']['props'][0]['description'] = '林青曾见过的木棍，由另一人持有'
    assert drama_assets._reference_requirements(current, 'character', entity)['related_known_props'] == []
    current['plan']['props'][0]['description'] = '林青随身携带的木棍'
    assert drama_assets._reference_requirements(current, 'character', entity)['related_known_props'] == current['plan']['props']


def test_animation_medium_fact_reaches_compiler_actual_wire_and_qa_without_imposing_2d_or_3d(case, monkeypatch):
    store.update(case.episode['id'], {'format': 'animation'})
    case.state['compiled'] = 'CG animated identity reference of 林青, wearing blue coat and carrying 青铜重尺, thick broad rectangular ruler. Not a live-action photograph.'
    original_transport = llm._request
    def transport(cfg, role, system, payload, schema, output_dir, *args, **kwargs):
        if role == 'prompt_style_compiler':
            assert payload['requirements']['production_format'] == 'animation'
            assert 'animation本身不强制二维或三维' in system
            assert '不能生成真人实拍照片' in system
        return original_transport(cfg, role, system, payload, schema, output_dir, *args, **kwargs)
    monkeypatch.setattr(llm, '_request', transport)
    drama_assets.prepare(case.episode['id'], {})
    assert len(case.wires) == 2
    assert all('CG animated' in json.dumps(wire, ensure_ascii=False) for wire in case.wires)
    assert all(review['shot']['approved_reference_requirements']['production_format'] == 'animation' for review in case.reviews)
    from factory.reference_review import _approved_facts
    assert any(item['field'] == 'production_format' and item['text'] == 'animation'
               for item in _approved_facts(case.reviews[0]))


def test_medium_change_invalidates_unapproved_reference_compiler_cache(case):
    current = case.episode
    entity = current['plan']['characters'][0]
    requirements = drama_assets._reference_requirements(current, 'character', entity)
    profile = case.render_config['image_connection']
    first = drama_assets._compile_reference(current, {}, requirements, profile)
    assert drama_assets._compile_reference(current, {}, requirements, profile) == first
    changed_project = {**current, 'format': 'animation'}
    changed = drama_assets._reference_requirements(changed_project, 'character', entity)
    second = drama_assets._compile_reference(changed_project, {}, changed, profile)
    assert len(case.calls) == 2 and first[1]['signature'] != second[1]['signature']
    assert first[1]['requirements_sha256'] != second[1]['requirements_sha256']
    assert (case.folder / first[1]['path']).exists() and (case.folder / second[1]['path']).exists()
    assert changed['production_format'] == 'animation'


def test_existing_approved_shared_reference_is_not_regenerated_by_medium_fact_support(case, monkeypatch):
    drama_assets.prepare(case.episode['id'], {})
    original = copy.deepcopy(store.get(case.episode['id'], False)['drama_references'])
    store.update(case.episode['id'], {'format': 'animation'})
    def forbidden(*args, **kwargs):
        pytest.fail('An existing approved shared reference must not be recreated')
    monkeypatch.setattr(drama_assets, '_compile_reference', forbidden)
    monkeypatch.setattr(config, 'production_config', forbidden)
    drama_assets.prepare(case.episode['id'], {})
    assert store.get(case.episode['id'], False)['drama_references'] == original
    assert len(case.wires) == 2


@pytest.mark.parametrize('production_format', ['motion_comic', 'live_action'])
def test_other_medium_preserves_supplied_creative_contract_without_inferring_new_style(case, production_format):
    current = copy.deepcopy(case.episode)
    contract = {'schema_version': 1, 'visual_style': 'existing_approved_style', 'motion': 'existing_approved_motion'}
    current.update(format=production_format, creative_contract=contract)
    facts = drama_assets._reference_requirements(current, 'character', current['plan']['characters'][0])
    assert facts['production_format'] == production_format and facts['creative_contract'] == contract
    facts['creative_contract']['visual_style'] = 'not-an-edit-to-project'
    assert current['creative_contract'] == contract
    from factory.reference_review import _approved_facts
    assert any(item['field'] == 'creative_contract.motion' and item['text'] == contract['motion']
               for item in _approved_facts({'shot': {'approved_reference_requirements': facts}}))


def test_controller_instruction_hash_changes_artifact_without_overwriting_old_result(case, monkeypatch):
    current = case.episode
    requirements = drama_assets._reference_requirements(current, 'character', current['plan']['characters'][0])
    profile = case.render_config['image_connection']
    first = drama_assets._compile_reference(current, {}, requirements, profile)
    old_path = case.folder / first[1]['path']
    old_bytes = old_path.read_bytes()
    original_system = drama_assets.REFERENCE_COMPILER_SYSTEM
    assert first[1]['compiler_system_sha256'] == core.fingerprint(original_system)
    # A formatting-only controller change must still select a distinct artifact.
    monkeypatch.setattr(drama_assets, 'REFERENCE_COMPILER_SYSTEM', original_system + '\n')
    second = drama_assets._compile_reference(current, {}, requirements, profile)
    assert len(case.calls) == 2 and first[1]['signature'] != second[1]['signature']
    assert first[1]['path'] != second[1]['path'] and old_path.read_bytes() == old_bytes
    assert first[1]['compiler_system_sha256'] != second[1]['compiler_system_sha256']
    assert core.read_json(case.folder / second[1]['path'])['compiler_system_sha256'] == core.fingerprint(original_system + '\n')
    assert drama_assets._compile_reference(current, {}, requirements, profile) == second
    assert len(case.calls) == 2 and old_path.read_bytes() == old_bytes


def test_actual_compiler_instructions_keep_names_out_of_images_and_architecture_surfaces_blank(case, monkeypatch):
    observed = []
    original_transport = llm._request
    def transport(cfg, role, system, payload, schema, output_dir, *args, **kwargs):
        if role == 'prompt_style_compiler':
            observed.append(system)
            assert '实体原名仅作身份对应资料，不得写在画面' in system
            assert '角色、道具和环境均不生成可读文字或标记' in system
            assert 'blank/unlettered surfaces' in system
            assert '保留批准的金色装饰和建筑结构' in system
            assert '不要使用Chinese calligraphy或ornate lettering' in system
        return original_transport(cfg, role, system, payload, schema, output_dir, *args, **kwargs)
    monkeypatch.setattr(llm, '_request', transport)
    current = case.episode
    facts = drama_assets._reference_requirements(current, 'character', current['plan']['characters'][0])
    drama_assets._compile_reference(current, {}, facts, case.render_config['image_connection'])
    assert len(observed) == 1 and case.calls[0]['compiler_system_sha256'] == core.fingerprint(drama_assets.REFERENCE_COMPILER_SYSTEM)
