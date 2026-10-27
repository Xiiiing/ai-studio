"""Frame-counted CPU editing, durable state and source ownership regression."""
import copy
import json
import uuid

import av
import numpy as np
import pytest

from factory import store, edit_timeline as timeline, edit_versions as edits
from factory.edit_render import render
from factory.edit_validation import seed_validation_project
from studio import core


@pytest.fixture
def project(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'ROOT',tmp_path)
    monkeypatch.setattr(store,'DATA',tmp_path/'runtime')
    monkeypatch.setattr(store,'DATABASE',tmp_path/'runtime'/'editor.sqlite3')
    store.initialize()
    return seed_validation_project()


def mutate(project,action,**extra):
    return edits.mutate(project['id'],{'action':action,'expected_revision':store.get(project['id'],False)['revision'],
        'request_id':uuid.uuid4().hex,**extra})


def queued_job(project):
    """Claim only an edit queue record; queue integration is tested separately."""
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        job=dict(db.execute("SELECT * FROM jobs WHERE project_id=? AND kind='timeline_edit' AND status='queued'",(project['id'],)).fetchone())
        db.execute("UPDATE jobs SET status='running',lease=lease+1 WHERE id=?",(job['id'],))
        job['status']='running';job['lease']+=1
        return job


def test_sources_default_and_immutable_version_replay_conflicts(project):
    catalog=timeline.catalog(project)
    document=catalog['default']
    assert len(document['clips'])==2 and len(document['audio'])==2
    assert document['subtitles'][0]['anchor_clip_id']=='c1'
    payload={'action':'save','request_id':'first','expected_revision':project['revision'],'document':document,'label':'原始镜头'}
    result=edits.mutate(project['id'],payload)
    assert edits.mutate(project['id'],payload)['versions'][0]['id']==result['versions'][0]['id']
    with pytest.raises(ValueError,match='请求编号'):
        edits.mutate(project['id'],{**payload,'label':'不同内容'})
    with pytest.raises(ValueError,match='项目已更新'):
        edits.mutate(project['id'],{**payload,'request_id':'second'})
    old=copy.deepcopy(result['versions'][0])
    newer=mutate(project,'save',document=document,label='恢复历史为新版本',parent_id=old['id'])
    assert newer['versions'][0]=={**old,'source_current':True,'review_current':False}
    assert newer['versions'][1]['id']!=old['id']
    assert store.get(project['id'],False)['delivery']==project['delivery']


def test_source_hash_and_path_are_authoritative(project,tmp_path):
    catalog=timeline.catalog(project)
    first=next(s for s in catalog['sources'].values() if s['kind']=='video')
    receipt=copy.deepcopy(first['receipt'])
    with pytest.raises(ValueError,match='escapes|不属于'):
        timeline.verified({**receipt,'path':'../escape.mp4'},project)
    path=core.ROOT/receipt['path'];path.write_bytes(b'corrupted')
    with pytest.raises(ValueError,match='回执'):
        timeline.catalog(project)


def test_invalid_ranges_audio_tail_transition_and_duration_confirmation(project):
    catalog=timeline.catalog(project);doc=catalog['default']
    bad=copy.deepcopy(doc);bad['clips'][0]['out_frame']=1000
    with pytest.raises(ValueError):timeline.normalize(bad,catalog['sources'])
    bad=copy.deepcopy(doc);bad['clips'][1]['transition']={'kind':'dissolve','frames':72}
    with pytest.raises(ValueError):timeline.normalize(bad,catalog['sources'])
    bad=copy.deepcopy(doc);bad['audio'][0]['start_frame']=140
    with pytest.raises(ValueError,match='截断对白'):timeline.normalize(bad,catalog['sources'])
    changed=copy.deepcopy(doc);changed['clips'][1]['out_frame']=60;changed['audio']=[]
    changed=timeline.normalize(changed,catalog['sources'])
    assert 'duration_confirmation' in {i['code'] for i in timeline.check_document(project,changed,catalog['sources'])['issues']}
    changed['confirmed_duration_frames']=132
    changed['subtitles'][0]['text']='文字变化'
    assert 'caption_text_changed' in {i['code'] for i in timeline.check_document(project,changed,catalog['sources'])['issues']}


def test_delete_required_event_and_draft_cannot_formal_export(project):
    cat=timeline.catalog(project);doc=cat['default'];doc['clips']=doc['clips'][:1];doc['audio']=[];doc['confirmed_duration_frames']=72
    result=mutate(project,'save',document=doc,label='缺少事件')
    item=result['versions'][-1]
    assert item['structural']['status']=='fail'
    assert any(i['code']=='missing_event' for i in item['structural']['issues'])
    with pytest.raises(ValueError,match='先检查'):
        mutate(project,'export',version_id=item['id'])
    with pytest.raises(ValueError,match='完成当前版本检查'):
        mutate(project,'adopt',version_id=item['id'],confirmed=True,human_reviewed=True)


@pytest.mark.parametrize('transition,overlap',[('cut',0),('dissolve',12),('fadeblack',12)])
def test_actual_ffmpeg_trim_reorder_split_transitions_audio_and_subtitles(project,transition,overlap):
    cat=timeline.catalog(project);doc=cat['default']
    first,second=doc['clips']
    # Reorder and split the former first source, retaining source ownership.
    doc['clips']=[second,{**copy.deepcopy(first),'out_frame':36},
                  {**copy.deepcopy(first),'id':'split','in_frame':36,'transition':{'kind':transition,'frames':overlap}}]
    doc['audio'][0]['start_frame']=84
    doc['audio'][0]['anchor_clip_id']='c1'
    doc['audio'][1]['out_frame']=144-overlap
    doc['subtitles'][0].update(start_frame=84,end_frame=108)
    doc['confirmed_duration_frames']=144-overlap
    doc=timeline.normalize(doc,cat['sources'])
    result=render(doc,cat['sources'],core.ROOT/'production'/project['slug']/'edits'/'fixture')
    assert result['technical_qa']['clean']['video_frames']==144-overlap
    assert result['semantic_status']=='not_reviewed'
    srt=(core.ROOT/next(r['path'] for r in result['artifacts'] if r['path'].endswith('.srt'))).read_text()
    assert '00:00:03,500 --> 00:00:04,500' in srt
    with av.open(str(core.ROOT/result['path'])) as container:
        frame=next(container.decode(video=0)).to_ndarray(format='rgb24')
        assert frame.shape==(288,160,3)
    with av.open(str(core.ROOT/result['path'])) as container:
        stream=container.streams.audio[0]
        samples=np.concatenate([f.to_ndarray().ravel() for f in container.decode(stream)])
        assert np.max(np.abs(samples))>0.01


def test_preview_worker_preserves_workflow_and_restart_reuses_saved_result(project,monkeypatch):
    cat=timeline.catalog(project)
    result=mutate(project,'save',document=cat['default'],label='新剪辑')
    vid=result['versions'][-1]['id'];mutate(project,'preview',version_id=vid)
    job=queued_job(project)
    before=store.get(project['id'],False)
    edits.run(job)
    current=store.get(project['id'],False)
    assert current['status']==before['status'] and current['pause_requested'] is True
    assert current['delivery']==before['delivery']
    version=current['timeline_edits']['versions'][-1]
    assert version['preview']['technical_qa']['clean']['video_frames']==144
    assert version['semantic']['status']=='not_reviewed'
    with store.connection() as db:
        assert db.execute('SELECT status FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]=='completed'
    preview=copy.deepcopy(version['preview'])
    mutate(project,'preview',version_id=vid)
    monkeypatch.setattr('factory.edit_render.render',lambda *a,**k:pytest.fail('must reuse checked immutable preview after restart'))
    edits.run(queued_job(project))
    assert edits.snapshot(project['id'])['versions'][-1]['preview']==preview


def test_cancelled_queued_job_keeps_draft_and_old_media(project):
    cat=timeline.catalog(project)
    result=mutate(project,'save',document=cat['default'],label='取消渲染')
    result=mutate(project,'preview',version_id=result['versions'][-1]['id'])
    jid=result['task']['job_id'];mutate(project,'cancel',job_id=jid)
    with store.connection() as db:
        assert db.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()[0]=='cancelled'
    assert edits.snapshot(project['id'])['task']['status']=='cancelled'
    assert store.get(project['id'],False)['delivery']==project['delivery']


def test_late_worker_cannot_publish_and_review_failure_not_approval(project,monkeypatch):
    cat=timeline.catalog(project)
    result=mutate(project,'save',document=cat['default'],label='审核失败')
    result=mutate(project,'check',version_id=result['versions'][-1]['id'],authorize_review=True)
    monkeypatch.setattr(edits,'_semantic',lambda *a,**k:{'status':'uncertain','scope':'synthetic test uncertainty'})
    job=queued_job(project);edits.run(job)
    version=edits.snapshot(project['id'])['versions'][-1]
    assert version['semantic']['status']=='uncertain'
    with pytest.raises(ValueError,match='先检查'):
        mutate(project,'export',version_id=version['id'])
    # Replaying a completed lease is no-op and cannot overwrite the immutable result.
    monkeypatch.setattr(edits,'_semantic',lambda *a,**k:pytest.fail('late lease'))
    edits.run(job)
    assert edits.snapshot(project['id'])['versions'][-1]['semantic']['status']=='uncertain'


def synthetic_bound_review(project,item,preview,*args):
    spans = timeline.positions(item['document']['clips'])
    samples = [{'clip_id':p['clip_id'],'frame':p['start_frame'],'seconds':p['start_frame']/24} for p in spans]
    required = timeline.check_document(project,item['document'],item['sources'])['required_event_ids']
    return {'status':'pass','edit_signature':item['document_signature'],'media_sha256':preview['artifacts'][1]['sha256'],
        'scope':'synthetic_test_transport_not_model_quality','observed_end':'Synthetic CPU pattern final frame, not real actors or artistic quality.','checks':[
            {'status':'pass','issues':[],'signature':core.fingerprint(['events',item['document_signature']]),
             'review_kind':'event','event_ids':required,'samples':samples,'summary':'controlled test inference'},
            {'status':'pass','issues':[],'signature':core.fingerprint(['continuity',item['document_signature']]),
             'review_kind':'continuity','event_ids':[],'samples':samples,'summary':'controlled test inference'}]}


def test_checked_export_adoption_is_new_delivery_and_preserves_old_history(project,monkeypatch):
    cat=timeline.catalog(project)
    result=mutate(project,'save',document=cat['default'],label='人工将要观看的剪辑')
    vid=result['versions'][-1]['id']
    # Controlled transport fixture, never a claim of actual model quality.
    monkeypatch.setattr(edits,'_semantic',synthetic_bound_review)
    mutate(project,'check',version_id=vid,authorize_review=True);edits.run(queued_job(project))
    mutate(project,'export',version_id=vid);edits.run(queued_job(project))
    before=store.get(project['id'],False)
    with pytest.raises(ValueError,match='明确确认'):
        mutate(project,'adopt',version_id=vid,confirmed=True,human_reviewed=False)
    adopted=mutate(project,'adopt',version_id=vid,confirmed=True,human_reviewed=True)
    after=store.get(project['id'],False)
    assert after['delivery']['edit_version_id']==vid
    assert after['status']=='awaiting_final'  # Existing final producer approval remains required.
    assert after['delivery_history'][-1]['delivery']==before['delivery']
    assert after['script']==before['script'] and after['shots']==before['shots']
    assert adopted['versions'][-1]['source_current'] is True
    assert after['timeline_edits']['base_delivery']==project['delivery']
    assert timeline.catalog(after)['default']['clips']==cat['default']['clips']


def test_running_cancel_is_cooperative_and_does_not_pause_or_destroy_production(project,monkeypatch):
    cat=timeline.catalog(project)
    result=mutate(project,'save',document=cat['default'],label='执行中取消')
    mutate(project,'preview',version_id=result['versions'][-1]['id'])
    job=queued_job(project)
    def cancelled_render(document,sources,folder,control):
        mutate(project,'cancel',job_id=job['id'])
        assert control()
        from factory.scheduler import ProductionPaused
        raise ProductionPaused('已取消CPU渲染')
    monkeypatch.setattr('factory.edit_render.render',cancelled_render)
    edits.run(job)
    current=store.get(project['id'],False)
    assert current['timeline_edits']['task']['status']=='cancelled'
    assert current['delivery']==project['delivery'] and current['status']==project['status']
    with store.connection() as db:
        assert db.execute('SELECT status FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]=='cancelled'


def test_long_episode_flattens_owned_units_and_offsets_independent_tracks(project):
    from factory import dramas
    second=seed_validation_project()
    parent=dramas.record('episode','合成测试长集','包含两个独立CPU测试单元')
    parent.update(status='awaiting_final',pause_requested=True,unit_ids=[project['id'],second['id']],
                  delivery={'source_signature':'parent-synthetic','resolution':[160,288]})
    with store.connection() as db:
        dramas.insert(db,parent)
    for index,unit in enumerate((project,second),1):
        store.update(unit['id'],{'record_kind':'scene_unit','episode_id':parent['id'],'global_prefix':f'U{index:02}'})
    cat=timeline.catalog(store.get(parent['id'],False))
    assert len(cat['default']['clips'])==4
    assert cat['default']['confirmed_duration_frames']==288
    assert [s['start_frame'] for s in cat['default']['subtitles']]==[12,156]
    assert len({s['id'] for s in cat['sources'].values()})==8
    unrelated=copy.deepcopy(parent);unrelated['unit_ids']=[second['id']];unrelated['id']='f'*12
    with pytest.raises(ValueError,match='不属于当前剧集'):
        timeline.catalog(unrelated)


def test_caption_origin_cannot_be_forged_to_bypass_confirmation(project):
    cat=timeline.catalog(project)
    doc=cat['default'];doc['subtitles'][0].update(text='改写字幕',source_text='改写字幕')
    with pytest.raises(ValueError,match='来源文字不可改写'):
        mutate(project,'save',document=doc,label='伪造原字幕')


def test_unknown_review_intent_blocks_new_version_model_resubmission(project):
    cat=timeline.catalog(project)
    result=mutate(project,'save',document=cat['default'],label='第一次审核')
    first=result['versions'][-1]['id']
    folder=edits._folder(project,first)/'reviews'/'unknown'
    core.write_json(folder/'intent.json',{'status':'unknown'})
    result=mutate(project,'save',document=cat['default'],label='另存不能掩盖未知请求')
    with pytest.raises(ValueError,match='新版本不能重复请求'):
        mutate(project,'check',version_id=result['versions'][-1]['id'],authorize_review=True)
    # CPU-only preview remains usable without issuing a model request.
    result=mutate(project,'preview',version_id=result['versions'][-1]['id'])
    assert result['task']['authorize_review'] is False


def test_legacy_missing_event_contract_keeps_preview_but_cannot_export(project):
    project=store.update(project['id'],{'outline':{},'proposals':[],'resolved_story_brief':None})
    cat=timeline.catalog(project)
    result=mutate(project,'save',document=cat['default'],label='历史项目待启用覆盖合约')
    item=result['versions'][-1]
    assert item['structural']['status']=='fail'
    assert any(i['code']=='coverage_contract_missing' for i in item['structural']['issues'])
    result=mutate(project,'preview',version_id=item['id']);edits.run(queued_job(project))
    assert edits.snapshot(project['id'])['versions'][-1]['preview']
    with pytest.raises(ValueError,match='先检查'):
        mutate(project,'export',version_id=item['id'])


def test_false_pass_with_empty_or_stale_checks_cannot_publish(project,monkeypatch):
    cat=timeline.catalog(project)
    result=mutate(project,'save',document=cat['default'],label='拒绝空审核')
    vid=result['versions'][-1]['id']
    def false_pass(p,item,preview,*args):
        answer=synthetic_bound_review(p,item,preview)
        answer['checks']=[]
        return answer
    monkeypatch.setattr(edits,'_semantic',false_pass)
    mutate(project,'check',version_id=vid,authorize_review=True);edits.run(queued_job(project))
    result=edits.snapshot(project['id'])
    assert result['task']['status']=='failed'
    assert '不能以pass标签通过' in result['task']['error']
    assert result['versions'][-1]['semantic']['status']=='not_reviewed'
    with pytest.raises(ValueError,match='先检查'):
        mutate(project,'export',version_id=vid)
    sample=result['versions'][-1]
    sample['preview']={'artifacts':[{'path':'episode_clean.mp4','sha256':'new'}],'source_signature':sample['document_signature']}
    sample['semantic']={'status':'pass','edit_signature':sample['document_signature'],'media_sha256':'old','checks':[{'status':'pass'}]}
    assert edits.semantic_current(sample) is False


def test_adoption_invalidates_parent_bundle_and_removes_old_human_approval(project,monkeypatch):
    from factory import dramas
    parent=dramas.record('drama','隔离旧整剧交付','测试新剪辑不会继承人工审批')
    parent.update(episodes=[project['id']],batches=[],plans=[],plan_version=1,episode_count=1,
        delivery_bundle={'source_signature':'old-bundle','current':True,'archive':{'path':'unchanged-archive.zip'}},
        series_review={'status':'pass','source_signature':'old-review'})
    with store.connection() as db:dramas.insert(db,parent)
    store.update(project['id'],{'drama_id':parent['id'],'record_kind':'episode','status':'approved',
        'accepted_at':'old-date','accepted_by':'old-producer','approved_at':'old-date',
        'episode_review':{'status':'pass','signature':'old-episode-review'}})
    cat=timeline.catalog(store.get(project['id'],False))
    result=mutate(project,'save',document=cat['default'],label='整剧中的新剪辑')
    vid=result['versions'][-1]['id']
    monkeypatch.setattr(edits,'_semantic',synthetic_bound_review)
    mutate(project,'check',version_id=vid,authorize_review=True);edits.run(queued_job(project))
    mutate(project,'export',version_id=vid);edits.run(queued_job(project))
    mutate(project,'adopt',version_id=vid,confirmed=True,human_reviewed=True)
    current=store.get(project['id'],False);root=store.get(parent['id'],False)
    assert current['status']=='awaiting_final'
    assert not any(k in current for k in ('accepted_by','accepted_at','approved_at'))
    assert current['episode_review_history'][-1]['signature']=='old-episode-review'
    assert current['episode_review']['signature'] != 'old-episode-review'
    assert current['episode_review']['edit_version_id']==vid
    assert root['delivery_bundle']['current'] is False
    assert root['delivery_bundle']['archive']==parent['delivery_bundle']['archive']
    assert root['series_review']['current'] is False
    assert any(d['type']=='final' and d['status']=='pending' for d in current['decisions'])


def adopted_fixture(project,monkeypatch):
    monkeypatch.setattr(edits,'_semantic',synthetic_bound_review)
    cat=timeline.catalog(store.get(project['id'],False))
    result=mutate(project,'save',document=cat['default'],label='验证真实终审路由')
    vid=result['versions'][-1]['id']
    mutate(project,'check',version_id=vid,authorize_review=True);edits.run(queued_job(project))
    mutate(project,'export',version_id=vid);edits.run(queued_job(project))
    mutate(project,'adopt',version_id=vid,confirmed=True,human_reviewed=True)
    return store.get(project['id'],False)


def test_adopted_short_cut_uses_current_langgraph_final_gate_without_original_production(project,monkeypatch,tmp_path):
    from factory import workflows, supervision
    monkeypatch.setattr(workflows,'DATA',tmp_path/'graph')
    monkeypatch.setattr(workflows,'CHECKPOINTS',tmp_path/'graph'/'checkpoints.sqlite3')
    monkeypatch.setattr(workflows,'production',lambda state:pytest.fail('adopted cut must not regenerate original media'))
    monkeypatch.setattr(supervision,'supervise_gate',lambda *a,**k:pytest.fail('new edit final approval must be human'))
    with workflows.durable_graph(project['workflow_version']) as graph:
        graph.update_state({'configurable':{'thread_id':project['id']}},
            {'project_id':project['id'],'route':'continue','final_generation':project['production_revision']},as_node='production')
    adopted=adopted_fixture(project,monkeypatch)
    assert adopted['delivery_complete_revision']==adopted['production_revision']
    gate=next(d for d in adopted['decisions'] if d['status']=='pending' and d['type']=='final')
    assert gate['id']==f"{project['id']}:final:{adopted['production_revision']}"
    # Resume can only wait for the new gate; it cannot delegate final approval.
    store.update(project['id'],{'pause_requested':False,'status':'queued'})
    workflows.advance(project['id'])
    waiting=store.get(project['id'],False)
    assert waiting['status']=='awaiting_final'
    store.decide(project['id'],{'type':'final','decision_id':gate['id'],'choice':'approve','expected_revision':waiting['revision']})
    source=copy.deepcopy(waiting['delivery'])
    workflows.advance(project['id'])
    accepted=store.get(project['id'],False)
    assert accepted['status']=='approved' and accepted['delivery']==source
    assert accepted['decision_effects'][gate['id']]['route']=='done'


def test_adopted_reedit_returns_to_timeline_without_overwriting_cut(project,monkeypatch,tmp_path):
    from factory import workflows
    monkeypatch.setattr(workflows,'DATA',tmp_path/'graph')
    monkeypatch.setattr(workflows,'CHECKPOINTS',tmp_path/'graph'/'checkpoints.sqlite3')
    monkeypatch.setattr(workflows,'production',lambda state:pytest.fail('manual timeline reedit is not generation'))
    adopted=adopted_fixture(project,monkeypatch)
    gate=next(d for d in adopted['decisions'] if d['status']=='pending' and d['type']=='final')
    store.decide(project['id'],{'type':'final','decision_id':gate['id'],'choice':'reedit','feedback':'将第二段剪短一秒',
        'expected_revision':adopted['revision']})
    result=workflows.advance(project['id'])
    assert result['route']=='timeline_edit'
    current=store.get(project['id'],False)
    assert current['status']=='paused' and current['delivery']==adopted['delivery']
    assert current['needs_decision']['edit_version_id']==adopted['timeline_edits']['adopted_version_id']
    assert current['needs_decision']['feedback']=='将第二段剪短一秒'


def test_long_episode_final_approval_does_not_reassemble_original_units(project,monkeypatch):
    from factory import long_episodes
    store.update(project['id'],{'record_kind':'episode'})
    adopted=adopted_fixture(project,monkeypatch)
    gate=next(d for d in adopted['decisions'] if d['status']=='pending' and d['type']=='final')
    store.decide(project['id'],{'type':'final','decision_id':gate['id'],'choice':'approve','expected_revision':adopted['revision']})
    monkeypatch.setattr('factory.episode_assembly.assemble',lambda *a,**k:pytest.fail('must not overwrite new cut with original units'))
    monkeypatch.setattr(long_episodes,'plan_units',lambda *a,**k:pytest.fail('manual final gate must not replan units'))
    result=long_episodes.run(store.get(project['id'],False),{})
    assert result['route']=='done'
    current=store.get(project['id'],False)
    assert current['status']=='approved' and current['delivery']==adopted['delivery']


def test_edited_finalization_respects_pending_new_creative_contract(project,monkeypatch):
    adopted=adopted_fixture(project,monkeypatch)
    current=store.update(project['id'],{'creative_quality_review_required':True})
    assert edits.adopted_current(current) is False
    with pytest.raises(ValueError,match='新创作合约审核'):
        mutate(project,'adopt',version_id=adopted['timeline_edits']['adopted_version_id'],confirmed=True,human_reviewed=True)


def test_changed_event_description_invalidates_previous_cut_even_when_ids_unchanged(project,monkeypatch):
    adopted=adopted_fixture(project,monkeypatch)
    vid=adopted['timeline_edits']['adopted_version_id']
    outline=copy.deepcopy(adopted['outline']);outline['beats'][0]['description']='不同的必要剧情要求，ID不变'
    store.update(project['id'],{'outline':outline})
    assert edits.snapshot(project['id'])['versions'][-1]['source_current'] is False
    with pytest.raises(ValueError,match='源制作版本已变化'):
        mutate(project,'export',version_id=vid)


def test_validation_seed_never_exposes_a_generation_job_during_media_creation(project,monkeypatch):
    import subprocess
    original=subprocess.run
    observed=[]
    def guarded_cpu_command(*args,**kwargs):
        with store.connection() as db:
            count=db.execute("SELECT count(*) FROM jobs WHERE kind='workflow' AND status IN ('queued','running')").fetchone()[0]
            observed.append(count)
            assert count==0
        return original(*args,**kwargs)
    monkeypatch.setattr('factory.edit_validation.subprocess.run',guarded_cpu_command)
    created=seed_validation_project()
    assert observed and set(observed)=={0}
    assert created['is_validation'] is True and created['pause_requested'] is True
    assert created['status']=='awaiting_final'


def test_subtitle_text_edit_retains_original_audio_receipts_and_requires_review(project):
    catalog=timeline.catalog(project)
    voices={sid:s['receipt']['sha256'] for sid,s in catalog['sources'].items() if s['kind']=='voice'}
    document=catalog['default'];document['subtitles'][0]['text']='新的字幕文字，不是新配音'
    document['caption_text_confirmed']=True
    result=mutate(project,'save',document=document,label='只改字幕')
    version=result['versions'][-1]
    assert version['document']['subtitles'][0]['source_text']=='CPU测试音，不是人物配音'
    assert version['semantic']['status']=='not_reviewed'
    assert {sid:s['receipt']['sha256'] for sid,s in version['sources'].items() if s['kind']=='voice'}==voices
    assert all(core.sha256(core.ROOT/version['sources'][sid]['receipt']['path'])==sha for sid,sha in voices.items())


def test_explicit_screenplay_revision_can_resume_after_long_unit_revision_boundary(project,monkeypatch):
    adopted=adopted_fixture(project,monkeypatch)
    gate=next(d for d in adopted['decisions'] if d['status']=='pending' and d['type']=='final')
    store.decide(project['id'],{'type':'final','decision_id':gate['id'],'choice':'revise','feedback':'修改批准范围内的人物动作',
        'expected_revision':adopted['revision']})
    # The long-unit mutation has consumed revision_action and advanced production,
    # while retaining the adopted cut until a new version is available.
    current=store.update(project['id'],{'production_revision':adopted['production_revision']+1,'revision_action':None})
    edits.require_adopted_current(current)
    assert current['delivery']['edit_version_id']==adopted['delivery']['edit_version_id']
