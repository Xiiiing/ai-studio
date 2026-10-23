import copy
import json

import pytest

from factory import dramas, store, production_completion as completion
from factory import production_continuation, production_recovery as recovery
from factory.creative_evidence_io import digest
from factory.scheduler import Budget, ProductionPaused, ProductionNeedsDecision
from studio import core
from test_factory_dramas import isolated, ready
from test_factory_production_strategy import case, prepare, compile_, candidate
from tests.test_factory_role_routing import mock_transport


def grant(project):
    row = {'schema': completion.VERSION, 'actor': 'producer', 'mode': 'until_complete',
           'drama_id': project.get('drama_id') or project['id'],
           'episode_ids': [project.get('episode_id') or project['id']]}
    project[completion.FIELD] = {'authorization': row, 'sha256': digest(row)}


def test_completion_authorization_preserves_history_and_inherits_only_selected_episodes(isolated):
    p = ready()
    episodes = [e['id'] for e in p['episodes']]
    store.update(p['id'], {'status': 'blocked', 'automation': {'enabled': True, 'episode_numbers': [1, 2]}})
    before = {eid: store.get(eid, False) for eid in episodes}
    body = {'action': 'authorize_completion', 'request_id': 'producer-no-quotas',
            'expected_revision': dramas.snapshot(p['id'])['revision']}
    result = dramas.control(p['id'], body)
    assert dramas.control(p['id'], body)['revision'] == result['revision']
    for eid in episodes[:2]:
        updated = store.get(eid, False)
        assert completion.authorization(updated)['episode_ids'] == episodes[:2]
        assert {k: v for k, v in updated.items() if k not in {completion.FIELD, 'revision', 'updated_at'}} == {
            k: v for k, v in before[eid].items() if k not in {completion.FIELD, 'revision', 'updated_at'}}
        unit = {'id': 'new-unit', 'episode_id': eid, 'drama_id': p['id']}
        completion.inherit(updated, unit)
        assert completion.authorization(unit)
        unit['episode_id'] = episodes[2]
        with pytest.raises(ValueError): completion.authorization(unit)
    assert store.get(episodes[2], False) == before[episodes[2]]
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running','waiting_remote')").fetchone()[0] == 0


@pytest.mark.parametrize('unsafe', ['job', 'remote', 'scope'])
def test_completion_authorization_is_atomic_at_unsafe_boundary(isolated, unsafe):
    p = ready()
    eid = p['episodes'][0]['id']
    store.update(p['id'], {'status': 'blocked', 'automation': {'enabled': True, 'episode_numbers': [1]}})
    if unsafe == 'job':
        with store.connection() as db: store._enqueue(db, eid, 'workflow')
    elif unsafe == 'remote':
        core.write_json(isolated / 'production' / ('factory-' + eid) / 'renders/S01/take_001/submission.json',
                        {'state': 'running', 'gpu_service_job_id': 'a' * 64})
    else:
        store.update(p['id'], {'automation': {'enabled': True, 'episode_numbers': [99]}})
    with store.connection() as db: before = tuple(db.iterdump())
    with pytest.raises(ValueError):
        dramas.control(p['id'], {'action': 'authorize_completion', 'request_id': 'bad',
            'expected_revision': dramas.snapshot(p['id'])['revision']})
    with store.connection() as db: assert tuple(db.iterdump()) == before


def test_unrestricted_budget_keeps_receipts_pause_and_individual_timeout():
    budget = Budget({'max_gpu_seconds': 2, 'max_wall_seconds': 2, 'unrestricted_production': True},
                    {'gpu_seconds': 500, 'elapsed_seconds': 500})
    budget.check(require_gpu=True)
    assert budget.timeout(12, gpu_count=2) == 12
    budget.record_remote('a' * 64, 7)
    budget.record_remote('a' * 64, 7)
    assert budget.snapshot()['gpu_seconds'] == 507
    assert budget.snapshot()['cumulative_limits_enforced'] is False
    json.dumps(budget.snapshot(), allow_nan=False)
    with pytest.raises(ProductionPaused): budget.check(lambda: 'pause')
    with pytest.raises(ProductionNeedsDecision): budget.record_remote('a' * 64, 6)


def test_unrestricted_recovery_keeps_counts_and_unknown_submission_fence(case):
    p, cfg, target, qa, previous, root = case
    target['kind'] = 'image'
    grant(p)
    original = copy.deepcopy(p['budget'])
    p['production']['gpu_seconds'] = 999999
    previous['attempt'] = 100
    assert production_continuation.ceiling(p, cfg, target) is None
    for i in range(12):
        previous.update(take=f'take_{101+i:03d}', attempt=101+i)
        record = prepare(case)
        assert record['cycles']['asset'] == i + 1
        assert record['cycles']['max_asset'] is None
        if i >= 2: assert record['strategy'] == 'evidence_directed_revision'
    assert p['budget'] == original
    previous['state'] = 'submitted'
    assert prepare(case)['reason'] == 'unresolved_submission'
    previous['state'] = 'failed'
    p['pause_requested'] = True
    assert prepare(case)['reason'] == 'paused'


def test_actual_reference_roles_survive_strategy_compilation(case):
    from factory import production_strategy as strategy
    p, cfg, target, qa, previous, root = case
    target['kind'] = 'image'
    scene, prop = root / 'production/scene.png', root / 'production/prop.png'
    scene.write_bytes(b'isolated scene reference fixture')
    prop.write_bytes(b'isolated prop reference fixture')
    target['references'] += [scene, prop]
    target['reference_roles'] = ['woman']
    target['asset_references'] = [
        {'kind': 'scene', 'entity_id': 'room', 'sha256': core.sha256(scene)},
        {'kind': 'prop', 'entity_id': 'cup', 'sha256': core.sha256(prop)}]
    record = prepare(case)
    bindings = record['source']['reference_bindings']
    assert [(b['reference_index'], b['kind'], b['entity_id']) for b in bindings] == [
        (1, 'character', 'woman'), (2, 'scene', 'room'), (3, 'prop', 'cup')]
    prompt = strategy._prompt(candidate(target['shot']), record['source'], {}, record['strategy'])
    assert 'Reference image 1 (woman) supplies facial identity' in prompt
    assert 'Reference image 2 (room) supplies the approved environment' in prompt
    assert 'Reference image 3 (cup) supplies the complete standalone prop' in prompt
    target['asset_references'][1]['entity_id'] = 'another-prop'
    assert not recovery.applicable(record, p, cfg, target, qa, previous_attempt=previous, root=root)


def test_authorized_continuity_repairs_pass_old_limit_without_resetting_history(isolated, monkeypatch):
    from test_factory_continuity_return import setup_runtime, rejection
    from factory import drama_continuity, drama_runtime
    p, episode, calls = setup_runtime(monkeypatch)
    current = store.get(episode['id'], False)
    grant(current)
    store.update(current['id'], {completion.FIELD: current[completion.FIELD]})
    reviews = []
    def review(ep, units, *args):
        reviews.append(copy.deepcopy(units))
        if len(reviews) <= 5:
            raise rejection(units)
        return {'status': 'pass'}
    monkeypatch.setattr(drama_continuity, 'review', review)
    drama_runtime.execute_episode(episode['id'])
    result = store.get(episode['id'], False)
    target = store.get(result['unit_ids'][1], False)
    assert calls.count(target['id']) == 6
    assert len(result['continuity_repair_history']) == 5
    assert len(target['delivery_history']) == 5
    assert target['production']['gpu_seconds'] == 12
    assert completion.authorization(target)
    assert result['status'] == 'awaiting_final'
    assert not result.get('accepted_by')


@pytest.mark.parametrize('approved', [True, False, 'recover'])
def test_adaptive_image_requires_independent_contract_check_before_media(case, monkeypatch, approved):
    p, cfg, target, qa, previous, root = case
    grant(p)
    target['kind'] = 'image'
    target['key'] = 'S02'
    target['shot']['prompt'] = 'The woman stands with the white cup floating in front of her.'
    for i in range(2):
        previous.update(take=f'take_{i+3:03d}', attempt=i+3)
        prepare(case)
    previous.update(take='take_005', attempt=5)
    path = root / 'production/factory-abc123/assets/shots/S02/take_005/attempt.json'
    core.write_json(path, {**previous, 'prompt': 'A woman holds a cup.'})
    calls = []
    def respond(request):
        payload = json.loads(request['messages'][-1]['content'])
        calls.append(payload)
        if 'compiled_visual_prompt' in payload:
            result = {'passed': approved is True or (approved == 'recover' and len(calls) > 2), 'reason': 'Fixture independent starting-instant comparison.'}
        else:
            result = candidate(target['shot'], payload['project']['strategy'])
            result['plan']['image']['description'] = 'The woman stands with both empty hands visible, separated from the floating cup by a clear air gap.'
            result['performance']['start'] = 'She is seated and holds the cup before standing up.'
            if approved == 'recover' and 'contract_feedback' in payload['project']:
                result['plan']['image']['description'] += ' The cup is suspended at chest height.'
        return {'model': request['model'], 'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(result)}}]}
    mock_transport(monkeypatch, respond)
    if approved:
        result = compile_(case)
        assert 'clear air gap' in result['parameters']['prompt']
        assert 'seated' not in result['parameters']['prompt']
        assert 'before standing up' not in result['parameters']['prompt']
        assert "Both of the subject's empty hands" in result['parameters']['prompt']
        assert "at chest height" in result['parameters']['prompt']
        assert "All visible mouths stay closed" in result['parameters']['prompt']
        assert result['quality_status'] == 'pending_independent_actual_media_review'
        assert result['visual_contract_verification']['passed']
        assert len(result['rejected_compilations']) == (1 if approved == 'recover' else 0)
    else:
        with pytest.raises(ProductionNeedsDecision, match='重复返回'): compile_(case)
    assert len(calls) == (4 if approved == 'recover' else 2 if approved else 3)
    assert 'failed_visual_prompt' not in calls[0]['project']
    assert calls[0]['project']['failed_visual_prompt_sha256'] == digest('A woman holds a cup.')
    assert calls[1]['failed_visual_prompt'] == 'A woman holds a cup.'
    assert calls[1]['approved_contract'] == target['shot']
    assert 'episode' not in calls[0]['project']['project_requirements']
    assert calls[0]['project']['approved_starting_frame']['prompt'] == target['shot']['prompt']
