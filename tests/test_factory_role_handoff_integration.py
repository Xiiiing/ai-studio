"""Controller pipeline, restart, real HTTP and existing intervention boundaries.

Providers are deterministic local fixtures; no external model work is started.
"""
import copy
import json

import pytest
from factory import creative, schemas, store, role_handoffs as handoffs
from factory.execution_context import current_execution
from studio import core
from test_factory_shot_workbench import project
from test_factory_team_ledger import http_client, database_dump, task
from tests import test_factory_creative as creative_fixtures
from tests.test_factory_storyboard import direction_response


def test_missing_action_scoped_repair_re_review_and_restart_preserve_other_shots():
    case = creative_fixtures.CreativeContractTests('test_approved_compact_target_reused_only_with_same_sources_policy_and_body')
    case.setUp()
    try:
        case.config['creative'] = {'separate_storyboard': True, 'max_review_rounds': 2}
        writers, directors, critics = [], [], []
        def reply(**request):
            if request['role'] == 'storyboard_director':
                directors.append(copy.deepcopy(request))
                return direction_response(request['payload'])
            result = case.reply(**request)
            if request['role'] == 'screenwriter':
                writers.append(copy.deepcopy(request))
                if len(writers) == 1:
                    result['shots'][2]['action'] = ' ' * 20
                else:
                    # Even a provider ignoring the requested scope cannot replace
                    # unrelated shots or approved identity fields.
                    result['title'] = '不允许采用的新剧名'
                    result['cast']['female'] = 'A completely different adult identity with a different face'
                    result['shots'][0]['action'] = '不允许采用的无关剧情，改变了镜头中的故事情节。'
                    result['shots'][2]['action'] = '成年女主把钥匙放在桌上，归还完成。'
            elif request['role'] == 'independent_critic':
                critics.append(copy.deepcopy(request))
            return result
        case.model.side_effect = reply
        p = case.approved_project()
        result = creative.create_script_and_styles(p, case.config, None)
        assert result['script_review']['passed']
        assert len(writers) == len(critics) == 2 and len(directors) == 1
        assert writers[1]['payload']['supplement_request']['scope'] == ['S03']
        assert writers[1]['payload']['only_revise_shot_ids'] == ['S03']
        assert len(writers[1]['payload']['locked_shot_ids']) == 14
        assert directors[0]['payload']['editable_shot_ids'] == ['S03']
        initial = critics[0]['payload']['episode']
        assert result['episode']['title'] == initial['title']
        assert result['episode']['cast'] == initial['cast']
        assert all(a == b for a, b in zip(initial['shots'], result['episode']['shots']) if a['id'] != 'S03')
        assert critics[0]['payload']['technical_findings'][0]['code'] == 'missing_shot_action'
        assert critics[1]['payload']['episode'] == result['episode']
        folder = schemas.planning_dir(p)
        snapshot = handoffs.snapshot(folder, p)
        returned = next(r for r in snapshot['handoffs'] if r['state'] == 'returned')
        assert returned['source'] == 'screenwriter' and returned['target'] == 'storyboard_director'
        assert snapshot['requests'][0]['state'] == 'supplemented'
        old = handoffs.detail(folder, p, returned['id'])
        assert not old['value']['shots'][2]['action'].strip()
        assert any(r['contract'] == 'visual_design' and r['state'] == 'accepted' for r in snapshot['handoffs'])
        before_calls = len(case.calls)
        replay = creative.create_script_and_styles(p, case.config, None)
        assert replay['episode'] == result['episode']
        assert len(case.calls) == before_calls
        assert handoffs.snapshot(folder, p)['total'] == snapshot['total']
    finally:
        case.tearDown()
        case.doCleanups()


def test_semantic_return_replays_partial_director_result_after_restart():
    case = creative_fixtures.CreativeContractTests('test_approved_compact_target_reused_only_with_same_sources_policy_and_body')
    case.setUp()
    try:
        case.config['creative'] = {'separate_storyboard': True, 'max_review_rounds': 2}
        def reply(**request):
            if request['role'] == 'storyboard_director':
                case.calls.append(copy.deepcopy(request))
                result = direction_response(request['payload'])
                if len([c for c in case.calls if c['role'] == 'storyboard_director']) == 1:
                    result['blocked_shots']['S03'] = {'reason': '原动作缺少完成状态', 'suggestion': '请补齐动作对应的可见结果'}
                return result
            return case.reply(**request)
        case.model.side_effect = reply
        p = case.approved_project()
        first = creative.create_script_and_styles(p, case.config, None)
        calls = len(case.calls)
        second = creative.create_script_and_styles(p, case.config, None)
        assert first['episode'] == second['episode'] and len(case.calls) == calls
        assert first['script_review']['passed'] and second['script_review']['passed']
    finally:
        case.tearDown()
        case.doCleanups()


def test_readonly_http_history_and_full_material_are_owned_and_bounded(project, http_client, tmp_path, monkeypatch):
    from factory import web
    monkeypatch.setattr(web, 'ROOT', tmp_path)
    path = f"/api/factory/projects/{project['id']}/handoffs"
    folder = tmp_path / 'production' / project['slug'] / 'planning'
    before = database_dump()
    response = http_client('GET', path)
    assert response[0] == 200, response
    assert response[1]['history_state'] == 'not_recorded'
    assert not folder.exists()
    value = {'title': '交接HTTP验收', 'shots': [{'id': 'S01', 'action': '成年角色放下钥匙'}]}
    record = handoffs.submit(folder, project, source='screenwriter', target='storyboard_director',
        source_stage='screenplay', target_stage='storyboard', contract='episode_direction', value=value, scope=['S01'])
    handoffs.receive(folder, project, record['id'], role='storyboard_director', stage='storyboard', value=value)
    status, page = http_client('GET', path + '?offset=0&limit=1')
    assert status == 200 and page['total'] == 1 and 'value' not in page['handoffs'][0]
    assert http_client('GET', path + '/' + record['id'])[1]['value'] == value
    assert http_client('GET', path + '?offset=1&limit=1')[1]['handoffs'] == []
    for query in ('?limit=61', '?limit=-1', '?limit=0', '?offset=0&offset=1', '?role=arbitrary'):
        assert http_client('GET', path + query)[0] == 400
    assert http_client('POST', path, value)[0] == 405
    assert http_client('GET', path + '/' + '0' * 32)[0] == 404
    other = store.create({'brief': '另一个隔离项目，不能读取其他项目的冻结资料'})
    assert http_client('GET', f"/api/factory/projects/{other['id']}/handoffs/{record['id']}")[0] == 404
    assert http_client('GET', path, headers={'Origin': 'https://untrusted.invalid'})[0] == 403
    # The only business DB change above was our explicit creation of another project.
    after = database_dump()
    http_client('GET', path)
    assert database_dump() == after and before != after


def test_live_version_pause_and_existing_task_lock_fences(project, tmp_path):
    from factory import team_control, team_ledger
    from factory.scheduler import ProductionPaused
    folder = tmp_path / 'journal'
    p = store.update(project['id'], {'status': 'creating'})
    options = dict(source='screenwriter', target='independent_critic', source_stage='screenplay',
                   target_stage='script_review', contract='episode_review', value={'shots': [{'id': 'S01', 'action': '放下钥匙'}]})
    token = current_execution.set({'project_id': p['id'], 'task_id': 'script'})
    try:
        row = handoffs.submit(folder, p, **options)
        store.update(p['id'], {'script_generation': p.get('script_generation', 1) + 1})
        with pytest.raises(ValueError, match='版本'):
            handoffs.receive(folder, p, row['id'], role='independent_critic', stage='script_review', value=options['value'])
        p = store.get(p['id'], False)
        store.update(p['id'], {'pause_requested': True})
        with pytest.raises(ProductionPaused): handoffs.submit(folder, p, **options)
        p = store.update(p['id'], {'pause_requested': False})
    finally:
        current_execution.reset(token)
    # A real existing control-table pause must also block a new receiver call.
    ident = 'image:S01'
    operation = {'action': 'pause', 'request_id': 'handoff-task-pause',
        'expected_control_revision': team_ledger.team_snapshot(p['id'])['control_revision'],
        'expected_generation': task(p['id'], ident)['generation']}
    team_control.task_action(p['id'], ident, operation)
    token = current_execution.set({'project_id': p['id'], 'task_id': ident})
    try:
        with pytest.raises(team_control.TeamInterventionPending): handoffs.submit(folder, p, **options)
    finally:
        current_execution.reset(token)


def test_planning_call_binds_exact_world_and_feedback_and_rejects_missing_material(project, tmp_path, monkeypatch):
    from factory import drama_planning
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    p = store.update(project['id'], {'status': 'creating'})
    world = {'world': '虚构世界', 'characters': [{'name': '成年女主'}], 'visual_rules': '水墨画风'}
    captures = []
    def reply(*args, **kwargs):
        captures.append(handoffs.current())
        return {'number': 1}
    monkeypatch.setattr(drama_planning, 'cached_json_call', reply)
    payload = {'world': world, 'number': 1, 'review_feedback': ['上一稿缺少人物动机']}
    assert drama_planning.call(p, {}, 'screenwriter', 'drama-outline-1-2-1', '测试', payload, {}) == {'number': 1}
    assert len(captures[0]) == 2
    folder = tmp_path/'production'/p['slug']/'planning'
    snap = handoffs.snapshot(folder, p)
    assert {r['source'] for r in snap['handoffs']} == {'story_editor', 'story_critic'}
    transfer = next(r for r in snap['handoffs'] if r['contract'] == 'world_outline')
    assert handoffs.detail(folder, p, transfer['id'])['value'] == world
    from factory.workflows import ReviewBlocked
    with pytest.raises(ReviewBlocked):
        drama_planning.call(p, {}, 'screenwriter', 'drama-outline-1-2-2', '测试', {'world': {}}, {})
    assert len(captures) == 1


def test_existing_locked_result_blocks_new_handoff_without_changing_material(project, tmp_path):
    from factory import team_control, team_ledger
    from test_factory_shot_workbench import fixture_candidate
    from test_factory_team_control import action
    receipt = fixture_candidate(project, 'take_001')
    token = team_ledger.begin_unit(project['id'], 'image', 'S01', input_version='locked-handoff')
    team_ledger.finish_unit(token, artifact=receipt)
    team_control.task_action(project['id'], 'image:S01', action(project, 'image:S01', 'lock'))
    p = store.update(project['id'], {'status': 'creating'})
    before = database_dump()
    material_before = (core.ROOT / receipt['path']).read_bytes()
    execution = current_execution.set({'project_id': p['id'], 'task_id': 'image:S01'})
    try:
        with pytest.raises(team_control.TeamInterventionPending, match='锁定'):
            handoffs.submit(tmp_path/'locked-journal', p, source='visual_designer', target='image_generator',
                source_stage='visual_design', target_stage='image', contract='review_feedback',
                value={'issues': ['模型不能越过已锁定成果']})
    finally:
        current_execution.reset(execution)
    assert database_dump() == before
    assert (core.ROOT / receipt['path']).read_bytes() == material_before
    assert not (tmp_path/'locked-journal').exists()


def test_transport_receipt_links_handoff_and_cache_without_duplicate_call(tmp_path, monkeypatch):
    from factory import context_manifest as cm
    from tests.test_factory_context_manifest import project as context_project
    from tests.test_factory_role_routing import routing_config, mock_transport, SCHEMA
    calls, _ = mock_transport(monkeypatch)
    monkeypatch.setattr(schemas, 'ROOT', tmp_path)
    p = context_project()
    folder = schemas.planning_dir(p)
    material = {'issues': ['请保留当前版本的结尾']}
    row = handoffs.submit(folder, p, source='independent_critic', target='screenwriter',
        source_stage='script_review', target_stage='screenplay', contract='review_feedback', value=material)
    handoffs.receive(folder, p, row['id'], role='screenwriter', stage='screenplay', value=material)
    with handoffs.bind(folder, row):
        for _ in range(2):
            schemas.cached_json_call(p, routing_config(), 'screenwriter', 'handoff-transport', 'rules', material, SCHEMA)
    assert len(calls) == 1
    events = handoffs.detail(folder, p, row['id'])['events']
    actual = next(e for e in events if e['action'] == 'call_recorded')
    cached = next(e for e in events if e['action'] == 'cached_result_reused')
    assert actual['status'] == 'succeeded' and actual['delivery'] == 'response_received'
    assert cached['origin_call_id'] == actual['call_id']
    receipt = json.loads(next((folder/'llm/calls').glob('*/receipt.json')).read_text())
    assert receipt['context_manifest']['controlled_handoff_ids'] == [row['id']]
    assert cm.public(receipt['context_manifest'])['controlled_handoff_ids'] == [row['id']]
    # Auxiliary evidence failure after obtaining a response must not repeat a call.
    monkeypatch.setattr(handoffs, 'observe_call', lambda *a, **kw: (_ for _ in ()).throw(OSError('fixture disk unavailable')))
    with handoffs.bind(folder, row):
        assert schemas.cached_json_call(p, routing_config(), 'screenwriter', 'another-stage', 'rules', material, SCHEMA) == {'ok': True}
    assert len(calls) == 2
    receipts = [json.loads(f.read_text()) for f in (folder/'llm/calls').glob('*/receipt.json')]
    assert any(r.get('controlled_handoff_observation') == 'unavailable' for r in receipts)
