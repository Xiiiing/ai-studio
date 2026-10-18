"""Lifecycle summaries must reflect real evidence without mutating on reads."""
import json

from factory import drama_workflow, dramas, store
from studio import core
from test_factory_dramas import isolated, input_payload, plan, ready


def stages(value):
    return {row['id']: row for row in value['workflow']['stages']}


def test_blocked_unconfirmed_planning_keeps_review_and_evidence(isolated):
    p = dramas.create(input_payload())
    store.update(p['id'], {'draft_plan': plan(), 'plan_review': {'passed': False, 'issues': ['集间因果不连贯']},
        'status': 'blocked', 'stage': 'plan_repair'})
    directory = isolated / 'production' / p['slug'] / 'planning/cache'
    directory.mkdir(parents=True)
    core.write_json(directory / ('drama-world-1-' + 'a'*24 + '.json'), {
        'signature': 'a'*64, 'generated_at': '2026-10-18T00:00:00Z',
        'value': {'world': '真实设定', 'storyline': '真实主线', 'api_key': 'never-return-secret'},
        'prompt': {'system': 'never-return-prompt'}})
    core.write_json(directory / ('drama-plan-review-1-0-' + 'b'*24 + '.json'), {
        'signature': 'b'*64, 'value': {'passed': False, 'issues': ['集间因果不连贯']}})
    with store.connection() as db:
        before = db.total_changes
        result = dramas.snapshot(p['id'], db=db)
        assert db.total_changes == before
    assert stages(result)['planning']['status'] == 'succeeded'
    assert stages(result)['review']['status'] == 'blocked'
    assert stages(result)['confirmation']['status'] == 'pending'
    tasks = result['workflow']['tasks']
    assert len(tasks) == 2
    assert {task['role'] for task in tasks} == {'story_editor', 'story_critic'}
    assert next(t for t in tasks if t['role'] == 'story_critic')['status'] == 'blocked'
    assert 'never-return' not in json.dumps(result['workflow'])
    assert not any(t.get('project_id') == p['id'] for t in tasks)


def test_missing_evidence_does_not_create_directory_or_invent_calls(isolated):
    p = dramas.create(input_payload(source_text='用户完整的来源剧本文本仅用于创作分析'))
    root = isolated / 'production' / p['slug']
    assert not root.exists()
    result = dramas.snapshot(p['id'])
    assert not root.exists()
    assert result['workflow']['tasks'] == []
    assert stages(result)['planning']['status'] == 'pending'
    assert '用户完整的来源剧本文本' not in json.dumps(result['workflow'], ensure_ascii=False)
    assert 'workflow' not in dramas.list_dramas()['dramas'][0]


def test_confirmed_drama_links_episodes_and_real_batches(isolated):
    p = ready()
    assert stages(p)['confirmation']['status'] == 'succeeded'
    assert len([t for t in p['workflow']['tasks'] if t.get('project_id')]) == 3
    p = dramas.generate(p['id'], {'expected_revision': p['revision'], 'request_id': 'batch', 'episode_numbers': [1, 3]})
    store.update(p['episodes'][0]['id'], {'status': 'blocked', 'error': '真实制作问题'})
    store.update(p['episodes'][2]['id'], {'status': 'awaiting_final', 'delivery': {'fixture': True}})
    result = dramas.snapshot(p['id'])
    assert stages(result)['production']['status'] == 'blocked'
    assert stages(result)['acceptance']['status'] == 'waiting'
    task = next(t for t in result['workflow']['tasks'] if t['id'].startswith('batch:'))
    assert len(task['output']['members']) == 2
    links = [t for t in result['workflow']['tasks'] if t.get('project_id')]
    assert {t['project_id'] for t in links} == {e['id'] for e in result['episodes']}
    assert next(t for t in links if t['project_id'] == p['episodes'][0]['id'])['issues'] == ['真实制作问题']


def test_long_episode_links_unit_and_keeps_short_legacy_endpoint(isolated):
    p = ready()
    eid = p['episodes'][0]['id']
    # Short episodes retain their ordinary LangGraph/team workbench.
    assert dramas.episode_snapshot(p['id'], eid)['workflow'] is None
    e = store.get(eid, False)
    unit = dramas.record('scene_unit', 'E001-C01-U001', '真实场景任务', parent_id=eid,
        episode_id=eid, drama_id=p['id'], scene_id='C01', global_prefix='E001-C01-U001',
        status='unit_ready', stage='complete', progress=100)
    with store.connection() as db: dramas.insert(db, unit)
    store.update(eid, {'duration_seconds': 300, 'unit_ids': [unit['id']],
        'scene_plan': [{'scene_id': 'C01'}], 'scene_review': {'passed': True}, 'status': 'producing', 'stage': 'assembly'})
    with store.connection() as db:
        store._enqueue(db, eid, 'drama_episode')
    result = dramas.episode_snapshot(p['id'], eid)
    assert stages(result)['scenes']['status'] == 'succeeded'
    assert stages(result)['units']['status'] == 'succeeded'
    assert stages(result)['assembly']['status'] == 'running'
    assert result['workflow']['tasks'][0]['project_id'] == unit['id']
    assert result['workflow']['tasks'][0]['status'] == 'succeeded'


def test_receipt_uses_recorded_model_and_deduplicates_validated_output(isolated):
    p = dramas.create(input_payload())
    cache = isolated / 'production' / p['slug'] / 'planning/cache'
    cache.mkdir(parents=True)
    value = {'summary': '来源资料的实际整理结果'}
    core.write_json(cache / ('drama-source-0-' + 'c'*24 + '.json'), {'signature': 'c'*64, 'value': value})
    call = cache.parent / 'llm/calls/20261018T000000-story_editor-test'
    call.mkdir(parents=True)
    core.write_json(call / 'receipt.json', {'role': 'story_editor', 'status': 'succeeded',
        'configured_model': 'old-model', 'response_model': 'actual-model',
        'output_sha256': drama_workflow.digest(value), 'endpoint': 'private-endpoint', 'prompt': 'private-prompt'})
    core.write_json(call / 'parsed.json', value)
    core.write_json(call / 'request-01.json', {'messages': [
        {'role': 'system', 'content': 'never-return-system-prompt'},
        {'role': 'user', 'content': json.dumps({'brief': '实际创作方向', 'source': 'never-return-source'})}]})
    tasks = dramas.snapshot(p['id'])['workflow']['tasks']
    assert len(tasks) == 1
    assert tasks[0]['model'] == 'actual-model'
    assert tasks[0]['output'] == value
    assert 'private-' not in json.dumps(tasks)
    assert tasks[0]['input']['summary'] == {'brief': '实际创作方向'}
    assert 'never-return' not in json.dumps(tasks)


def test_corrupt_or_symlink_evidence_is_not_read(isolated, tmp_path):
    p = dramas.create(input_payload())
    cache = isolated / 'production' / p['slug'] / 'planning/cache'
    cache.mkdir(parents=True)
    bad = cache / ('drama-world-1-' + 'a'*24 + '.json')
    bad.write_text('{invalid', encoding='utf-8')
    external = isolated / 'private.json'
    external.write_text(json.dumps({'signature': 'b'*64, 'value': {'world': 'private-target'}}), encoding='utf-8')
    (cache / ('drama-world-2-' + 'b'*24 + '.json')).symlink_to(external)
    result = dramas.snapshot(p['id'])['workflow']
    assert result['tasks'] == []
    assert result['evidence']['unreadable'] == 1
    assert 'private-target' not in json.dumps(result)
    bad.unlink()
    (cache / ('drama-world-2-' + 'b'*24 + '.json')).unlink()
    cache.rmdir()
    cache.symlink_to(isolated, target_is_directory=True)
    assert dramas.snapshot(p['id'])['workflow']['tasks'] == []


def test_active_job_only_counts_real_planning_jobs(isolated):
    p = dramas.create(input_payload())
    assert p['workflow']['active_job'] is True
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='completed'")
        store._enqueue(db, p['id'], 'review_advice')
    assert dramas.snapshot(p['id'])['workflow']['active_job'] is False


def test_confirmed_legacy_without_review_does_not_invent_review_pass(isolated):
    p = ready()
    store.update(p['id'], {'plan_review': None})
    result = dramas.snapshot(p['id'])
    assert stages(result)['confirmation']['status'] == 'succeeded'
    assert stages(result)['review']['status'] == 'pending'
    assert stages(result)['review']['output'] == '尚无已保存的独立审核结果'


def test_unfinished_receipt_without_active_job_is_historical_waiting(isolated):
    p = dramas.create(input_payload())
    with store.connection() as db: db.execute("UPDATE jobs SET status='failed'")
    call = isolated / 'production' / p['slug'] / 'planning/llm/calls/20261018T000000-screenwriter-test'
    call.mkdir(parents=True)
    core.write_json(call / 'receipt.json', {'role': 'screenwriter', 'status': 'starting'})
    task = dramas.snapshot(p['id'])['workflow']['tasks'][0]
    assert task['status'] == 'waiting'
    assert task['execution_status'] == 'starting'
    assert task['issues']


def test_summary_size_is_bounded_across_nested_content():
    value = {'world': '世'*8000, 'storyline': '故'*5000,
        'characters': [{'name': '人'*30, 'identity_constraints': '设'*2000} for _ in range(16)]}
    summary = drama_workflow._summary(value)
    assert len(json.dumps(summary, ensure_ascii=False)) < 6000
