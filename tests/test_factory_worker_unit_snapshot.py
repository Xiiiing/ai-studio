"""Independent scene-unit worker jobs retain the authorized batch configuration."""
from __future__ import annotations

import json
import time

import pytest

from factory import config, drama_runtime, dramas, long_episodes, store, team_control, worker, workflows
from factory.execution_context import current_job
from factory.remote_tasks import ProductionDeferred
from factory.scheduler import ProductionPaused
from studio import core
from test_factory_long_episodes import episode_fixture


def configuration(marker, audio_provider='legacy'):
    return {'schema_version': 1, 'marker': marker,
        'llm_profiles': {'fixture': {'provider': 'llama_cpp', 'base_url': 'http://127.0.0.1:19999/v1',
            'model': marker + '-model', 'context_tokens': 32768, 'vision': True, 'network_mode': 'loopback'}},
        'llm_roles': {'visual_critic': 'fixture', 'executive_producer': 'fixture'},
        'audio_profiles': {'wkp_tts': {'provider': audio_provider, 'enabled': True, 'model': 'Qwen3-TTS'}},
        'audio_roles': {'speech_generator': 'wkp_tts'},
        'production': {'gpu_service': {'base_url': 'http://127.0.0.1:19999/inference'}},
        'quality': {'semantic_enabled': True}}


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    monkeypatch.setattr(store, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(store, 'DATABASE', tmp_path / 'data/studio.sqlite3')
    monkeypatch.setattr(workflows, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(workflows, 'CHECKPOINTS', tmp_path / 'data/checkpoints.sqlite3')
    path = tmp_path / 'global-fixture.json'
    path.write_text(json.dumps(configuration('global', 'qwen_tts_local')))
    monkeypatch.setattr(config, 'config_read_path', lambda: path)
    store.initialize()
    return path


def unit_job(kind='workflow', **overrides):
    parent = store.create({'brief': '隔离父集，所有执行效果均为测试模拟'})
    store.update(parent['id'], {'record_kind': 'episode', 'status': 'producing'})
    unit = store.create({'brief': '隔离制作单元，不调用任何真实模型'})
    frozen = configuration('frozen')
    unit = store.update(unit['id'], {'record_kind': 'scene_unit', 'workflow_version': 3,
        'episode_id': parent['id'], 'parent_id': parent['id'], 'execution_config': frozen, **overrides})
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='completed'")
        store._enqueue(db, unit['id'], kind)
    job = store.claim_job(unit['id'])
    assert job and job['kind'] == kind
    return unit, job, frozen


def assert_frozen(unit, job):
    assert current_job.get()['id'] == job['id']
    cfg = config.load_config()
    assert cfg['marker'] == 'frozen'
    assert config.resolve_llm(cfg, 'executive_producer')['model'] == 'frozen-model'
    resolved = config.production_config(store.get(unit['id'], False), cfg)
    assert resolved['audio_connection']['provider'] == 'legacy'
    assert resolved['gpu_service']['base_url'] == 'http://127.0.0.1:19999/inference'
    return cfg


def test_workflow_and_postprocessing_use_same_unit_snapshot_despite_global_edit(environment, monkeypatch):
    unit, job, frozen = unit_job()
    seen = []
    def advance(pid):
        assert pid == unit['id']
        cfg = assert_frozen(unit, job)
        seen.append('graph')
        environment.write_text(json.dumps(configuration('edited-global', 'qwen_tts_local')))
        cfg['production']['gpu_service']['base_url'] = 'http://invalid-mutated-copy.invalid'
        with config.config_snapshot():
            assert_frozen(unit, job)
        store.update(pid, {'status': 'unit_ready'})
    def pending(pid):
        assert_frozen(unit, job)
        seen.append('pending')
    # Exercise the real worker -> advance -> nested config_snapshot boundary.
    monkeypatch.setattr(workflows, '_advance', advance)
    monkeypatch.setattr(team_control, 'process_pending', pending)
    worker.handle(job)
    assert seen == ['graph', 'pending']
    assert store.get(unit['id'], False)['execution_config'] == frozen
    assert config.load_config()['marker'] == 'edited-global'
    assert config._CONFIG_SNAPSHOT.get() is None and current_job.get() is None
    with store.connection() as db:
        assert db.execute('SELECT status FROM jobs WHERE id=?', (job['id'],)).fetchone()[0] == 'completed'


def test_independent_team_instruction_uses_frozen_role_and_media_configuration(environment, monkeypatch):
    unit, job, frozen = unit_job('team_instruction')
    seen = []
    def pending(pid):
        cfg = assert_frozen(unit, job)
        environment.write_text(json.dumps(configuration('new-global', 'qwen_tts_local')))
        assert config.load_config() == cfg
        seen.append(pid)
    monkeypatch.setattr(team_control, 'process_pending', pending)
    monkeypatch.setattr(worker, 'advance', lambda *args: pytest.fail('Team instructions must not implicitly execute the graph'))
    worker.handle(job)
    assert seen == [unit['id']]
    assert store.get(unit['id'], False)['execution_config'] == frozen
    assert config.load_config()['marker'] == 'new-global'
    assert config._CONFIG_SNAPSHOT.get() is None and current_job.get() is None


@pytest.mark.parametrize('failure,expected_status', [
    (ProductionPaused('fixture pause'), 'paused'),
    (RuntimeError('fixture transport failure'), 'blocked'),
    (ProductionDeferred(time.time() + 1), 'waiting_remote'),
    (team_control.TeamInterventionPending('fixture instruction boundary'), 'paused'),
])
def test_pause_error_and_remote_wait_restore_outer_context(environment, monkeypatch, failure, expected_status):
    unit, job, frozen = unit_job()
    seen = []
    def advance(pid):
        assert_frozen(unit, job)
        raise failure
    def pending(pid):
        assert_frozen(unit, job)
        seen.append(pid)
    monkeypatch.setattr(workflows, '_advance', advance)
    monkeypatch.setattr(team_control, 'process_pending', pending)
    sentinel = current_job.set({'id': 'outer-job', 'lease': 11})
    try:
        with config.config_snapshot(configuration('outer')):
            worker.handle(job)
            assert config.load_config()['marker'] == 'outer'
            assert current_job.get() == {'id': 'outer-job', 'lease': 11}
    finally:
        current_job.reset(sentinel)
    assert store.get(unit['id'], False)['status'] == expected_status
    assert store.get(unit['id'], False)['execution_config'] == frozen
    assert config.load_config()['marker'] == 'global'
    assert config._CONFIG_SNAPSHOT.get() is None and current_job.get() is None
    assert bool(seen) == isinstance(failure, (ProductionDeferred, team_control.TeamInterventionPending))


def test_uncaught_interrupt_restores_snapshot_and_worker_context(environment, monkeypatch):
    unit, job, frozen = unit_job()
    class FixtureInterrupt(BaseException):
        pass
    def advance(pid):
        assert_frozen(unit, job)
        raise FixtureInterrupt()
    monkeypatch.setattr(workflows, '_advance', advance)
    with pytest.raises(FixtureInterrupt):
        worker.handle(job)
    assert config.load_config()['marker'] == 'global'
    assert config._CONFIG_SNAPSHOT.get() is None and current_job.get() is None
    assert store.get(unit['id'], False)['execution_config'] == frozen


@pytest.mark.parametrize('reason', ['retry_limit', 'production_recovery_request_upstream'])
def test_bounded_production_stop_keeps_specific_next_step_in_worker_event(environment, monkeypatch, reason):
    from factory.scheduler import ProductionNeedsDecision
    unit, job, _ = unit_job()
    explanation = 'S02 的授权次数已用尽；请核对材料与授权，直接继续不会获得新额度。'
    def advance(pid):
        raise ProductionNeedsDecision(explanation, reason, {'asset': 'S02', 'attempts': 3})
    monkeypatch.setattr(workflows, '_advance', advance)
    worker.handle(job)
    current = store.get(unit['id'], False)
    assert current['status'] == 'blocked'
    assert current['needs_decision']['message'] == explanation
    with store.connection() as db:
        last = db.execute("SELECT message FROM events WHERE project_id=? AND kind='review_blocked' ORDER BY id DESC LIMIT 1", (unit['id'],)).fetchone()
    assert last['message'] == explanation


@pytest.mark.parametrize('value', ['missing', None, [], 'legacy-config'])
def test_legacy_unit_without_explicit_snapshot_keeps_existing_configuration_behavior(environment, monkeypatch, value):
    unit, job, frozen = unit_job(execution_config=value)
    if value == 'missing':
        with store.connection() as db:
            p = store._read(db, unit['id']);p.pop('execution_config');store._write(db, p)
    seen = []
    def advance(pid):
        seen.append(config.load_config()['marker'])
        store.update(pid, {'status': 'unit_ready'})
    monkeypatch.setattr(workflows, '_advance', advance)
    monkeypatch.setattr(team_control, 'process_pending', lambda pid: seen.append(config.load_config()['marker']))
    worker.handle(job)
    assert seen == ['global', 'global']
    assert config._CONFIG_SNAPSHOT.get() is None


@pytest.mark.parametrize('record_kind', ['project', 'episode'])
def test_non_unit_jobs_keep_their_original_snapshot_boundary(environment, monkeypatch, record_kind):
    unit, job, frozen = unit_job(record_kind=record_kind)
    seen = []
    def execute(pid):
        seen.append(config.load_config()['marker'])
        # Episode execution owns its own explicit snapshot, unchanged by handle.
        if record_kind == 'episode':
            with config.config_snapshot(store.get(pid, False)['execution_config']):
                seen.append(config.load_config()['marker'])
        store.update(pid, {'status': 'producing'})
    monkeypatch.setattr(workflows, '_advance', execute)
    monkeypatch.setattr(drama_runtime, 'execute_episode', execute)
    monkeypatch.setattr(dramas, 'after_episode', lambda pid: None)
    monkeypatch.setattr(team_control, 'process_pending', lambda pid: None)
    worker.handle(job)
    assert seen == (['global', 'frozen'] if record_kind == 'episode' else ['global'])
    assert config._CONFIG_SNAPSHOT.get() is None


@pytest.mark.parametrize('action', ['resume', 'retry'])
def test_parent_recovery_clears_only_requeued_unit_current_error_and_preserves_history(environment, monkeypatch, action):
    drama, episode = episode_fixture(300, monkeypatch)
    long_episodes.plan_units(episode, {})
    episode = store.get(episode['id'], False)
    ids = episode['unit_ids']
    for uid, state in zip(ids, ['blocked', 'paused', 'failed', 'unit_ready']):
        store.update(uid, {'status': state, 'error': 'retained old failure',
            'needs_decision': {'type': 'runtime', 'message': 'old diagnostic'},
            'production': {'gpu_seconds': 12.25}, 'delivery': {'source_signature': 'approved-fixture'},
            'delivery_history': [{'source_signature': 'previous-fixture'}]})
        store.event(uid, 'Original failure evidence', 'error', {'code': 'original-fixture'})
    store.update(episode['id'], {'status': 'blocked', 'error': 'parent failure'})
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='completed'")
    before = {uid: store.get(uid) for uid in ids}
    drama = dramas.snapshot(drama['id'])
    dramas.control(drama['id'], {'action': action, 'expected_revision': drama['revision'],
        'request_id': action + '-fixture', 'episode_id': episode['id']})
    for uid in ids[:3]:
        current = store.get(uid)
        assert current['status'] == 'queued' and current['error'] is None and current['needs_decision'] is None
        for field in ['production', 'budget', 'delivery', 'delivery_history', 'events', 'execution_config']:
            assert current[field] == before[uid][field]
    assert store.get(ids[-1]) == before[ids[-1]]


def test_parent_resume_does_not_clear_error_of_unit_already_producing(environment, monkeypatch):
    drama, episode = episode_fixture(300, monkeypatch)
    long_episodes.plan_units(episode, {})
    episode = store.get(episode['id'], False)
    uid = episode['unit_ids'][0]
    store.update(uid, {'status': 'producing', 'error': 'active request evidence', 'needs_decision': {'code': 'active-fixture'}})
    store.update(episode['id'], {'status': 'blocked'})
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='completed'")
    before = store.get(uid, False)
    drama = dramas.snapshot(drama['id'])
    dramas.control(drama['id'], {'action': 'resume', 'expected_revision': drama['revision'],
        'request_id': 'active-fixture', 'episode_id': episode['id']})
    current = store.get(uid, False)
    assert current['status'] == 'producing'
    assert current['error'] == before['error'] and current['needs_decision'] == before['needs_decision']


def test_explicit_empty_unit_snapshot_never_falls_back_to_global_services(environment, monkeypatch):
    unit, job, _ = unit_job(execution_config={})
    seen = []
    def advance(pid):
        seen.append(config.load_config())
        store.update(pid, {'status': 'unit_ready'})
    monkeypatch.setattr(workflows, '_advance', advance)
    monkeypatch.setattr(team_control, 'process_pending', lambda pid: seen.append(config.load_config()))
    worker.handle(job)
    assert seen == [{}, {}]
    assert config.load_config()['marker'] == 'global'
    assert store.get(unit['id'], False)['execution_config'] == {}


def test_parent_resume_keeps_diagnostics_during_child_worker_failure_window(environment, monkeypatch):
    drama, episode = episode_fixture(300, monkeypatch)
    long_episodes.plan_units(episode, {})
    episode = store.get(episode['id'], False)
    uid = episode['unit_ids'][0]
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='completed'")
        store._enqueue(db, uid)
    child_job = store.claim_job(uid)
    assert child_job
    # Simulate the real interval after the worker stores its failure and before
    # that same worker finishes its still-running job lease.
    store.update(uid, {'status': 'blocked', 'error': 'active worker diagnostic',
                       'needs_decision': {'type': 'runtime', 'code': 'active-worker-fixture'}})
    store.update(episode['id'], {'status': 'blocked'})
    before = store.get(uid, False)
    drama = dramas.snapshot(drama['id'])
    dramas.control(drama['id'], {'action': 'resume', 'expected_revision': drama['revision'],
        'request_id': 'active-worker-fixture', 'episode_id': episode['id']})
    current = store.get(uid, False)
    assert current['error'] == before['error'] and current['needs_decision'] == before['needs_decision']
    with store.connection() as db:
        assert db.execute('SELECT status FROM jobs WHERE id=?', (child_job['id'],)).fetchone()[0] == 'running'
