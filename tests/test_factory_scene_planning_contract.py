"""Scene reviews share the fixed episode contract and survive bounded recovery."""
import copy

import pytest
from jsonschema import ValidationError, validate

from factory import config, drama_planning, dramas, llm, long_episodes, store, workflows
from factory.scheduler import ProductionPaused
from studio.core import fingerprint
from test_factory_dramas import isolated, input_payload, plan


def episode():
    root = dramas.create(input_payload(duration_seconds=120, episode_count=2))
    # Exercise persisted0.2.5 scene contracts: candidate() intentionally has no
    # later beat/source/continuity evidence. New-contract scene coverage has its
    # own current-schema integration cases; do not invent it in this fixture.
    root = store.update(root['id'], {'creative_quality_contract': None, 'model_capability_snapshot': None})
    document = plan(2)
    document['relationships'] = ['主角与搭档共同寻找信件']
    document['scenes'] = [{'entity_id': 'station', 'name': '车站', 'description': '唯一车站'}]
    document['props'] = [{'entity_id': 'letter', 'name': '信件', 'description': '唯一信件'}]
    document['episodes'][1]['depends_on'] = [1]
    store.update(root['id'], {'draft_plan': document, 'plan_review': {'passed': True, 'issues': []}, 'status': 'awaiting_plan'})
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='completed'")
    root = dramas.snapshot(root['id'])
    root = dramas.confirm(root['id'], {'expected_revision': root['revision'], 'request_id': 'confirm'})
    root = dramas.generate(root['id'], {'expected_revision': root['revision'], 'request_id': 'generate', 'episode_numbers': [1, 2]})
    return store.get(root['episodes'][0]['id'], False)


def candidate(seconds=(60, 60)):
    return {'scenes': [{'scene_id': f'C{i:02d}', 'title': f'场景{i}',
        'synopsis': '角色在车站寻找信件，找到线索后交给搭档。', 'seconds': value,
        'characters': ['lead'], 'location_id': 'station', 'prop_ids': ['letter'],
        'opening_state': '抵达车站', 'ending_state': '发现线索'} for i, value in enumerate(seconds, 1)]}


def test_fixed_contract_reaches_both_roles_and_keeps_two_120_second_episodes(isolated, monkeypatch):
    current = episode()
    calls = []

    def call(project, cfg, role, stage, instructions, payload, schema, *args):
        calls.append((role, instructions, copy.deepcopy(payload)))
        assert payload['duration_contract']['scope'] == 'single_episode'
        assert payload['duration_contract']['target_seconds'] == 120
        assert payload['required_states']['ending'] == current['outline']['ending_state']
        assert payload['world'] == current['plan']['world']
        assert payload['relationships'] == current['plan']['relationships']
        assert payload['known_locations'] == current['plan']['scenes']
        assert payload['props'] == current['plan']['props']
        assert payload['downstream_context'] == [{'number': 2, 'opening_state': '抵达车站'}]
        if role == 'screenwriter':
            assert payload['previous_candidate'] is None
            value = candidate()
        else:
            assert payload['duration_contract']['actual_total_seconds'] == 120
            assert payload['duration_contract']['scene_seconds'] == [60, 60]
            assert payload['review_scope'] == 'current_scene_outline_not_unprovided_dialogue_or_finished_media'
            assert '逐字引文' in instructions and '不得假设未提供的完整对白' in instructions
            value = {'passed': True, 'issues': []}
        validate(value, schema)
        return value

    monkeypatch.setattr(long_episodes, 'call', call)
    long_episodes.plan_units(current, {})
    result = store.get(current['id'], False)
    assert [c[0] for c in calls] == ['screenwriter', 'independent_critic']
    assert sum(store.get(uid, False)['duration_seconds'] for uid in result['unit_ids']) == 120
    history = result['scene_plan_history']
    assert len(history) == 1 and history[0]['status'] == 'approved'
    assert history[0]['candidate_sha256'] == fingerprint(candidate())
    assert history[0]['review'] == result['scene_review']
    siblings = dramas.snapshot(current['drama_id'])['episodes']
    assert len(siblings) == 2 and [e['duration_seconds'] for e in siblings] == [120, 120]
    assert siblings[1]['status'] == 'queued'
    with store.connection() as db:
        assert not db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='queued'", (siblings[1]['id'],)).fetchone()


def test_duration_failure_returns_candidate_and_numeric_diagnostics_without_review(isolated, monkeypatch):
    current = episode()
    calls = []

    def call(project, cfg, role, stage, instructions, payload, schema, *args):
        calls.append((role, stage))
        if role == 'independent_critic':
            return {'passed': True, 'issues': []}
        if stage.endswith('-1'):
            return candidate((60, 90))
        assert payload['previous_candidate'] == candidate((60, 90))
        error = payload['technical_feedback'][0]
        assert (error['expected_seconds'], error['actual_seconds'], error['difference_seconds']) == (120, 150, 30)
        assert payload['feedback'] == [error['message']]
        return candidate()

    monkeypatch.setattr(long_episodes, 'call', call)
    long_episodes.plan_units(current, {})
    result = store.get(current['id'], False)
    assert calls == [('screenwriter', 'episode-scenes-1-1'), ('screenwriter', 'episode-scenes-1-2'),
                     ('independent_critic', 'episode-scenes-review-1-2')]
    assert [e['status'] for e in result['scene_plan_history']] == ['technical_rejected', 'approved']
    assert result['scene_plan_history'][0]['review'] is None


def test_real_review_issue_requires_revision_and_preserves_other_scene(isolated, monkeypatch):
    current = episode()
    old = candidate()
    corrected = copy.deepcopy(old)
    corrected['scenes'][1]['opening_state'] = '主角已经接过前场信件'
    issue = 'C02 opening_state“抵达车站”与C01 ending_state“发现线索”之间缺少信件承接；在原60秒内明确接过信件。'
    reviews = []

    def call(project, cfg, role, stage, instructions, payload, schema, *args):
        if role == 'screenwriter':
            if stage.endswith('-1'):
                return copy.deepcopy(old)
            assert payload['previous_candidate'] == old and payload['feedback'] == [issue]
            assert payload['technical_feedback'] == []
            return copy.deepcopy(corrected)
        reviews.append(copy.deepcopy(payload))
        return {'passed': len(reviews) > 1, 'issues': [] if len(reviews) > 1 else [issue]}

    monkeypatch.setattr(long_episodes, 'call', call)
    long_episodes.plan_units(current, {})
    result = store.get(current['id'], False)
    assert len(reviews) == 2 and reviews[1]['previous_review_feedback'] == [issue]
    assert result['scene_plan'][0] == old['scenes'][0]
    assert [v['status'] for v in result['scene_plan_history']] == ['returned', 'approved']
    assert result['scene_plan_history'][0]['review']['issues'] == [issue]


@pytest.mark.parametrize('pause_at', ['review', 'commit'])
def test_pause_resume_keeps_candidate_review_and_no_duplicate_round(isolated, monkeypatch, pause_at):
    current = episode()
    roles = []
    paused = False

    def call(project, cfg, role, stage, instructions, payload, schema, *args):
        nonlocal paused
        roles.append(role)
        if role == 'screenwriter':
            return candidate()
        if not paused:
            paused = True
            store.update(current['id'], {'pause_requested': True})
            if pause_at == 'review':
                raise ProductionPaused('模拟审核调用前暂停')
        return {'passed': True, 'issues': []}

    monkeypatch.setattr(long_episodes, 'call', call)
    with pytest.raises(ProductionPaused):
        long_episodes.plan_units(current, {})
    partial = store.get(current['id'], False)
    assert not partial['unit_ids'] and len(partial['scene_plan_history']) == 1
    assert partial['scene_plan_history'][0]['status'] == ('reviewing' if pause_at == 'review' else 'approved')
    store.update(current['id'], {'pause_requested': False})
    long_episodes.plan_units(store.get(current['id'], False), {})
    result = store.get(current['id'], False)
    assert roles.count('screenwriter') == 1
    assert roles.count('independent_critic') == (2 if pause_at == 'review' else 1)
    assert len(result['scene_plan_history']) == 1 and result['scene_plan_history'][0]['status'] == 'approved'
    assert result['unit_ids']


def test_three_rejections_remain_bounded_across_reentry_and_explicit_retry(isolated, monkeypatch):
    current = episode()
    calls = []

    def call(project, cfg, role, stage, instructions, payload, schema, *args):
        calls.append(stage)
        return candidate() if role == 'screenwriter' else {'passed': False, 'issues': ['C01 synopsis“交给搭档”但人物未列搭档，修正参与者。']}

    monkeypatch.setattr(long_episodes, 'call', call)
    for _ in range(2):
        with pytest.raises(workflows.ReviewBlocked) as error:
            long_episodes.plan_units(store.get(current['id'], False), {})
        assert error.value.details['type'] == 'episode_scenes'
    rejected = store.get(current['id'], False)
    assert len(calls) == 6 and len(rejected['scene_plan_history']) == 3
    assert not rejected['unit_ids']
    store.update(current['id'], {'status': 'blocked', 'needs_decision': error.value.details})
    root = dramas.snapshot(current['drama_id'])
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='completed'")
    dramas.control(current['drama_id'], {'expected_revision': root['revision'], 'request_id': 'retry',
        'action': 'retry', 'episode_id': current['id']})
    with pytest.raises(workflows.ReviewBlocked):
        long_episodes.plan_units(store.get(current['id'], False), {})
    after = store.get(current['id'], False)
    assert after['scene_generation'] == 2 and len(after['scene_plan_history']) == 6
    assert calls[6] == 'episode-scenes-2-1'
    assert after['scene_plan_history'][:3] == rejected['scene_plan_history']


def test_scene_schema_bounds_follow_fixed_duration_without_changing_legacy_call():
    assert long_episodes.scene_schema(['lead'])['properties']['scenes']['maxItems'] == 60
    schema = long_episodes.scene_schema(['lead'], 120)
    assert schema['properties']['scenes']['maxItems'] == 8
    validate(candidate((15,) * 8), schema)
    with pytest.raises(ValidationError):
        validate(candidate((15,) * 9), schema)
    with pytest.raises(ValidationError):
        validate(candidate((121,)), schema)


@pytest.mark.parametrize('pause_role', ['screenwriter', 'independent_critic'])
def test_pause_after_cached_call_reuses_actual_cache(isolated, monkeypatch, pause_role):
    current = episode()
    model_calls = []
    monkeypatch.setattr(config, 'llm_identity', lambda *args: {'provider': 'fixture', 'model': 'scene-model'})

    def json_call(**kwargs):
        model_calls.append(kwargs['role'])
        if kwargs['role'] == pause_role:
            store.update(current['id'], {'pause_requested': True})
        return candidate() if kwargs['role'] == 'screenwriter' else {'passed': True, 'issues': []}

    monkeypatch.setattr(llm, 'json_call', json_call)
    # Use the real planning adapter and cache; only transport is a fixture.
    monkeypatch.setattr(long_episodes, 'call', drama_planning.call)
    with pytest.raises(ProductionPaused):
        long_episodes.plan_units(current, {})
    partial = store.get(current['id'], False)
    assert bool(partial.get('scene_plan_history')) == (pause_role == 'independent_critic')
    store.update(current['id'], {'pause_requested': False})
    long_episodes.plan_units(store.get(current['id'], False), {})
    assert model_calls == ['screenwriter', 'independent_critic']
    result = store.get(current['id'], False)
    assert len(result['scene_plan_history']) == 1 and result['unit_ids']


@pytest.mark.parametrize('change', ['config', 'prompt_snapshot', 'generation'])
def test_changed_inputs_keep_history_and_do_not_reuse_approved_old_candidate(isolated, monkeypatch, change):
    current = episode()
    roles = []

    def call(project, cfg, role, stage, instructions, payload, schema, *args):
        roles.append(role)
        if role == 'screenwriter':
            return candidate()
        store.update(current['id'], {'pause_requested': True})
        return {'passed': True, 'issues': []}

    monkeypatch.setattr(long_episodes, 'call', call)
    with pytest.raises(ProductionPaused):
        long_episodes.plan_units(current, {})
    original = copy.deepcopy(store.get(current['id'], False)['scene_plan_history'])
    patch = {'pause_requested': False}
    if change == 'prompt_snapshot':
        patch['prompt_snapshot'] = {'versions': {}, 'overrides': {'role': '测试项目新要求'}}
    if change == 'generation':
        patch['scene_generation'] = 2
    store.update(current['id'], patch)
    with pytest.raises(ProductionPaused):
        long_episodes.plan_units(store.get(current['id'], False), {'model': 'changed'} if change == 'config' else {})
    result = store.get(current['id'], False)
    assert roles == ['screenwriter', 'independent_critic'] * 2
    assert len(result['scene_plan_history']) == 2
    assert result['scene_plan_history'][0] == original[0]
    assert not result['unit_ids']


def test_late_generation_cannot_publish_units_or_overwrite_newer_history(isolated, monkeypatch):
    current = episode()

    def call(project, cfg, role, stage, instructions, payload, schema, *args):
        if role == 'screenwriter':
            return candidate()
        store.update(current['id'], {'scene_generation': 2})
        return {'passed': True, 'issues': []}

    monkeypatch.setattr(long_episodes, 'call', call)
    with pytest.raises(ProductionPaused, match='版本已改变'):
        long_episodes.plan_units(current, {})
    result = store.get(current['id'], False)
    assert not result['unit_ids']
    assert len(result['scene_plan_history']) == 1 and result['scene_plan_history'][0]['status'] == 'reviewing'
    assert result['scene_generation'] == 2
