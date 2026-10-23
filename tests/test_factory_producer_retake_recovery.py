"""Explicit retakes preserve lifetime accounting without inventing failed QA."""
import copy

import pytest

from factory import production, production_recovery
from factory.production_execution import DEFAULT_POLICY
from factory.scheduler import ProductionNeedsDecision
from studio import core
from tests.test_factory_autonomous_execution import autonomous, reviewer, h3_setup


@pytest.fixture
def retake(autonomous):
    project, config, _, events, compiles, executions, frame = autonomous
    config['production_recovery'] = copy.deepcopy(DEFAULT_POLICY)
    evaluate, _ = reviewer(fail_first=False)
    run = production._Run(project, config, None, None, evaluator=evaluate)
    shot = run.shots[0]
    shot['render_revisions'] = {'image': 1}
    shot['shot_revision'] = 2
    target = run._target(shot['id'], run.folder / 'assets/shots' / shot['id'],
                        'The explicitly requested new framing.', [frame], kind='image', shot=shot, review=True)
    old = {'take': 'take_001', 'attempt': 1, 'stage': 'image', 'state': 'passed',
           'source_signature': 'old-approved-source', 'shot_revision': 1,
           'production_strategy': {'signature': 'old-frozen-strategy'},
           'qa': {'status': 'pass', 'issues': []}, 'prompt': 'Old approved framing.'}
    (target['folder'] / old['take']).mkdir(parents=True)
    run._save_attempt(target, old)
    return run, target, old


def test_new_requested_revision_uses_new_prompt_and_keeps_approved_history_and_counter(retake, monkeypatch):
    run, target, old = retake
    before = (target['folder'] / 'take_001/attempt.json').read_bytes()
    monkeypatch.setattr(production_recovery, 'prepare_repair',
                        lambda *a, **k: pytest.fail('A passed review is not a failure diagnosis'))
    run._prepare_system_repair(target)
    result = run._new_attempt(target)
    assert result['take'] == 'take_002' and result['attempt'] == 2
    assert result['prompt'] == target['prompt'] and result['repair'] == {}
    assert result['render_revision'] == 1
    assert result['producer_retake']['previous_take'] == 'take_001'
    assert result['producer_retake']['attempts_used_before'] == 1
    assert run.production['recovery']['status'] == 'repairing'
    assert 'production_recovery' not in result
    assert len(run._quota_attempts(target)) == 2
    assert (target['folder'] / 'take_001/attempt.json').read_bytes() == before


@pytest.mark.parametrize('change', [
    {'state': 'rendering'}, {'qa': {'status': 'fail', 'issues': []}},
    {'render_revision': 1}, {'render_revision': 2}, {'render_revision': True},
    {'generation_plan': {'source': {'approved_shot': {'render_revisions': {'image': 1}}}}},
])
def test_same_revision_failed_or_unresolved_request_cannot_skip_repair(retake, change):
    run, target, old = retake
    assert not run._producer_retake(target, [{**old, **change}])
    assert not run._producer_retake({**target, 'upstream_return': {'case_id': 'known'}}, [old])
    assert not run._producer_retake({**target, '_frame_observation_assessment': {'status': 'fail'}}, [old])


def test_source_change_without_explicit_revision_or_same_source_is_not_producer_retake(retake):
    run, target, old = retake
    assert not run._producer_retake(target, [{**old, 'source_signature': target['signature']}])
    target['shot']['render_revisions'] = {}
    assert not run._producer_retake(target, [old])


def test_requested_retake_cannot_reset_or_exceed_lifetime_attempt_limit(retake):
    run, target, old = retake
    for number in range(2, 6):
        record = {**old, 'take': f'take_{number:03}', 'attempt': number}
        (target['folder'] / record['take']).mkdir()
        run._save_attempt(target, record)
    with pytest.raises(ProductionNeedsDecision) as exc:
        run._new_attempt(target)
    assert exc.value.reason == 'retry_limit'
    assert len(run._quota_attempts(target)) == 5
    assert not (target['folder'] / 'take_006').exists()
