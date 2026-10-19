"""Durable scoped automation; model calls are fixtures, queue/worker are real."""
import copy
import json

import pytest

from factory import drama_automation as automation, drama_planning, drama_runtime, dramas, store, worker
from studio.core import fingerprint
from test_factory_dramas import isolated, input_payload, plan, ready


CONFIG = {'enabled': True, 'episode_numbers': [1, 2, 3],
    'budget': {'gpu_minutes': 120, 'wall_minutes': 180, 'max_attempts': 3}}


def create(**values):
    return dramas.create(input_payload(automation=copy.deepcopy(CONFIG), **values))


def reviewed(pid, document=None):
    document = document or plan()
    return store.update(pid, {'draft_plan': document, 'plan_review': {'passed': True, 'issues': []},
        'plan_review_sha256': fingerprint(document), 'plan_phase': 'approved', 'status': 'awaiting_plan'})


def complete_jobs(pid):
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='completed' WHERE project_id=?", (pid,))


def action(p, kind, key=None, **values):
    return dramas.control(p['id'], {'expected_revision': p['revision'], 'request_id': key or kind,
        'action': kind, **values})


def test_worker_planning_review_dispatches_once_after_lease_released(isolated, monkeypatch):
    p = create()
    planning = store.claim_job()
    observed = []
    def run(pid):
        reviewed(pid)
        automation.reconcile_project(pid)  # Still owns root planning lease.
        observed.append(dramas.snapshot(pid))
    monkeypatch.setattr(drama_runtime, 'run_plan', run)
    worker.handle(planning)
    assert observed[0]['plan_version'] == 0 and observed[0]['episodes'] == []
    result = dramas.snapshot(p['id'])
    assert result['plan_version'] == 1 and len(result['episodes']) == 3 and len(result['batches']) == 1
    assert result['plans'][-1]['actor'] == 'automation'
    assert result['plans'][-1]['authorization']['request_id'] == 'create'
    assert result['automation_state']['status'] == 'producing'
    assert 'never-return-this' not in json.dumps(result)
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0] == 1
    for _ in range(3): automation.recover()
    assert dramas.snapshot(p['id'])['revision'] == result['revision']
    assert len(dramas.snapshot(p['id'])['batches']) == 1
    worker.handle(planning)  # Completed lease is not authorization.
    assert len(dramas.snapshot(p['id'])['batches']) == 1


def test_independent_reviewer_emits_binding_used_by_real_worker(isolated, monkeypatch):
    p = create()
    expected = plan()
    def call(project, config, role, stage, instructions, payload, *args):
        if role == 'story_editor': return {k: v for k, v in expected.items() if k != 'episodes'}
        if role == 'screenwriter': return expected['episodes'][payload['number'] - 1]
        assert role == 'story_critic'
        return {'passed': True, 'issues': []}
    monkeypatch.setattr(drama_planning, 'call', call)
    worker.handle(store.claim_job())
    result = dramas.snapshot(p['id'])
    assert result['plan_review_sha256'] == fingerprint(expected)
    assert result['plan_version'] == 1 and len(result['batches']) == 1


def test_crash_between_planning_finish_and_reconcile_recovers_atomically(isolated):
    p = create()
    job = store.claim_job()
    reviewed(p['id'])
    store.finish_job(job['id'], lease=job['lease'])
    assert not dramas.snapshot(p['id'])['episodes']
    automation.recover()
    assert len(dramas.snapshot(p['id'])['batches']) == 1
    queued = store.claim_job()
    assert queued['kind'] == 'drama_episode'
    store.recover_jobs()
    automation.recover()
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE project_id=? AND status='queued'", (queued['project_id'],)).fetchone()[0] == 1


@pytest.mark.parametrize('patch', [
    {'plan_review_sha256': None}, {'plan_review': None}, {'plan_review': {'passed': True, 'issues': ['still broken']}},
    {'plan_phase': 'reviewing'}, {'draft_plan': {**plan(), 'world': '后改了世界观但未审核'}},
])
def test_missing_stale_or_failed_review_never_fakes_confirmation(isolated, patch):
    p = create(); complete_jobs(p['id']); reviewed(p['id'])
    store.update(p['id'], patch)
    automation.recover()
    result = dramas.snapshot(p['id'])
    assert result['plan_version'] == 0 and result['batches'] == []
    assert result['automation_state']['attention'][0]['code'] == 'review_required'


def test_uncertainties_block_without_auto_accepting_user_choice(isolated):
    p = create(); complete_jobs(p['id'])
    document = plan(); document['uncertainties'] = ['尚不明确结尾是否接受信件']
    reviewed(p['id'], document); automation.recover()
    result = dramas.snapshot(p['id'])
    assert result['plan_version'] == 0
    assert result['automation_state']['attention'][0]['code'] == 'plan_uncertainty'


def test_scoped_dependency_never_expands_authorized_numbers(isolated):
    selected = {**CONFIG, 'episode_numbers': [2, 3]}
    p = dramas.create(input_payload(automation=selected)); complete_jobs(p['id'])
    document = plan(); document['episodes'][1]['depends_on'] = [1]
    reviewed(p['id'], document); automation.recover()
    result = dramas.snapshot(p['id'])
    assert [e['status'] for e in result['episodes']] == ['draft', 'waiting_dependency', 'queued']
    assert result['batches'][0]['episode_numbers'] == [2, 3]
    assert result['automation_state']['attention'][0]['code'] == 'dependency_scope'
    assert store.get(store.claim_job()['project_id'], False)['episode_number'] == 3


def test_pause_after_review_prevents_dispatch_and_resume_uses_same_authorization(isolated):
    p = create(); complete_jobs(p['id']); reviewed(p['id'])
    p = action(dramas.snapshot(p['id']), 'pause')
    automation.recover()
    assert dramas.snapshot(p['id'])['plan_version'] == 0
    p = action(p, 'resume')
    assert p['plan_version'] == 1 and len(p['batches']) == 1
    assert p['automation']['authorization']['request_id'] == 'create'
    p = action(p, 'pause', 'pause-production')
    assert store.claim_job() is None
    automation.recover()
    assert dramas.snapshot(p['id'])['automation_state']['status'] == 'paused'
    p = action(p, 'resume', 'resume-production')
    assert len(p['batches']) == 1 and store.claim_job()['kind'] == 'drama_episode'


def test_legacy_enable_is_cas_idempotent_and_preserves_started_episodes(isolated):
    p = ready()
    assert p['automation_state']['status'] == 'disabled'
    p = dramas.generate(p['id'], {'request_id': 'manual', 'expected_revision': p['revision'], 'episode_numbers': [1]})
    episode = store.get(p['episodes'][0]['id'], False)
    payload = {'request_id': 'enable', 'action': 'enable_automation', 'expected_revision': p['revision'], 'automation': CONFIG}
    result = dramas.control(p['id'], payload)
    assert len(result['batches']) == 2
    assert store.get(episode['id'], False) == episode
    assert dramas.control(p['id'], payload)['revision'] == result['revision']
    with pytest.raises(ValueError, match='更新'):
        dramas.control(p['id'], {**payload, 'request_id': 'stale'})
    with pytest.raises(ValueError, match='请求标识'):
        dramas.control(p['id'], {**payload, 'automation': {**CONFIG, 'episode_numbers': [1]}})


def test_enabling_legacy_unconfirmed_plan_requires_real_review(isolated):
    p = dramas.create(input_payload()); complete_jobs(p['id'])
    store.update(p['id'], {'draft_plan': plan(), 'plan_review': {'passed': True}, 'status': 'awaiting_plan'})
    p = action(dramas.snapshot(p['id']), 'enable_automation', automation=CONFIG)
    assert p['plan_version'] == 0 and p['stage'] == 'plan_review'
    assert p['plan_review'] is None and store.claim_job()['kind'] == 'drama_plan'


def test_episode_progress_waits_for_manual_final_and_failures_not_retried(isolated, monkeypatch):
    p = create(); complete_jobs(p['id']); reviewed(p['id']); automation.recover()
    executed = []
    def run(pid):
        executed.append(pid)
        store.update(pid, {'status': 'awaiting_final', 'stage': 'final_review', 'progress': 95,
            'delivery': {'source_signature': pid}})
    monkeypatch.setattr(drama_runtime, 'execute_episode', run)
    while job := store.claim_job(): worker.handle(job)
    result = dramas.snapshot(p['id'])
    assert len(executed) == 3 and result['automation_state']['status'] == 'awaiting_final'
    assert all(not e.get('accepted_by') for e in result['episodes'])
    automation.recover()
    assert store.claim_job() is None
    blocked = store.update(result['episodes'][0]['id'], {'status': 'blocked', 'error': 'GPU 预算用尽',
        'needs_decision': {'type': 'budget'}})
    automation.recover()
    assert dramas.snapshot(p['id'])['automation_state']['status'] == 'blocked'
    assert store.get(blocked['id'], False)['status'] == 'blocked'
    assert store.claim_job() is None


@pytest.mark.parametrize('config', [True, {'enabled': 1}, {'enabled': True, 'episode_numbers': []},
    {**CONFIG, 'episode_numbers': [True]}, {**CONFIG, 'episode_numbers': [1, 1]},
    {**CONFIG, 'budget': {**CONFIG['budget'], 'max_attempts': 6}}, {**CONFIG, 'extra': 'unknown'}])
def test_invalid_authorization_never_creates_project(isolated, config):
    with pytest.raises(ValueError): dramas.create(input_payload(automation=config))
    assert dramas.list_dramas()['dramas'] == []


def test_manual_plan_edit_waits_for_safe_pause_and_independent_review(isolated, monkeypatch):
    p = create(); complete_jobs(p['id']); reviewed(p['id']); automation.recover()
    p = dramas.snapshot(p['id'])
    edited = copy.deepcopy(plan()); edited['episodes'][0]['ending_state'] = '发现经过确认的新线索'
    with pytest.raises(ValueError, match='先暂停'):
        dramas.confirm(p['id'], {'expected_revision': p['revision'], 'request_id': 'unsafe', 'plan': edited})
    running = store.claim_job()
    p = action(p, 'pause')
    with pytest.raises(ValueError, match='安全边界'):
        dramas.confirm(p['id'], {'expected_revision': p['revision'], 'request_id': 'still-running', 'plan': edited})
    store.update(running['project_id'], {'status': 'paused'})
    store.finish_job(running['id'], lease=running['lease'])
    originals = [store.get(e['id'], False) for e in p['episodes']]
    p = dramas.confirm(p['id'], {'expected_revision': p['revision'], 'request_id': 'safe-edit', 'plan': edited})
    assert p['automation']['suspended'] and p['stage'] == 'plan_review' and p['plan_review'] is None
    assert p['plan_version'] == 1
    def review_only(project, config, role, stage, instructions, payload, *args):
        assert role == 'story_critic'
        assert payload['episodes'][0]['ending_state'] == edited['episodes'][0]['ending_state']
        return {'passed': True, 'issues': []}
    monkeypatch.setattr(drama_planning, 'call', review_only)
    worker.handle(store.claim_job())
    p = dramas.snapshot(p['id'])
    assert p['plan_review_sha256'] == fingerprint(edited) and p['plan_version'] == 1
    assert p['automation_state']['status'] == 'paused' and store.claim_job() is None
    p = action(p, 'resume')
    assert p['plan_version'] == 2 and len(p['batches']) == 1
    # Existing episode outputs and their fixed planning basis remain intact.
    for original in originals:
        current = store.get(original['id'], False)
        assert current['plan'] == original['plan'] and current['plan_version'] == 1
    assert p['affected_episodes']


def test_dispatch_failure_is_atomic_and_requires_explicit_reenable(isolated, monkeypatch):
    from factory import config
    p = create(); complete_jobs(p['id']); reviewed(p['id'])
    original_config = config.load_config
    monkeypatch.setattr(config, 'load_config', lambda: (_ for _ in ()).throw(ValueError('配置格式无效')))
    automation.recover()
    p = dramas.snapshot(p['id'])
    assert p['plan_version'] == 0 and p['episodes'] == [] and p['batches'] == []
    assert p['automation_state']['status'] == 'blocked'
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM series_bibles').fetchone()[0] == 0
    revision = p['revision']; automation.recover()
    assert dramas.snapshot(p['id'])['revision'] == revision
    monkeypatch.setattr(config, 'load_config', original_config)
    p = action(p, 'enable_automation', automation=CONFIG)
    assert p['plan_version'] == 1 and len(p['batches']) == 1


def test_recovery_after_failed_episode_releases_unrelated_queue_without_retry(isolated):
    p = create(); complete_jobs(p['id']); reviewed(p['id']); automation.recover()
    first = store.claim_job()
    store.update(first['project_id'], {'status': 'blocked', 'error': 'GPU 预算用尽'})
    store.finish_job(first['id'], error='GPU 预算用尽', lease=first['lease'])
    # Simulate crash after aggregation and before dispatch of next episode.
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        root = dramas.require(db, p['id']); dramas.aggregate(db, root); store._write(db, root)
    automation.recover()
    next_job = store.claim_job()
    assert store.get(next_job['project_id'], False)['episode_number'] == 2
    assert store.get(first['project_id'], False)['status'] == 'blocked'


def test_automation_workflow_shows_real_actor_and_final_human_gate(isolated):
    p = create(); complete_jobs(p['id']); reviewed(p['id']); automation.recover()
    result = dramas.snapshot(p['id'])
    nodes = {n['id']: n for n in result['workflow']['graph']['nodes']}
    assert nodes['confirmation']['owner'] == 'orchestrator'
    assert '授权' in nodes['confirmation']['description']
    assert nodes['acceptance']['owner'] == 'producer'


def test_reading_automation_snapshot_never_dispatches_or_writes(isolated):
    p = create(); complete_jobs(p['id']); reviewed(p['id'])
    root = store.get(p['id'], False)
    for _ in range(3):
        assert dramas.snapshot(p['id'])['automation_state']['status'] == 'planning'
        assert dramas.list_dramas()['dramas'][0]['plan_version'] == 0
    assert store.get(p['id'], False) == root
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM projects').fetchone()[0] == 1


def test_concurrent_authorization_uses_cas_without_duplicate_batch(isolated):
    from concurrent.futures import ThreadPoolExecutor
    p = ready()
    def enable(index):
        try:
            return action(p, 'enable_automation', f'enable-{index}', automation=CONFIG)
        except ValueError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(enable, (1, 2)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert any(isinstance(result, str) and '更新' in result for result in results)
    assert len(dramas.snapshot(p['id'])['batches']) == 1


def test_http_create_and_explicit_enable_accept_automation_contract(isolated):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from factory.web import Handler
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    def post(path, value, code):
        request = urllib.request.Request(base + path, json.dumps(value).encode(),
            {'Origin': base, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(request) as response:
            assert response.status == code
            return json.load(response)
    try:
        p = post('/api/factory/dramas', input_payload(automation=CONFIG), 201)
        assert p['automation']['enabled'] and p['automation_state']['status'] == 'planning'
        p = post('/api/factory/dramas/' + p['id'] + '/actions', {'action': 'enable_automation',
            'automation': {**CONFIG, 'episode_numbers': [1]}, 'request_id': 'authorize-narrow-scope',
            'expected_revision': p['revision']}, 202)
        assert p['automation']['episode_numbers'] == [1]
        assert p['automation']['authorization']['request_id'] == 'authorize-narrow-scope'
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_late_recovered_planning_checkpoint_cannot_overwrite_current_review(isolated):
    from factory.execution_context import current_job
    from factory.scheduler import ProductionPaused
    p = create(); old_job = store.claim_job()
    store.recover_jobs(); current = store.claim_job()
    assert current['lease'] > old_job['lease']
    latest = reviewed(p['id'])
    token = current_job.set(old_job)
    try:
        with pytest.raises(ProductionPaused, match='执行权'):
            drama_planning._save(p['id'], {'draft_plan': {**plan(), 'world': '迟到的旧世界观'}})
        with store.connection() as db:
            with pytest.raises(ProductionPaused, match='执行权'): drama_planning._authority(db, p['id'])
    finally:
        current_job.reset(token)
    assert store.get(p['id'], False) == latest
    # Current worker can commit; finishing the stale lease cannot trigger work.
    token = current_job.set(current)
    try:
        drama_planning._save(p['id'], {'stage': 'plan_confirmation'})
    finally:
        current_job.reset(token)
    assert not store.finish_job(old_job['id'], lease=old_job['lease'])
    automation.recover()
    assert dramas.snapshot(p['id'])['plan_version'] == 0
    store.finish_job(current['id'], lease=current['lease'])
    automation.recover()
    assert dramas.snapshot(p['id'])['plan_version'] == 1


def test_paused_initial_plan_edit_reviews_while_automation_stays_suspended(isolated, monkeypatch):
    p = create(); complete_jobs(p['id']); reviewed(p['id'])
    p = action(dramas.snapshot(p['id']), 'pause')
    assert p['episodes'] == []
    edited = copy.deepcopy(plan()); edited['episodes'][0]['ending_state'] = '用户确认的新结尾状态'
    p = dramas.confirm(p['id'], {'expected_revision': p['revision'], 'request_id': 'edit-initial', 'plan': edited})
    assert p['automation']['suspended'] and not p['pause_requested']
    assert p['plan_version'] == 0 and p['stage'] == 'plan_review'
    def review(project, config, role, stage, instructions, payload, *args):
        assert role == 'story_critic'
        return {'passed': True, 'issues': []}
    monkeypatch.setattr(drama_planning, 'call', review)
    worker.handle(store.claim_job())
    p = dramas.snapshot(p['id'])
    assert p['plan_phase'] == 'approved' and p['plan_version'] == 0
    assert p['automation_state']['status'] == 'paused' and store.claim_job() is None
    p = action(p, 'resume')
    assert p['plan_version'] == 1 and len(p['batches']) == 1
    assert p['plans'][-1]['document'] == edited


@pytest.mark.parametrize('phase', ['writing', 'reviewing', 'approved', 'exhausted'])
def test_changed_planning_feedback_restarts_bounded_writing_and_reaches_all_roles(isolated, monkeypatch, phase):
    p = create(); complete_jobs(p['id']); reviewed(p['id'])
    original_brief = p['brief']
    store.update(p['id'], {'plan_cycle': 2, 'plan_attempt': 3, 'plan_phase': phase,
        'status': 'blocked', 'error': 'old failure'})
    p = dramas.snapshot(p['id'])
    feedback = '保留车站人物和画风，结尾改为成年姐妹在站台和解。'
    body = {'action': 'retry', 'request_id': 'new-feedback', 'expected_revision': p['revision'], 'feedback': feedback}
    p = dramas.control(p['id'], body)
    assert p['brief'] == original_brief and p['plan_feedback'] == feedback
    assert p['plan_cycle'] == 3 and p['plan_attempt'] == 1 and p['plan_phase'] == 'writing'
    assert p['plan_review'] is None and p['plan_review_sha256'] is None
    assert p['plan_feedback_history'][-1]['request_id'] == 'new-feedback'
    assert dramas.control(p['id'], body)['plan_cycle'] == 3
    calls = []
    expected = plan()
    def call(project, config, role, stage, instructions, payload, *args):
        calls.append((role, stage, payload))
        assert payload['producer_feedback'] == feedback
        if role == 'story_editor': return {k: v for k, v in expected.items() if k != 'episodes'}
        if role == 'screenwriter': return expected['episodes'][payload['number'] - 1]
        assert role == 'story_critic'
        return {'passed': True, 'issues': []}
    monkeypatch.setattr(drama_planning, 'call', call)
    worker.handle(store.claim_job())
    assert calls[0][0] == 'story_editor' and calls[0][1] == 'drama-world-3-1'
    assert {c[0] for c in calls} == {'story_editor', 'screenwriter', 'story_critic'}
    result = dramas.snapshot(p['id'])
    assert result['plan_phase'] == 'approved' and result['plan_attempt'] == 1
    assert result['plan_version'] == 1 and result['brief'] == original_brief


def test_same_feedback_resume_keeps_review_cycle_and_no_duplicate_history(isolated):
    p = create(); complete_jobs(p['id'])
    p = store.update(p['id'], {'plan_cycle': 2, 'plan_attempt': 2, 'plan_phase': 'reviewing',
        'draft_plan': plan(), 'plan_feedback': '保留车站结尾', 'status': 'paused', 'pause_requested': True})
    result = action(p, 'resume', feedback='  保留车站结尾  ')
    assert result['plan_cycle'] == 2 and result['plan_attempt'] == 2 and result['plan_phase'] == 'reviewing'
    assert result.get('plan_feedback_history', []) == []


@pytest.mark.parametrize('feedback', [None, 42, {'text': 'invalid'}, '很' * 4001])
def test_planning_feedback_validation_is_transactional(isolated, feedback):
    p = create(); complete_jobs(p['id'])
    with pytest.raises(ValueError, match='补充要求'):
        action(p, 'retry', feedback=feedback)
    assert dramas.snapshot(p['id'])['revision'] == p['revision']
    assert 'plan_feedback' not in store.get(p['id'], False)


def test_budget_exhaustion_reauthorization_preserves_spend_then_explicit_resume(isolated, monkeypatch):
    from factory.scheduler import Budget
    p = create(); complete_jobs(p['id']); reviewed(p['id']); automation.recover()
    episode = dramas.snapshot(p['id'])['episodes'][0]
    usage = {'gpu_seconds': 7200, 'elapsed_seconds': 4800, 'remote_gpu_receipts': {'receipt': 7200},
        'successful_asset': {'sha256': 'saved-artifact'}, 'max_gpu_seconds': 7200, 'max_wall_seconds': 10800}
    e = store.update(episode['id'], {'status': 'blocked', 'error': 'budget exhausted', 'production': usage,
        'needs_decision': {'type': 'budget', 'exhausted_limits': ['gpu']}})
    complete_jobs(e['id'])
    p = action(dramas.snapshot(p['id']), 'pause')
    new_config = {**CONFIG, 'budget': {'gpu_minutes': 180, 'wall_minutes': 240, 'max_attempts': 4}}
    before_plan = store.get(e['id'], False)['plan']
    p = action(p, 'enable_automation', 'raise-budget', automation=new_config)
    current = store.get(e['id'], False)
    assert p['automation']['suspended'] and p['automation_state']['status'] == 'paused'
    assert current['status'] == 'paused' and current['budget']['gpu_minutes'] == 180
    assert current['budget']['spent_gpu_minutes'] == 120
    assert current['production']['gpu_seconds'] == usage['gpu_seconds']
    assert current['production']['remote_gpu_receipts'] == usage['remote_gpu_receipts']
    assert current['production']['successful_asset'] == usage['successful_asset']
    assert current['plan'] == before_plan
    assert current['execution_config']['production']['max_gpu_seconds'] == 10800
    assert current['execution_config']['creative']['max_review_rounds'] == 4
    assert current['production']['max_attempts'] == 4
    assert store.claim_job() is None
    automation.recover(); assert store.claim_job() is None
    p = action(p, 'resume')
    job = store.claim_job(); assert job['project_id'] == e['id']
    observed = []
    def execute(pid):
        resumed = store.get(pid, False)
        limits = resumed['execution_config']['production']
        budget = Budget(limits, resumed['production'])
        assert budget.base_gpu == 7200 and budget.base_wall == 4800
        budget.check(require_gpu=True)
        observed.append(budget.max_gpu)
        # Still blocked work is not automatically retried after this execution.
        store.update(pid, {'status': 'blocked', 'error': 'needs explicit handling'})
    monkeypatch.setattr(drama_runtime, 'execute_episode', execute)
    worker.handle(job)
    assert observed == [10800]
    automation.recover()
    assert store.get(e['id'], False)['status'] == 'blocked'
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE project_id=? AND status IN ('running','queued')", (e['id'],)).fetchone()[0] == 0


def test_begun_budget_adjustment_requires_global_pause_and_no_running_job(isolated):
    p = create(); complete_jobs(p['id']); reviewed(p['id']); automation.recover()
    p = dramas.snapshot(p['id'])
    config = {**CONFIG, 'budget': {**CONFIG['budget'], 'gpu_minutes': 180}}
    with pytest.raises(ValueError, match='先暂停'):
        action(p, 'enable_automation', automation=config)
    job = store.claim_job()
    p = action(p, 'pause')
    with pytest.raises(ValueError, match='安全边界'):
        action(p, 'enable_automation', 'active-budget', automation=config)
    assert store.get(job['project_id'], False)['budget']['gpu_minutes'] == 120
    assert dramas.snapshot(p['id'])['revision'] == p['revision']


def test_long_budget_adjustment_updates_only_unfinished_units_and_preserves_completed(isolated):
    p = create(duration_seconds=300); complete_jobs(p['id']); reviewed(p['id']); automation.recover()
    p = dramas.snapshot(p['id']); e = store.get(p['episodes'][0]['id'], False)
    complete_jobs(e['id'])
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        units = []
        for number, state in [(1, 'unit_ready'), (2, 'blocked')]:
            unit = dramas.record('scene_unit', f'unit {number}', 'preserved', episode_id=e['id'], drama_id=p['id'],
                budget={**CONFIG['budget'], 'spent_gpu_minutes': 120}, status=state, duration_seconds=75,
                execution_config=copy.deepcopy(e['execution_config']), plan=copy.deepcopy(e['plan']),
                production={'gpu_seconds': 7200, 'elapsed_seconds': 9000, 'artifact': 'success' if number == 1 else 'pending'},
                delivery={'source_signature': 'finished'} if number == 1 else None)
            dramas.insert(db, unit); units.append(unit)
        e.update(unit_ids=[u['id'] for u in units], status='blocked', production={'gpu_seconds': 14400},
            budget={**CONFIG['budget'], 'spent_gpu_minutes': 240})
        store._write(db, e)
    # Unrelated completed episode remains byte-for-byte untouched.
    completed = store.update(p['episodes'][1]['id'], {'status': 'awaiting_final', 'delivery': {'source_signature': 'done'}})
    p = action(dramas.snapshot(p['id']), 'pause')
    completed_unit = store.get(units[0]['id'], False)
    new_config = {**CONFIG, 'budget': {'gpu_minutes': 180, 'wall_minutes': 240, 'max_attempts': 4}}
    p = action(p, 'enable_automation', automation=new_config)
    assert store.get(units[0]['id'], False) == completed_unit
    assert store.get(completed['id'], False) == completed
    pending = store.get(units[1]['id'], False)
    assert pending['budget']['gpu_minutes'] == 180 and pending['budget']['spent_gpu_minutes'] == 120
    assert pending['production']['gpu_seconds'] == 7200 and pending['production']['elapsed_seconds'] == 9000
    assert pending['production']['artifact'] == 'pending' and pending['execution_config']['production']['max_wall_seconds'] == 14400
    parent = store.get(e['id'], False)
    assert parent['budget']['spent_gpu_minutes'] == 240
    assert parent['total_authorized_gpu_minutes'] == 300  # 120 finished + 180 unfinished.
    assert parent['total_authorized_wall_minutes'] == 420
    assert parent['pause_requested'] and store.claim_job() is None


@pytest.mark.parametrize('budget', [
    {'gpu_minutes': 119, 'wall_minutes': 240, 'max_attempts': 4},
    {'gpu_minutes': 180, 'wall_minutes': 179, 'max_attempts': 4},
    {'gpu_minutes': 180, 'wall_minutes': 240, 'max_attempts': 2},
    {'gpu_minutes': 180, 'wall_minutes': 240, 'max_attempts': 4},
])
def test_reauthorization_rejects_cap_decrease_or_below_cumulative_use(isolated, budget):
    p = create(); complete_jobs(p['id']); reviewed(p['id']); automation.recover()
    p = dramas.snapshot(p['id'])
    e = store.update(p['episodes'][0]['id'], {'status': 'blocked', 'production': {'gpu_seconds': 11000}})
    complete_jobs(e['id'])
    p = action(dramas.snapshot(p['id']), 'pause')
    before = store.get(e['id'], False)
    with pytest.raises(ValueError, match='不能降低|累计已用'):
        action(p, 'enable_automation', automation={**CONFIG, 'budget': budget})
    assert store.get(e['id'], False) == before
    assert dramas.snapshot(p['id'])['revision'] == p['revision']
