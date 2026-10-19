"""CPU/protocol tests; generated fixtures do not prove artistic improvement."""
import copy
import json
from pathlib import Path
import uuid

from PIL import Image
import pytest
from factory import generation_pipeline as g, store, schemas, llm
from factory.scheduler import ProductionNeedsDecision
from factory.prompt_registry import load_prompt
from test_factory_team_ledger import project, http_client
from tests.test_factory_role_routing import routing_config, mock_transport


def shot():
    return {'id': 'S01', 'seconds': 5, 'prompt': 'A woman holds a white cup on the left side of the image.',
        'action': 'Turn toward the camera.', 'motion_prompt': 'A small head turn toward camera.',
        'camera_motion': 'hold', 'characters': ['female'], 'reference_group': 'female',
        'character_states': {'female': {'prop': 'white cup', 'hand': 'original holding hand'}},
        'continuity': 'The same cup stays in the original holding hand.', 'dialogue': []}


def candidate(s=None):
    s = s or shot()
    return {'status': 'ready', 'reason': '', 'approved_action': s['action'], 'camera_motion': s['camera_motion'],
        'image': {'moment': 'start', 'description': 'A woman holds a white cup beside a window.'},
        'video': {'initial_state': 'The woman faces the window holding her cup.',
            'action': 'She turns her head gently toward camera; the cup stays in the original holding hand.',
            'end_state': 'She faces camera with the same cup.', 'camera': 'Locked camera.', 'requires_body_motion': True}}


def observation(alignment='match'):
    return {'alignment': alignment, 'summary': '首帧中角色持白杯，面向窗外。',
        'observations': [{'subject': 'female', 'visible_state': '白杯位于画面左侧持物手中', 'image_region': '画面左侧', 'certainty': 'visible'}],
        'conflicts': ['道具持有人与批准状态矛盾'] if alignment == 'conflict' else []}


def idle(p):
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='cancelled' WHERE project_id=?", (p['id'],))
    return store.update(p['id'], {'status':'paused', 'pause_requested':True})


def body(p, enabled=True):
    return {'request_id': uuid.uuid4().hex, 'expected_revision':p['revision'], 'enabled':enabled, 'confirmed':True}


@pytest.fixture
def production(project, tmp_path, monkeypatch):
    import studio.core as core
    monkeypatch.setattr(g, 'ROOT', tmp_path)
    monkeypatch.setattr(schemas, 'ROOT', tmp_path)
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    p=idle(project)
    p=store.update(p['id'], {'format':'animation', 'script': {'synopsis':'保留动作因果，不恢复已删除的雨伞', 'ending':'误会闭合', 'cast':{'female':'Adult woman, black hair'}, 'shots':[shot()]}})
    g.configure(p['id'], body(p))
    p=store.get(p['id'], False)
    frame=tmp_path/'production'/p['slug']/'frame.png';frame.parent.mkdir(parents=True);Image.new('RGB',(64,64),'white').save(frame)
    cfg=routing_config();cfg['llm_roles']['storyboard_director']='qwen'
    config={'generation_pipeline_config':cfg, 'image_width':576,'image_height':1024,
            'video_connection':{'provider':'comfyui_h3','model':'MiniMax-H3','enabled':True},
            'audio_connection':{'provider':'legacy'}, 'image_connection':{'provider':'comfyui_qwen_image21','model':'qwen','enabled':True}}
    return p, frame, config


def transport(monkeypatch, s=None, alignment='match'):
    def responder(request):
        images=isinstance(request['messages'][-1]['content'],list)
        result=observation(alignment) if images else candidate(s)
        return {'model':request['model'], 'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]}
    return mock_transport(monkeypatch,responder)[0]


def test_http_opt_in_idempotence_and_old_outputs_preserved(project, http_client, monkeypatch):
    monkeypatch.setattr(llm,'_request',lambda *a,**k:pytest.fail('Preview/configure cannot call a model'))
    base=f"/api/factory/projects/{project['id']}/generation-pipeline"
    assert http_client('GET',base)[1]['enabled'] is False
    assert http_client('POST',base,body(project))[0] == 409
    p=idle(project);p=store.update(p['id'],{'shots':[{'id':'S01','seconds':5,'image':{'path':'approved.png'}}],'delivery':{'path':'final.mp4'}})
    request=body(p)
    first=http_client('POST',base,request)
    assert first[0] == 200 and http_client('POST',base,request)==first
    after=store.get(p['id'],False)
    assert after['shots']==p['shots'] and after['delivery']==p['delivery']
    assert http_client('GET',base)[1]['shots'][0]['state']=='approved_spec_only'
    assert http_client('GET',base+'?unexpected=x')[0]==400
    assert http_client('POST',base,body(after),headers={'Origin':'https://cross-origin.example'})[0]==403


def test_image_plan_is_one_instant_and_bound_to_actual_references(production, monkeypatch):
    p,frame,config=production;calls=transport(monkeypatch)
    result=g.prepare(p,shot(),[frame],config=config,phase='image')
    assert len(calls)==1 and result['parameters']['reference_sha256']==[g.sha256(frame)]
    assert 'Single starting instant' in result['parameters']['prompt']
    assert result['source']['approved_shot']['action']==shot()['action']
    assert g.prepare(p,shot(),[frame],config=config,phase='image')==result and len(calls)==1
    assert result['quality_status']=='pending_actual_media_review'


def test_real_pixel_delivery_precedes_director_and_h3_request(production, monkeypatch):
    p,frame,config=production;calls=transport(monkeypatch)
    result=g.prepare(p,shot(),[frame],config=config,phase='video')
    assert len(calls)==2
    content=calls[0]['request']['messages'][-1]['content']
    assert content[1]['image_url']['url'].startswith('data:image/jpeg;base64,')
    assert 'first_frame_pixels_only' in calls[1]['request']['messages'][-1]['content']
    assert result['first_frame']['sha256']==g.sha256(frame)
    assert result['parameters']['prompt'].startswith('For the target video, at 0.00 seconds')
    assert shot()['action'] in result['parameters']['prompt']
    assert result['parameters']['frames']>0 and result['parameters']['camera_motion']=='hold'
    assert result['first_frame']['quality_approval'] is False
    assert g.prepare(p,shot(),[frame],config=config,phase='video')==result and len(calls)==2
    receipt=list((g.folder(p)/'vision').glob('calls/*/receipt.json'))[0]
    assert json.loads(receipt.read_text())['prompt']['version']=='1.0.0'


@pytest.mark.parametrize('alignment',['conflict','uncertain'])
def test_conflicting_or_uncertain_first_frame_prevents_video_and_director_calls(production,monkeypatch,alignment):
    p,frame,config=production;calls=transport(monkeypatch,alignment=alignment)
    with pytest.raises(ProductionNeedsDecision) as exc:
        g.prepare(p,shot(),[frame],config=config,phase='video')
    assert exc.value.reason=='generation_frame_conflict' and len(calls)==1
    with pytest.raises(ProductionNeedsDecision):
        g.prepare(p,shot(),[frame],config=config,phase='video')
    assert len(calls)==1


@pytest.mark.parametrize('edit',[{'approved_action':'删除转头动作'}, {'camera_motion':'push_in'}, {'image':{'moment':'start','description':'A woman holds a cup then turns her head.'}}])
def test_model_cannot_change_facts_or_put_a_sequence_in_a_still(production,monkeypatch,edit):
    p,frame,config=production
    monkeypatch.setattr(schemas,'cached_json_call',lambda *a,**k: {**candidate(),**edit})
    with pytest.raises(ProductionNeedsDecision):g.prepare(p,shot(),[frame],config=config,phase='image')
    records=g.inventory(p)
    assert any(r.get('state')=='blocked' for r in records)
    assert not any(r.get('state')=='unknown' for r in records)


def test_media_capability_checks_happen_before_model_requests(production,monkeypatch):
    p,frame,config=production;monkeypatch.setattr(llm,'_request',lambda *a,**k:pytest.fail('No model for impossible capabilities'))
    for changed,phase,refs in [({'lip_sync':True},'image',[frame]),({},'image',[frame]*5),({'action':''},'video',[frame])]:
        with pytest.raises(ProductionNeedsDecision):g.prepare(p,{**shot(),**changed},refs,config=config,phase=phase)
    with pytest.raises(ProductionNeedsDecision):g.prepare({**p,'format':'motion_comic'},shot(),[frame],config=config,phase='video')


def test_unknown_completion_cannot_repost_after_restart_or_changed_inputs(production,monkeypatch):
    p,frame,config=production
    monkeypatch.setattr(schemas,'cached_json_call',lambda *a,**k:(_ for _ in ()).throw(TimeoutError('uncertain')))
    with pytest.raises(TimeoutError):g.prepare(p,shot(),[frame],config=config,phase='image')
    monkeypatch.setattr(schemas,'cached_json_call',lambda *a,**k:pytest.fail('Must not replay'))
    with pytest.raises(ProductionNeedsDecision) as exc:g.prepare(p,{**shot(),'prompt':'New input'},[frame],config=config,phase='image')
    assert exc.value.reason=='generation_plan_unknown'
    with pytest.raises(ValueError,match='未知'):g.configure(p['id'],body(p,False))
    r=next(r for r in g.inventory(p) if r.get('state')=='unknown')
    g.settle(p['id'],{'request_id':uuid.uuid4().hex,'expected_revision':p['revision'],'record_id':r['id'],'confirmed':True})
    assert next(item for item in g.inventory(p) if item['id']==r['id'])['state']=='closed'
    with pytest.raises(ProductionNeedsDecision) as closed:
        g.prepare(p,{**shot(),'prompt':'Changed after settlement'},[frame],config=config,phase='image')
    assert closed.value.reason=='generation_plan_closed'


def test_changed_reference_pixels_and_feedback_invalidate_compilation(production,monkeypatch):
    p,frame,config=production;calls=transport(monkeypatch)
    old=g.prepare(p,shot(),[frame],config=config,phase='image')
    Image.new('RGB',(64,64),'red').save(frame)
    new=g.prepare(p,shot(),[frame],config=config,phase='image')
    assert old['signature']!=new['signature'] and len(calls)==2
    changed={**p,'script_feedback':'不得恢复删除的雨伞，道具仍由原持物手持有。'}
    latest=g.prepare(changed,shot(),[frame],config=config,phase='image')
    assert latest['signature']!=new['signature'] and len(calls)==3
    assert old==g.inventory(p)[0]['document'] or old in [r['document'] for r in g.inventory(p)]


def test_audio_compilation_locks_text_speaker_and_actual_voice_mapping(production):
    p,frame,config=production
    line={'id':'S01_D01','role':'female','text':'杯子给你','emotion':'平静'}
    p['script']['shots'][0]['dialogue']=[line]
    voices={'female':{'speaker':'Serena','language':'Chinese'}}
    g.audio(p,[line],voices,config)
    record=next(r['document'] for r in g.inventory(p) if r['document'].get('phase')=='audio')
    assert record['lines']==[line] and record['voices']==voices
    with pytest.raises(ProductionNeedsDecision):g.audio(p,[{**line,'text':'改变剧情'}],voices,config)


def test_all_registered_contracts_remain_loadable():
    assert load_prompt('storyboard_director','3.1.0').spec.version=='3.1.0'
    assert load_prompt('storyboard_director','4.0.0').spec.output_schema_refs==('factory.generation_pipeline:PLAN_SCHEMA',)


def test_production_target_consumes_plan_and_resumes_frozen_attempt_without_model_replay(production, monkeypatch):
    from factory import production as renderer
    from factory.image_generation import image_request
    p,frame,config=production
    monkeypatch.setattr(renderer,'ROOT',g.ROOT)
    calls=transport(monkeypatch)
    # Use the real production target, not just the plan helper. Downstream GPU
    # request compilation must consume the plan's prompt and actual dimensions.
    cfg={**config,'video_connection': {'provider':'comfyui_wan','model':'Wan2.2-TI2V-5B','enabled':True}, 'image_width':288,'image_height':512}
    run=renderer._Run(p,cfg,None,None)
    folder=run.folder/'assets/shots/S01'
    target=run._target('S01',folder,'legacy text',[frame],shot=run.shots[0],review=True)
    assert len(calls)==1 and target['generation_plan']['parameters']['width']==288
    request=image_request({'provider':'legacy'},target['prompt'],7,288,512,['actual-reference.png'])
    assert any(node['inputs'].get('text') == target['generation_plan']['parameters']['prompt'] for node in request.values())
    attempt=folder/'take_001/attempt.json'
    g.write_json(attempt,{'generation_plan':target['generation_plan']})
    monkeypatch.setattr(schemas,'cached_json_call',lambda *a,**k:pytest.fail('Resume must reuse frozen compilation'))
    resumed=run._target('S01',folder,'legacy text',[frame],shot=run.shots[0],review=True)
    assert resumed['signature']==target['signature'] and resumed['prompt']==target['prompt']
    # A valid legacy receipt is reused after opt-in, with no compilation.
    other=run.folder/'legacy/S01'
    legacy=copy.deepcopy(run.project);legacy['generation_pipeline']=None
    legacy_run=renderer._Run(legacy,cfg,None,None)
    old=legacy_run._target('S01',other,'legacy text',[frame],shot=legacy_run.shots[0])
    receipt={**g.media_receipt(frame),'source_signature':old['signature'],'qa':{'status':'pass'}}
    g.write_json(other/'selected.json',receipt)
    preserved=run._target('S01',other,'legacy text',[frame],shot=run.shots[0])
    assert 'generation_plan' not in preserved
    assert renderer.read_json(other/'selected.json')==receipt


def test_changed_frame_during_visual_call_is_not_usable_evidence(production, monkeypatch):
    p,frame,config=production
    def response(request):
        Image.new('RGB',(64,64),'blue').save(frame)
        return {'model':request['model'],'choices':[{'finish_reason':'stop','message':{'content':json.dumps(observation())}}]}
    calls,_=mock_transport(monkeypatch,response)
    with pytest.raises(ProductionNeedsDecision) as exc:g.prepare(p,shot(),[frame],config=config,phase='video')
    assert exc.value.reason=='generation_frame_changed' and len(calls)==1
    assert not any(r['document'].get('phase')=='video' for r in g.inventory(p))


@pytest.mark.parametrize("status",["queued","waiting_remote"])
def test_configuration_blocks_queued_and_unknown_paid_media_work(production,status):
    p,frame,config=production
    with store.connection() as db:
        db.execute("UPDATE jobs SET status=? WHERE project_id=?",(status,p['id']))
    with pytest.raises(ValueError,match='在途'):g.configure(p['id'],body(p,False))


def test_prepared_request_records_actual_repair_prompt_and_seed(production,monkeypatch):
    from factory import production as renderer
    p,frame,config=production
    monkeypatch.setattr(renderer,'ROOT',g.ROOT)
    transport(monkeypatch)
    cfg={**config,'video_connection':{'provider':'comfyui_wan','model':'Wan2.2-TI2V-5B','enabled':True},'image_width':288,'image_height':512}
    run=renderer._Run(p,cfg,None,None)
    target=run._target('S01',run.folder/'assets/shots/S01','original',[frame],shot=run.shots[0])
    monkeypatch.setattr(run,'_team_admit_target',lambda t:None)
    monkeypatch.setattr(run,'_team_begin_target',lambda t,r:None)
    monkeypatch.setattr(run,'_attempts',lambda t:[{'state':'failed','qa':{'issues':[{'code':'blur'}]}}])
    record=run._new_attempt(target)
    assert record['prompt'].startswith(target['prompt']) and record['repair']['prompt_prefix']
    evidence=next(r['document'] for r in g.inventory(p) if r['document'].get('phase')=='request')
    assert evidence['parameters']['prompt']==record['prompt']
    assert evidence['parameters']['seed']==record['seed']
    assert evidence['parameters']['reference_sha256']==[g.sha256(frame)]
    assert evidence['state']=='prepared_request_only'


def test_preview_exposes_invalid_legacy_shot_without_altering_it(production):
    p,frame,config=production
    p['script']['shots'][0]['seconds']=60
    preview=g.preview(p)
    assert preview['shots'][0]['state']=='spec_invalid'
    assert p['script']['shots'][0]['seconds']==60


def test_pause_or_budget_boundary_between_observation_and_plan_does_not_repost(production,monkeypatch):
    from factory.scheduler import ProductionPaused
    p,frame,config=production;calls=transport(monkeypatch)
    boundaries=[]
    def stop():
        boundaries.append(True)
        if len(boundaries)==2:raise ProductionPaused('pause before next request')
    with pytest.raises(ProductionPaused):g.prepare(p,shot(),[frame],config=config,phase='video',checkpoint=stop)
    assert len(calls)==1
    assert not any(r.get('state')=='unknown' for r in g.inventory(p))
    g.prepare(p,shot(),[frame],config=config,phase='video')
    assert len(calls)==2 # Frozen first-frame observation reused, only the plan runs.


def test_cloud_voice_uses_adapter_profile_binding_and_fallback_not_local_voice(production):
    p,frame,config=production
    line={'id':'S01_D01','role':'female','text':'杯子留在原持物手中'}
    p['script']['shots'][0]['dialogue']=[line]
    config={**config,'audio_connection':{'provider':'dashscope_tts','voices':{'female':'Cherry'},'language':'Chinese'}}
    g.audio(p,[line],{'female':{'speaker':'Serena'}},config)
    audio=next(r['document'] for r in g.inventory(p) if r['document'].get('phase')=='audio')
    assert audio['voices']['female']['speaker']=='Cherry'
    assert audio['lines'][0]['text']==line['text']


def test_live_compile_intent_is_running_and_crashed_intent_becomes_unknown(production):
    p,frame,config=production
    def running():
        assert next(r for r in g.inventory(p) if r.get('state'))['state']=='running'
        raise TimeoutError('worker interrupted before a terminal result')
    with pytest.raises(TimeoutError):g._once(p,'image',{'case':'live-worker'},running)
    assert next(r for r in g.inventory(p) if r.get('state'))['state']=='unknown'


@pytest.mark.parametrize('role',['storyboard_director','visual_critic'])
def test_unpriced_compiler_models_stop_before_any_model_or_paid_media_request(production,monkeypatch,role):
    p,frame,config=production
    cfg=config['generation_pipeline_config']
    cfg['llm_profiles']['vendor']={**copy.deepcopy(cfg['llm_profiles']['qwen']), 'provider':'openai_compatible',
        'base_url':'https://model-vendor.example/v1', 'network_mode':'remote', 'allowed_hosts':['model-vendor.example'], 'enabled':True}
    cfg['llm_roles'][role]='vendor'
    monkeypatch.setattr(llm,'_request',lambda *a,**k:pytest.fail('Unpriced calls cannot run'))
    with pytest.raises(ProductionNeedsDecision) as exc:g.prepare(p,shot(),[frame],config=config,phase='video' if role=='visual_critic' else 'image')
    assert exc.value.reason=='generation_model_budget_required'
    assert g.inventory(p)==[]
