"""Opt-in whole-drama contracts; synthetic tests do not certify film quality."""
import copy
import json
import time

import pytest

from factory import dramas, store, drama_intent as intent, drama_runtime, drama_automation, worker
from factory import agent_loop, generation_pipeline, drama_resources, drama_delivery
from factory.scheduler import ProductionBudgetExceeded, ProductionNeedsDecision
from factory.remote_tasks import ProductionDeferred
from tests.test_factory_dramas import isolated, input_payload, plan


def create(**values):
    return dramas.create(input_payload(production_mode='one_sentence', **values))


def reviewed(p):
    document = plan()
    for episode in document['episodes']:
        episode['beats'] = [{'id': f"E{episode['number']}B{i}", 'description': f'必要事件{i}明确推进剧情'} for i in (1, 2)]
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='completed' WHERE project_id=?", (p['id'],))
    store.update(p['id'], {'draft_plan': document, 'plan_review': {'passed': True, 'issues': []},
        'plan_review_sha256': intent.fingerprint(document), 'plan_phase': 'approved', 'status': 'awaiting_plan'})
    drama_automation.recover()
    return dramas.snapshot(p['id'])


def test_creation_freezes_and_inherits_capabilities_without_leaking_configuration(isolated):
    p = create()
    raw = store.get(p['id'], False)
    assert agent_loop.enabled(raw) and generation_pipeline.enabled(raw)
    assert 'execution_config' not in p and 'never-return-this' not in json.dumps(p)
    assert p['whole_drama_budget'] == {'gpu_minutes': 360, 'spent_gpu_minutes': 0, 'remaining_gpu_minutes': 360, 'scope': 'whole_drama'}
    assert dramas.create(input_payload(production_mode='one_sentence'))['id'] == p['id']
    p = reviewed(p)
    for e in p['episodes']:
        child = store.get(e['id'], False)
        assert child['production_spec'] == raw['production_spec']
        assert child['execution_config']['private_fixture'] == 'never-return-this'
        assert child['agent_loop'] == raw['agent_loop']
        assert child['narrative_scope']['series_finale'] == (child['episode_number'] == 3)


def test_brief_only_request_has_bounded_three_episode_defaults(isolated):
    p = dramas.create({'production_mode': 'one_sentence', 'brief': '两位成年人寻找旧信并在结尾和解', 'request_id': 'minimal'})
    assert p['episode_count'] == 3 and p['duration_seconds'] == 60 and p['format'] == 'animation'
    assert p['automation']['episode_numbers'] == [1, 2, 3]


def test_legacy_creation_does_not_activate_new_policy(isolated):
    p = dramas.create(input_payload())
    assert not intent.enabled(p) and not agent_loop.enabled(p) and not generation_pipeline.enabled(p)
    assert 'whole_drama_budget' not in p


@pytest.mark.parametrize('automation', [{'enabled': False}, {'enabled': True, 'episode_numbers': [1]}])
def test_whole_drama_authorization_cannot_silently_select_partial_scope(isolated, automation):
    with pytest.raises(ValueError, match='全部剧集'):
        create(automation=automation)


def test_fixed_whole_budget_counts_leaf_units_once_and_preserves_spend(isolated):
    p = reviewed(create())
    first, second, _ = [store.get(e['id'], False) for e in p['episodes']]
    store.update(first['id'], {'production': {'gpu_seconds': 120 * 60}, 'budget': {**first['budget'], 'spent_gpu_minutes': 120}})
    current = store.update(second['id'], {'production': {'gpu_seconds': 30 * 60}})
    assert intent.bound_budget(current, {'max_gpu_seconds': 100000})['max_gpu_seconds'] == 240 * 60
    with store.connection() as db:
        root = store._read(db, p['id'])
        unit = dramas.record('scene_unit', '单元', '已完成的场景单元', drama_id=p['id'], episode_id=first['id'], production={'gpu_seconds': 120 * 60})
        dramas.insert(db, unit)
        first['unit_ids'] = [unit['id']]
        store._write(db, first)
        assert intent.budget_snapshot(db, root)['spent_gpu_minutes'] == 150
    store.update(second['id'], {'production': {'gpu_seconds': 240 * 60}})
    with pytest.raises(ProductionBudgetExceeded):
        intent.bound_budget(store.get(second['id'], False), {'max_gpu_seconds': 100000})


def test_layered_prompt_and_multiple_voice_slots_are_opt_in(isolated):
    from factory.profile_creative import adapt_system
    from factory.prompt_registry import load_prompt
    from factory.production_profiles import speech_limit, validate_episode
    from factory.schemas import episode_schema
    p = reviewed(create())
    eid = p['episodes'][0]['id']
    drama_runtime.setup_episode(store.get(eid, False))
    e = store.get(eid, False)
    text = str(adapt_system(load_prompt('screenwriter'), e))
    assert '每镜最多一条短中文画外音' not in text
    assert '不得提前结束全剧' in text
    assert speech_limit(e, 6) == 18
    schema = episode_schema(10, e)['properties']['shots']['items']['properties']
    assert schema['dialogue']['maxItems'] == 4 and 'beat_ids' in schema
    assert schema['dialogue']['items']['properties']['start']['maximum'] == 6
    assert '全剧已铺垫' in intent.instructions(store.get(p['episodes'][2]['id'], False))


def test_planning_discovery_does_not_check_image_audio_or_video(isolated, monkeypatch):
    from tests.test_factory_role_routing import routing_config
    import factory.providers as providers
    from factory.gpu_client import GPUClient
    p = store.get(create()['id'], False)
    seen = []
    monkeypatch.setattr(providers, 'check_model', lambda model, **kw: seen.append(model['model']))
    monkeypatch.setattr(GPUClient, 'request', lambda *a, **kw: pytest.fail('planning must not query media'))
    drama_resources.ensure(p, routing_config(), role='screenwriter')
    assert seen == ['fixture-qwen']


def test_resource_wait_defers_same_worker_job_without_charging_or_resetting(isolated, monkeypatch):
    from tests.test_factory_role_routing import routing_config
    import factory.providers as providers
    p = create()
    def unavailable(*args, **kw):
        raise ConnectionError('synthetic offline discovery')
    monkeypatch.setattr(providers, 'check_model', unavailable)
    monkeypatch.setattr(drama_runtime, 'run_plan', lambda pid: drama_resources.ensure(store.get(pid, False), routing_config(), role='screenwriter'))
    job = store.claim_job()
    worker.handle(job)
    with store.connection() as db:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone()
        assert row['status'] == 'queued' and row['next_poll_at'] > time.time()
        db.execute('UPDATE jobs SET next_poll_at=0 WHERE id=?', (job['id'],))
    before = store.get(p['id'], False)
    assert before['production']['remote_wait']['wait_kind'] == 'resources'
    resumed = store.claim_job()
    assert resumed['id'] == job['id'] and resumed['lease'] > job['lease']
    after = store.get(p['id'], False)
    assert after['production'].get('gpu_seconds', 0) == 0
    assert after['production'].get('elapsed_seconds', 0) == 0


def test_invalid_role_config_is_not_a_transient_resource_wait(isolated):
    p = store.get(create()['id'], False)
    with pytest.raises(ProductionNeedsDecision) as exc:
        drama_resources.ensure(p, {'setup_incomplete': True}, role='screenwriter')
    assert exc.value.reason == 'resource_wait_configuration'


def test_unverified_dependency_cannot_use_planned_state(isolated):
    p = reviewed(create())
    first, second = p['episodes'][:2]
    store.update(first['id'], {'status': 'awaiting_final', 'delivery': {'source_signature': 'planned-is-not-evidence'}})
    with pytest.raises(ProductionNeedsDecision, match='实际末态'):
        drama_runtime.capture_dependencies(store.get(second['id'], False))


def test_plan_events_cannot_be_missing_or_duplicate(isolated):
    p = store.get(create()['id'], False)
    document = plan()
    with pytest.raises(dramas.PlanValidationError, match='必要事件'):
        dramas.validate_plan(document, p)


def test_missing_delivery_file_does_not_create_a_complete_bundle(isolated):
    p = reviewed(create())
    with pytest.raises(ProductionNeedsDecision) as exc:
        drama_delivery.assemble(p['id'])
    assert exc.value.reason == 'series_incomplete'
    assert not store.get(p['id'], False).get('delivery_bundle')


def delivery_records(p):
    """Unit-test snapshots, never used by the offline full-pipeline acceptance."""
    episodes = []
    for item in p['episodes']:
        e = store.get(item['id'], False)
        e = store.update(e['id'], {'status': 'awaiting_final', 'delivery': {'source_signature': e['id'], 'artifacts': []},
            'episode_review': {'status': 'pass', 'signature': e['id'], 'checks': [{'status': 'pass'}], 'issues': [],
                'production_revision': e['production_revision'], 'outline_sha256': intent.fingerprint(e['outline']),
                'delivery_signature': e['id'], 'evidence': {'samples': [{'shot_id': 'S01'}]}}})
        episodes.append(e)
    return episodes


def test_series_return_is_atomic_and_preserves_costs_and_prior_media(isolated):
    p = reviewed(create())
    episodes = delivery_records(p)
    root = store.get(p['id'], False)
    bad = {'status': 'fail', 'summary': '末集结局缺失', 'issues': ['结局没有出现'],
           'repairs': [{'episode_number': 1, 'shot_ids': ['S01'], 'reason': '持物不符'},
                       {'episode_number': 3, 'shot_ids': ['S99'], 'reason': '结局缺失'}]}
    with pytest.raises(ProductionNeedsDecision) as exc:
        drama_delivery._return_series(root, episodes, bad)
    assert exc.value.reason == 'series_review_invalid_scope'
    assert store.get(episodes[0]['id'], False)['delivery'] == episodes[0]['delivery']
    assert not store.get(root['id'], False).get('series_repair_history')
    bad['repairs'] = bad['repairs'][:1]
    drama_delivery._return_series(root, episodes, bad)
    returned = store.get(episodes[0]['id'], False)
    assert returned['delivery_history'][-1] == episodes[0]['delivery']
    assert returned['budget'] == episodes[0]['budget']
    assert returned['script_feedback']['shot_ids'] == ['S01']
    assert returned['script_generation'] == episodes[0]['script_generation'] + 1
    assert store.get(episodes[1]['id'], False)['delivery'] == episodes[1]['delivery']


def test_series_return_does_not_reset_attempt_limits(isolated):
    p = reviewed(create())
    episodes = delivery_records(p)
    root = store.get(p['id'], False)
    store.update(root['id'], {'series_repair_history': [{}, {}]})
    root = store.get(root['id'], False)
    with pytest.raises(ProductionNeedsDecision) as exc:
        drama_delivery._return_series(root, episodes, {'repairs': [{'episode_number': 3, 'shot_ids': ['S01'], 'reason': '结局缺失'}]})
    assert exc.value.reason == 'series_review_exhausted'
    assert store.get(episodes[2]['id'], False)['delivery'] == episodes[2]['delivery']


def test_upstream_media_revision_invalidates_adopted_dependency(isolated):
    p = reviewed(create())
    episodes = delivery_records(p)
    second = store.update(episodes[1]['id'], {'dependency_context': [{'episode_id': episodes[0]['id'], 'delivery_signature': 'obsolete'}]})
    assert drama_runtime.stale_dependencies(second, episodes)
    with pytest.raises(ProductionNeedsDecision) as exc:
        drama_runtime.capture_dependencies(second)
    assert exc.value.reason == 'dependency_version_changed'


def test_late_lease_cannot_return_or_publish_episode(isolated):
    from factory.execution_context import current_job
    from factory.scheduler import ProductionPaused
    p = reviewed(create())
    episodes = delivery_records(p)
    token = current_job.set({'id': 'missing-job', 'project_id': p['id'], 'kind': 'drama_delivery', 'lease': 1})
    try:
        with pytest.raises(ProductionPaused):
            drama_delivery._return_series(store.get(p['id'], False), episodes, {'summary': '迟到审核',
                'repairs': [{'episode_number': 1, 'shot_ids': ['S01'], 'reason': '持物不符'}]})
    finally:
        current_job.reset(token)
    assert store.get(episodes[0]['id'], False)['delivery'] == episodes[0]['delivery']


def test_corrupt_review_cache_requires_reconciliation(tmp_path):
    from studio import core
    path = tmp_path / 'review.json'
    core.write_json(path, {'signature': 'same', 'value': {'status': 'pass'}, 'sha256': 'altered'})
    with pytest.raises(ProductionNeedsDecision) as exc:
        drama_delivery._read_review(path, 'same')
    assert exc.value.reason == 'episode_review_corrupt'


@pytest.mark.parametrize('verdict', ['pass', 'uncertain'])
def test_scene_endpoint_observes_actual_video_and_preserves_uncertainty(isolated, monkeypatch, verdict):
    import subprocess
    from factory.audio_editor import FFMPEG
    from factory import llm, providers
    from studio import core
    from tests.test_factory_role_routing import routing_config
    p = reviewed(create())
    parent = store.get(p['episodes'][0]['id'], False)
    unit = dramas.record('scene_unit', '实际末帧单元', '仅验证实际末帧证据的单元',
        drama_id=p['id'], episode_id=parent['id'], episode_number=1, duration_seconds=6,
        outline={'beats': [], 'ending_state': '计划交出了杯子'}, shots=[{'id': 'S01', 'seconds': 6, 'beat_ids': []}])
    with store.connection() as db:
        dramas.insert(db, unit)
    path = core.ROOT / 'production' / unit['slug'] / 'release/episode_clean.mp4'
    path.parent.mkdir(parents=True)
    subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
        'color=c=blue:size=32x64:rate=24:duration=6', '-c:v', 'libx264', '-threads', '1', str(path)], check=True)
    unit = store.update(unit['id'], {'delivery': {'source_signature': 'actual-unit', 'artifacts': [core.media_receipt(path)]}})
    seen = []
    monkeypatch.setattr(providers, 'check_model', lambda *a, **kw: None)
    def observe(config, role, system, payload, schema, folder, **kwargs):
        seen.append(kwargs['content'])
        return {'status': verdict, 'summary': '观察实际蓝色帧，不能证明持物交接。',
                'checks': [{'beat_id': 'unit_endpoint', 'status': verdict, 'shot_ids': ['S01'], 'evidence': '实际末帧为蓝色。'}],
                'observed_end': '蓝色画面，持物情况不可见', 'issues': []}
    monkeypatch.setattr(llm, '_request', observe)
    if verdict == 'uncertain':
        with pytest.raises(ProductionNeedsDecision) as exc:
            drama_delivery.review_episode(unit, routing_config())
        assert exc.value.reason == 'episode_review_uncertain'
    else:
        drama_delivery.review_episode(unit, routing_config())
        adopted = drama_delivery.adopted_end(store.get(unit['id'], False))
        assert adopted['state'] != unit['outline']['ending_state']
        assert adopted['media_sha256'] == core.sha256(path)
        assert adopted['samples'][0]['timestamp'] == pytest.approx(6 - 1/24, abs=1e-6)
    assert any(part['type'] == 'image_url' for part in seen[0])
    saved = store.get(unit['id'], False)
    assert saved['delivery'] == unit['delivery'] and not saved.get('episode_repair_history')
    assert saved['episode_review']['evidence']['coverage'] == 'sampled_shot_endpoints_not_full_film_or_audio_observation'
