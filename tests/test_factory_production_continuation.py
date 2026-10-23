"""Explicit producer continuation; all media/model calls use isolated fixtures."""
import copy
import json

import pytest

from factory import config, dramas, store, production_continuation as continuation
from factory import production_recovery as recovery, production_strategy as strategy
from factory.creative_evidence_io import digest
from factory.production_execution import DEFAULT_POLICY
from factory.scheduler import ProductionNeedsDecision
from studio import core
from test_factory_dramas import isolated, ready
from test_factory_production_strategy import case, prepare, compile_, candidate, observe
from tests.test_factory_role_routing import mock_transport


def add_grant(project, configuration, target):
    row = {'schema': continuation.VERSION, 'actor': 'producer', 'project_id': project['id'],
           'asset': target['key'], 'stage': target['kind'], 'source_signature': target['signature'],
           'budget': {k: project['budget'][k] for k in ('gpu_minutes', 'wall_minutes', 'max_attempts')},
           'base_attempt_limit': 5, 'extra_attempts': 2, 'lifetime_attempt_limit': 7}
    saved = {'authorization': row, 'sha256': digest(row)}
    project.setdefault('production_continuations', {})[target['kind'] + ':' + target['key']] = saved
    return saved


@pytest.fixture
def stopped(isolated, monkeypatch):
    p = ready()
    eid = p['episodes'][0]['id']
    configuration = {'max_attempts': 3, 'max_gpu_seconds': 12000, 'max_wall_seconds': 10800,
                     'production_recovery': copy.deepcopy(DEFAULT_POLICY)}
    monkeypatch.setattr(config, 'production_config', lambda project, snapshot: configuration)
    shot = {'id': 'S02', 'prompt': 'Visible mouths remain naturally closed.', 'action': 'Lean forward.'}
    store.update(eid, {'status': 'blocked', 'episode': {'shots': [shot]},
        'budget': {'gpu_minutes': 200, 'wall_minutes': 180, 'max_attempts': 3, 'spent_gpu_minutes': 155.8},
        'production': {'gpu_seconds': 9348, 'elapsed_seconds': 8600},
        'needs_decision': {'code': 'retry_limit', 'details': {'asset': 'S02', 'stage': 'video'}}})
    store.update(p['id'], {'status': 'blocked', 'automation': {'enabled': True, 'episode_numbers': [1]}})
    for i in range(1, 6):
        folder = isolated / 'production' / ('factory-' + eid) / 'renders/S02' / f'take_{i:03d}'
        core.write_json(folder / 'attempt.json', {'take': folder.name, 'attempt': i, 'stage': 'video',
            'state': 'failed', 'source_signature': 'original', 'prompt': shot['prompt'], 'qa': {'status': 'fail'}})
    p = dramas.snapshot(p['id'])
    payload = {'action': 'authorize_asset_continuation', 'request_id': 'producer-two-more',
               'expected_revision': p['revision'], 'unit_id': eid, 'shot_ids': ['S02']}
    return p, eid, payload, configuration


def test_authorization_is_atomic_scoped_idempotent_and_preserves_every_existing_fact(stopped, isolated):
    p, eid, payload, cfg = stopped
    old = store.get(eid, False)
    evidence = {f: f.read_bytes() for f in isolated.rglob('attempt.json')}
    result = dramas.control(p['id'], payload)
    updated = store.get(eid, False)
    assert {k: v for k, v in updated.items() if k not in {'production_continuations', 'revision', 'updated_at'}} == {
        k: v for k, v in old.items() if k not in {'production_continuations', 'revision', 'updated_at'}}
    assert all(f.read_bytes() == data for f, data in evidence.items())
    assert result['status'] == 'blocked'
    grant = updated['production_continuations']['video:S02']['authorization']
    assert grant['lifetime_attempt_limit'] == 7 and grant['extra_attempts'] == 2
    assert dramas.control(p['id'], payload)['revision'] == result['revision']
    with pytest.raises(ValueError, match='已经追加'):
        dramas.control(p['id'], {**payload, 'request_id': 'another-id', 'expected_revision': result['revision']})
    assert 'production_continuations' not in store.get(p['episodes'][1]['id'], False)
    assert continuation.ceiling(updated, cfg, {'kind': 'video', 'key': 'S02', 'signature': 'original'}) == 7
    assert continuation.ceiling(updated, cfg, {'kind': 'video', 'key': 'S03', 'signature': 'other'}) == 5
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running','waiting_remote')").fetchone()[0] == 0


def test_explicit_unit_budget_preserves_grant_quota_usage_other_units_and_history(stopped, isolated):
    p, eid, payload, cfg = stopped
    dramas.control(p['id'], payload)
    original = store.get(eid, False)
    other = store.get(p['episodes'][1]['id'], False)
    store.update(p['id'], {'status': 'paused', 'automation': {
        'enabled': True, 'episode_numbers': [1], 'suspended': True}})
    p = dramas.snapshot(p['id'])
    body = {'action': 'authorize_unit_budget', 'request_id': 'only-first-unit-budget',
            'expected_revision': p['revision'], 'unit_id': eid, 'gpu_minutes': 300, 'wall_minutes': 270}
    evidence = {f: f.read_bytes() for f in isolated.rglob('attempt.json')}
    result = dramas.control(p['id'], body)
    updated = store.get(eid, False)
    assert updated['budget'] == {**original['budget'], 'gpu_minutes': 300, 'wall_minutes': 270}
    assert updated['production']['gpu_seconds'] == original['production']['gpu_seconds']
    assert updated['production']['elapsed_seconds'] == original['production']['elapsed_seconds']
    assert updated['production_continuation_history'] == [original['production_continuations']['video:S02']]
    assert continuation.ceiling(updated, cfg, {'kind': 'video', 'key': 'S02', 'signature': 'original'}) == 7
    assert continuation.ceiling(updated, cfg, {'kind': 'video', 'key': 'S03', 'signature': 'other'}) == 5
    assert updated['status'] == original['status'] and result['status'] == 'paused'
    assert store.get(other['id'], False) == other
    assert all(f.read_bytes() == data for f, data in evidence.items())
    assert dramas.control(p['id'], body)['revision'] == result['revision']
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running','waiting_remote')").fetchone()[0] == 0


@pytest.mark.parametrize('change', ['running', 'remote', 'wrong_unit', 'decrease', 'nan', 'attempts', 'not_paused', 'grant_tampered'])
def test_unit_budget_rejects_unsafe_or_expanded_scope_atomically(stopped, isolated, change):
    p, eid, payload, cfg = stopped
    dramas.control(p['id'], payload)
    store.update(p['id'], {'status': 'paused', 'automation': {
        'enabled': True, 'episode_numbers': [1], 'suspended': True}})
    body = {'action': 'authorize_unit_budget', 'request_id': 'budget-check',
            'unit_id': eid, 'gpu_minutes': 300, 'wall_minutes': 270}
    if change == 'running':
        with store.connection() as db: store._enqueue(db, eid, 'workflow')
    elif change == 'remote':
        core.write_json(isolated / 'production' / ('factory-' + eid) / 'renders/S02/take_005/submission.json',
                        {'state': 'running', 'gpu_service_job_id': 'a' * 64})
    elif change == 'wrong_unit': body['unit_id'] = p['episodes'][1]['id']
    elif change == 'decrease': body['gpu_minutes'] = 199
    elif change == 'nan': body['gpu_minutes'] = float('nan')
    elif change == 'attempts': body['max_attempts'] = 5
    elif change == 'not_paused': store.update(p['id'], {'status': 'producing'})
    elif change == 'grant_tampered':
        grants = copy.deepcopy(store.get(eid, False)['production_continuations'])
        grants['video:S02']['sha256'] = 'broken'
        store.update(eid, {'production_continuations': grants})
    body['expected_revision'] = dramas.snapshot(p['id'])['revision']
    with store.connection() as db: before = tuple(db.iterdump())
    with pytest.raises(ValueError): dramas.control(p['id'], body)
    with store.connection() as db: assert tuple(db.iterdump()) == before


def test_single_scene_budget_does_not_expand_sibling_or_future_batch(stopped):
    p, eid, _, _ = stopped
    units = [store.create({'brief': 'Offline scene-unit budget fixture.'}) for _ in range(2)]
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='cancelled' WHERE project_id IN (?,?)", tuple(u['id'] for u in units))
    for unit in units:
        store.update(unit['id'], {'record_kind': 'scene_unit', 'episode_id': eid, 'status': 'paused',
            'budget': {'gpu_minutes': 200, 'wall_minutes': 180, 'max_attempts': 3},
            'production': {'gpu_seconds': 100, 'elapsed_seconds': 200}})
    store.update(eid, {'unit_ids': [u['id'] for u in units]})
    automation = {'enabled': True, 'episode_numbers': [1], 'suspended': True,
                  'budget': {'gpu_minutes': 120, 'wall_minutes': 180, 'max_attempts': 3}}
    store.update(p['id'], {'status': 'paused', 'automation': automation})
    other = store.get(units[1]['id'], False)
    result = dramas.control(p['id'], {'action': 'authorize_unit_budget', 'request_id': 'scene-only',
        'unit_id': units[0]['id'], 'gpu_minutes': 300, 'wall_minutes': 270,
        'expected_revision': dramas.snapshot(p['id'])['revision']})
    assert store.get(units[1]['id'], False) == other
    assert store.get(units[0]['id'], False)['budget']['gpu_minutes'] == 300
    assert store.get(eid, False)['total_authorized_gpu_minutes'] == 500
    assert result['automation'] == automation


@pytest.mark.parametrize('change', ['running', 'wrong_unit', 'wrong_shot', 'missing_attempt', 'unknown_remote', 'budget', 'six_attempts'])
def test_authorization_refuses_unsafe_or_unverified_boundaries(stopped, isolated, change):
    p, eid, payload, cfg = stopped
    folder = isolated / 'production' / ('factory-' + eid) / 'renders/S02/take_005'
    if change == 'running':
        with store.connection() as db: store._enqueue(db, eid, 'workflow')
    elif change == 'wrong_unit': payload['unit_id'] = p['episodes'][1]['id']
    elif change == 'wrong_shot': payload['shot_ids'] = ['S03']
    elif change == 'missing_attempt': (folder / 'attempt.json').unlink()
    elif change == 'unknown_remote': core.write_json(folder / 'submission.json', {'state': 'running', 'gpu_service_job_id': 'a' * 64})
    elif change == 'budget': store.update(eid, {'production': {'gpu_seconds': 12000}})
    elif change == 'six_attempts':
        extra = core.read_json(folder / 'attempt.json'); extra['attempt'] = 6
        core.write_json(folder.parent / 'take_006/attempt.json', extra)
    old = store.get(eid, False)
    with pytest.raises(ValueError): dramas.control(p['id'], payload)
    assert store.get(eid, False) == old


@pytest.mark.parametrize('change', ['source', 'budget', 'quota', 'digest'])
def test_grant_cannot_transfer_or_expand(case, change):
    p, cfg, target, _, _, _ = case
    saved = add_grant(p, cfg, target)
    if change == 'source': target['signature'] = 'different'
    elif change == 'budget': p['budget']['gpu_minutes'] += 1
    elif change == 'quota':
        saved['authorization']['extra_attempts'] = 20
        saved['sha256'] = digest(saved['authorization'])
    else: saved['sha256'] = 'tampered'
    with pytest.raises(ValueError): continuation.ceiling(p, cfg, target)


@pytest.mark.parametrize('approve_translation', [True, False])
def test_two_new_strategies_keep_contract_and_stop_at_seven(case, monkeypatch, approve_translation):
    p, cfg, target, qa, previous, root = case
    p['brief'] = 'Keep the original narrative and voice.'
    target['shot']['action'] = 'Lean forward slightly, conveying urgent intelligence.'
    target['shot']['prompt'] += ' Her expression conveys urgent news. All dialogue is off-screen voice-over.'
    target['shot']['continuity'] = 'She reports the message.'
    calls = []
    def respond(request):
        content = request['messages'][-1]['content']
        if isinstance(content, list): result = observe()
        else:
            payload = json.loads(content)
            if 'compiled_visual_prompt' in payload:
                calls.append(payload)
                result = {'passed': approve_translation, 'reason': 'Fixture physical-action contract comparison.'}
            else: result = candidate(target['shot'], payload['project']['strategy'])
        return {'model': request['model'], 'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(result)}}]}
    mock_transport(monkeypatch, respond)
    # Retain both old strategy records, not a reset or replacement.
    prepare(case)
    previous.update(take='take_004', attempt=4); prepare(case)
    old = {f: f.read_bytes() for f in (root / 'production/factory-abc123/recovery').glob('*.json')}
    previous.update(take='take_005', attempt=5)
    assert prepare(case)['reason'] == 'retry_limit'
    add_grant(p, cfg, target)
    record = prepare(case)
    assert record['strategy'] == 'physical_action_only' and record['cycles']['asset'] == 3
    if not approve_translation:
        with pytest.raises(ProductionNeedsDecision, match='视觉转译'): compile_(case, record)
        return
    compiled = compile_(case, record)
    assert 'conveying urgent intelligence' not in compiled['parameters']['prompt']
    assert 'urgent news' not in compiled['parameters']['prompt']
    assert calls[-1]['approved_contract']['prompt'] == target['shot']['prompt']
    assert calls[-1]['approved_contract']['action'] == target['shot']['action']
    assert compiled['plan']['plan']['approved_action'] == target['shot']['action']
    assert compiled['visual_contract_verification']['passed']
    assert compiled['quality_status'] == 'pending_independent_actual_media_review'
    previous.update(take='take_006', attempt=6)
    second = prepare(case)
    assert second['strategy'] == 'closed_lip_body_sequence' and second['cycles']['asset'] == 4
    assert compile_(case, second)['parameters']['prompt'] != compiled['parameters']['prompt']
    previous.update(take='take_007', attempt=7)
    assert prepare(case)['reason'] == 'retry_limit'
    assert all(f.read_bytes() == content for f, content in old.items())
    assert p['budget']['max_attempts'] == 3 and p['production']['gpu_seconds'] == 500
