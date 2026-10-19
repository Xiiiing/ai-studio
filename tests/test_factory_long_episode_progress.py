"""Long-episode preparation exposes real work without changing execution rights."""
from __future__ import annotations

import time

import pytest

from factory import drama_assets, drama_runtime, long_episodes, store, workflows
from factory.execution_context import current_job
from factory.remote_tasks import ProductionDeferred
from factory.scheduler import ProductionPaused
from test_factory_dramas import isolated
from test_factory_long_episodes import episode_fixture


@pytest.fixture
def case(isolated, monkeypatch):
    drama, episode = episode_fixture(300, monkeypatch)
    long_episodes.plan_units(episode, {})
    episode = store.get(episode['id'], False)
    first = episode['unit_ids'][0]
    store.update(first, {'production': {'gpu_seconds': 17.25, 'remote_gpu_receipts': {'fixture-job': 17.25}},
                         'drama_references': {'approved': {'sha256': 'original-approved-receipt'}}})
    return episode['id'], first


def test_prepare_is_visible_before_adapter_and_remote_wait_reentry(case, monkeypatch):
    eid, uid = case
    before = store.get(uid, False)
    calls = []
    def prepare(ident, config, control):
        calls.append(ident)
        parent, unit = store.get(eid, False), store.get(uid, False)
        assert parent['status'] == unit['status'] == 'producing'
        assert parent['stage'] == unit['stage'] == 'shared_references'
        assert parent['active_unit_id'] == uid
        assert unit['drama_references'] == before['drama_references']
        assert unit['production'] == before['production']
        assert unit['budget'] == before['budget']
        assert control() is False
        if len(calls) == 1:
            raise ProductionDeferred(time.time() + 1, 'remote_poll', {'job_id': 'same-accepted-job'})
    def advance(ident):
        assert ident == uid
        parent = store.get(eid, False)
        assert parent['status'] == 'producing' and parent['stage'] == 'scene_units'
        raise ProductionPaused('stop fixture before any media generation')
    monkeypatch.setattr(drama_assets, 'prepare', prepare)
    monkeypatch.setattr(workflows, 'advance', advance)
    with pytest.raises(ProductionDeferred):
        drama_runtime.execute_episode(eid)
    assert store.get(eid, False)['stage'] == 'shared_references'
    # Worker wait metadata is not erased by preparation reentry.
    store.update(eid, {'status': 'waiting_remote', 'production': {'phase': 'remote_poll', 'remote_wait': {'job_id': 'same-accepted-job'}}})
    store.update(uid, {'status': 'waiting_remote'})
    with pytest.raises(ProductionPaused):
        drama_runtime.execute_episode(eid)
    assert calls == [uid, uid]
    parent, unit = store.get(eid, False), store.get(uid, False)
    assert parent['production']['remote_wait'] == {'job_id': 'same-accepted-job'}
    assert parent['production']['phase'] == 'remote_poll'
    assert unit['drama_references'] == before['drama_references']
    assert unit['production'] == before['production']
    assert unit['budget'] == before['budget']


@pytest.mark.parametrize('status,stage', [('scripting', 'screenplay'), ('producing', 'production'),
                                         ('awaiting_style', 'style_decision')])
def test_preparation_does_not_relabel_later_stage_or_overwrite_gate(case, status, stage):
    eid, uid = case
    store.update(uid, {'status': status, 'stage': stage, 'decisions': [{'id': 'pending-style', 'status': 'pending'}],
                       'selected_style': None, 'error': 'retained diagnostic'})
    before = store.get(uid, False)
    assert long_episodes._reference_stage(eid, uid)
    assert store.get(uid, False) == before
    parent = store.get(eid, False)
    assert parent['stage'] == 'scene_units' and parent['active_unit_id'] == uid


@pytest.mark.parametrize('owner', ['episode', 'unit'])
@pytest.mark.parametrize('state', ['paused', 'cancelled', 'pause_requested'])
def test_paused_or_cancelled_owners_are_not_resurrected(case, owner, state):
    eid, uid = case
    ident = eid if owner == 'episode' else uid
    store.update(ident, {'pause_requested': True} if state == 'pause_requested' else {'status': state})
    before = [store.get(i, False) for i in (eid, uid)]
    with pytest.raises(ProductionPaused):
        long_episodes._reference_stage(eid, uid)
    assert [store.get(i, False) for i in (eid, uid)] == before


def test_completed_unit_is_not_marked_or_prepared_again(case, monkeypatch):
    eid, uid = case
    store.update(uid, {'status': 'unit_ready', 'stage': 'unit_ready', 'delivery': {'source_signature': 'approved-unit'}})
    before = [store.get(i, False) for i in (eid, uid)]
    assert long_episodes._reference_stage(eid, uid) is False
    assert [store.get(i, False) for i in (eid, uid)] == before


def test_pause_arriving_during_prepare_prevents_graph_advance(case, monkeypatch):
    eid, uid = case
    def prepare(*args):
        store.update(eid, {'pause_requested': True, 'status': 'paused'})
    monkeypatch.setattr(drama_assets, 'prepare', prepare)
    monkeypatch.setattr(workflows, 'advance', lambda *args: pytest.fail('Paused preparation must not advance the graph'))
    with pytest.raises(ProductionPaused):
        drama_runtime.execute_episode(eid)
    assert store.get(eid, False)['status'] == 'paused'
    assert store.get(eid, False)['pause_requested'] is True


def test_stale_worker_cannot_publish_preparation_state(case):
    eid, uid = case
    job = store.claim_job(eid)
    assert job
    token = current_job.set({**job, 'lease': job['lease'] - 1})
    before = [store.get(i, False) for i in (eid, uid)]
    try:
        with pytest.raises(ProductionPaused) as failure:
            long_episodes._reference_stage(eid, uid)
        assert failure.value.reason == 'stale_job'
        assert [store.get(i, False) for i in (eid, uid)] == before
    finally:
        current_job.reset(token)


def test_late_prepare_result_does_not_publish_or_advance_after_lease_loss(case, monkeypatch):
    eid, uid = case
    job = store.claim_job(eid)
    assert job
    token = current_job.set(job)
    expected = []
    def prepare(*args):
        with store.connection() as db:
            db.execute("UPDATE jobs SET lease=lease+1,status='queued' WHERE id=?", (job['id'],))
        expected.extend(store.get(i, False) for i in (eid, uid))
    monkeypatch.setattr(drama_assets, 'prepare', prepare)
    monkeypatch.setattr(workflows, 'advance', lambda *args: pytest.fail('A stale worker must not advance'))
    try:
        with pytest.raises(ProductionPaused) as failure:
            drama_runtime.execute_episode(eid)
        assert failure.value.reason == 'stale_job'
        assert [store.get(i, False) for i in (eid, uid)] == expected
    finally:
        current_job.reset(token)
