"""Real request fences and frozen projections; no test calls a model."""
import copy
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from factory import model_capabilities as c, generation_pipeline, production, providers
from factory.scheduler import ProductionNeedsDecision
from factory.video_providers import H3_PRODUCTION_PROFILE


def configuration():
    return {'image_connection': {'provider': 'comfyui_qwen_image21', 'model': 'Qwen-Image-2.1', 'enabled': True},
            'video_connection': copy.deepcopy(H3_PRODUCTION_PROFILE),
            'audio_connection': {'provider': 'legacy', 'model': 'Qwen3-TTS', 'enabled': True}}


def project():
    return {'id': 'a' * 12, 'format': 'animation'}


def request(**updates):
    return {'prompt': 'An adult turns beside a cup.', 'references': ['frame.png'],
            'width': 576, 'height': 1024, 'seconds': 5, **updates}


def test_actual_h3_contract_does_not_inherit_other_h3_protocols():
    cap = c.describe(H3_PRODUCTION_PROFILE, 'video')
    assert cap['reference_counts'] == [1]
    assert cap['first_frame'] is True and not cap['last_frame']
    assert not cap['reference_video'] and not cap['reference_audio'] and not cap['native_audio']
    assert cap['dimensions']['max_pixels'] == 768 * 1344
    assert cap['verification'] == 'adapter_contract_only_not_generation_or_vendor_verification'


@pytest.mark.parametrize('changes,field', [
    ({'references': []}, 'references'), ({'references': ['a', 'b']}, 'references'),
    ({'width': 577}, 'dimensions'), ({'width': 1536, 'height': 1536}, 'dimensions'),
    ({'fps': 30}, 'fps'), ({'seconds': 16}, 'seconds'), ({'seconds': float('nan')}, 'seconds'),
    ({'seconds': True}, 'seconds'), ({'last_frame': 'tail.png'}, 'last_frame'),
    ({'reference_video': ['clip.mp4']}, 'reference_video'),
    ({'reference_audio': ['voice.wav']}, 'reference_audio'), ({'native_audio': True}, 'native_audio'),
    ({'lip_sync': True}, 'lip_sync'),
])
def test_requests_rejected_without_mutating_inputs(changes, field):
    value = request(**changes)
    before = copy.deepcopy(value)
    with pytest.raises(c.CapabilityError) as error:
        c.validate(c.describe(H3_PRODUCTION_PROFILE, 'video'), value)
    assert error.value.details['field'] == field
    assert value.keys() == before.keys()
    if 'references' in changes:
        assert value['references'] == before['references']


@pytest.mark.parametrize('duration,resolution,valid', [(6, '768P', True), (10, '768P', True),
    (6, '1080P', True), (10, '1080P', False), (6, '512P', False)])
def test_minimax_combination_matrix_shared_with_catalog(duration, resolution, valid):
    cap = c.describe({'provider': 'minimax', 'model': 'MiniMax-Hailuo-2.3', 'duration': duration}, 'video')
    value = request(duration=duration, resolution=resolution)
    if valid:
        assert c.validate(cap, value)['status'] == 'valid'
    else:
        with pytest.raises(c.CapabilityError): c.validate(cap, value)


def test_qwen_and_flux_reference_counts_match_executable_bounds():
    qwen = c.describe(configuration()['image_connection'], 'image')
    assert qwen['reference_counts'] == [0, 1, 2, 3, 4]
    for provider in ('legacy', 'comfyui_local', 'comfyui_external'):
        assert c.describe({'provider': provider}, 'image')['reference_counts'] == [0, 1, 2, 3, 4]
    c.validate(qwen, request(references=[], seconds=None))
    with pytest.raises(c.CapabilityError): c.validate(qwen, request(width=128))
    with pytest.raises(c.CapabilityError): c.validate(qwen, request(references=['a'] * 5))


def test_cloud_dimensions_cannot_silently_upscale_small_request():
    cap = c.describe({'provider': 'dashscope_image', 'model': 'qwen-image-3.0-pro'}, 'image')
    assert cap['reference_counts'] == [0, 1, 2, 3]
    with pytest.raises(c.CapabilityError, match='不会自动缩放'):
        c.validate(cap, request(width=128, height=128, references=[]))


def test_actual_reference_bytes_checked_before_paid_request(tmp_path):
    cap = c.describe({'provider': 'minimax', 'model': 'MiniMax-Hailuo-2.3'}, 'video')
    frame = tmp_path / 'frame.png'
    Image.new('RGB', (256, 512), 'red').save(frame)
    with pytest.raises(c.CapabilityError, match='尺寸或比例'):
        c.validate(cap, request(references=[frame], inspect_files=True))
    Image.new('RGB', (384, 704), 'red').save(frame)
    assert c.validate(cap, request(references=[frame], inspect_files=True))['status'] == 'valid'


def test_unknown_adapter_is_explicit_and_never_submitted():
    cap = c.describe({'provider': 'made_up_adapter', 'model': 'anything'}, 'video')
    assert cap['status'] == 'unknown' and cap['unknown'] == ['adapter']
    with pytest.raises(c.CapabilityError, match='尚未明确'): c.validate(cap, request())


def test_frozen_snapshot_does_not_follow_config_or_secret_changes():
    cfg, p = configuration(), project()
    saved = copy.deepcopy(c.pin(p, cfg))
    cfg['video_connection']['base_url'] = 'https://other.example'
    cfg['video_connection']['api_key_env'] = 'NOT_A_CAPABILITY'
    assert c.active(p, cfg, 'video') == saved['profiles']['video']
    cfg['video_connection']['model'] = 'different-model'
    assert c.pin(p, cfg) == saved
    with pytest.raises(c.CapabilityError, match='明确应用'): c.active(p, cfg, 'video')
    assert c.adopt(p, cfg)['profiles']['video']['model'] == 'different-model'
    assert saved['profiles']['video']['model'] == 'MiniMax-H3'
    assert p['model_capability_history'] == [saved]


def test_legacy_project_reads_do_not_create_snapshots():
    p = project()
    assert not c.enabled(p)
    c.active(p, configuration(), 'video')
    assert 'model_capability_snapshot' not in p


def test_generation_planner_uses_the_same_pinned_contract():
    p, cfg = project(), configuration()
    c.pin(p, cfg)
    cap = generation_pipeline.capabilities(cfg, p, 'image')
    assert cap['schema'] == c.VERSION
    assert cap['reference_counts'] == p['model_capability_snapshot']['profiles']['image']['reference_counts']
    assert cap['reference_limit'] == 4 and cap['single_instant'] is True


@pytest.mark.parametrize('kind,width,height,seconds,refs', [
    ('image', 128, 128, None, []),
    ('image', 576, 1024, None, ['frame'] * 5),
    ('video', 576, 1024, 16, ['frame']),
    ('video', 576, 1024, 6, ['frame', 'another']),
])
def test_real_attempt_boundary_rejects_before_discovery_upload_or_model(monkeypatch, kind, width, height, seconds, refs):
    # Call the real attempt entry point; any service discovery or other side
    # effect would fail this test before an attempt folder can be created.
    from factory import drama_resources
    monkeypatch.setattr(drama_resources, 'ensure', lambda *a, **k: pytest.fail('Invalid request reached discovery'))
    run = object.__new__(production._Run)
    run.project, run.config = project(), configuration()
    c.pin(run.project, run.config)
    run.image_size = run.video_size = (width, height)
    target = {'kind': kind, 'prompt': 'Approved scene.', 'references': refs, 'seconds': seconds}
    with pytest.raises(ProductionNeedsDecision) as error:
        run._new_attempt(target)
    assert error.value.reason == 'model_capability'
    assert 'field' in error.value.details


def test_custom_workflow_reference_slots_and_missing_parameters_are_explicit(monkeypatch):
    from factory import workflow_templates as wt
    template = {'kind': 'video', 'mappings': [{'source': 'prompt'}, {'source': 'reference:0'}]}
    monkeypatch.setattr(wt, 'load_template', lambda ident: template)
    cap = c.describe({'provider': 'comfyui_external', 'model': 'custom', 'workflow_template': 'a' * 64}, 'video')
    assert cap['reference_counts'] == [1] and cap['status'] == 'partial'
    with pytest.raises(c.CapabilityError, match='未映射制作参数'):
        c.validate(cap, request())
    template['mappings'] += [{'source': s} for s in ('width', 'height', 'frames', 'fps')]
    cap = c.describe({'provider': 'comfyui_external', 'model': 'custom', 'workflow_template': 'a' * 64}, 'video')
    assert c.validate(cap, request())['status'] == 'valid'


def test_public_inventory_separates_capabilities_from_editable_profiles():
    from factory.config import ROOT
    import json
    config = json.loads((ROOT / 'config/factory.empty.example.json').read_text())
    result = providers.public_inventory(config)
    assert result['model_capability_schema'] == c.VERSION
    for kind in ('image', 'audio', 'video'):
        assert set(result['model_capabilities'][kind]) == set(result[kind + '_profiles'])
        for key, cap in result['model_capabilities'][kind].items():
            assert cap == c.describe(result[kind + '_profiles'][key], kind)
            assert 'model_capabilities' not in result[kind + '_profiles'][key]
    assert result['model_capability_presets']['video']['comfyui_h3']['reference_counts'] == [1]


def test_motion_comic_does_not_require_an_enabled_video_generator():
    p, cfg = project(), configuration()
    p['format'] = 'motion_comic'
    cfg['video_connection']['enabled'] = False
    saved = c.pin(p, cfg)['profiles']['video']
    assert saved['provider'] == 'procedural_camera' and saved['enabled'] is True
    assert c.validate(saved, request())['status'] == 'valid'


def test_unknown_snapshot_version_cannot_fall_back_to_legacy():
    p = {**project(), 'model_capability_snapshot': {'version': 'future-contract'}}
    with pytest.raises(c.CapabilityError, match='版本不受'):
        c.validate_target(p, configuration(), {'kind': 'video'}, 576, 1024)


def test_adapter_name_cannot_masquerade_as_another_model():
    cap = c.describe({'provider': 'comfyui_qwen_image21', 'model': 'another-image-model'}, 'image')
    assert cap['status'] == 'unknown' and 'adapter_model_mismatch' in cap['unknown']
    with pytest.raises(c.CapabilityError): c.validate(cap, request(references=[]))


def test_unsupported_image_inputs_and_output_count_do_not_get_ignored():
    cap = c.describe(configuration()['image_connection'], 'image')
    for value in ({'first_frame': 'frame.png'}, {'output_count': 2}):
        with pytest.raises(c.CapabilityError): c.validate(cap, request(**value))


def test_empty_configuration_pin_is_read_only_and_contains_no_grants(monkeypatch):
    import json
    from factory import llm, config
    from factory.gpu_client import GPUClient
    monkeypatch.setattr(llm, 'request_json', lambda *a, **k: pytest.fail('Capability pin invoked a model'))
    monkeypatch.setattr(GPUClient, 'run', lambda *a, **k: pytest.fail('Capability pin invoked GPU generation'))
    cfg = json.loads((config.ROOT / 'config/factory.empty.example.json').read_text())
    p = {**project(), 'budget': {'gpu_minutes': 999, 'authorization_id': 'another-project-grant'},
         'execution_config': cfg}
    saved = c.pin(p, cfg)
    rendered = json.dumps(saved)
    assert 'another-project-grant' not in rendered and 'authorization' not in rendered
    assert saved['profiles']['image']['enabled'] is False
    assert saved['profiles']['video']['provider'] == 'comfyui_wan'


def test_one_sentence_h3_pin_uses_selected_frozen_connections_without_copying_grants(monkeypatch):
    from factory import llm
    from factory.gpu_client import GPUClient
    monkeypatch.setattr(llm, 'request_json', lambda *a, **k: pytest.fail('Capability pin invoked model inference'))
    monkeypatch.setattr(GPUClient, 'run', lambda *a, **k: pytest.fail('Capability pin invoked GPU generation'))
    cfg = configuration()
    p = {**project(), 'production_mode': 'one_sentence', 'execution_config': cfg,
         'production_spec': {'authorization_id': 'prior-grant', 'whole_drama_budget': {'limit': 999}}}
    saved = c.pin(p, p['execution_config'])
    assert saved['profiles']['video']['provider'] == 'comfyui_h3'
    assert saved['profiles']['video']['model'] == 'MiniMax-H3'
    assert saved['profiles']['video']['reference_counts'] == [1]
    assert set(saved) == {'version', 'profiles'}
    assert 'prior-grant' not in repr(saved)
    child = {**project(), 'model_capability_snapshot': copy.deepcopy(saved)}
    c.adopt(child, {**cfg, 'video_connection': {**cfg['video_connection'], 'enabled': False}})
    assert p['model_capability_snapshot'] == saved
    assert child['model_capability_snapshot'] != saved


@pytest.mark.parametrize('provider', ['legacy', 'comfyui_local'])
@pytest.mark.parametrize('invalid,field', [('references', 'references'), ('disabled', 'enabled'),
                                         ('dimensions', 'dimensions'), ('changed', 'snapshot')])
def test_cloud_repair_checks_pinned_image_contract_before_any_image_work(monkeypatch, tmp_path, provider, invalid, field):
    from factory import team_control
    monkeypatch.setattr(team_control, 'is_locked', lambda *args: False)
    run = object.__new__(production._Run)
    run.project, run.config = project(), configuration()
    run.config['image_connection'] = {'provider': provider, 'model': 'FLUX.2-klein-4B', 'enabled': invalid != 'disabled'}
    c.pin(run.project, run.config)
    if invalid == 'changed':
        run.config['image_connection']['revision'] = 'different-image-revision'
    run.ident = run.project['id']
    run.image_size = (129, 128) if invalid == 'dimensions' else (576, 1024)
    run.video_size = (576, 1024)
    run.style_prompt = 'Approved watercolor.'
    count = 4 if invalid == 'references' else 0
    run.assets = {'characters': {str(i): tmp_path / f'character-{i}.png' for i in range(count)}}
    run.receipt_path = Path
    unexpected = lambda *args, **kwargs: pytest.fail('Invalid repair reached an image adapter or worker')
    run.scheduler = SimpleNamespace(worker=unexpected)
    run._target = run._render_targets = unexpected
    target = {'key': 'S01', 'folder': tmp_path, 'references': [tmp_path / 'frame.png'],
              'shot': {'id': 'S01', 'prompt': 'Approved adult beside a cup.'}}
    record = {'take': 'take_002', 'seed': 1, 'repair': {'regenerate_keyframe': True}}
    with pytest.raises(ProductionNeedsDecision) as error:
        run._remote_first_frame(target, record)
    assert error.value.reason == 'model_capability'
    assert error.value.details['field'] == field
    assert len(target['references']) == 1 and len(run.assets['characters']) == count


@pytest.mark.parametrize('invalid', ['references', 'prepared_prompt'])
def test_resumed_cloud_target_is_checked_before_repair_or_paid_intent(tmp_path, invalid):
    run = object.__new__(production._Run)
    run.project, run.config = project(), configuration()
    run.config['video_connection'] = {'provider': 'minimax', 'model': 'MiniMax-Hailuo-2.3',
                                      'enabled': True, 'duration': 6, 'resolution': '768P'}
    c.pin(run.project, run.config)
    run.image_size = run.video_size = (384, 512)
    frame = tmp_path / 'frame.png'
    Image.new('RGB', (384, 512), 'gray').save(frame)
    unexpected = lambda *args, **kwargs: pytest.fail('Invalid resumed request reached repair or paid intent preparation')
    run.boundary = lambda: None
    run._remote_first_frame = run._new_attempt = unexpected
    target = {'kind': 'video', 'prompt': 'Approved turn.', 'seconds': 4,
              'references': [frame] * (2 if invalid == 'references' else 1),
              'resume_attempt': {'prompt': 'x' * 2001 if invalid == 'prepared_prompt' else 'Approved turn.'}}
    with pytest.raises(ProductionNeedsDecision) as error:
        run._prepare_cloud_targets([target])
    assert error.value.reason == 'model_capability'
    assert error.value.details['field'] == ('references' if invalid == 'references' else 'prompt')
