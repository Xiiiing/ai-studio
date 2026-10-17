import copy
import json
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

import pytest
from factory import dramas, store, worker, config, workflows


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DATABASE', tmp_path / 'studio.sqlite3')
    monkeypatch.setattr(workflows, 'DATA', tmp_path)
    monkeypatch.setattr(workflows, 'CHECKPOINTS', tmp_path / 'checkpoints.sqlite3')
    monkeypatch.setattr(config, 'load_config', lambda: {'private_fixture': 'never-return-this'})
    store.initialize()
    return tmp_path


def input_payload(**values):
    return {'title': '车站的约定', 'brief': '两个成年人在车站发现遗失的信件，逐集查明真相。',
        'episode_count': 3, 'duration_seconds': 60, 'format': 'motion_comic', 'request_id': 'create', **values}


def plan(count=3):
    return {'world': '当代虚构城市与车站', 'storyline': '从遗失信件发现真相，再完成和解。',
        'visual_rules': '统一水彩画风', 'characters': [{'character_id': 'lead', 'name': '林',
        'identity_constraints': '成年女性，短发', 'default_wardrobe': '蓝衣', 'voice_id': 'female'}],
        'scenes': [], 'props': [], 'uncertainties': [], 'episodes': [{'number': i,
        'title': f'第{i}封信', 'synopsis': f'角色在车站发现第{i}封信，并通过信件了解真相。',
        'opening_state': '抵达车站', 'ending_state': '发现线索', 'characters': ['lead'],
        'depends_on': []} for i in range(1, count+1)]}


def ready():
    p = dramas.create(input_payload())
    store.update(p['id'], {'draft_plan': plan(), 'plan_review': {'passed': True, 'issues': []}, 'status': 'awaiting_plan'})
    # No planning worker is executed by this metadata fixture.
    with store.connection() as db: db.execute("UPDATE jobs SET status='completed'")
    p = dramas.snapshot(p['id'])
    return dramas.confirm(p['id'], {'expected_revision': p['revision'], 'request_id': 'confirm'})


def test_create_confirm_generate_are_idempotent_and_private(isolated):
    p = ready()
    assert len(p['episodes']) == 3 and p['plan_version'] == 1
    assert dramas.create(input_payload())['id'] == p['id']
    payload = {'expected_revision': p['revision'], 'request_id': 'batch', 'episode_numbers': [1, 3]}
    result = dramas.generate(p['id'], payload)
    again = dramas.generate(p['id'], payload)
    assert len(again['batches']) == 1
    assert 'never-return-this' not in json.dumps(result)
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0] == 1
    with pytest.raises(ValueError, match='请求标识'):
        dramas.generate(p['id'], {**payload, 'episode_numbers': [2]})
    with pytest.raises(ValueError, match='更新'):
        dramas.generate(p['id'], {**payload, 'request_id': 'new'})


def test_worker_failure_releases_unrelated_episode_and_dependencies_wait(isolated, monkeypatch):
    p = ready()
    # Episode 3 depends on the unresolved production of 1; 2 does not.
    new_plan = plan()
    new_plan['episodes'][2]['depends_on'] = [1]
    store.update(p['id'], {'draft_plan': new_plan, 'plan_review': {'passed': True}})
    p = dramas.snapshot(p['id'])
    p = dramas.confirm(p['id'], {'expected_revision': p['revision'], 'request_id': 'confirm2'})
    p = dramas.generate(p['id'], {'expected_revision': p['revision'], 'request_id': 'batch', 'episode_numbers': [1,2,3]})
    from factory import drama_runtime
    def execute(pid):
        if store.get(pid, False)['episode_number'] == 1: raise RuntimeError('fixture inference failed')
        store.update(pid, {'status': 'awaiting_final', 'delivery': {'fixture': True}, 'progress': 95})
    monkeypatch.setattr(drama_runtime, 'execute_episode', execute)
    first = store.claim_job()
    worker.handle(first)
    second = store.claim_job()
    assert store.get(second['project_id'], False)['episode_number'] == 2
    worker.handle(second)
    assert store.claim_job() is None
    result = dramas.snapshot(p['id'])
    assert [e['status'] for e in result['episodes']] == ['blocked', 'awaiting_final', 'waiting_dependency']


def test_pause_cancel_and_foreign_episode_are_fenced(isolated):
    p = ready()
    p = dramas.generate(p['id'], {'expected_revision': p['revision'], 'request_id': 'batch', 'episode_numbers': [1,2]})
    p = dramas.control(p['id'], {'expected_revision': p['revision'], 'request_id': 'pause', 'action': 'pause', 'batch_id': p['batches'][0]['id']})
    assert [e['status'] for e in p['episodes']] == ['paused', 'paused', 'draft']
    assert store.claim_job() is None
    with pytest.raises(ValueError):
        dramas.control(p['id'], {'expected_revision': p['revision'], 'request_id': 'foreign', 'action': 'resume', 'episode_id': 'f'*12})
    p = dramas.control(p['id'], {'expected_revision': p['revision'], 'request_id': 'resume', 'action': 'resume', 'episode_id': p['episodes'][1]['id']})
    assert store.claim_job()['project_id'] == p['episodes'][1]['id']


def test_v3_graph_and_pinned_short_profile_keep_stable_identity(isolated):
    from factory.drama_runtime import setup_episode
    p = ready()
    episode = p['episodes'][0]
    setup_episode(episode)
    e = store.get(episode['id'], False)
    assert e['production_profile']['characters'][0]['character_id'] == 'lead'
    assert e['production_profile']['characters'][0]['voice_id'] == 'female'
    graph = workflows.build_graph(None, 3).get_graph().to_json()
    assert any(edge['source'] == '__start__' and edge['target'] == 'story_resolution' for edge in graph['edges'])


def test_real_http_create_get_and_origin_boundary(isolated):
    from factory.web import Handler
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        req = urllib.request.Request(base + '/api/factory/dramas', json.dumps(input_payload()).encode(),
            {'Origin': base, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req) as r: p = json.load(r)
        with urllib.request.urlopen(base+'/api/factory/dramas/'+p['id']) as r: assert json.load(r)['id'] == p['id']
        req = urllib.request.Request(base+'/api/factory/dramas', b'{}', {'Origin': 'https://evil.example', 'Content-Type': 'application/json'})
        with pytest.raises(urllib.error.HTTPError) as error: urllib.request.urlopen(req)
        assert error.value.code == 403
    finally:
        server.shutdown(); server.server_close(); thread.join()
