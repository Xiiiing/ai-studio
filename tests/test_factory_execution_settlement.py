"""Real SQLite claims and worker completion; transport/model/assembly are fixtures."""
import time

import pytest

from factory import (store, dramas, drama_automation, drama_runtime, long_episodes,
                     workflows, worker, drama_assets, drama_continuity, episode_assembly, config)
from factory.scheduler import ProductionNeedsDecision, ProductionPaused
from factory.remote_tasks import ProductionDeferred
from studio.core import fingerprint
from test_factory_dramas import isolated, input_payload, plan


@pytest.fixture
def production(isolated, monkeypatch):
    monkeypatch.setattr(config, 'load_config', lambda: {'llm': {'provider': 'llama_cpp'}})
    p = dramas.create(input_payload(episode_count=2, duration_seconds=120, automation={
        'enabled': True, 'episode_numbers': [1, 2],
        'budget': {'gpu_minutes': 120, 'wall_minutes': 180, 'max_attempts': 3}}))
    document = plan(2)
    document['episodes'][1]['depends_on'] = [1]
    store.update(p['id'], {'draft_plan': document, 'plan_review': {'passed': True, 'issues': []},
        'plan_review_sha256': fingerprint(document), 'plan_phase': 'approved', 'status': 'awaiting_plan'})
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='completed'")
    drama_automation.recover()
    def scenes(project, config, role, stage, *args):
        if 'review' in stage:
            return {'passed': True, 'issues': []}
        return {'scenes': [{'scene_id': f'C{i:02d}', 'title': f'场景{i}',
            'synopsis': '角色走入车站寻找书信，并且最终发现可见的线索。', 'seconds': 40,
            'characters': ['lead'], 'location_id': '', 'prop_ids': [],
            'opening_state': '进入车站', 'ending_state': '发现线索'} for i in range(1, 4)]}
    monkeypatch.setattr(long_episodes, 'call', scenes)
    monkeypatch.setattr(drama_assets, 'prepare', lambda *args: None)
    monkeypatch.setattr(drama_continuity, 'review', lambda *args: {'status': 'pass', 'scope': 'synthetic_fixture'})
    monkeypatch.setattr(episode_assembly, 'assemble', lambda ep, units, control: {
        'source_signature': ep['id'], 'duration_seconds': 120, 'scope': 'synthetic_fixture'})
    return p['id']


def generated(uid):
    unit = store.get(uid, False)
    store.update(uid, {'status': 'unit_ready', 'progress': 100,
        'delivery': {'source_signature': uid, 'scope': 'synthetic_fixture'},
        'script': {'shots': [{'id': 'S01', 'continuity': '蓝衣'}], 'ending': '发现线索'},
        'production': {**unit.get('production', {}), 'gpu_seconds': 7}})


def test_long_unit_failure_is_persisted_to_episode_and_root_without_get(production, monkeypatch):
    def fail(uid):
        unit = store.get(uid, False)
        store.update(uid, {'production': {'gpu_seconds': 12, 'recovery': {
            'status': 'stopped', 'asset': 'S02', 'stage': 'video', 'reason': 'attempts_exhausted'}}})
        raise ProductionNeedsDecision('动作返修额度已用尽，保留原视频', 'production_recovery_stop', {'unit_id': uid})
    monkeypatch.setattr(workflows, 'advance', fail)
    job = store.claim_job()
    worker.handle(job)
    root = store.get(production, False)
    episode = store.get(job['project_id'], False)
    unit = store.get(episode['active_unit_id'], False)
    assert root['status'] == episode['status'] == unit['status'] == 'blocked'
    assert root['error'] == unit['error'] == episode['error']
    assert root['needs_decision']['episode_id'] == episode['id']
    assert unit['production']['gpu_seconds'] == 12
    assert unit['budget']['max_attempts'] == 3
    with store.connection() as db:
        before = db.execute('SELECT snapshot FROM projects ORDER BY id').fetchall()
    for _ in range(2): dramas.snapshot(production)
    with store.connection() as db:
        assert [tuple(r) for r in before] == [tuple(r) for r in db.execute('SELECT snapshot FROM projects ORDER BY id')]
    store.recover_jobs(); drama_automation.recover()
    assert store.claim_job() is None
    assert store.get(production, False)['status'] == 'blocked'


def test_worker_finishes_two_120_second_episodes_without_browser_continue(production, monkeypatch):
    monkeypatch.setattr(workflows, 'advance', generated)
    completed = []
    while job := store.claim_job():
        worker.handle(job)
        completed.append(job['project_id'])
    root = store.get(production, False)
    episodes = [store.get(eid, False) for eid in root['episodes']]
    assert completed == root['episodes']
    assert root['status'] == 'awaiting_final'
    assert all(e['status'] == 'awaiting_final' and e['delivery']['duration_seconds'] == 120 for e in episodes)
    assert all(len(e['unit_ids']) == 3 and all(store.get(uid, False)['status'] == 'unit_ready' for uid in e['unit_ids']) for e in episodes)
    assert not any(e.get('accepted_by') for e in episodes)
    store.recover_jobs(); drama_automation.recover()
    assert store.claim_job() is None


@pytest.mark.parametrize('outcome', ['pause', 'wait'])
def test_pause_and_durable_wait_propagate_without_misreporting_failure(production, monkeypatch, outcome):
    def wait(uid):
        if outcome == 'pause':
            raise ProductionPaused('已暂停')
        raise ProductionDeferred(time.time()+60, 'quality_review', {'reason': 'fixture_saved_review'})
    monkeypatch.setattr(workflows, 'advance', wait)
    job = store.claim_job(); worker.handle(job)
    episode = store.get(job['project_id'], False)
    unit = store.get(episode['active_unit_id'], False)
    assert episode['status'] == unit['status'] == ('paused' if outcome == 'pause' else 'waiting_remote')
    assert store.get(production, False)['status'] == ('paused' if outcome == 'pause' else 'producing')
    assert not episode.get('needs_decision')


def transient():
    return ProductionNeedsDecision('审核暂时断线', 'semantic_unavailable', {
        'resume_action': 'review_existing_assets_only',
        'pending': [{'asset': 'S02', 'stage': 'video', 'path': 'production/fixture/video.mp4',
                     'take': 'take_003', 'retryable_transport': True}]})


def test_typed_transport_deferrals_are_bounded_and_survive_worker_restart(production, monkeypatch):
    calls = []
    def unavailable(uid):
        calls.append(uid)
        unit = store.get(uid, False)
        store.update(uid, {'production': {**unit['production'], 'gpu_seconds': 18,
            'existing_media_fixture': 'take_003'}})
        raise transient()
    monkeypatch.setattr(workflows, 'advance', unavailable)
    job = store.claim_job()
    for count in (1, 2, 3):
        worker.handle(job)
        episode = store.get(job['project_id'], False)
        assert episode['production']['review_transport_recovery']['count'] == min(count, 2)
        unit = store.get(episode['active_unit_id'], False)
        assert unit['production']['gpu_seconds'] == 18
        assert unit['production']['existing_media_fixture'] == 'take_003'
        if count == 3:
            assert episode['status'] == unit['status'] == store.get(production, False)['status'] == 'blocked'
            break
        assert episode['status'] == unit['status'] == 'waiting_remote'
        assert dramas.snapshot(production)['automation_state']['status'] == 'reviewing'
        store.recover_jobs(); drama_automation.recover()
        assert store.claim_job() is None  # Not due yet, restart does not reset backoff.
        with store.connection() as db:
            db.execute('UPDATE jobs SET next_poll_at=0 WHERE id=?', (job['id'],))
        job = store.claim_job()
    assert len(calls) == 3
    assert store.claim_job() is None


@pytest.mark.parametrize('change', [
    {'retryable_transport': False}, {'retryable_transport': 'true'}, {'retryable_transport': None},
    {'path': ''}, {'take': ''},
])
def test_unknown_or_content_uncertain_reviews_never_auto_retry(production, monkeypatch, change):
    def fail(uid):
        exc = transient(); exc.details['pending'][0].update(change); raise exc
    monkeypatch.setattr(workflows, 'advance', fail)
    job = store.claim_job(); worker.handle(job)
    episode = store.get(job['project_id'], False)
    assert episode['status'] == 'blocked'
    assert not episode['production'].get('review_transport_recovery')
    assert store.claim_job() is None


def test_stale_worker_cannot_publish_multilevel_failure(production, monkeypatch):
    def late(uid):
        with store.connection() as db:
            db.execute('UPDATE jobs SET lease=lease+1,status="queued" WHERE id=?', (job['id'],))
        store.update(job['project_id'], {'status': 'producing', 'error': None})
        raise ProductionNeedsDecision('迟到失败不能发布', 'production_recovery_stop')
    monkeypatch.setattr(workflows, 'advance', late)
    job = store.claim_job(); worker.handle(job)
    assert store.get(job['project_id'], False)['status'] == 'producing'
    assert store.get(production, False)['status'] == 'producing'


@pytest.mark.parametrize('phase', ['diagnosing','planning','handing_off','repairing','reviewing'])
def test_current_recovery_role_and_reason_are_projected_without_writes(production, monkeypatch, phase):
    episode = store.get(store.get(production, False)['episodes'][0], False)
    long_episodes.plan_units(episode, episode['execution_config'])
    episode = store.get(episode['id'], False); uid = episode['unit_ids'][0]
    store.update(episode['id'], {'active_unit_id': uid, 'status': 'producing'})
    store.update(uid, {'production': {'recovery': {'status': phase, 'role': 'storyboard_director',
        'asset': 'S02', 'stage': 'video', 'message': '按审核证据调整表演和镜头'}}})
    result = dramas.snapshot(production)['automation_state']
    assert result['status'] == phase and result['reason'] == '按审核证据调整表演和镜头'
    assert result['recovery']['role'] == 'storyboard_director'


def test_restart_repairs_stale_unit_and_root_status_without_resubmitting(production, monkeypatch):
    episode = store.get(store.get(production, False)['episodes'][0], False)
    long_episodes.plan_units(episode, episode['execution_config'])
    episode = store.get(episode['id'], False); uid = episode['unit_ids'][0]
    store.update(uid, {'status': 'producing', 'production': {'gpu_seconds': 44, 'recovery': {'status': 'stopped'}}})
    store.update(episode['id'], {'active_unit_id': uid, 'status': 'blocked', 'error': '次数已耗尽',
        'needs_decision': {'code': 'retry_limit', 'asset': 'S02'}})
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='failed'")
    drama_automation.recover()
    assert store.get(uid, False)['status'] == store.get(production, False)['status'] == 'blocked'
    assert store.get(uid, False)['production']['gpu_seconds'] == 44
    assert store.claim_job() is None


def test_review_service_recovers_and_original_job_continues_without_extra_generation(production, monkeypatch):
    calls = []
    def execute(uid):
        calls.append(uid)
        if len(calls) == 1:
            raise transient()
        generated(uid)
    monkeypatch.setattr(workflows, 'advance', execute)
    job = store.claim_job(); worker.handle(job)
    with store.connection() as db:
        db.execute('UPDATE jobs SET next_poll_at=0 WHERE id=?', (job['id'],))
    resumed = store.claim_job(); assert resumed['id'] == job['id']
    worker.handle(resumed)
    episode = store.get(job['project_id'], False)
    assert episode['status'] == 'awaiting_final'
    assert episode['production']['review_transport_recovery']['status'] == 'resolved'
    assert episode['production']['review_transport_recovery']['count'] == 1
    assert store.claim_job()['project_id'] != episode['id']


@pytest.mark.parametrize('provider', ['openai', 'deepseek', None])
def test_unknown_paid_review_transport_is_not_resubmitted(production, monkeypatch, provider):
    episode = store.get(store.get(production, False)['episodes'][0], False)
    configuration = episode['execution_config']
    configuration['llm']['provider'] = provider
    store.update(episode['id'], {'execution_config': configuration})
    def unavailable(uid): raise transient()
    monkeypatch.setattr(workflows, 'advance', unavailable)
    job = store.claim_job(); worker.handle(job)
    current = store.get(episode['id'], False)
    assert current['status'] == 'blocked'
    assert not current['production'].get('review_transport_recovery')
    assert store.claim_job() is None


def test_independent_scene_job_failure_propagates_without_waiting_for_parent_job(production, monkeypatch):
    episode = store.get(store.get(production, False)['episodes'][0], False)
    long_episodes.plan_units(episode, episode['execution_config'])
    episode = store.get(episode['id'], False); uid = episode['unit_ids'][0]
    store.update(episode['id'], {'active_unit_id': uid, 'status': 'producing'})
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='completed'")
        store._enqueue(db, uid)
    def fail(pid): raise ProductionNeedsDecision('独立岗位返修已停止', 'production_recovery_stop')
    monkeypatch.setattr(worker, 'advance', fail)
    job = store.claim_job(uid); worker.handle(job)
    assert store.get(uid, False)['status'] == store.get(episode['id'], False)['status'] == store.get(production, False)['status'] == 'blocked'
    assert store.get(episode['id'], False)['needs_decision']['unit_id'] == uid


def test_independent_scene_wait_then_ready_requeues_parent_without_browser(production, monkeypatch):
    episode = store.get(store.get(production, False)['episodes'][0], False)
    long_episodes.plan_units(episode, episode['execution_config'])
    episode = store.get(episode['id'], False); uid = episode['unit_ids'][0]
    store.update(episode['id'], {'active_unit_id': uid, 'status': 'producing'})
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='completed'")
        store._enqueue(db, uid)
    def delayed(pid):
        raise ProductionDeferred(time.time()+30, 'quality_review', {'reason': 'saved_media_fixture'})
    monkeypatch.setattr(worker, 'advance', delayed)
    job = store.claim_job(uid); worker.handle(job)
    assert store.get(episode['id'], False)['status'] == 'waiting_remote'
    with store.connection() as db:
        db.execute('UPDATE jobs SET next_poll_at=0 WHERE id=?', (job['id'],))
    monkeypatch.setattr(worker, 'advance', generated)
    resumed = store.claim_job(uid); worker.handle(resumed)
    assert store.get(uid, False)['status'] == 'unit_ready'
    assert store.get(episode['id'], False)['status'] == 'queued'
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE project_id=? AND status='queued'", (episode['id'],)).fetchone()[0] == 1
    dramas.settle_execution(uid, resumed)
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE project_id=? AND status='queued'", (episode['id'],)).fetchone()[0] == 1


def test_read_projects_stopped_unit_truth_without_mutating_stale_episode(production, monkeypatch):
    episode = store.get(store.get(production, False)['episodes'][0], False)
    long_episodes.plan_units(episode, episode['execution_config'])
    episode = store.get(episode['id'], False); uid = episode['unit_ids'][0]
    store.update(episode['id'], {'active_unit_id': uid, 'status': 'producing'})
    store.update(uid, {'production': {'recovery': {'status': 'stopped', 'message': '没有安全的下一步方案'}}})
    result = dramas.snapshot(production)
    assert result['automation_state']['status'] == 'blocked'
    assert result['automation_state']['reason'] == '没有安全的下一步方案'
    assert store.get(episode['id'], False)['status'] == 'producing'  # GET is read-only.


def test_pause_arriving_during_transport_failure_never_becomes_blocked(production, monkeypatch):
    def unavailable(uid):
        unit = store.get(uid, False)
        store.update(unit['episode_id'], {'pause_requested': True, 'status': 'paused'})
        raise transient()
    monkeypatch.setattr(workflows, 'advance', unavailable)
    job = store.claim_job(); worker.handle(job)
    episode = store.get(job['project_id'], False)
    assert episode['status'] == 'paused'
    assert not episode['production'].get('review_transport_recovery')
    assert store.get(episode['active_unit_id'], False)['status'] == 'paused'
