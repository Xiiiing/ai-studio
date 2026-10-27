"""Actual paused-project HTTP/worker review path; external inference is synthetic."""
import copy
import json
import shutil
import subprocess
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from factory import store, config, creative, schemas, creative_contracts, quality_recheck as recheck, worker, workflows, llm
from studio import core
from tests.test_factory_creative import fixture

CFG = {'llm': {'base_url': 'http://127.0.0.1:8090/v1', 'model': 'readonly-fixture', 'vision': True,
               'context_tokens': 262144, 'context_validation': 'conservative_estimate'}}


@pytest.fixture
def database(tmp_path, monkeypatch):
    from factory import episode_assembly
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    monkeypatch.setattr(schemas, 'ROOT', tmp_path)
    monkeypatch.setattr(episode_assembly, 'ROOT', tmp_path)
    monkeypatch.setattr(store, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(store, 'DATABASE', tmp_path / 'data/studio.sqlite3')
    monkeypatch.setattr(workflows, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(workflows, 'CHECKPOINTS', tmp_path / 'data/checkpoints.sqlite3')
    monkeypatch.setattr(config, 'load_config', lambda: copy.deepcopy(CFG))
    store.initialize()
    return tmp_path


@pytest.fixture(scope='module')
def movie(tmp_path_factory):
    from studio.media import FFMPEG
    path = tmp_path_factory.mktemp('quality-readonly') / 'episode_clean.mp4'
    subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=64x96:rate=24:duration=60',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=60', '-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(path)], check=True, capture_output=True)
    return path


def existing(database, movie=None, status='awaiting_final'):
    p = store.create({'brief': '两位成年人在车站归还遗失的钥匙', 'prepare_materials': True})
    proposal = fixture(schemas.PROPOSAL_ITEM_SCHEMA)
    proposal.update(id='A', eligible=True)
    proposal['characters'][0]['role'] = 'female'; proposal['characters'][1]['role'] = 'male'
    p = store.update(p['id'], {'proposals': [proposal], 'selected_story': 'A', 'selected_style': 'approved-style', 'execution_config': copy.deepcopy(CFG)})
    raw = fixture(schemas.episode_schema(project=p))
    for shot in raw['shots']:
        shot['beat_ids'] = []
        shot['continuity_state'] = {'mode': 'continuous', 'opening_state': '成年人物持有钥匙站在门前', 'ending_state': '成年人物仍在门前持有钥匙', 'allowed_changes': []}
    raw['shots'][0]['beat_ids'] = ['approved_story']
    episode = creative._canonical_episode(raw, p)
    patch = {'script': episode, 'episode': episode, 'script_review': {'passed': True, 'review': {'old': 'immutable historical review'}},
        'status': status, 'pause_requested': False, 'accepted_by': 'original-producer', 'accepted_at': 'old-date',
        'decisions': [{'id': f"{p['id']}:final:1", 'type': 'final', 'status': 'pending', 'options': copy.deepcopy(recheck.FINAL_OPTIONS)}], 'answers': {}}
    if movie:
        folder = database / 'production' / p['slug']; folder.mkdir(parents=True, exist_ok=True)
        core.write_json(folder / 'factory_owner.json', {'project_id': p['id']})
        path = folder / 'episode_clean.mp4'; shutil.copyfile(movie, path)
        patch['delivery'] = {'source_signature': 'original-media-family', 'artifacts': [core.media_receipt(path)],
            'duration_seconds': 60, 'fps': 24, 'resolution': [64, 96], 'video_url': '/media/' + str(path.relative_to(database))}
    p = store.update(p['id'], patch)
    return p


def adopt(p):
    return creative_contracts.adopt(p['id'], {'expected_revision': p['revision'], 'request_id': 'explicit-adoption', 'confirmed': True})


def queue(p, rid='readonly-review'):
    return recheck.configure(p['id'], {'expected_revision': p['revision'], 'request_id': rid, 'confirmed': True, 'authorize_review': True})


def synthetic(monkeypatch, *, text_score=8, media_status='pass'):
    calls = []
    def request(*args, **kwargs):
        role, payload, schema = args[1], args[3], args[4]
        calls.append({'role': role, 'payload': copy.deepcopy(payload)})
        value = fixture(schema)
        if role == 'independent_critic': value['scores'] = {k: text_score for k in schemas.SCORE_KEYS}
        elif 'status' in value: value['status'] = media_status
        return value
    monkeypatch.setattr(llm, '_request', request)
    monkeypatch.setattr('factory.production.generate_project', lambda *a, **k: pytest.fail('read-only review must not generate media'))
    return calls


def execute(pid):
    job = store.claim_job(pid)
    assert job and job['kind'] == recheck.KIND
    worker.handle(job)
    return store.get(pid, False), job


def test_adoption_revokes_current_approval_and_old_gate_but_preserves_every_source(database, movie):
    p = existing(database, movie, 'approved')
    script, delivery = copy.deepcopy(p['script']), copy.deepcopy(p['delivery'])
    sha = core.sha256(core.ROOT / delivery['artifacts'][0]['path'])
    changed = adopt(p)
    assert changed['status'] == 'paused' and changed['pause_requested']
    assert changed['script'] == script and changed['delivery'] == delivery
    assert 'accepted_by' not in changed
    assert changed['creative_quality_adoptions'][-1]['previous_approval']['accepted_by'] == 'original-producer'
    assert changed['decisions'][0]['status'] == 'superseded'
    with store.connection() as db:
        assert not db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running')", (p['id'],)).fetchone()
    with pytest.raises(ValueError, match='新创作契约'):
        store.decide(p['id'], {'type': 'final', 'choice': 'approve', 'decision_id': p['decisions'][0]['id'], 'expected_revision': changed['revision']})
    assert core.sha256(core.ROOT / delivery['artifacts'][0]['path']) == sha


def test_actual_http_queue_worker_rechecks_existing_files_and_requires_fresh_human_approval(database, movie, monkeypatch):
    from factory.web import Handler
    p = adopt(existing(database, movie))
    sha = core.sha256(core.ROOT / p['delivery']['artifacts'][0]['path'])
    calls = synthetic(monkeypatch)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        body = {'expected_revision': p['revision'], 'request_id': 'http-existing-review', 'confirmed': True, 'authorize_review': True}
        request = urllib.request.Request(base + f"/api/factory/projects/{p['id']}/creative-quality/review", json.dumps(body).encode(), {'Origin': base, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(request) as response: assert json.load(response)['recheck']['task']['status'] == 'queued'
    finally:
        server.shutdown(); server.server_close(); thread.join()
    current, job = execute(p['id'])
    task = current['quality_recheck']['task']
    assert task['status'] == 'pass', task
    assert current['status'] == 'awaiting_final' and not current['creative_quality_review_required']
    assert current['script'] == p['script'] and 'accepted_by' not in current
    assert recheck.final_current(current)
    assert calls and {c['role'] for c in calls} == {'independent_critic', 'visual_critic'}
    assert all(c['payload'].get('scope') != 'full_film' for c in calls)
    gate = next(g for g in current['decisions'] if g['id'] == task['final_gate_id'])
    assert gate['status'] == 'pending' and gate['id'] != p['decisions'][0]['id']
    with store.connection() as db: assert db.execute('SELECT status FROM jobs WHERE id=?', (job['id'],)).fetchone()[0] == 'completed'
    current = store.decide(p['id'], {'type': 'final', 'choice': 'approve', 'decision_id': gate['id'], 'expected_revision': current['revision']})
    followup = store.claim_job(p['id']); assert followup
    worker.handle(followup)
    approved = store.get(p['id'], False)
    assert approved['status'] == 'approved' and approved['accepted_by'] == 'producer'
    assert core.sha256(core.ROOT / approved['delivery']['artifacts'][0]['path']) == sha


def test_missing_new_metadata_returns_actionable_feedback_without_model_or_generation(database, monkeypatch):
    p = existing(database, status='paused')
    episode = copy.deepcopy(p['script']); episode['shots'][0].pop('continuity_state'); episode['shots'][0]['beat_ids'] = []
    p = store.update(p['id'], {'script': episode, 'episode': episode})
    p = adopt(p)
    monkeypatch.setattr(llm, '_request', lambda *a, **kw: pytest.fail('missing metadata must not call models'))
    queue(p)
    current, _ = execute(p['id'])
    assert current['quality_recheck']['task']['status'] == 'needs_metadata'
    assert current['creative_quality_review_required'] and current['script'] == episode
    assert current['needs_decision']['allowed_actions'] == ['provide_feedback']
    assert '不会重写或重制' in current['quality_recheck']['task']['message']


@pytest.mark.parametrize('text_score,media_status', [(5, 'pass'), (8, 'fail'), (8, 'uncertain')])
def test_low_scores_failure_and_uncertainty_cannot_clear_current_review(database, movie, monkeypatch, text_score, media_status):
    p = adopt(existing(database, movie))
    synthetic(monkeypatch, text_score=text_score, media_status=media_status)
    queue(p)
    current, _ = execute(p['id'])
    assert current['quality_recheck']['task']['status'] in {'fail', 'uncertain'}
    assert current['creative_quality_review_required']
    assert current['script'] == p['script'] and current['delivery'] == p['delivery']
    assert not any(g['status'] == 'pending' and g['type'] == 'final' for g in current['decisions'])


def test_unknown_result_is_not_reposted_even_by_new_request_id(database, monkeypatch):
    p = adopt(existing(database, status='paused'))
    count = []
    def unknown(*a, **kw): count.append(1); raise ConnectionError('synthetic unknown')
    monkeypatch.setattr(llm, '_request', unknown)
    queue(p)
    current, _ = execute(p['id'])
    assert current['quality_recheck']['task']['status'] == 'unknown'
    with pytest.raises(ValueError, match='结果未知'): queue(current, 'different-request')
    assert len(count) == 1


def test_invalid_authorization_legacy_marker_and_stale_revision_do_not_queue(database):
    p = existing(database, status='paused')
    body = {'expected_revision': p['revision'], 'request_id': 'authorize', 'confirmed': True, 'authorize_review': False}
    with pytest.raises(ValueError): recheck.configure(p['id'], body)
    with pytest.raises(ValueError): recheck.configure(p['id'], {**body, 'authorize_review': True, 'expected_revision': 0})
    p = store.update(p['id'], {'creative_quality_contract': None})
    with pytest.raises(ValueError, match='先明确启用'): queue(p)
    with store.connection() as db: assert not db.execute('SELECT 1 FROM jobs WHERE project_id=? AND kind=?', (p['id'], recheck.KIND)).fetchone()


def test_stale_lease_does_not_publish_or_call_models(database, monkeypatch):
    p = adopt(existing(database, status='paused')); queue(p)
    job = store.claim_job(p['id']); assert job
    with store.connection() as db: db.execute('UPDATE jobs SET lease=lease+1 WHERE id=?', (job['id'],))
    monkeypatch.setattr(llm, '_request', lambda *a, **kw: pytest.fail('stale lease cannot call'))
    recheck.run(job)
    assert store.get(p['id'], False)['creative_quality_review_required']


def test_changed_file_after_pass_cannot_be_approved(database, movie, monkeypatch):
    p = adopt(existing(database, movie)); synthetic(monkeypatch); queue(p)
    current, _ = execute(p['id']); assert recheck.final_current(current)
    path = core.ROOT / current['delivery']['artifacts'][0]['path']
    with path.open('ab') as stream: stream.write(b'changed')
    with pytest.raises(ValueError, match='不匹配'):
        store.decide(p['id'], {'type': 'final', 'choice': 'approve', 'decision_id': current['quality_recheck']['task']['final_gate_id'], 'expected_revision': current['revision']})


def test_actual_cross_cut_failure_is_actionable_and_does_not_replace_delivery(database, movie, monkeypatch):
    p = adopt(existing(database, movie)); synthetic(monkeypatch)
    original = llm._request
    def request(*args, **kwargs):
        value = original(*args, **kwargs)
        if 'previous_observed' in args[4].get('properties', {}):
            value.update(status='fail', issues=['S02首帧的钥匙持有人与前镜矛盾'])
        return value
    monkeypatch.setattr(llm, '_request', request)
    queue(p); current, _ = execute(p['id'])
    assert current['quality_recheck']['task']['status'] == 'fail'
    assert current['delivery'] == p['delivery']
    issue = current['needs_decision']['issues'][0]
    assert '钥匙持有人' in issue['description'] and issue['shot_ids'] == ['S02']
    assert issue['auto_repair'] is False


def test_cancelled_project_cannot_publish_a_late_review(database, monkeypatch):
    p = adopt(existing(database, status='paused')); calls = synthetic(monkeypatch)
    original = llm._request
    def request(*args, **kwargs):
        value = original(*args, **kwargs)
        store.update(p['id'], {'status': 'cancelled', 'pause_requested': True})
        return value
    monkeypatch.setattr(llm, '_request', request)
    queue(p); current, _ = execute(p['id'])
    assert current['status'] == 'cancelled' and current['creative_quality_review_required']
    assert 'accepted_by' not in current
    assert len(calls) == 1


def test_queued_recheck_survives_worker_recovery_and_uses_existing_private_authority(database, monkeypatch):
    p = adopt(existing(database, status='paused')); calls = synthetic(monkeypatch)
    queue(p); first = store.claim_job(p['id']); assert first
    assert store.recover_jobs() == 1
    recovered = store.claim_job(p['id']); assert recovered and recovered['id'] == first['id'] and recovered['lease'] > first['lease']
    worker.handle(recovered)
    current = store.get(p['id'], False)
    assert current['quality_recheck']['task']['status'] == 'pass'
    assert current['quality_recheck']['task']['media_review']['status'] == 'not_created'
    assert not current['creative_quality_review_required']
    assert len(calls) == 1


@pytest.mark.parametrize('kind', ['queued_child', 'unknown_reference_cloud'])
def test_adoption_cannot_race_long_children_or_bypass_unknown_reference_requests(database, kind):
    p = existing(database, status='paused')
    child = store.create({'brief': '同一剧集的历史制作单元', 'prepare_materials': True})
    p = store.update(p['id'], {'unit_ids': [child['id']]})
    with store.connection() as db:
        if kind == 'queued_child': store._enqueue(db, child['id'])
        else:
            db.execute("INSERT INTO cloud_tasks(intent_id,project_id,shot_id,attempt,take,provider,model,request_signature,status,currency,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ('reference-unknown', child['id'], 'reference:character', 1, 'take_001', 'synthetic', 'synthetic', 'bound', 'unknown', 'CNY', 1, 1))
    before = store.get(p['id'], False)
    with pytest.raises(ValueError, match='在途任务|未核对云任务'):
        adopt(before)
    after = store.get(p['id'], False)
    assert after['revision'] == before['revision'] and after.get('accepted_by') == 'original-producer'
    assert not after.get('creative_quality_adoptions')


def test_persisted_old_review_complete_markers_cannot_skip_explicit_recheck(database, monkeypatch):
    p = existing(database, status='paused')
    p = store.update(p['id'], {'script_complete_generation': 1, 'script_review_route': 'continue',
        'screenplay_complete_round': [1, 1], 'script_review_complete_round': [1, 1], 'delivery_complete_revision': 1})
    p = adopt(p); calls = synthetic(monkeypatch); queue(p)
    current, _ = execute(p['id'])
    assert current['quality_recheck']['task']['status'] == 'pass'
    assert len(calls) == 1 and calls[0]['role'] == 'independent_critic'
    assert current['script_review']['origin'] == recheck.KIND


def test_queue_request_replay_is_idempotent_and_no_supplied_reviewer_grant_no_job(database):
    p = adopt(existing(database, status='paused'))
    before = p['revision']
    first = queue(p, 'same-request')
    second = queue(p, 'same-request')
    assert first['recheck']['task']['job_id'] == second['recheck']['task']['job_id']
    with store.connection() as db: assert db.execute('SELECT count(*) FROM jobs WHERE project_id=? AND kind=?', (p['id'], recheck.KIND)).fetchone()[0] == 1
