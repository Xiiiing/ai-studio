"""Real HTTP -> paused project's durable queue -> existing CPU worker."""
import copy
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from factory import store, worker
from factory.web import Handler
from test_factory_edit_timeline import project


def test_http_preview_claims_paused_project_without_generation_probes(project, monkeypatch):
    from factory import production_resource_wait
    monkeypatch.setattr(production_resource_wait, 'before_execution',
        lambda *args: pytest.fail('CPU editing must not probe generation resources'))
    original = copy.deepcopy(store.get(project['id'], False))
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    url = f'{base}/api/factory/projects/{project["id"]}/edits'
    def call(body=None):
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
            headers={'Origin': base, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req) as response:
            return json.load(response)
    try:
        snapshot = call()
        saved = call({'action': 'save', 'expected_revision': snapshot['revision'], 'request_id': 'http-save',
            'document': snapshot['catalog']['default'], 'label': '匿名CPU剪辑'})
        version = saved['versions'][-1]['id']
        queued = call({'action': 'preview', 'expected_revision': saved['revision'], 'request_id': 'http-preview', 'version_id': version})
        job = store.claim_job(project['id'])
        assert job and job['id'] == queued['task']['job_id'] and job['kind'] == 'timeline_edit'
        worker.handle(job)
        ready = call()
        assert ready['task']['status'] == 'completed'
        assert ready['versions'][-1]['preview']['duration_seconds'] == 6
        assert ready['versions'][-1]['preview']['fps'] == 24
        assert ready['versions'][-1]['semantic']['status'] == 'not_reviewed'
        current = store.get(project['id'], False)
        assert current['status'] == original['status'] and current['pause_requested'] == original['pause_requested']
        assert current['delivery'] == original['delivery'] and current['budget'] == original['budget']
        with store.connection() as db:
            assert db.execute('SELECT status FROM jobs WHERE id=?', (job['id'],)).fetchone()[0] == 'completed'
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_unmatched_edit_job_cannot_use_paused_project_exception(project):
    with store.connection() as db:
        job_id = store._enqueue(db, project['id'], 'timeline_edit')
    assert store.claim_job(project['id']) is None
    with store.connection() as db:
        assert db.execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()[0] == 'cancelled'
    assert store.get(project['id'], False)['status'] == 'awaiting_final'
