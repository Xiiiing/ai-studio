"""New creation opts in; legacy descendants and adoption remain explicit."""
import pytest
from factory import store, dramas, creative_contracts as quality


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(store, 'DATABASE', tmp_path / 'data/studio.sqlite3')
    store.initialize()


def test_new_projects_get_contract_and_legacy_descendants_do_not(database):
    current = store.create({'brief': '两位成年人在车站归还一封遗失的信件'})
    assert quality.enabled(current)
    parent = dramas.record('drama', '历史剧本', '保持原先执行契约')
    with store.connection() as db:
        # Insert an authentic historical snapshot, without new-create defaults.
        db.execute('INSERT INTO projects(id,updated,revision,snapshot) VALUES(?,?,?,?)',
                   (parent['id'], 1, 1, store.encoded(parent)))
        child = dramas.record('episode', '历史子集', '继承历史父项目', drama_id=parent['id'])
        dramas.insert(db, child)
    assert not quality.enabled(store.get(child['id'], False))


def test_adoption_requires_confirmation_and_preserves_story(database, monkeypatch):
    monkeypatch.setattr('factory.review_advice.ensure_no_gpu_intents', lambda p: None)
    p = store.create({'brief': '两位成年人在车站归还一封遗失的信件', 'prepare_materials': True})
    original = {'shots': [{'id': 'S01', 'action': '交还信件'}]}
    p = store.update(p['id'], {'creative_quality_contract': None, 'script': original})
    payload = {'expected_revision': p['revision'], 'request_id': 'adoption-1', 'confirmed': False}
    with pytest.raises(ValueError): quality.adopt(p['id'], payload)
    assert store.get(p['id'], False)['revision'] == p['revision']
    payload['confirmed'] = True
    adopted = quality.adopt(p['id'], payload)
    assert quality.enabled(adopted)
    assert adopted['script'] == original and adopted['creative_quality_review_required']
    assert quality.adopt(p['id'], payload)['revision'] == adopted['revision']


@pytest.mark.parametrize('endpoint', ['creative-quality', 'model-capabilities'])
def test_quality_http_projection_does_not_write_or_generate(database, endpoint):
    import json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from factory.web import Handler
    p = store.create({'brief': '两位成年人在车站归还一封遗失的信件', 'prepare_materials': True})
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/factory/projects/{p["id"]}/{endpoint}') as r:
            result = json.load(r)
        assert result['enabled']
        if endpoint == 'creative-quality':
            assert result['history_preserved']
        else:
            assert result['can_adopt'] and result['snapshot']['version'] == 'ai-studio.model-capabilities.v1'
        assert store.get(p['id'], False)['revision'] == p['revision']
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_capability_adoption_cas_and_running_guard(database):
    from factory import capability_adoption
    p = store.create({'brief': '两位成年人在车站归还一封遗失的信件', 'prepare_materials': True})
    p = store.update(p['id'], {'model_capability_snapshot': None})
    body = {'request_id': 'capability-v1', 'expected_revision': p['revision'] - 1, 'confirmed': True}
    with pytest.raises(ValueError): capability_adoption.adopt(p['id'], body)
    body['expected_revision'] = p['revision']
    changed = capability_adoption.adopt(p['id'], body)
    assert changed['model_capability_snapshot']['version'] == 'ai-studio.model-capabilities.v1'
    assert capability_adoption.adopt(p['id'], body)['revision'] == changed['revision']
    with store.connection() as db:
        store._enqueue(db, p['id'])
        db.execute("UPDATE jobs SET status='running' WHERE project_id=?", (p['id'],))
    assert not capability_adoption.snapshot(p['id'])['can_adopt']
    with pytest.raises(ValueError):
        capability_adoption.adopt(p['id'], {**body, 'request_id': 'capability-v2', 'expected_revision': changed['revision']})


def test_coverage_upgrade_does_not_change_children_of_older_editorial_contract():
    from factory import story_coverage
    old = {'creative_quality_contract': {'version': quality.VERSION}}
    child = quality.initialize({'id': '111122223333'}, old)
    assert quality.enabled(child) and not story_coverage.enabled(child)
    new = quality.initialize({'id': 'aaaabbbbcccc'})
    assert story_coverage.enabled(new)
