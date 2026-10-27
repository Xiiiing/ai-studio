"""Lifecycle summaries must reflect real evidence without mutating on reads."""
import json

from factory import drama_workflow, dramas, store
from studio import core
from test_factory_dramas import isolated, input_payload, plan_for, ready


def stages(value):
    return {row['id']: row for row in value['workflow']['stages']}


def test_blocked_unconfirmed_planning_keeps_review_and_evidence(isolated):
    p = dramas.create(input_payload())
    store.update(p['id'], {'draft_plan': plan_for(p), 'plan_review': {'passed': False, 'issues': ['集间因果不连贯']},
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
    assert tasks[0]['artifacts'][0]['label'] == '完整结果原文'
    assert tasks[0]['artifacts'][0]['artifact_url'].endswith('/' + call.name + '/parsed.json')
    assert 'private-' not in json.dumps(tasks)
    assert tasks[0]['input']['summary'] == {'brief': '实际创作方向'}
    assert 'never-return' not in json.dumps(tasks)
    core.write_json(call / 'parsed.json', {'summary': '已经与回执签名不符的文件'})
    assert 'artifacts' not in dramas.snapshot(p['id'])['workflow']['tasks'][0]


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


def test_active_return_and_public_history_do_not_publish_private_drafts(isolated):
    p = dramas.create(input_payload())
    store.update(p['id'], {'draft_plan':plan_for(p), 'plan_phase':'writing', 'plan_attempt':2,
        'plan_review':{'passed':False,'issues':['承接冲突']},
        'plan_repair_basis':{'private_input':'never-expose-repair-basis'},
        'plan_history':[{'cycle':1,'attempt':1,'draft_plan':{'archive':'never-expose-archived-draft'},
            'review':{'passed':False,'issues':['承接冲突']},'returned_to':['screenwriter']}]})
    result = dramas.snapshot(p['id'])
    assert stages(result)['planning']['status'] == 'running'
    assert stages(result)['review']['status'] == 'returned'
    assert result['review_history'][0]['review']['issues'] == ['承接冲突']
    assert 'plan_history' not in result and 'plan_repair_basis' not in result
    assert 'never-expose' not in json.dumps(result)


def test_drama_graph_links_real_lifecycle_and_scopes_review_records(isolated):
    p = dramas.create(input_payload())
    store.update(p['id'], {'draft_plan': plan_for(p), 'plan_review': {'passed': False, 'issues': ['承接冲突']},
        'status': 'blocked', 'stage': 'plan_repair', 'plan_phase': 'blocked',
        'plan_history': [{'cycle': 1, 'attempt': 1, 'review': {'passed': False, 'issues': ['承接冲突']}}]})
    workflow = dramas.snapshot(p['id'])['workflow']
    graph, flow = workflow['graph'], workflow['flow']
    assert graph['project_id'] == flow['project_id'] == p['id']
    assert graph['revision'] == flow['revision']
    assert graph['active_node'] == 'review'
    assert [node['id'] for node in graph['nodes']] == [row['id'] for row in workflow['stages']]
    assert [group['node_ids'] for group in graph['presentation']['stages']] == [[row['id']] for row in workflow['stages']]
    assert any(edge['source'] == 'review' and edge['target'] == 'planning' and edge['loop'] for edge in graph['edges'])
    assert any(edge['source'] == 'acceptance' and edge['target'] == 'production' and edge['loop'] for edge in graph['edges'])
    assert all(isinstance(node['input'], str) and isinstance(node['output'], str) for node in graph['nodes'])
    assert next(node for node in graph['nodes'] if node['id'] == 'review')['status'] == 'blocked'
    assert flow['nodes'] == []  # A lifecycle projection does not fabricate executions.
    review = next(step for step in flow['steps'] if step['id'] == 'review')
    assert review['outputs'][0]['value']['issues'] == ['承接冲突']
    assert workflow['tasks'][0]['stage_id'] == 'review'


def test_pending_and_returning_graphs_do_not_claim_success_without_evidence(isolated):
    p = dramas.create(input_payload())
    workflow = p['workflow']
    assert all(node['status'] == 'pending' for node in workflow['graph']['nodes'])
    assert workflow['graph']['active_node'] == 'planning'
    store.update(p['id'], {'draft_plan': plan_for(p), 'plan_review': {'passed': False, 'issues': ['退回编剧']},
        'plan_phase': 'writing', 'plan_attempt': 2})
    workflow = dramas.snapshot(p['id'])['workflow']
    assert workflow['graph']['active_node'] == 'planning'
    assert next(node for node in workflow['graph']['nodes'] if node['id'] == 'planning')['status'] == 'running'
    assert next(node for node in workflow['graph']['nodes'] if node['id'] == 'review')['status'] == 'blocked'
    assert not workflow['flow']['nodes']


def test_long_graph_retains_unit_links_and_continuity_return_route(isolated):
    p = ready()
    eid = p['episodes'][0]['id']
    unit = dramas.record('scene_unit', 'E001-C01-U001', '场景制作', parent_id=eid,
        episode_id=eid, drama_id=p['id'], scene_id='C01', global_prefix='E001-C01-U001',
        status='unit_ready', stage='complete', progress=100)
    with store.connection() as db:
        dramas.insert(db, unit)
        store._enqueue(db, eid, 'drama_episode')
    store.update(eid, {'duration_seconds': 300, 'unit_ids': [unit['id']],
        'scene_plan': [{'scene_id': 'C01'}], 'scene_review': {'passed': True},
        'status': 'producing', 'stage': 'assembly'})
    workflow = dramas.episode_snapshot(p['id'], eid)['workflow']
    graph = workflow['graph']
    assert graph['active_node'] == 'assembly'
    assert [node['id'] for node in graph['nodes']] == ['scenes', 'units', 'assembly', 'acceptance']
    assert next(node for node in graph['nodes'] if node['id'] == 'assembly')['status'] == 'running'
    assert any(edge['source'] == 'assembly' and edge['target'] == 'units' and edge['loop'] for edge in graph['edges'])
    task = next(task for task in workflow['tasks'] if task.get('project_id') == unit['id'])
    assert task['stage_id'] == 'units'


def test_confirmation_node_is_human_only_after_review_and_never_invents_legacy_review(isolated):
    p = dramas.create(input_payload())
    store.update(p['id'], {'draft_plan': plan_for(p), 'plan_review': {'passed': True, 'issues': []}})
    workflow = dramas.snapshot(p['id'])['workflow']
    assert workflow['graph']['active_node'] == 'confirmation'
    assert next(node for node in workflow['graph']['nodes'] if node['id'] == 'confirmation')['status'] == 'human'
    p = ready()
    store.update(p['id'], {'plan_review': None})
    workflow = dramas.snapshot(p['id'])['workflow']
    assert next(node for node in workflow['graph']['nodes'] if node['id'] == 'review')['status'] == 'pending'
    assert workflow['graph']['active_node'] == 'production'
    assert all(task['stage_id'] == 'production' for task in workflow['tasks'] if task.get('project_id'))


def test_paused_planning_graph_keeps_saved_draft_and_paused_work_state(isolated):
    p = dramas.create(input_payload())
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='completed'")
    store.update(p['id'], {'draft_plan': plan_for(p), 'plan_phase': 'writing', 'status': 'paused'})
    workflow = dramas.snapshot(p['id'])['workflow']
    assert workflow['graph']['active_node'] == 'planning'
    assert next(node for node in workflow['graph']['nodes'] if node['id'] == 'planning')['status'] == 'paused'
    assert next(step for step in workflow['flow']['steps'] if step['id'] == 'planning')['outputs'][0]['value']['storyline']


def test_revised_shared_plan_does_not_reuse_older_confirmation(isolated):
    p = ready()
    assert p['plan_version'] == 1
    assert p['workflow']['graph']['active_node'] == 'production'
    changed = json.loads(json.dumps(p['draft_plan']))
    changed['world'] += '，补充新的车站区域'
    p = dramas.confirm(p['id'], {'expected_revision': p['revision'], 'request_id': 'edit-plan', 'plan': changed})
    assert p['plan_version'] == 1 and p['plan_phase'] == 'reviewing'
    assert p['workflow']['graph']['active_node'] == 'review'
    assert stages(p)['confirmation']['status'] == 'pending'
    assert stages(p)['confirmation']['output'] == {'plan_version': 0, 'previous_plan_version': 1}
    assert next(node for node in p['workflow']['graph']['nodes'] if node['id'] == 'review')['status'] == 'running'

    for phase, expected in [('writing', 'planning'), ('returned', 'planning'), ('exhausted', 'review')]:
        store.update(p['id'], {'plan_phase': phase, 'plan_review': {'passed': False, 'issues': ['修改要求']}})
        current = dramas.snapshot(p['id'])
        assert current['workflow']['graph']['active_node'] == expected
        assert stages(current)['confirmation']['status'] == 'pending'

    with store.connection() as db:
        db.execute("UPDATE jobs SET status='completed'")
    store.update(p['id'], {'plan_phase': 'approved', 'plan_review': {'passed': True, 'issues': []},
        'status': 'awaiting_plan', 'stage': 'plan_confirmation'})
    p = dramas.snapshot(p['id'])
    assert p['workflow']['graph']['active_node'] == 'confirmation'
    assert stages(p)['confirmation']['status'] == 'waiting'
    assert stages(p)['confirmation']['output']['previous_plan_version'] == 1
    assert next(node for node in p['workflow']['graph']['nodes'] if node['id'] == 'confirmation')['status'] == 'human'

    p = dramas.confirm(p['id'], {'expected_revision': p['revision'], 'request_id': 'confirm-revised-plan'})
    assert p['plan_version'] == 2 and p['status'] == 'ready' and p['plan_phase'] == 'approved'
    assert p['workflow']['graph']['active_node'] == 'production'
    assert stages(p)['confirmation']['status'] == 'succeeded'
    assert stages(p)['confirmation']['output'] == {'plan_version': 2}
    store.update(p['id'], {'status': 'producing'})
    assert dramas.snapshot(p['id'])['workflow']['graph']['active_node'] == 'production'


def test_collaboration_department_catalog_matches_production_and_does_not_invent_staff_runs(isolated):
    from factory.team_ledger import DEPARTMENTS
    from factory.team_blueprint import collaboration_departments
    project = dramas.create(input_payload())
    result = dramas.snapshot(project['id'])['workflow']
    catalog = {d['id']: d for d in result['graph']['departments']}
    assert catalog['story']['roles'] == ['researcher', 'creator', 'story_editor', 'screenwriter']
    assert catalog['review']['roles'] == ['story_critic', 'independent_critic', 'visual_critic']
    assert catalog['operations']['roles'] == ['orchestrator']
    assert all(catalog[d['id']]['roles'] == d['roles'] for d in DEPARTMENTS)
    assert result['tasks'] == []
    assert result['flow']['nodes'] == []
    # The presentation must not expose mutable role lists from the catalog.
    catalog['story']['roles'].clear()
    assert collaboration_departments()[1]['roles'] == DEPARTMENTS[1]['roles']


def test_handoff_reads_actual_receiver_input_and_keeps_product_content_only(isolated):
    p = dramas.create(input_payload())
    cache = isolated / 'production' / p['slug'] / 'planning/cache'
    cache.mkdir(parents=True)
    world = {'world': '车站与旧城的统一世界设定', 'storyline': '成年旧友寻找旧信的真相'}
    core.write_json(cache / ('drama-world-1-' + 'd'*24 + '.json'), {
        'signature': 'd'*64, 'generated_at': '2026-10-18T00:00:00Z', 'value': world})
    call = cache.parent / 'llm/calls/20261018T000100-screenwriter-handoff'
    call.mkdir(parents=True)
    core.write_json(call / 'receipt.json', {'role': 'screenwriter', 'status': 'starting', 'started_at': 1792281660})
    core.write_json(call / 'request-01.json', {'messages': [
        {'role': 'system', 'content': 'private-prompt-never-return'},
        {'role': 'user', 'content': json.dumps({'world': world, 'source_excerpt': 'private-source-never-return'})}]})
    workflow = dramas.snapshot(p['id'])['workflow']
    receiver = next(t for t in workflow['tasks'] if t['role'] == 'screenwriter')
    linked = receiver['received'][0]
    assert linked['source_role'] == 'story_editor'
    assert linked['source_task_id'].startswith('cache:drama-world-1-')
    assert linked['basis'] == 'recorded_input_content_match'
    assert linked['value'] == world
    assert 'private-' not in json.dumps(workflow)
    # A changed receiver input cannot inherit the earlier output's delivery.
    core.write_json(call / 'request-01.json', {'messages': [
        {'role': 'user', 'content': json.dumps({'world': {**world, 'world': '另一个世界版本'}})}]})
    assert 'received' not in next(t for t in dramas.snapshot(p['id'])['workflow']['tasks'] if t['role'] == 'screenwriter')


def test_content_handoff_rejects_ambiguous_or_future_outputs_and_short_shared_values():
    from factory.handoff_evidence import attach_received_content
    world = {'world': '车站与旧城', 'storyline': '成年旧友沿旧信寻找真相'}
    first = {'id': 'first', 'role': 'story_editor', 'finished_at': 10}
    later = {'id': 'later', 'role': 'story_editor', 'finished_at': 30}
    receiver = {'id': 'receiver', 'role': 'screenwriter', 'started_at': 20}
    attach_received_content([(first, world), (later, world)], [(receiver, {'world': world})], drama_workflow._summary)
    assert receiver['received'][0]['source_task_id'] == 'first'
    receiver.pop('received')
    first.pop('finished_at')
    later.pop('finished_at')
    attach_received_content([(first, world), (later, world)], [(receiver, {'world': world})], drama_workflow._summary)
    assert 'received' not in receiver
    attach_received_content([(first, {'passed': True, 'issues': []})], [(receiver, {'review_feedback': []})], drama_workflow._summary)
    assert 'received' not in receiver
