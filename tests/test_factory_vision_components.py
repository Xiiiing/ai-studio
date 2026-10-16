import struct
import pytest
from factory.vision_components import gguf_metadata, verify_pair, prepare_projector


def gguf(path, name, vision=False):
    fields = {'general.name': name, 'general.architecture': 'clip' if vision else 'qwen35',
              'general.type': 'mmproj' if vision else 'model'}
    if vision:
        fields['clip.has_vision_encoder'] = True
    def string(s):
        data = s.encode()
        return struct.pack('<Q', len(data)) + data
    data = b'GGUF' + struct.pack('<IQQ', 3, 0, len(fields))
    for key, value in fields.items():
        data += string(key) + struct.pack('<I', 7 if isinstance(value, bool) else 8)
        data += struct.pack('<?', value) if isinstance(value, bool) else string(value)
    path.write_bytes(data)
    return path


def test_matched_identity_not_generic_filename(tmp_path):
    model = gguf(tmp_path / 'model.gguf', 'Qwen3.8-27B')
    projector = gguf(tmp_path / 'mmproj.gguf', 'Qwen3.6-35B-A3B', True)
    with pytest.raises(ValueError, match='Both GGUF'):
        verify_pair(model, projector)
    gguf(projector, 'Qwen3.8-27B', True)
    assert verify_pair(model, projector)['vision']
    gguf(projector, 'Qwen3.8-27B', False)
    with pytest.raises(ValueError, match='projector metadata'):
        verify_pair(model, projector)


def test_malformed_header_and_existing_artifact_preserved(tmp_path):
    path = tmp_path / 'bad.gguf'
    path.write_bytes(b'GGUF')
    with pytest.raises(ValueError, match='Truncated'):
        gguf_metadata(path)
    from factory.vision_components import QWEN38_PROJECTOR
    target = tmp_path / QWEN38_PROJECTOR['filename']
    target.write_bytes(b'existing unrelated file')
    with pytest.raises(ValueError, match='preserved'):
        prepare_projector(tmp_path)
    assert target.read_bytes() == b'existing unrelated file'


def test_production_migration_requires_actual_requested_reviewer_and_recipe():
    import copy
    from pathlib import Path
    from factory.video_providers import H3_PROFILE, H3_PRODUCTION_PROFILE
    from scripts import deploy_cami, configure_wkp_h3
    current = deploy_cami.new_configuration({key: Path('/private') / key for key in deploy_cami.TLS_ENV})
    current['video_profiles']['wkp_h3'] = copy.deepcopy(H3_PROFILE)
    original = copy.deepcopy(current)
    class Client:
        base = deploy_cami.WKP
        model = 'Qwen3.6-35B-A3B-UD-Q4_K_S'
        def require_h3(self, profile):
            assert profile == H3_PRODUCTION_PROFILE
        def request(self, route):
            name = route.split('/')[2]
            if route.endswith('/models'):
                return {'data': [{'id': self.model if name == 'vision' else 'actual-' + name}]}
            return {'modalities': {'vision': name == 'vision'}, 'default_generation_settings': {'n_ctx': 16384}}
    client = Client()
    with pytest.raises(ValueError, match='not the actual Wkp vision service'):
        configure_wkp_h3.verified_configuration(current, client, quality='production', vision_model='ai-studio-qwen38-vision')
    assert current == original
    client.model = 'ai-studio-qwen38-vision'
    planned = configure_wkp_h3.verified_configuration(current, client, quality='production', vision_model=client.model)
    assert planned['video_profiles']['wkp_h3'] == H3_PRODUCTION_PROFILE
    assert planned['production']['video_width'] == 768
    assert planned['llm_profiles']['vision']['model'] == client.model
    assert current == original


def test_qwen38_default_context_holds_reference_and_temporal_review():
    from pathlib import Path
    from scripts import deploy_cami
    from factory.config import resolve_llm
    from factory.llm import _review_sample_limit, _contract_review_schema
    config = deploy_cami.new_configuration({key: Path('/private') / key for key in deploy_cami.TLS_ENV})
    profile = resolve_llm(config, 'visual_critic', require_vision=True)
    assert profile['image_token_budget'] == 1024
    reference = [{'type': 'text', 'text': '角色参考'},
                 {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,'}}]
    assert _review_sample_limit(profile, reference, _contract_review_schema(True), 8, 2200) >= 4
