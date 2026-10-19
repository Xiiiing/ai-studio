"""Reported planning defects must repair or stop with a usable next action."""
import copy

import pytest

from factory import drama_planning as planning, dramas, store, worker, workflows
from test_factory_dramas import isolated, input_payload, plan


def project(**kwargs):
    p = dramas.create(input_payload(**kwargs))
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='completed' WHERE project_id=?", (p['id'],))
    return p


def models(monkeypatch, defect):
    calls = []
    expected = plan()

    def call(p, config, role, stage, instructions, payload, *args):
        calls.append((role, stage, copy.deepcopy(payload)))
        attempt = int(stage.split('-')[3])
        if role == 'story_editor':
            world = {k: copy.deepcopy(v) for k, v in expected.items() if k != 'episodes'}
            if attempt == 1 and defect == 'identity':
                world['characters'].append({**world['characters'][0], 'character_id': 'other', 'voice_id': 'male'})
            return world
        if role == 'screenwriter':
            outline = copy.deepcopy(expected['episodes'][payload['number']-1])
            if attempt == 1 and payload['number'] == 2:
                if defect == 'dependency': outline['depends_on'] = [2]
                if defect == 'duplicate': outline = {**copy.deepcopy(expected['episodes'][0]), 'number': 2}
                if defect == 'empty_episode':
                    outline['synopsis'] = outline['opening_state'] = outline['ending_state'] = '前集结束后众人站在车站里保持沉默。'
            return outline
        return {'passed': True, 'issues': []}
    monkeypatch.setattr(planning, 'call', call)
    return calls


@pytest.mark.parametrize('defect', ['dependency', 'duplicate', 'identity', 'empty_episode'])
def test_contract_defects_repair_within_bound_without_independent_false_approval(isolated, monkeypatch, defect):
    p = project()
    calls = models(monkeypatch, defect)
    planning.run(p['id'], {})
    result = store.get(p['id'], False)
    assert result['plan_phase'] == 'approved' and result['plan_attempt'] == 2
    assert not result['plan_version'] and not result['episodes']
    rejected, accepted = result['plan_history']
    assert rejected['review']['source'] == 'contract'
    assert not rejected['review']['passed'] and accepted['review']['passed']
    assert len([c for c in calls if c[0] == 'story_critic']) == 1
    if defect != 'identity':
        # Repairing episode 2 preserves the world and earlier episode; the
        # following episode is rebuilt with the repaired predecessor.
        assert len([c for c in calls if c[0] == 'story_editor']) == 1
        assert len([c for c in calls if c[0] == 'screenwriter' and c[2]['number'] == 1]) == 1
        assert [r['kind'] for r in accepted['reused']] == ['world', 'outline']
        assert result['draft_plan']['episodes'][0] == rejected['draft_plan']['episodes'][0]
    else:
        assert len([c for c in calls if c[0] == 'story_editor']) == 2


def test_legacy_invalid_review_draft_restarts_with_diagnostic_not_same_cached_rejection(isolated, monkeypatch):
    p = project()
    invalid = plan()
    invalid['episodes'][1]['depends_on'] = [2]
    root = store.update(p['id'], {'draft_plan': invalid, 'plan_phase': 'reviewing', 'plan_cycle': 1,
        'plan_attempt': 3, 'status': 'blocked', 'error': '剧集依赖只能引用前面的集数',
        'plan_review': {'passed': False, 'issues': ['原独立审核意见保持原文']}})
    request = {'expected_revision': root['revision'], 'request_id': 'repair-legacy', 'action': 'resume'}
    result = dramas.control(p['id'], request)
    assert result['plan_cycle'] == 2 and result['plan_attempt'] == 1
    assert dramas.control(p['id'], request)['revision'] == result['revision']
    calls = models(monkeypatch, None)
    planning.run(p['id'], {})
    result = store.get(p['id'], False)
    assert result['plan_phase'] == 'approved'
    assert all(c[0] != 'story_editor' for c in calls)
    assert all(c[2].get('number') != 1 for c in calls if c[0] == 'screenwriter')
    assert result['plan_history'][0]['draft_plan'] == invalid
    assert result['plan_review_archive'][0]['review'] == root['plan_review']


def test_user_requirements_are_pinned_and_timing_means_each_episode(isolated, monkeypatch):
    p = project(episode_count=2, duration_seconds=120)
    initial = plan(2)
    p = store.update(p['id'], {'draft_plan': initial, 'plan_phase': 'exhausted', 'plan_attempt': 3,
        'plan_cycle': 1, 'plan_review': {'passed': False, 'issues': ['第二集要有新进展']}, 'status': 'blocked'})
    feedback = '保留女主的已定身份，第二集找到新的证人，不重复第一集的发现。'
    request = {'expected_revision': p['revision'], 'request_id': 'user-feedback', 'action': 'resume', 'feedback': feedback}
    result = dramas.control(p['id'], request)
    assert dramas.control(p['id'], request)['revision'] == result['revision']
    calls = []
    def call(p, cfg, role, stage, instructions, payload, *args):
        calls.append(payload)
        assert payload['producer_feedback'] == feedback
        assert payload['episode_seconds'] == 120 and payload['total_seconds'] == 240
        assert payload['duration_scope'] == 'per_episode'
        if role == 'story_editor': return {k: copy.deepcopy(v) for k, v in initial.items() if k != 'episodes'}
        if role == 'screenwriter': return copy.deepcopy(initial['episodes'][payload['number']-1])
        assert '剧情连续不要求填写' in instructions
        return {'passed': True, 'issues': []}
    monkeypatch.setattr(planning, 'call', call)
    planning.run(p['id'], {})
    result = store.get(p['id'], False)
    assert len(calls) == 4
    assert result['planning_feedback'] == feedback
    assert result['brief'] == p['brief'] and result['duration_seconds'] == 120 and result['episode_count'] == 2
    assert not result['episodes'] and not result['plan_version']


def test_invalid_user_feedback_does_not_erase_saved_work_or_enqueue(isolated):
    p = project()
    p = store.update(p['id'], {'status': 'blocked', 'draft_plan': plan(), 'plan_phase': 'exhausted'})
    for value in ({}, 'x'*4001):
        with pytest.raises(ValueError, match='4000'):
            dramas.control(p['id'], {'expected_revision': p['revision'], 'request_id': str(type(value)), 'action': 'resume', 'feedback': value})
    assert store.get(p['id'], False) == p
    assert store.claim_job() is None


def test_repeated_contract_failures_exhaust_and_keep_every_candidate(isolated, monkeypatch):
    p = project()
    invalid = plan()
    invalid['episodes'][1]['depends_on'] = [2]
    def call(p, cfg, role, stage, instructions, payload, *args):
        if role == 'story_editor': return {k: copy.deepcopy(v) for k, v in invalid.items() if k != 'episodes'}
        assert role == 'screenwriter', 'invalid candidates must not reach independent approval'
        return copy.deepcopy(invalid['episodes'][payload['number']-1])
    monkeypatch.setattr(planning, 'call', call)
    with pytest.raises(workflows.ReviewBlocked, match='3轮'):
        planning.run(p['id'], {})
    result = store.get(p['id'], False)
    assert len(result['plan_history']) == 3 and result['plan_phase'] == 'exhausted'
    assert all(not h['review']['passed'] for h in result['plan_history'])
    assert result['draft_plan'] == invalid and not result['episodes']


def test_worker_directs_whole_drama_to_its_actual_planning_action(isolated, monkeypatch):
    p = dramas.create(input_payload())
    from factory import drama_runtime
    def blocked(pid):
        raise workflows.ReviewBlocked('规划需要修复', {'type': 'drama_plan'})
    monkeypatch.setattr(drama_runtime, 'run_plan', blocked)
    worker.handle(store.claim_job())
    with store.connection() as db:
        event = db.execute("SELECT message,data FROM events WHERE project_id=? AND kind='review_blocked'", (p['id'],)).fetchone()
    assert '全剧规划' in event['message'] and '待办决策' not in event['message']
    assert '"next_tab":"plan"' in event['data']


def test_pause_after_scoped_repair_preserves_reused_artifacts_and_review_identity(isolated, monkeypatch):
    from factory.scheduler import ProductionPaused
    from studio.core import fingerprint
    p = project()
    calls = models(monkeypatch, 'dependency')
    update = store.update
    paused = False
    def pause_before_review(pid, patch):
        nonlocal paused
        result = update(pid, patch)
        if patch.get('plan_phase') == 'reviewing' and patch.get('plan_attempt') == 2 and not paused:
            paused = True
            update(pid, {'pause_requested': True, 'status': 'paused'})
            raise ProductionPaused('scoped candidate safely saved')
        return result
    monkeypatch.setattr(store, 'update', pause_before_review)
    with pytest.raises(ProductionPaused): planning.run(p['id'], {})
    before = store.get(p['id'], False)
    assert [r['kind'] for r in before['plan_reused']] == ['world', 'outline']
    split = len(calls)
    dramas.control(p['id'], {'expected_revision': before['revision'], 'request_id': 'resume-review', 'action': 'resume'})
    planning.run(p['id'], {})
    result = store.get(p['id'], False)
    assert [c[0] for c in calls[split:]] == ['story_critic']
    assert result['plan_history'][-1]['reused'] == before['plan_reused']
    assert result['plan_review']['candidate_sha256'] == fingerprint(before['draft_plan'])
    assert result['plan_cycle'] == before['plan_cycle'] and result['plan_attempt'] == 2


def test_native_outline_contract_pins_requested_episode_and_valid_references():
    from jsonschema import validate, ValidationError
    expected = plan()
    first = expected['episodes'][0]
    schema = planning.outline_schema(1, expected)
    validate(first, schema)
    for invalid in ({**first, 'number': 2}, {**first, 'depends_on': [1]}, {**first, 'characters': ['unknown']}):
        with pytest.raises(ValidationError): validate(invalid, schema)
    second = expected['episodes'][1]
    validate({**second, 'depends_on': [1]}, planning.outline_schema(2, expected))
    with pytest.raises(ValidationError): validate({**second, 'depends_on': [2]}, planning.outline_schema(2, expected))
