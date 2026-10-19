"""Durable controller evidence and material gates, no provider or production IO."""
import copy
import json
import sqlite3

import pytest
from factory import role_handoffs as handoffs


@pytest.fixture
def source(tmp_path):
    project={'id':'aabb11223344','workflow_version':2,'script_generation':3,'production_revision':1}
    value={'title':'交接测试','shots':[{'id':'S01','action':'人物推开门'},{'id':'S02','action':'人物递出钥匙'}]}
    return tmp_path/'planning',project,value


def submit(source, **changes):
    folder,project,value=source
    options={'source':'screenwriter','target':'storyboard_director','source_stage':'screenplay',
             'target_stage':'storyboard','contract':'episode_direction','value':value,'scope':['S01','S02']}
    options.update(changes)
    return handoffs.submit(folder,project,**options)


def receive(source, record, **changes):
    folder,project,value=source
    options={'role':'storyboard_director','stage':'storyboard','value':value}
    options.update(changes)
    return handoffs.receive(folder,project,record['id'],**options)


def test_old_history_is_unknown_without_creating_files(source):
    folder,project,_=source
    assert handoffs.snapshot(folder,project)['history_state']=='not_recorded'
    assert not folder.exists()


def test_exact_material_is_received_once_and_survives_reopening(source):
    row=submit(source);assert row['state']=='submitted'
    assert submit(source)==row
    assert receive(source,row)['accepted']
    assert receive(source,row)['accepted']
    folder,project,value=source
    result=handoffs.snapshot(folder,project)
    assert result['total']==1 and result['handoffs'][0]['state']=='accepted'
    assert [e['action'] for e in result['handoffs'][0]['events']]==['submitted','accepted']
    assert 'value' not in result['handoffs'][0]
    assert handoffs.detail(folder,project,row['id'])['value']==value


@pytest.mark.parametrize('change',[{'script_generation':4},{'production_revision':2},{'plan_version':1}])
def test_old_version_cannot_be_received_or_satisfy_new_work(source,change):
    row=submit(source);folder,project,value=source
    with pytest.raises(ValueError,match='版本'):
        handoffs.receive(folder,{**project,**change},row['id'],role='storyboard_director',stage='storyboard',value=value)
    assert not handoffs.snapshot(folder,{**project,**change})['handoffs'][0]['current_version']


@pytest.mark.parametrize('changes',[{'role':'screenwriter'},{'stage':'production'},{'value':{'shots':[]}}])
def test_wrong_receiver_or_payload_cannot_claim_receipt(source,changes):
    row=submit(source)
    with pytest.raises(ValueError,match='不一致'):receive(source,row,**changes)
    assert handoffs.snapshot(source[0],source[1])['handoffs'][0]['state']=='submitted'


def test_missing_action_returns_only_the_affected_shot_and_requires_exact_supplement(source):
    folder,project,value=source;value['shots'][1]['action']=' '
    row=submit(source);received=receive(source,row)
    assert not received['accepted']
    request=received['request'];assert request['target']=='screenwriter' and request['scope']==['S02']
    assert receive(source,row)['request']['id']==request['id']
    repaired=copy.deepcopy(value);repaired['shots'][1]['action']='人物递出钥匙'
    replacement=submit(source,value=repaired,scope=['S02'],resolves=request['id'])
    accepted=receive(source,replacement,value=repaired)
    assert accepted['accepted']
    snapshot=handoffs.snapshot(folder,project)
    assert snapshot['requests'][0]['state']=='supplemented'
    assert snapshot['requests'][0]['replacement_id']==replacement['id']
    assert snapshot['handoffs'][0]['scope']==['S02']
    assert handoffs.detail(folder,project,row['id'])['value']['shots'][1]['action']==' '


def test_supplement_cannot_expand_scope_or_overwrite_other_receiver(source):
    source[2]['shots'][1]['action']=''
    row=submit(source);request=receive(source,row)['request']
    with pytest.raises(ValueError,match='原请求'):
        submit(source,scope=['S01','S02'],resolves=request['id'])
    with pytest.raises(ValueError,match='原请求'):
        submit(source,scope=['S02'],resolves=request['id'],target='independent_critic')


def test_model_blocker_is_only_a_scoped_controller_request(source):
    row=submit(source);receive(source,row)
    issue={'code':'storyboard_feasibility','description':'动作缺少可见完成状态','shot_ids':['S02']}
    request=handoffs.return_for_supplement(source[0],source[1],row['id'],[issue])
    assert request['scope']==['S02'] and request['state']=='requested'
    assert not receive(source,row)['accepted']
    with pytest.raises(ValueError,match='扩展'):
        handoffs.return_for_supplement(source[0],source[1],row['id'],[{**issue,'shot_ids':['S99']}])


@pytest.mark.parametrize('approval',[None,{'passed':False},{'passed':True,'scores':{},'issues':[{'severity':'blocking'}]}])
def test_unreviewed_or_failed_material_cannot_enter_approved_design(source,approval):
    row=submit(source,target='visual_designer',target_stage='visual_design',contract='visual_design',
               value={'title':'测试','cast':{'female':'成年角色'}},approval=approval,scope=[])
    result=receive(source,row,role='visual_designer',stage='visual_design',value=row['value'])
    assert not result['accepted'] and result['request']['issues'][0]['code']=='handoff_approval'


def test_tampered_material_or_approval_is_never_received(source):
    row=submit(source)
    with sqlite3.connect(source[0]/'handoffs.sqlite3') as db:
        broken=copy.deepcopy(row);broken['value']['shots'][0]['action']='偷偷换稿'
        db.execute('update handoffs set record=? where id=?',(json.dumps(broken),row['id']))
    with pytest.raises(ValueError,match='校验失败'):receive(source,row)


def test_cache_observation_does_not_claim_new_model_call(source):
    row=submit(source);receive(source,row)
    with handoffs.bind(source[0],row):
        assert handoffs.current()==[row['id']]
        handoffs.observe_call(None,reused=True)
        handoffs.observe_call(None,reused=True)
    assert handoffs.current()==[]
    events=handoffs.snapshot(source[0],source[1])['handoffs'][0]['events']
    assert len([e for e in events if e['action']=='cached_result_reused'])==1
    assert not any(e['action']=='call_recorded' for e in events)


def test_paths_and_ids_cannot_escape_project(source,tmp_path):
    row=submit(source)
    with pytest.raises(ValueError):handoffs.detail(source[0],source[1],'../escape')
    linked=tmp_path/'linked';linked.symlink_to(source[0],target_is_directory=True)
    with pytest.raises(ValueError,match='软链接'):handoffs.snapshot(linked,source[1])
    with pytest.raises(ValueError):submit(source,scope=['S01','S01'])
    with pytest.raises(ValueError):submit(source,target='arbitrary_tool_executor')
