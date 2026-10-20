"""Explicit resource scheduling never changes a production authorization."""
import copy

import pytest

from factory import store, dramas, long_episodes
from studio import core
from tests.test_factory_execution_settlement import production
from tests.test_factory_autonomous_execution import autonomous, h3_setup
from test_factory_dramas import isolated


@pytest.fixture
def stopped(production):
    root = store.get(production, False)
    episode = store.get(root['episodes'][0], False)
    long_episodes.plan_units(episode, episode['execution_config'])
    episode = store.get(episode['id'], False)
    units = episode['unit_ids']
    store.update(units[0], {'budget': {'gpu_minutes': 200}, 'production': {
        'gpu_seconds': 7876.91, 'elapsed_seconds': 7492,
        'recovery': {'status': 'stopped', 'reason': 'retry_limit'}}})
    for uid in units:
        store.update(uid, {'status': 'blocked'})
    store.update(episode['id'], {'active_unit_id': units[0], 'status': 'blocked'})
    store.update(root['id'], {'status': 'blocked'})
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='failed'")
    return root['id'], units


def wait(pid, key='wait-resource-fixture', **extra):
    return dramas.control(pid, {'action': 'wait_for_resources', 'request_id': key,
        'expected_revision': store.get(pid, False)['revision'], **extra})


def test_resource_wait_queues_existing_work_and_preserves_all_unit_budgets(stopped):
    pid, units = stopped
    before = {uid: copy.deepcopy(store.get(uid, False)) for uid in units}
    root = wait(pid)
    assert root['automation_state']['status'] == 'waiting_resources'
    assert not root['pause_requested'] and not root['automation']['suspended']
    for uid, old in before.items():
        current = store.get(uid, False)
        assert current['budget'] == old['budget']
        assert current['execution_config'] == old['execution_config']
        assert current['production'] == {**old['production'], 'resource_wait': current['production']['resource_wait']}
        assert current['production']['resource_wait']['authorized_by'] == 'producer'
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0] == 1


def test_duplicate_wait_authorization_does_not_duplicate_jobs_or_refresh_allowance(stopped):
    pid, units = stopped
    first = wait(pid)
    authorization = copy.deepcopy(first['automation']['resource_wait'])
    wait(pid)
    again = wait(pid, 'different-request-id')
    assert again['automation']['resource_wait'] == authorization
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0] == 1


def test_wait_never_crosses_an_unknown_generation_submission(stopped):
    pid, units = stopped
    unit = store.get(units[0], False)
    core.write_json(core.ROOT / 'production' / unit['slug'] / 'renders/S01/take_001/submission.json',
                    {'prompt_id': 'unknown-fixture-result', 'state': 'submitted'})
    with pytest.raises(ValueError, match='对账'):
        wait(pid)
    assert not store.get(pid, False)['automation'].get('resource_wait')
    assert not store.get(units[0], False)['production'].get('resource_wait')


def test_wait_does_not_accept_new_budget_or_model_parameters(stopped):
    pid, units = stopped
    with pytest.raises(ValueError, match='未知字段'):
        wait(pid, automation={'budget': {'gpu_minutes': 999}})


def test_scheduler_events_preserve_producer_resource_authorization(autonomous):
    from factory import production as engine
    project, configuration, *_ = autonomous
    run = engine._Run(project, configuration, None, None, evaluator=lambda *a: {})
    authorization = {'enabled': True, 'authorized_by': 'producer', 'authorization_id': 'fixture-auth',
                     'status': 'resolved', 'reason': 'model_resources_unavailable', 'poll_seconds': 300}
    run.production['resource_wait'] = copy.deepcopy(authorization)
    for status in ['checking', 'waiting', 'ready']:
        event = {'status': status, 'scope': 'synthetic scheduler state'}
        run._resource_event(event)
        assert run.production['resource_wait'] == {**authorization, 'scheduler_event': event}
