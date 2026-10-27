"""Actual planning orchestration with mocked model calls, isolated durable state."""
import copy

import pytest

from factory import drama_planning, dramas, store, workflows
from test_factory_dramas import isolated, input_payload, plan_for


ISSUE = '第2集开场必须承接第1集结尾已发现线索的状态'


def pending():
    p = dramas.create(input_payload())
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='completed' WHERE project_id=?", (p['id'],))
    return p


def mock_roles(monkeypatch, reviews):
    calls = []
    decisions = iter(reviews)

    def call(project, config, role, stage, instructions, payload, *args):
        expected = plan_for(project)
        calls.append((role, stage, copy.deepcopy(payload)))
        if role == 'story_editor':
            return {k: copy.deepcopy(v) for k, v in expected.items() if k != 'episodes'}
        if role == 'screenwriter':
            return copy.deepcopy(expected['episodes'][payload['number']-1])
        if role == 'story_critic':
            passed = next(decisions)
            return {'passed': passed, 'issues': [] if passed else [ISSUE]}
        raise AssertionError(role)
    monkeypatch.setattr(drama_planning, 'call', call)
    return calls


def test_edited_plan_rejected_then_automatically_repaired_and_reviewed(isolated, monkeypatch):
    p = pending()
    original = plan_for(p)
    store.update(p['id'], {'draft_plan': original, 'plan_review': {'passed': True, 'issues': []}})
    edited = copy.deepcopy(original)
    edited['episodes'][0]['ending_state'] = '发现新线索'
    p = store.get(p['id'], False)
    dramas.confirm(p['id'], {'expected_revision': p['revision'], 'request_id': 'edit', 'plan': edited})
    calls = mock_roles(monkeypatch, [False, True])
    drama_planning.run(p['id'], {})
    result = store.get(p['id'], False)
    assert [c[0] for c in calls] == ['story_critic', 'story_editor', 'screenwriter', 'screenwriter', 'screenwriter', 'story_critic']
    assert result['status'] == 'awaiting_plan' and result['plan_phase'] == 'approved'
    assert result['plan_attempt'] == 2 and not result['episodes'] and result['plan_version'] == 0
    assert calls[1][2]['review_feedback'] == [ISSUE]
    authors = [c[2] for c in calls if c[0] == 'screenwriter']
    assert all(payload['review_feedback'] == [ISSUE] for payload in authors)
    assert authors[0]['previous_outline'] == edited['episodes'][0]
    assert result['plan_history'][0]['draft_plan'] == original
    assert result['plan_history'][-2]['draft_plan'] == edited
    assert result['plan_history'][-2]['returned_to'] == ['story_editor', 'screenwriter']
    assert [item['review']['passed'] for item in result['plan_history']] == [True, False, True]


def test_three_failed_reviews_keep_drafts_and_explicit_retry_uses_new_cycle(isolated, monkeypatch):
    p = pending()
    calls = mock_roles(monkeypatch, [False, False, False, True])
    with pytest.raises(workflows.ReviewBlocked, match='3轮'):
        drama_planning.run(p['id'], {})
    result = store.get(p['id'], False)
    assert result['plan_phase'] == 'exhausted' and result['plan_attempt'] == 3
    assert len(result['plan_history']) == 3
    assert result['plan_history'][-1]['returned_to'] == []
    assert result['plan_history'][-1]['phase'] == 'exhausted'
    assert all(item['draft_plan'] == plan_for(p) for item in result['plan_history'])
    before = {stage for _, stage, _ in calls}
    # Worker sets blocked when handling ReviewBlocked; emulate its final update.
    result = store.update(p['id'], {'status': 'blocked', 'error': 'exhausted'})
    dramas.control(p['id'], {'expected_revision': result['revision'], 'request_id': 'retry', 'action': 'retry'})
    split = len(calls)
    drama_planning.run(p['id'], {})
    result = store.get(p['id'], False)
    assert result['plan_cycle'] == 2 and result['plan_attempt'] == 1
    assert result['status'] == 'awaiting_plan' and result['error'] is None
    assert not before.intersection(stage for _, stage, _ in calls[split:])
    assert calls[split][2]['review_feedback'] == [ISSUE]
    assert len(result['plan_history']) == 4


def test_pause_after_return_resumes_next_attempt_without_repeating_rejection(isolated, monkeypatch):
    p = pending()
    calls = mock_roles(monkeypatch, [False, True])
    original_update = store.update
    paused = False

    def pause_after_return(pid, patch):
        nonlocal paused
        result = original_update(pid, patch)
        if patch.get('plan_phase') == 'returned' and not paused:
            paused = True
            original_update(pid, {'pause_requested': True, 'status': 'paused'})
            from factory.scheduler import ProductionPaused
            raise ProductionPaused('fixture safe boundary')
        return result
    monkeypatch.setattr(store, 'update', pause_after_return)
    from factory.scheduler import ProductionPaused
    with pytest.raises(ProductionPaused):
        drama_planning.run(p['id'], {})
    root = store.get(p['id'], False)
    assert root['plan_phase'] == 'returned' and root['plan_attempt'] == 1
    dramas.control(p['id'], {'expected_revision': root['revision'], 'request_id': 'resume', 'action': 'resume'})
    drama_planning.run(p['id'], {})
    result = store.get(p['id'], False)
    assert result['plan_cycle'] == 1 and result['plan_attempt'] == 2
    assert result['status'] == 'awaiting_plan'
    assert len([c for c in calls if c[0] == 'story_critic']) == 2
    assert len(result['plan_history']) == 2


def test_review_checkpoint_resumes_without_rewriting_draft(isolated, monkeypatch):
    p = pending()
    draft = plan_for(p)
    store.update(p['id'], {'draft_plan': draft, 'plan_cycle': 2, 'plan_attempt': 2,
        'plan_phase': 'reviewing', 'stage': 'plan_review', 'plan_history': []})
    calls = mock_roles(monkeypatch, [True])
    drama_planning.run(p['id'], {})
    assert [c[0] for c in calls] == ['story_critic']
    assert calls[0][1] == 'drama-plan-review-2-2-0'
    assert store.get(p['id'], False)['draft_plan'] == draft


def test_planning_resume_rejects_active_job_without_resetting_cycle(isolated):
    p = pending()
    with store.connection() as db:
        store._enqueue(db, p['id'], 'drama_plan')
    assert store.claim_job()['project_id'] == p['id']
    p = store.get(p['id'], False)
    with pytest.raises(ValueError, match='安全边界'):
        dramas.control(p['id'], {'expected_revision': p['revision'], 'request_id': 'resume', 'action': 'resume'})
    assert store.get(p['id'], False).get('plan_cycle') is None


def test_pause_mid_writing_reuses_completed_model_outputs_from_real_cache(isolated, monkeypatch):
    from factory import llm
    from factory.scheduler import ProductionPaused
    p = pending()
    expected = plan_for(p)
    expected['relationships'] = []
    model_calls = []
    paused = False

    def model(**kwargs):
        nonlocal paused
        role, payload = kwargs['role'], kwargs['payload']
        model_calls.append((role, payload.get('number')))
        if role == 'story_editor':
            return {k: copy.deepcopy(v) for k, v in expected.items() if k != 'episodes'}
        if role == 'screenwriter':
            if payload['number'] == 2 and not paused:
                paused = True
                store.update(p['id'], {'pause_requested': True, 'status': 'paused'})
            return copy.deepcopy(expected['episodes'][payload['number']-1])
        return {'passed': True, 'issues': []}
    monkeypatch.setattr(llm, 'json_call', model)
    cfg = {'llm': {'model': 'fixture'}}
    with pytest.raises(ProductionPaused):
        drama_planning.run(p['id'], cfg)
    root = store.get(p['id'], False)
    assert root['plan_phase'] == 'writing' and root['plan_attempt'] == 1
    dramas.control(p['id'], {'expected_revision': root['revision'], 'request_id': 'resume-cache', 'action': 'resume'})
    drama_planning.run(p['id'], cfg)
    assert model_calls == [('story_editor', None), ('screenwriter', 1), ('screenwriter', 2), ('screenwriter', 3), ('story_critic', None)]
    assert store.get(p['id'], False)['plan_phase'] == 'approved'


def test_runtime_failure_retry_preserves_active_cycle_and_repair_basis(isolated):
    p = pending()
    basis = {'plan': plan_for(p), 'issues': [ISSUE]}
    p = store.update(p['id'], {'status': 'blocked', 'plan_phase': 'writing',
        'plan_cycle': 4, 'plan_attempt': 2, 'plan_repair_basis': basis})
    dramas.control(p['id'], {'expected_revision': p['revision'], 'request_id': 'retry-runtime', 'action': 'retry'})
    current = store.get(p['id'], False)
    assert current['plan_cycle'] == 4 and current['plan_attempt'] == 2
    assert current['plan_repair_basis'] == basis and current['plan_phase'] == 'writing'


def test_approved_checkpoint_restores_awaiting_confirmation(isolated, monkeypatch):
    p = pending()
    store.update(p['id'], {'draft_plan':plan_for(p), 'plan_review':{'passed':True,'issues':[]},
        'plan_phase':'approved', 'plan_cycle':1, 'plan_attempt':2, 'status':'queued'})
    monkeypatch.setattr(drama_planning,'call',lambda *args:pytest.fail('approved planning must not call a model'))
    drama_planning.run(p['id'], {})
    assert store.get(p['id'], False)['status'] == 'awaiting_plan'
