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
