import json
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from factory import native_text, providers
from factory.environment import load_environment
from factory.config import list_roles, resolve_llm


@pytest.mark.parametrize("ident", ["anthropic", "gemini"])
def test_native_wire_auth_images_and_actual_model(ident, monkeypatch):
    observed = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            observed.append((self.path, dict(self.headers), json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            body = ({"model": "served", "stop_reason": "end_turn", "content": [{"type": "text", "text": '{"ok":true}'}], "usage": {"input_tokens": 7, "output_tokens": 4}}
                    if ident == "anthropic" else {"modelVersion": "served", "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"ok":true}'}]}}], "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 4}})
            self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(body).encode())
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    monkeypatch.setenv('NATIVE_TEST_KEY', 'private-test-key')
    try:
        p = providers.normalize_profile({**providers.presets(providers.PROVIDERS)[ident], 'base_url': f'http://127.0.0.1:{server.server_port}', 'network_mode': 'loopback', 'api_key_env': 'NATIVE_TEST_KEY'})
        messages = [{'role': 'system', 'content': 'Return JSON'}, {'role': 'user', 'content': [{'type': 'text', 'text': 'Review'}, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,YQ=='}}]}]
        parameters = {'max_tokens': 512, 'send_sampling_parameters': True, 'temperature': .5, 'top_p': .9}
        route, body = providers.chat_request(p, messages, {}, 'screenwriter', parameters)
        raw = providers.request_json(p['base_url'], route, body, profile=p)
        result = providers.chat_response(p, raw)
        assert result['model'] == 'served' and result['choices'][0]['finish_reason'] == 'stop'
        assert result['usage']['total_tokens'] == 11
        headers = {k.lower(): v for k, v in observed[0][1].items()}
        assert headers['x-api-key' if ident == 'anthropic' else 'x-goog-api-key'] == 'private-test-key'
        assert 'authorization' not in headers and 'private-test-key' not in observed[0][0]
        assert not providers.model_matches(p, 'served')
        assert 'base64' in json.dumps(body) if ident == 'anthropic' else 'inlineData' in json.dumps(body)
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_native_rejections_and_truncation():
    p = {'provider': 'anthropic'}
    assert native_text.response(p, {'stop_reason': 'max_tokens', 'content': []})['choices'][0]['finish_reason'] != 'stop'
    p = {'provider': 'gemini'}
    assert native_text.response(p, {'candidates': []})['choices'] == []
    assert native_text.response(p, {'candidates': [{'finishReason': 'SAFETY'}]})['choices'][0]['finish_reason'] != 'stop'


def test_environment_is_private_literal_atomic_and_does_not_override(tmp_path, monkeypatch):
    path = tmp_path / 'runtime.env'
    path.write_text('EXISTING=replace\nNEW_LITERAL="$(never-run)"\n'); path.chmod(0o600)
    monkeypatch.setenv('EXISTING', 'keep'); monkeypatch.delenv('NEW_LITERAL', raising=False)
    load_environment(path)
    import os
    assert os.environ['EXISTING'] == 'keep' and os.environ['NEW_LITERAL'] == '$(never-run)'
    monkeypatch.delenv('NEW_LITERAL')
    path.write_text('NEW_LITERAL=value\nbad line\n')
    with pytest.raises(ValueError): load_environment(path)
    assert 'NEW_LITERAL' not in os.environ
    path.chmod(0o644)
    with pytest.raises(ValueError, match='private'): load_environment(path)


def test_first_install_is_unconfigured_without_network_defaults():
    config = json.loads(Path('config/factory.empty.example.json').read_text())
    data = providers.public_inventory(config)
    assert data['profiles'] == [] and all(not r['enabled'] for r in data['roles'])
    assert providers.validate_registry({}, {}, config)['setup_incomplete']
    with pytest.raises(ValueError, match='配置模型'): resolve_llm(config, 'screenwriter')
    for ident, preset in data['presets'].items():
        if preset['model']:
            providers.normalize_profile(preset)
