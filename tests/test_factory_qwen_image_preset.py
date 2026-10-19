"""New base-model connections use 25 steps; saved and protocol inputs stay exact."""
import copy
import json
from pathlib import Path

import pytest

from factory import media_profiles, qwen_image21
from factory.image_generation import image_request


def qwen_profile(**values):
    return {'provider': 'comfyui_qwen_image21', 'model': 'Qwen-Image-2.1', 'enabled': True, **values}


def test_new_connection_preset_reaches_actual_image_request_with_25_steps():
    preset = media_profiles.inventory({})['media_presets']['image']['comfyui_qwen_image21']
    assert preset['steps'] == 25
    config = media_profiles.validate_registry({}, {
        'image_profiles': {'new_base': copy.deepcopy(preset)},
        'image_roles': {'image_generator': 'new_base'},
    })
    profile = media_profiles.resolve(config, 'image')
    request = image_request(profile, 'A fictional adult at a station.', 7, 512, 768)
    assert request['kind'] == 'qwen_image21' and request['steps'] == 25
    graph = qwen_image21.build_graph({k: v for k, v in request.items() if k not in {'kind', 'references'}}, [], 'wkp-image21/' + 'a' * 20)
    assert graph['6']['inputs']['steps'] == 25


def test_wkp_example_explicitly_selects_25_step_base_profile():
    path = Path(__file__).resolve().parents[1] / 'config/factory.wkp.example.json'
    config = json.loads(path.read_text())
    profile = media_profiles.resolve(config, 'image')
    assert profile['provider'] == 'comfyui_qwen_image21' and profile['steps'] == 25
    assert image_request(profile, 'An adult portrait.', 7, 512, 768)['steps'] == 25


@pytest.mark.parametrize('saved_steps', [8, None])
def test_inventory_preserves_historical_profiles_and_does_not_migrate_snapshots(saved_steps):
    profile = qwen_profile(**({'steps': saved_steps} if saved_steps is not None else {}))
    config = {'image_profiles': {'saved': profile}, 'image_roles': {'image_generator': 'saved'}}
    snapshot = {'execution_config': copy.deepcopy(config), 'generation': 3}
    original_config, original_snapshot = copy.deepcopy(config), copy.deepcopy(snapshot)
    inventory = media_profiles.inventory(config)
    assert inventory['media_presets']['image']['comfyui_qwen_image21']['steps'] == 25
    saved = inventory['image_profiles']['saved']
    if saved_steps is None:
        assert 'steps' not in saved
    else:
        assert saved['steps'] == saved_steps
    assert image_request(media_profiles.resolve(config, 'image'), 'An adult portrait.', 7, 512, 768)['steps'] == 8
    assert config == original_config and snapshot == original_snapshot
    # Mutable editable inventory is detached from the original saved profile.
    saved['steps'] = 25
    assert config == original_config and snapshot == original_snapshot


def test_explicit_eight_step_local_edit_remains_valid_after_creating_new_profile():
    config = {'image_profiles': {'edit': qwen_profile(steps=8)}, 'image_roles': {'image_generator': 'edit'}}
    original = copy.deepcopy(config)
    preset = media_profiles.inventory(config)['media_presets']['image']['comfyui_qwen_image21']
    updated = media_profiles.validate_registry(config, {
        'image_profiles': {**config['image_profiles'], 'new_base': preset},
        'image_roles': {'image_generator': 'new_base'},
    })
    assert config == original and updated['image_profiles']['edit']['steps'] == 8
    assert image_request(updated['image_profiles']['edit'], 'Repair this portrait.', 9, 512, 768)['steps'] == 8
    assert image_request(media_profiles.resolve(updated, 'image'), 'An adult portrait.', 9, 512, 768)['steps'] == 25


def test_omitted_protocol_steps_keep_eight_step_fallback_without_mutating_input():
    payload = {'prompt': 'An adult portrait.', 'seed': 7, 'width': 512, 'height': 768}
    original = copy.deepcopy(payload)
    normalized = qwen_image21.validate_request(payload)
    assert normalized['steps'] == 8 and payload == original
    profile = qwen_profile()
    assert 'steps' not in media_profiles.normalize_media(profile, 'image')
    assert image_request(profile, 'An adult portrait.', 7, 512, 768)['steps'] == 8
    assert 'steps' not in profile
