import copy
import json
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

import pytest
from factory import dramas, store, worker, config, workflows
from test_factory_workflow_v2 import runtime as graph_runtime


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    from studio import core
    monkeypatch.setattr(core,'ROOT',tmp_path)
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'DATABASE', tmp_path / 'studio.sqlite3')
    monkeypatch.setattr(workflows, 'DATA', tmp_path)
    monkeypatch.setattr(workflows, 'CHECKPOINTS', tmp_path / 'checkpoints.sqlite3')
    monkeypatch.setattr(config, 'load_config', lambda: {'private_fixture': 'never-return-this'})
    store.initialize()
    return tmp_path


def input_payload(**values):
    return {'title': '车站的约定', 'brief': '两个成年人在车站发现遗失的信件，逐集查明真相。',
        'episode_count': 3, 'duration_seconds': 60, 'format': 'motion_comic', 'request_id': 'create', **values}


def plan(count=3):
    return {'world': '当代虚构城市与车站', 'storyline': '从遗失信件发现真相，再完成和解。',
        'visual_rules': '统一水彩画风', 'characters': [{'character_id': 'lead', 'name': '林',
        'identity_constraints': '成年女性，短发', 'default_wardrobe': '蓝衣', 'voice_id': 'female'}],
        'scenes': [], 'props': [], 'uncertainties': [], 'episodes': [{'number': i,
        'title': f'第{i}封信', 'synopsis': f'角色在车站发现第{i}封信，并通过信件了解真相。',
        'opening_state': '抵达车站', 'ending_state': '发现线索', 'characters': ['lead'],
        'depends_on': []} for i in range(1, count+1)]}


def ready():
    p = dramas.create(input_payload())
    store.update(p['id'], {'draft_plan': plan(), 'plan_review': {'passed': True, 'issues': []}, 'status': 'awaiting_plan'})
    # No planning worker is executed by this metadata fixture.
    with store.connection() as db: db.execute("UPDATE jobs SET status='completed'")
    p = dramas.snapshot(p['id'])
    return dramas.confirm(p['id'], {'expected_revision': p['revision'], 'request_id': 'confirm'})


def test_create_confirm_generate_are_idempotent_and_private(isolated):
    p = ready()
    assert len(p['episodes']) == 3 and p['plan_version'] == 1
    assert dramas.create(input_payload())['id'] == p['id']
    payload = {'expected_revision': p['revision'], 'request_id': 'batch', 'episode_numbers': [1, 3]}
    result = dramas.generate(p['id'], payload)
    again = dramas.generate(p['id'], payload)
    assert len(again['batches']) == 1
    assert 'never-return-this' not in json.dumps(result)
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0] == 1
    with pytest.raises(ValueError, match='请求标识'):
        dramas.generate(p['id'], {**payload, 'episode_numbers': [2]})
    with pytest.raises(ValueError, match='更新'):
        dramas.generate(p['id'], {**payload, 'request_id': 'new'})


def test_worker_failure_releases_unrelated_episode_and_dependencies_wait(isolated, monkeypatch):
    p = ready()
    # Episode 3 depends on the unresolved production of 1; 2 does not.
    new_plan = plan()
    new_plan['episodes'][2]['depends_on'] = [1]
    store.update(p['id'], {'draft_plan': new_plan, 'plan_review': {'passed': True}})
    p = dramas.snapshot(p['id'])
    p = dramas.confirm(p['id'], {'expected_revision': p['revision'], 'request_id': 'confirm2'})
    p = dramas.generate(p['id'], {'expected_revision': p['revision'], 'request_id': 'batch', 'episode_numbers': [1,2,3]})
    from factory import drama_runtime
    def execute(pid):
        if store.get(pid, False)['episode_number'] == 1: raise RuntimeError('fixture inference failed')
        store.update(pid, {'status': 'awaiting_final', 'delivery': {'fixture': True}, 'progress': 95})
    monkeypatch.setattr(drama_runtime, 'execute_episode', execute)
    first = store.claim_job()
    worker.handle(first)
    second = store.claim_job()
    assert store.get(second['project_id'], False)['episode_number'] == 2
    worker.handle(second)
    assert store.claim_job() is None
    result = dramas.snapshot(p['id'])
    assert [e['status'] for e in result['episodes']] == ['blocked', 'awaiting_final', 'waiting_dependency']


def test_pause_cancel_and_foreign_episode_are_fenced(isolated):
    p = ready()
    p = dramas.generate(p['id'], {'expected_revision': p['revision'], 'request_id': 'batch', 'episode_numbers': [1,2]})
    p = dramas.control(p['id'], {'expected_revision': p['revision'], 'request_id': 'pause', 'action': 'pause', 'batch_id': p['batches'][0]['id']})
    assert [e['status'] for e in p['episodes']] == ['paused', 'paused', 'draft']
    assert store.claim_job() is None
    with pytest.raises(ValueError):
        dramas.control(p['id'], {'expected_revision': p['revision'], 'request_id': 'foreign', 'action': 'resume', 'episode_id': 'f'*12})
    p = dramas.control(p['id'], {'expected_revision': p['revision'], 'request_id': 'resume', 'action': 'resume', 'episode_id': p['episodes'][1]['id']})
    assert store.claim_job()['project_id'] == p['episodes'][1]['id']


def test_v3_graph_and_pinned_short_profile_keep_stable_identity(isolated):
    from factory.drama_runtime import setup_episode
    p = ready()
    episode = store.get(p['episodes'][0]['id'],False)
    setup_episode(episode)
    e = store.get(episode['id'], False)
    assert e['production_profile']['characters'][0]['character_id'] == 'lead'
    assert e['production_profile']['characters'][0]['voice_id'] == 'female'
    graph = workflows.build_graph(None, 3).get_graph().to_json()
    assert any(edge['source'] == '__start__' and edge['target'] == 'story_resolution' for edge in graph['edges'])


def test_real_http_create_get_and_origin_boundary(isolated):
    from factory.web import Handler
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        req = urllib.request.Request(base + '/api/factory/dramas', json.dumps(input_payload()).encode(),
            {'Origin': base, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req) as r: p = json.load(r)
        with urllib.request.urlopen(base+'/api/factory/dramas/'+p['id']) as r: assert json.load(r)['id'] == p['id']
        req = urllib.request.Request(base+'/api/factory/dramas', b'{}', {'Origin': 'https://evil.example', 'Content-Type': 'application/json'})
        with pytest.raises(urllib.error.HTTPError) as error: urllib.request.urlopen(req)
        assert error.value.code == 403
    finally:
        server.shutdown(); server.server_close(); thread.join()


@pytest.mark.parametrize('kind,expected',[('episode','awaiting_final'),('scene_unit','unit_ready')])
def test_actual_v3_checkpoint_starts_at_confirmed_outline_and_never_auto_approves(graph_runtime,monkeypatch,kind,expected):
    from factory import supervision
    p,calls=graph_runtime
    store.update(p['id'],{'workflow_version':3,'record_kind':kind,'team_mode':'delegated',
        'proposals':[{'id':'confirmed','title':'已确认大纲','eligible':True}], 'selected_story':'confirmed'})
    def auto_select(pid,gate):
        if gate['type']=='final': return None
        assert gate['type']=='style'
        store.decide(pid,{'type':'style','decision_id':gate['id'],'choice':'style-1'},actor='executive_producer')
        return store.answer_for(pid,gate['id'])
    monkeypatch.setattr(supervision,'supervise_gate',auto_select)
    workflows.advance(p['id'])
    result=store.get(p['id'],False)
    assert result['status']==expected and not result.get('accepted_by')
    assert 'research' not in calls and 'proposals' not in calls
    assert calls.count('production')==1
    if kind=='episode':
        assert result['decisions'][-1]['type']=='final' and result['decisions'][-1]['status']=='pending'
        workflows.advance(p['id'])
        assert calls.count('production')==1
    else:
        assert not any(g['type']=='final' for g in result['decisions'])


def test_plan_pause_without_episodes_and_chinese_source_headers(isolated):
    p=dramas.create(input_payload())
    p=dramas.control(p['id'],{'expected_revision':p['revision'],'request_id':'pause-plan','action':'pause'})
    assert p['status']=='paused' and store.claim_job() is None
    p=dramas.control(p['id'],{'expected_revision':p['revision'],'request_id':'resume-plan','action':'resume'})
    assert store.claim_job()['kind']=='drama_plan'
    script='第一集\n找到第一封信。\n第二集\n找到第二封信。\n第三集\n完成和解。'
    assert dramas.source_excerpt(script,2,3).strip()=='第二集\n找到第二封信。'


def test_dependency_receipt_is_pinned_and_later_changes_are_reported(isolated):
    from factory.drama_runtime import capture_dependencies
    p=ready();one,two=p['episodes'][:2]
    store.update(one['id'],{'status':'awaiting_final','delivery':{'source_signature':'first'},'script':{'ending':'拿到第一封信'}})
    e=store.get(two['id'],False);e['outline']['depends_on']=[1]
    store.update(e['id'],{'outline':e['outline']})
    assert capture_dependencies(store.get(e['id'],False))
    pinned=store.get(e['id'],False)['dependency_context']
    assert pinned[0]['delivery_signature']=='first' and pinned[0]['script_ending']=='拿到第一封信'
    store.update(one['id'],{'delivery':{'source_signature':'second'}})
    assert capture_dependencies(store.get(e['id'],False))
    assert store.get(e['id'],False)['dependency_context']==pinned
    assert dramas.snapshot(p['id'])['dependency_impacts'][0]['number']==2


def test_acceptance_rejects_stale_episode_revision(isolated):
    p=ready();e=p['episodes'][0]
    e=store.update(e['id'],{'duration_seconds':300,'status':'awaiting_final','delivery':{'source_signature':'fixture'}})
    p=dramas.snapshot(p['id'])
    body={'expected_revision':p['revision'],'expected_episode_revision':e['revision']-1,
        'request_id':'approve','action':'approve','episode_id':e['id']}
    with pytest.raises(ValueError,match='剧集已更新'):dramas.control(p['id'],body)
    approved=dramas.control(p['id'],{**body,'expected_episode_revision':e['revision']})
    assert approved['episodes'][0]['accepted_by']=='producer'


def test_planning_reviews_bounded_episode_groups_and_preserves_source(isolated,monkeypatch):
    from factory import drama_planning
    monkeypatch.setattr(__import__('studio.core',fromlist=['ROOT']),'ROOT',isolated)
    source='第一集\n成年角色在车站找到旧信。\n第二集\n沿旧信找到线索。'
    p=dramas.create(input_payload(episode_count=6,duration_seconds=300,source_text=source))
    expected=plan(6);review_sizes=[]
    def call(project,cfg,role,stage,instructions,payload,*args):
        if stage.startswith('drama-source-'):return {'summary':'按原文整理的车站与信件事件。'}
        if stage.startswith('drama-world-'):return {k:v for k,v in expected.items() if k!='episodes'}
        if stage.startswith('drama-outline-'):return expected['episodes'][payload['number']-1]
        if stage.startswith('drama-plan-review-'):
            review_sizes.append(len(payload['episodes']))
            return {'passed':True,'issues':[]}
        raise AssertionError(stage)
    monkeypatch.setattr(drama_planning,'call',call)
    drama_planning.run(p['id'],{})
    p=store.get(p['id'],False)
    assert p['status']=='awaiting_plan' and p['source_text']==source
    assert review_sizes==[3,3] and len(p['draft_plan']['episodes'])==6


def test_sixteen_characters_and_changed_shared_plan_do_not_overwrite_started_episode(isolated):
    from jsonschema import validate,ValidationError
    from factory import long_episodes,drama_planning
    p=ready()
    p=dramas.generate(p['id'],{'expected_revision':p['revision'],'request_id':'first-batch','episode_numbers':[1]})
    original=store.get(p['episodes'][0]['id'],False)
    edited=plan()
    edited['characters']=[{**edited['characters'][0],'character_id':f'person{i}','name':f'角色{i}'} for i in range(16)]
    for outline in edited['episodes']:outline['characters']=['person0']
    edited['relationships']=['person0与person1是成年兄妹，分别寻找旧信的线索。']
    root=store.update(p['id'],{'duration_seconds':300,'draft_plan':edited,'plan_review':{'passed':True}})
    p=dramas.confirm(p['id'],{'expected_revision':root['revision'],'request_id':'second-plan'})
    assert len(p['plans'][-1]['document']['characters'])==16
    assert store.get(original['id'],False)['plan']==original['plan']
    assert store.get(original['id'],False)['plan_version']==1
    assert any(e['number']==1 and e['pinned_version']==1 for e in p['affected_episodes'])
    p=dramas.generate(p['id'],{'expected_revision':p['revision'],'request_id':'second-batch','episode_numbers':[2]})
    assert store.get(p['episodes'][1]['id'],False)['plan_version']==2
    scene={'scene_id':'C01','title':'车站','synopsis':'五位成年人在车站发现旧信。','seconds':90,
        'characters':[f'person{i}' for i in range(5)],'location_id':'','prop_ids':[],
        'opening_state':'进入','ending_state':'找到信件'}
    with pytest.raises(ValidationError):validate({'scenes':[scene]},long_episodes.scene_schema([f'person{i}' for i in range(16)]))
    edited['characters'].append({**edited['characters'][0],'character_id':'person16'})
    with pytest.raises(ValidationError):validate(edited,drama_planning.plan_schema(3))


def test_confirmation_with_pending_review_requeues_without_crashing(isolated):
    p = dramas.create(input_payload())
    store.update(p['id'], {'draft_plan': plan(), 'plan_review': None, 'status': 'blocked'})
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='failed'")
    p = dramas.snapshot(p['id'])
    result = dramas.confirm(p['id'], {'expected_revision': p['revision'], 'request_id': 'pending-review'})
    assert result['status'] == 'queued'
    assert result['stage'] == 'plan_review'
    assert result['plan_version'] == 0
    assert result['workflow']['active_job']
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE kind='drama_plan' AND status='queued'").fetchone()[0] == 1
