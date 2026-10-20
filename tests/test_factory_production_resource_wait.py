"""Read-only readiness checks and durable SQLite waits never spend GPU/wall budget."""
import copy
import time
import urllib.error

import pytest

from factory import (store, worker, drama_runtime, production_resource_wait as resources,
                     remote_tasks, config)
from factory.providers import ProviderError
from factory.scheduler import ProductionNeedsDecision
from tests.test_factory_execution_settlement import production
from tests.test_factory_dramas import isolated
from tests.test_factory_role_routing import routing_config
from factory.production_execution import DEFAULT_POLICY
from factory.video_providers import H3_PROFILE
from studio.h3 import MODEL, REVISION


def marker():
    return {'enabled': True, 'status': 'waiting', 'reason': 'model_resources_unavailable',
            'message': '等待 Wkp 制作资源，恢复后系统自动继续。', 'poll_seconds': 300,
            'authorized_by': 'producer', 'authorization_id': 'resource-auth-fixture'}


def configuration():
    cfg = routing_config()
    cfg['llm_profiles']['critic']['vision'] = False
    cfg.update(video_profiles={'video': copy.deepcopy(H3_PROFILE)},
               video_roles={'video_generator': 'video'})
    cfg['production'] = {'gpu_service': {'base_url': 'http://127.0.0.1:8791'},
                         'production_recovery': copy.deepcopy(DEFAULT_POLICY)}
    return cfg


def ready_health():
    return {'ok': True, 'service': 'gpu_inference', 'workflow_execution': False, 'gpu_cards': [6, 7],
            'models': {'image': True, 'audio': True}, 'video_backends': {'comfyui_h3': {'model': MODEL, 'gpu_count': 2, 'revision': REVISION,
                'installed': True, 'execution': 'resident_comfy'}}}


def mocks(monkeypatch, *, model_error=None, gpu_ready=True):
    calls = []
    def check(profile, **kwargs):
        calls.append(('GET', 'models', profile['model']))
        if model_error:
            raise model_error
        return {'data': [{'id': profile['model']}]}
    def gpu(self, route, payload=None, **kwargs):
        calls.append(('GET' if payload is None else 'POST', route))
        assert payload is None and route == '/health'
        health = ready_health()
        if not gpu_ready:
            health['video_backends']['comfyui_h3']['installed'] = False
        return health
    monkeypatch.setattr(resources, 'check_model', check)
    monkeypatch.setattr('factory.gpu_client.GPUClient.request', gpu)
    return calls


def authorize_wait(pid):
    episode = store.get(store.get(pid, False)['episodes'][0], False)
    store.update(episode['id'], {'execution_config': configuration(), 'production': {
        'resource_wait': marker(), 'gpu_seconds': 131.282*60, 'elapsed_seconds': 700,
        'recovery': {'status': 'stopped', 'reason': 'retry_limit'}},
        'budget': {'gpu_minutes': 200, 'wall_minutes': 180, 'max_attempts': 3}})
    return store.get(episode['id'], False)


@pytest.mark.parametrize('reason', ['model_missing', 'http_500', 'transport_failed'])
def test_probe_uses_only_get_and_waits_for_missing_frozen_models(monkeypatch, reason):
    calls = mocks(monkeypatch, model_error=ProviderError(reason, 'Synthetic unserved model'))
    observed = resources.probe(configuration())
    assert not observed['ready'] and observed['missing']
    assert calls and all(c[0] == 'GET' for c in calls)


def test_cold_files_do_not_authorize_starting_a_resident_model(monkeypatch):
    calls = mocks(monkeypatch, gpu_ready=False)
    assert not resources.probe(configuration())['ready']
    assert calls[-1] == ('GET', '/health')


def test_current_legacy_tts_must_be_installed_without_starting_it(monkeypatch):
    calls = mocks(monkeypatch)
    from factory.gpu_client import GPUClient
    request = GPUClient.request
    def missing_audio(self, route, payload=None, **kwargs):
        health = request(self, route, payload, **kwargs)
        health['models']['audio'] = False
        return health
    monkeypatch.setattr(GPUClient, 'request', missing_audio)
    observed = resources.probe(configuration())
    assert not observed['ready']
    assert {'kind': 'audio', 'reason': 'configured_audio_backend_unavailable'} in observed['missing']
    assert all(call[0] == 'GET' for call in calls)


@pytest.mark.parametrize('code', ['missing_auth', 'http_401', 'invalid_models'])
def test_invalid_credentials_or_contract_stop_instead_of_polling(monkeypatch, code):
    mocks(monkeypatch, model_error=ProviderError(code, 'Private credential detail must not be public'))
    with pytest.raises(ProductionNeedsDecision) as error:
        resources.probe(configuration())
    assert error.value.reason == 'resource_wait_configuration'
    assert 'Private credential detail' not in str(error.value)


def test_worker_waits_without_process_pending_or_budget_spend_and_resumes_same_job(production, monkeypatch):
    episode = authorize_wait(production)
    prior_budget, prior_production = copy.deepcopy(episode['budget']), copy.deepcopy(episode['production'])
    calls = mocks(monkeypatch, model_error=ProviderError('model_missing', 'Synthetic resource owner has not restored model'))
    executed = []
    monkeypatch.setattr(drama_runtime, 'execute_episode', lambda pid: executed.append(pid))
    monkeypatch.setattr('factory.team_control.process_pending', lambda *a: pytest.fail('Resource waiting cannot process team instructions'))
    job = store.claim_job(); worker.handle(job)
    current = store.get(episode['id'], False)
    assert current['status'] == 'waiting_remote' and not executed
    assert current['budget'] == prior_budget
    assert current['production']['gpu_seconds'] == prior_production['gpu_seconds']
    assert current['production']['elapsed_seconds'] == prior_production['elapsed_seconds']
    with store.connection() as db:
        queued = dict(db.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone())
        assert queued['status'] == 'queued' and queued['phase'] == 'local_render'
        assert queued['next_poll_at'] > time.time()+295
        snapshot = store._read(db, episode['id'])
        snapshot['production']['remote_wait_started_at'] = time.time()-3600
        store._write(db, snapshot)
        db.execute('UPDATE jobs SET next_poll_at=0 WHERE id=?', (job['id'],))
    store.recover_jobs()
    resumed = store.claim_job()
    assert resumed['id'] == job['id'] and resumed['lease'] > job['lease']
    settled = store.get(episode['id'], False)
    assert settled['production']['elapsed_seconds'] == prior_production['elapsed_seconds']
    assert settled['production']['resource_wait_seconds'] >= 3599
    mocks(monkeypatch)
    worker.handle(resumed)
    assert executed == [episode['id']]
    final = store.get(episode['id'], False)
    assert final['production']['resource_wait']['status'] == 'resolved'
    assert final['production']['resource_wait']['prior_recovery'] == prior_production['recovery']
    assert final['production']['recovery']['status'] == 'diagnosing'
    assert final['budget'] == prior_budget
    assert all(c[0] == 'GET' for c in calls)


@pytest.mark.parametrize('action', ['pause', 'cancel', 'stale_lease'])
def test_pause_cancel_or_stale_lease_prevents_even_readiness_probes(production, monkeypatch, action):
    episode = authorize_wait(production)
    job = store.claim_job()
    if action == 'stale_lease':
        with store.connection() as db:
            db.execute('UPDATE jobs SET lease=lease+1 WHERE id=?', (job['id'],))
    else:
        store.update(episode['id'], {'pause_requested': True, 'status': 'cancelled' if action == 'cancel' else 'paused'})
    monkeypatch.setattr(resources, 'probe', lambda *a: pytest.fail('Pause/lease fence precedes resource GETs'))
    monkeypatch.setattr(drama_runtime, 'execute_episode', lambda *a: pytest.fail('No business execution'))
    worker.handle(job)
    final = store.get(episode['id'], False)
    assert final['budget'] == episode['budget']
    assert final['production']['elapsed_seconds'] == episode['production']['elapsed_seconds']


def test_changed_lease_during_probe_cannot_publish_ready_or_run(production, monkeypatch):
    episode = authorize_wait(production)
    job = store.claim_job()
    def probe(_):
        with store.connection() as db:
            db.execute('UPDATE jobs SET lease=lease+1 WHERE id=?', (job['id'],))
        return {'ready': True, 'missing': []}
    monkeypatch.setattr(resources, 'probe', probe)
    monkeypatch.setattr(drama_runtime, 'execute_episode', lambda *a: pytest.fail('Late readiness is not permission'))
    worker.handle(job)
    assert store.get(episode['id'], False)['production']['resource_wait']['status'] == 'waiting'


def test_resolved_authorization_rechecks_resources_for_each_future_job(production, monkeypatch):
    episode = authorize_wait(production)
    current = copy.deepcopy(marker()); current['status'] = 'resolved'
    store.update(episode['id'], {'production': {'resource_wait': current}})
    calls = mocks(monkeypatch, gpu_ready=False)
    monkeypatch.setattr(drama_runtime, 'execute_episode', lambda *a: pytest.fail('Unavailable resident model cannot execute'))
    job = store.claim_job(); worker.handle(job)
    updated = store.get(episode['id'], False)
    assert updated['production']['resource_wait']['status'] == 'waiting'
    assert updated['status'] == 'waiting_remote'
    assert calls and updated['production']['elapsed_seconds'] == episode['production']['elapsed_seconds']


def test_resource_recovery_resolves_same_authorization_units_without_changing_mixed_caps(production, monkeypatch):
    from factory import long_episodes
    episode = authorize_wait(production)
    long_episodes.plan_units(episode, episode['execution_config'])
    episode = store.get(episode['id'], False)
    before = {}
    for index, uid in enumerate(episode['unit_ids']):
        unit = store.get(uid, False)
        cfg = copy.deepcopy(episode['execution_config'])
        cfg['production']['max_gpu_seconds'] = 12000 if index == 0 else 7200
        store.update(uid, {'execution_config': cfg, 'budget': {'gpu_minutes': 200 if index == 0 else 120,
            'wall_minutes': 180, 'max_attempts': 3}, 'production': {'resource_wait': marker(),
            'gpu_seconds': 360 if index == 0 else 0, 'elapsed_seconds': 600 if index == 0 else 0,
            'recovery': {'status': 'stopped', 'reason': 'historical_retry_limit'}}})
        before[uid] = store.get(uid, False)
    mocks(monkeypatch)
    monkeypatch.setattr(drama_runtime, 'execute_episode', lambda *a: None)
    worker.handle(store.claim_job())
    for uid, original in before.items():
        updated = store.get(uid, False)
        assert updated['budget'] == original['budget']
        assert updated['execution_config'] == original['execution_config']
        assert updated['production']['gpu_seconds'] == original['production']['gpu_seconds']
        assert updated['production']['elapsed_seconds'] == original['production']['elapsed_seconds']
        assert updated['production']['resource_wait']['status'] == 'resolved'
        assert updated['production']['resource_wait']['prior_recovery'] == original['production']['recovery']
        assert updated['production']['recovery']['owner'] == 'resource_scheduler'


def test_invalid_resource_authentication_blocks_actual_worker_instead_of_requeue(production, monkeypatch):
    episode = authorize_wait(production)
    mocks(monkeypatch, model_error=ProviderError('missing_auth', 'Synthetic missing credential'))
    monkeypatch.setattr(drama_runtime, 'execute_episode', lambda *a: pytest.fail('Broken authentication cannot execute'))
    job = store.claim_job(); worker.handle(job)
    current = store.get(episode['id'], False)
    assert current['status'] == 'blocked'
    assert current['needs_decision']['code'] == 'resource_wait_configuration'
    assert current['budget'] == episode['budget']
    assert store.claim_job() is None


def test_pause_arriving_during_read_only_probe_wins_over_ready_result(production, monkeypatch):
    episode = authorize_wait(production)
    def probe(_):
        store.update(episode['id'], {'pause_requested': True, 'status': 'paused'})
        return {'ready': True, 'missing': []}
    monkeypatch.setattr(resources, 'probe', probe)
    monkeypatch.setattr(drama_runtime, 'execute_episode', lambda *a: pytest.fail('Pause wins over resource availability'))
    worker.handle(store.claim_job())
    current = store.get(episode['id'], False)
    assert current['status'] == 'paused'
    assert current['production']['resource_wait']['status'] == 'waiting'
    assert current['production']['elapsed_seconds'] == episode['production']['elapsed_seconds']


def test_disabled_frozen_model_stops_before_any_network_check(monkeypatch):
    cfg = configuration()
    cfg['llm']['enabled'] = False
    monkeypatch.setattr(resources, 'check_model', lambda *a, **k: pytest.fail('Disabled models must not be probed'))
    monkeypatch.setattr('factory.gpu_client.GPUClient.request', lambda *a, **k: pytest.fail('Invalid configuration must not query GPU service'))
    with pytest.raises(ProductionNeedsDecision) as error:
        resources.probe(cfg)
    assert error.value.reason == 'resource_wait_configuration'
