"""Protocol/control tests. Synthetic replies do not establish creative quality."""
import copy
import json
import uuid
from pathlib import Path

import pytest
from factory import agent_loop as a, agent_media, schemas, store, llm
from factory.agent_policy import DEFAULTS
from factory.creative_evidence_io import digest,read_json
from factory.scheduler import ProductionNeedsDecision,ProductionPaused
from test_factory_team_ledger import project,http_client
from tests.test_factory_role_routing import routing_config,mock_transport

SCHEMA={'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}
VALUE={'ok':True}


def config_body(p,**settings):
    return {'request_id':uuid.uuid4().hex,'expected_revision':p['revision'],'enabled':True,'confirmed':True,
            'settings':{**DEFAULTS,'max_output_tokens':512,'total_output_tokens':4096,**settings}}


@pytest.fixture
def actor(project,tmp_path,monkeypatch):
    import studio.core as core
    monkeypatch.setattr(a,'ROOT',tmp_path);monkeypatch.setattr(agent_media,'ROOT',tmp_path)
    monkeypatch.setattr(schemas,'ROOT',tmp_path);monkeypatch.setattr(core,'ROOT',tmp_path)
    with store.connection() as db:db.execute("UPDATE jobs SET status='cancelled' WHERE project_id=?",(project['id'],))
    p=store.update(project['id'],{'status':'paused','pause_requested':True,'brief':'保留白杯原持物手，不恢复已删除的雨伞','synthetic_fixture':'agent_protocol_test'})
    a.configure(p['id'],config_body(p))
    p=store.update(p['id'],{'status':'scripting','pause_requested':False})
    return p,routing_config()


def replies(monkeypatch,actions,*,native=True,after=None):
    remaining=iter(actions)
    def respond(request):
        action=next(remaining)
        if after:after(action)
        message=({'content':None,'reasoning_content':'PRIVATE_PROVIDER_REASONING_NOT_PUBLIC',
            'tool_calls':[{'id':'call_'+uuid.uuid4().hex,'type':'function','function':{'name':action['tool'],'arguments':json.dumps(action['arguments'],ensure_ascii=False)}}]} if native else {'content':json.dumps(action,ensure_ascii=False)})
        return {'model':request['model'],'usage':{'completion_tokens':20},'choices':[{'finish_reason':'tool_calls' if native else 'stop','message':message}]}
    return mock_transport(monkeypatch,respond)[0]


def steps():
    return [{'tool':'plan','arguments':{'steps':['读取用户要求与批准材料','形成当前岗位候选并检查后提交']}},
            {'tool':'read_material','arguments':{'name':'project:brief'}},
            {'tool':'draft_artifact','arguments':{'artifact':VALUE}},
            {'tool':'check_artifact','arguments':{}},
            {'tool':'submit_artifact','arguments':{'sha256':digest(VALUE)}}]


def call(actor):
    p,cfg=actor
    return schemas.cached_json_call(p,cfg,'screenwriter','agent-contract','遵守批准事实，只完成当前岗位合约。',{},SCHEMA,max_tokens=512)


@pytest.mark.parametrize('native',[True,False])
def test_real_cached_role_boundary_runs_bounded_tools_and_reuses_completed_result(actor,monkeypatch,native):
    p,cfg=actor
    if not native:
        p=store.update(p['id'],{'agent_loop':{**p['agent_loop'],'settings':{**p['agent_loop']['settings'],'protocol':'structured'}}});actor=(p,cfg)
    calls=replies(monkeypatch,steps(),native=native)
    assert call(actor)==VALUE and len(calls)==5
    assert call(actor)==VALUE and len(calls)==5
    result=a.snapshot(p);run=result['runs'][0]
    assert run['state']=='completed' and run['quality_approval'] is False
    assert run['spent_output_tokens']==100
    assert 'PRIVATE_PROVIDER_REASONING_NOT_PUBLIC' not in json.dumps(result)
    assert run['evidence_type']=='synthetic_fixture'
    assert [e['tool'] for e in run['events'] if e['event']=='tool_end']==[s['tool'] for s in steps()]
    assert a.detail(p,run['id'])['candidate']==VALUE
    if native:
        assert calls[1]['request']['messages'][2]['reasoning_content']=='PRIVATE_PROVIDER_REASONING_NOT_PUBLIC'
        assert 'response_format' not in calls[0]['request']


def test_unchecked_or_wrong_hash_cannot_submit_and_rejections_are_visible(actor,monkeypatch):
    p,_=actor;sequence=steps()
    sequence.insert(3,{'tool':'submit_artifact','arguments':{'sha256':digest(VALUE)}})
    sequence.insert(5,{'tool':'submit_artifact','arguments':{'sha256':'a'*64}})
    calls=replies(monkeypatch,sequence)
    assert call(actor)==VALUE and len(calls)==7
    events=a.snapshot(p)['runs'][0]['events']
    assert sum(e.get('status')=='rejected' for e in events)==2


def test_pause_after_model_response_resumes_without_reposting(actor,monkeypatch):
    p,cfg=actor;paused=[]
    def pause(action):
        if not paused:
            paused.append(True);store.update(p['id'],{'pause_requested':True,'status':'paused'})
    calls=replies(monkeypatch,steps(),after=pause)
    with pytest.raises(ProductionPaused):call(actor)
    assert len(calls)==1
    resumed=store.update(p['id'],{'pause_requested':False,'status':'scripting'})
    # The first response is already durable. The mocked iterator advances only
    # four more times while the exact saved plan is recovered.
    assert call((resumed,cfg))==VALUE and len(calls)==5
    assert a.snapshot(resumed)['runs'][0]['state']=='completed'


def test_changed_input_fences_late_result_and_retains_evidence(actor,monkeypatch):
    p,_=actor
    calls=replies(monkeypatch,steps(),after=lambda action:store.update(p['id'],{'script_generation':p['script_generation']+1}))
    with pytest.raises(ProductionNeedsDecision) as exc:call(actor)
    assert exc.value.reason=='agent_stale_input' and len(calls)==1
    assert a.snapshot(p)['runs'][0]['state']=='superseded'
    assert list(a.root(p).glob('*/turn-001/calls/*/response-01.json'))


def test_unknown_model_submission_is_not_reposted_or_bypassed_by_config_or_new_task(actor,monkeypatch):
    p,cfg=actor
    calls,_=mock_transport(monkeypatch,lambda req:(_ for _ in ()).throw(TimeoutError('unknown post')))
    with pytest.raises(ProductionNeedsDecision) as exc:call(actor)
    assert exc.value.reason=='agent_unknown'
    with pytest.raises(ProductionNeedsDecision):call(actor)
    with pytest.raises(ProductionNeedsDecision):schemas.cached_json_call(p,cfg,'screenwriter','other-task','规则',{},SCHEMA,max_tokens=512)
    assert len(calls)==1
    idle=store.update(p['id'],{'status':'paused','pause_requested':True})
    with pytest.raises(ValueError,match='未知'):a.configure(p['id'],config_body(idle))
    unknown=next(r for r in a.snapshot(idle)['runs'] if r['state']=='unknown')
    ack={'request_id':uuid.uuid4().hex,'expected_revision':idle['revision'],'run_id':unknown['id'],'confirmed':True}
    first=a.settle(idle['id'],ack);assert a.settle(idle['id'],ack)==first
    assert a.detail(idle,unknown['id'])['state']=='closed'


def test_repeated_reads_stop_for_no_progress(actor,monkeypatch):
    p,_=actor;read={'tool':'read_material','arguments':{'name':'project:brief'}}
    replies(monkeypatch,[read]*5)
    with pytest.raises(ProductionNeedsDecision) as exc:call(actor)
    assert exc.value.reason=='agent_exhausted'
    assert '进展' in a.snapshot(p)['runs'][0]['reason']


def test_unauthorized_tool_has_no_effect_and_can_be_corrected(actor,monkeypatch):
    p,_=actor
    replies(monkeypatch,[{'tool':'run_shell','arguments':{'cmd':'delete everything'}},*steps()])
    assert call(actor)==VALUE
    assert any(e.get('status')=='rejected' and e.get('tool')=='run_shell' for e in a.snapshot(p)['runs'][0]['events'])


def test_output_allowance_stops_before_next_model_call(actor,monkeypatch):
    p,cfg=actor
    p=store.update(p['id'],{'agent_loop':{**p['agent_loop'],'settings':{**p['agent_loop']['settings'],'total_output_tokens':512}}})
    calls,_=mock_transport(monkeypatch,lambda req:{'model':req['model'],'usage':{'completion_tokens':512},'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[{'id':'one','function':{'name':'plan','arguments':json.dumps({'steps':['完成任务']})}}]}}]})
    with pytest.raises(ProductionNeedsDecision) as exc:call((p,cfg))
    assert exc.value.reason=='agent_exhausted' and len(calls)==1


def test_unpriced_planner_is_stopped_before_request(actor,monkeypatch):
    p,cfg=actor
    cfg['llm_profiles']['qwen'].update(provider='openai_compatible',base_url='https://model-vendor.example/v1',enabled=True,network_mode='remote',allowed_hosts=['model-vendor.example'])
    monkeypatch.setattr(llm,'_request',lambda *a,**k:pytest.fail('No unpriced call'))
    with pytest.raises(ProductionNeedsDecision) as exc:call((p,cfg))
    assert exc.value.reason=='agent_blocked'


def test_cache_reuse_has_verified_controller_origin_without_fake_model_call(actor,monkeypatch):
    p,_=actor;calls=replies(monkeypatch,steps())
    call(actor);call(actor)
    saved=read_json(next((a.root(p).parent/'context-reuse').glob('*.json')))
    assert saved['origin_state']=='verified_agent_artifact'
    assert saved['model_call_started'] is False and saved['origin'] is None
    assert saved['agent_origin']['run_id']==a.snapshot(p)['runs'][0]['id']
    assert len(calls)==5


@pytest.mark.parametrize('tool',['plan','draft_artifact','check_artifact','submit_artifact'])
def test_pause_after_tool_result_is_recovered_without_duplicate_model_or_action_charge(actor,monkeypatch,tool):
    p,cfg=actor;calls=replies(monkeypatch,steps());original=a.Loop.dispatch;paused=[]
    def dispatch(loop,action):
        result=original(loop,action)
        if action['tool']==tool and not paused:
            paused.append(True);store.update(p['id'],{'status':'paused','pause_requested':True})
        return result
    monkeypatch.setattr(a.Loop,'dispatch',dispatch)
    with pytest.raises(ProductionPaused):call(actor)
    resumed=store.update(p['id'],{'status':'scripting','pause_requested':False})
    assert call((resumed,cfg))==VALUE and len(calls)==5
    row=a._rows(resumed)[0]
    assert len(row['started_actions'])==5 and row['turns']==5


def test_stale_worker_lease_cannot_publish_saved_model_response(actor,monkeypatch):
    from factory.execution_context import current_job
    from factory import remote_tasks
    p,_=actor;valid=[True]
    monkeypatch.setattr(remote_tasks,'owns_job',lambda job:valid[0])
    calls=replies(monkeypatch,steps(),after=lambda _:valid.__setitem__(0,False))
    token=current_job.set({'id':9,'lease':1})
    try:
        with pytest.raises(ProductionNeedsDecision) as exc:call(actor)
        assert exc.value.reason=='agent_stale_lease' and len(calls)==1
        assert not list(a.root(p).glob('*/result.json'))
    finally:current_job.reset(token)


def test_invalid_tool_parameters_are_feedback_not_unhandled_worker_failure(actor,monkeypatch):
    p,_=actor
    replies(monkeypatch,[{'tool':'read_material','arguments':{'name':'/etc/passwd'}},
                        {'tool':'plan','arguments':{'steps':['x'],'grant_permission':True}},*steps()])
    assert call(actor)==VALUE
    assert sum(e.get('status')=='rejected' for e in a.snapshot(p)['runs'][0]['events'])==2


def test_context_overflow_preserves_required_facts_and_stops_before_post(actor,monkeypatch):
    p,_=actor;calls,_=mock_transport(monkeypatch)
    monkeypatch.setattr(llm,'context_budget',lambda *args:{'fits':False})
    with pytest.raises(ProductionNeedsDecision) as exc:call(actor)
    assert '必需材料' in str(exc.value) and calls==[]


def test_http_read_config_cas_idempotency_and_no_work_scheduled(actor,http_client):
    p,_=actor;idle=store.update(p['id'],{'status':'paused','pause_requested':True})
    path=f"/api/factory/projects/{p['id']}/agent-loop"
    from test_factory_team_ledger import database_dump
    before=database_dump();assert http_client('GET',path)[0]==200;assert database_dump()==before
    body=config_body(idle,max_turns=8)
    status,result=http_client('POST',path,body)
    assert status==200
    assert http_client('POST',path,body)==(200,result)
    assert http_client('POST',path,{**body,'settings':{}})[0]==409
    assert http_client('POST',path,config_body(idle))[0] in (400,409)
    assert http_client('GET',path+'?unexpected=1')[0]==400
    assert http_client('GET',path,headers={'Origin':'https://cross-site.example'})[0]==403
    with store.connection() as db:
        assert not db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running')",(p['id'],)).fetchone()


@pytest.mark.parametrize('field',['max_turns','max_actions','max_output_tokens','total_output_tokens','max_wall_seconds'])
def test_invalid_caps_and_active_project_do_not_change_snapshot(actor,field):
    p,_=actor
    with pytest.raises(ValueError):a.configure(p['id'],config_body(p,**{field:0}))
    with pytest.raises(ValueError,match='暂停'):a.configure(p['id'],config_body(p))
    assert store.get(p['id'],False)['agent_loop']==p['agent_loop']


@pytest.mark.parametrize('case',json.loads((Path(__file__).resolve().parents[1]/'evaluation/creative/corpus-v1.json').read_text())['cases'],ids=lambda c:c['id'])
def test_frozen_corpus_requirements_survive_actual_loop_wire_and_material_paging(actor,monkeypatch,case):
    p,cfg=actor
    p=store.update(p['id'],{**{k:v for k,v in case['project'].items() if k not in {'id','slug'}},'duration_seconds':case['project']['production_profile']['duration_seconds']})
    calls=replies(monkeypatch,steps())
    output=schemas.cached_json_call(p,cfg,'screenwriter','frozen-corpus-protocol','保留冻结输入与删除要求',case['inputs']['screenwriter'],SCHEMA,max_tokens=512)
    assert output==VALUE
    encoded=json.dumps(calls[0]['request'],ensure_ascii=False)
    assert case['project']['brief'] in encoded
    assert json.loads(calls[2]['request']['messages'][-1]['content'])['sha256']==digest(p['brief'])


@pytest.mark.parametrize('crash',[False,True])
def test_actual_pixels_are_delegated_and_changed_media_fences_cached_submission(actor,monkeypatch,crash):
    from PIL import Image
    from studio.core import media_receipt
    p,cfg=actor
    image=a.root(p).parent.parent/'shots/S01/frame.png';image.parent.mkdir(parents=True)
    Image.new('RGB',(768,1024),'white').save(image)
    record=media_receipt(image)
    p=store.update(p['id'],{'shots':[{'id':'S01','seconds':5,'action':'白杯留在原持物手','image':record}]})
    sequence=iter([steps()[0],{'tool':'inspect_media','arguments':{'shot_id':'S01','kind':'image'}},*steps()[2:]])
    visual={'alignment':'conflict','summary':'合成模型观察：所见杯子位置与目标冲突',
        'observations':[{'subject':'杯子','visible_state':'画面左侧','image_region':'左侧','certainty':'visible'}],
        'conflicts':['所见杯子不在指定手中']}
    def respond(req):
        if req.get('tools'):
            action=next(sequence)
            return {'model':req['model'],'usage':{'completion_tokens':20},'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[{'id':uuid.uuid4().hex,'function':{'name':action['tool'],'arguments':json.dumps(action['arguments'])}}]}}]}
        return {'model':req['model'],'usage':{'completion_tokens':30},'choices':[{'finish_reason':'stop','message':{'content':json.dumps(visual,ensure_ascii=False)}}]}
    calls,_=mock_transport(monkeypatch,respond)
    original_write=agent_media.write_json;failed=[]
    def save(path,value):
        original_write(path,value)
        if crash and path.name=='result.json' and not failed:
            failed.append(True);raise SystemExit('process died after durable visual result')
    monkeypatch.setattr(agent_media,'write_json',save)
    if crash:
        with pytest.raises(SystemExit):call((p,cfg))
    assert call((p,cfg))==VALUE and len(calls)==6
    visual_request=next(c['request'] for c in calls if not c['request'].get('tools'))
    assert 'data:image/' in json.dumps(visual_request)
    evidence=read_json(next(a.root(p).glob('*/inspection-*/result.json')))['result']
    assert evidence['observation']==visual and evidence['quality_approval'] is False
    assert evidence['motion_completion']=='not_established'
    assert a.snapshot(p)['runs'][0]['spent_output_tokens']==130
    Image.new('RGB',(768,1024),'red').save(image)
    with pytest.raises(ProductionNeedsDecision) as exc:call((p,cfg))
    assert exc.value.reason=='agent_stale_media' and len(calls)==6


def test_director_agent_supplement_is_persisted_scoped_and_replayed_without_new_call(actor,monkeypatch):
    from factory import creative,creative_runtime,role_handoffs
    from tests.test_factory_creative import CreativeContractTests
    fixture=CreativeContractTests();fixture.setUp()
    monkeypatch.setattr(schemas,'ROOT',a.ROOT)
    try:
        p,cfg=actor
        # Only construct a deterministic episode; this fixture does not invoke
        # the creator loop and is explicitly marked synthetic by actor.
        plan=creative.duration_plan(p)
        raw=fixture.reply(schema=creative.episode_schema(len(plan),p),payload={},role='screenwriter')
        episode=creative._canonical_episode(raw,p)
        selected={'id':'A','logline':'虚构城市故事','eligible':True}
        p=store.update(p['id'],{'screenplay_draft':episode,'creative_round':1,'proposals':[selected],'selected_story':'A'})
        calls=replies(monkeypatch,[steps()[0],{'tool':'request_supplement','arguments':{'reason':'S03 的杯子交接缺少动作依据','shot_ids':['S03']}}])
        patch=creative_runtime.direct_screenplay(p,cfg,None)
        request=patch['storyboard_supplement_request']
        assert request['source']=='storyboard_director' and request['target']=='screenwriter' and request['scope']==['S03']
        assert patch['screenplay_draft']==episode and patch['storyboard_receipt']['submission']=='agent_handoff_recorded'
        assert a.snapshot(p)['runs'][0]['state']=='handoff' and len(calls)==2
        assert creative_runtime.direct_screenplay(p,cfg,None)==patch and len(calls)==2
        from factory.prompt_workbench import EMPTY
        snapshot={'schema':'ai-studio.prompt-snapshot.v1','versions':{},'overrides':{**EMPTY,'project':'WORKBENCH_PROJECT_REQUIREMENT'}}
        snapshot['sha256']=digest(snapshot)
        p=store.update(p['id'],{**patch,'creative_round':2,'prompt_snapshot':snapshot})
        original=copy.deepcopy(episode);malicious=copy.deepcopy(raw)
        malicious['title']='unrelated title';malicious['shots'][0]['action']='无关剧情不可采用，其他镜头和角色应当完整保留。'
        malicious['shots'][2]['action']='成年主角把白杯递给搭档，交接在本镜内完整完成。'
        writes=[]
        def writer(*args,**kwargs):writes.append(args);return malicious
        monkeypatch.setattr(creative,'cached_json_call',writer)
        repaired=creative_runtime.write_screenplay(p,cfg,None)['screenplay_draft']
        assert writes[0][5]['only_revise_shot_ids']==['S03']
        assert 'WORKBENCH_PROJECT_REQUIREMENT' in writes[0][4]
        assert repaired['title']==original['title'] and repaired['cast']==original['cast']
        assert all(old==new for old,new in zip(original['shots'],repaired['shots']) if old['id']!='S03')
        assert repaired['shots'][2]['action']!=original['shots'][2]['action']
    finally:fixture.tearDown();fixture.doCleanups()


def test_worker_blocks_supplement_without_unpriced_advice_and_clears_context(actor,monkeypatch):
    from factory import worker,review_advice
    from factory.execution_context import current_job
    p,_=actor
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='queued' WHERE project_id=?",(p['id'],))
    job=store.claim_job(p['id'])
    seen=[]
    def advance(pid):
        seen.append(current_job.get())
        raise a.SupplementRequested('需要出品人提供外部依据',[])
    monkeypatch.setattr(worker,'advance',advance)
    monkeypatch.setattr(review_advice,'eligible',lambda p:pytest.fail('No extra advisory model after agent stop'))
    worker.handle(job)
    assert seen[0]['lease']==job['lease'] and current_job.get() is None
    value=store.get(p['id'],False)
    assert value['status']=='blocked' and value['needs_decision']['type']=='production'
    assert value['needs_decision']['code']=='agent_supplement_requested'


def test_late_worker_does_not_overwrite_current_project_after_loop_fence(actor,monkeypatch):
    from factory import worker
    p,_=actor
    with store.connection() as db:db.execute("UPDATE jobs SET status='queued' WHERE project_id=?",(p['id'],))
    job=store.claim_job(p['id'])
    def advance(pid):
        with store.connection() as db:db.execute("UPDATE jobs SET lease=lease+1 WHERE id=?",(job['id'],))
        store.update(pid,{'continuation_sentinel':'新的worker已经继续工作','status':'scripting'})
        raise ProductionNeedsDecision('旧租约失效','agent_stale_lease')
    monkeypatch.setattr(worker,'advance',advance)
    worker.handle(job)
    assert store.get(p['id'],False)['continuation_sentinel']=='新的worker已经继续工作'
    assert store.get(p['id'],False)['status']=='scripting'


def test_action_cap_is_checked_before_dispatch_and_candidate_is_retained(actor,monkeypatch):
    p,cfg=actor
    p=store.update(p['id'],{'agent_loop':{**p['agent_loop'],'settings':{**p['agent_loop']['settings'],'max_actions':4}}})
    calls=replies(monkeypatch,steps())
    with pytest.raises(ProductionNeedsDecision) as exc:call((p,cfg))
    assert exc.value.reason=='agent_exhausted' and len(calls)==4
    row=a._rows(p)[0]
    assert row['candidate']==VALUE and len(row['started_actions'])==4
    assert not (a.root(p)/row['id']/'result.json').exists()
