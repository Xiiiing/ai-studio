"""A failed boundary is returned to its owner; unrelated successful media stays put."""
import copy
import pytest
from factory import store, dramas, long_episodes, drama_runtime, workflows, drama_assets, drama_continuity, episode_assembly
from studio import core
from test_factory_dramas import isolated
from test_factory_long_episodes import episode_fixture


def setup_runtime(monkeypatch):
    p,e=episode_fixture(300,monkeypatch)
    monkeypatch.setattr(drama_assets,'prepare',lambda *args:None)
    monkeypatch.setattr(episode_assembly,'assemble',lambda *args:{'source_signature':'assembled','duration_seconds':300})
    calls=[]
    def advance(uid):
        unit=store.get(uid,False)
        calls.append(uid)
        store.update(uid, {'status':'unit_ready','progress':100,
            'delivery':{'source_signature':uid+':'+str(unit['production_revision'])},
            'production':{'gpu_seconds':unit.get('production',{}).get('gpu_seconds',0)+2},
            'script':{'ending':'角色站在门边','shots':[{'id':'S01','continuity':'蓝衣角色持有信件'}]}})
    monkeypatch.setattr(workflows,'advance',advance)
    return p,e,calls


def rejection(units):
    return workflows.ReviewBlocked('开场身份不一致',{'type':'episode_continuity', 'unit_id':units[1]['id'],
        'review':{'status':'fail','issues':['蓝衣变为红衣'], 'from_unit':units[0]['id'], 'to_unit':units[1]['id']}})


def test_rejected_boundary_repairs_only_its_unit_then_assembles(isolated,monkeypatch):
    p,e,calls=setup_runtime(monkeypatch)
    reviews=[]
    def review(episode,units,*args):
        reviews.append(copy.deepcopy(units))
        if len(reviews)==1:raise rejection(units)
        repaired=units[1]
        assert repaired['previous_unit_state']['ending']=='角色站在门边'
        feedback=repaired['revision_request']['feedback']
        assert '蓝衣变为红衣' in feedback['notes'] and '角色站在门边' in feedback['notes']
        assert feedback['shot_ids']==['S01']
        assert repaired['continuity_video_repair']['production_revision']==repaired['production_revision']
        return {'status':'pass'}
    monkeypatch.setattr(drama_continuity,'review',review)
    drama_runtime.execute_episode(e['id'])
    result=store.get(e['id'],False)
    target=result['unit_ids'][1]
    assert calls.count(target)==2
    assert all(calls.count(uid)==1 for uid in result['unit_ids'] if uid!=target)
    repaired=store.get(target,False)
    assert repaired['production']['gpu_seconds']==4
    assert repaired['delivery_history']==[reviews[0][1]['delivery']]
    assert result['status']=='awaiting_final' and not result.get('accepted_by')
    assert result['continuity_repair']['status']=='passed'
    assert result['production']['gpu_seconds']==10


def test_three_failed_reviews_preserve_history_and_stop_without_assembly(isolated,monkeypatch):
    p,e,calls=setup_runtime(monkeypatch)
    monkeypatch.setattr(drama_continuity,'review',lambda ep,units,*args:(_ for _ in ()).throw(rejection(units)))
    monkeypatch.setattr(episode_assembly,'assemble',lambda *args:pytest.fail('failed continuity must not assemble'))
    with pytest.raises(workflows.ReviewBlocked,match='额度用尽'):
        drama_runtime.execute_episode(e['id'])
    result=store.get(e['id'],False)
    target=result['unit_ids'][1]
    assert calls.count(target)==3 and all(calls.count(uid)==1 for uid in result['unit_ids'] if uid!=target)
    assert result['continuity_repair']['round']==3
    assert result['continuity_repair']['status']=='exhausted'
    assert len(result['continuity_repair_history'])==3
    assert result['continuity_repair_history'][-1]['returned_to']==[]
    assert store.get(target,False)['production']['gpu_seconds']==6
    assert len(store.get(target,False)['delivery_history'])==2
    assert not result.get('delivery')
    # Explicit retry starts a fresh allowance; snapshots and historical media stay.
    store.update(e['id'],{'status':'blocked'})
    root=dramas.snapshot(p['id'])
    dramas.control(p['id'],{'expected_revision':root['revision'],'request_id':'retry-continuity',
        'action':'retry','episode_id':e['id']})
    resumed=store.get(e['id'],False)
    assert resumed['continuity_repair_cycle']==2 and resumed['continuity_repair'] is None
    assert len(resumed['continuity_repair_history'])==3


def test_pause_after_return_resumes_unit_without_spending_an_extra_repair(isolated,monkeypatch):
    p,e,calls=setup_runtime(monkeypatch)
    review_calls=[]
    def review(ep,units,*args):
        review_calls.append(1)
        if len(review_calls)==1:raise rejection(units)
        return {'status':'pass'}
    monkeypatch.setattr(drama_continuity,'review',review)
    original=long_episodes.return_continuity_issue
    def pause_after_return(ep,failure):
        value=original(ep,failure)
        store.update(ep['id'],{'pause_requested':True})
        return value
    monkeypatch.setattr(long_episodes,'return_continuity_issue',pause_after_return)
    with pytest.raises(Exception,match='暂停'):drama_runtime.execute_episode(e['id'])
    before=store.get(e['id'],False)
    assert len(before['continuity_repair_history'])==1
    store.update(e['id'],{'pause_requested':False})
    drama_runtime.execute_episode(e['id'])
    after=store.get(e['id'],False)
    assert after['status']=='awaiting_final'
    assert len(after['continuity_repair_history'])==1
    assert calls.count(after['unit_ids'][1])==2


def test_non_continuity_failure_does_not_trigger_generation(isolated,monkeypatch):
    p,e,calls=setup_runtime(monkeypatch)
    monkeypatch.setattr(drama_continuity,'review',lambda *args:(_ for _ in ()).throw(workflows.ReviewBlocked('服务异常',{'type':'runtime'})))
    with pytest.raises(workflows.ReviewBlocked,match='服务异常'):drama_runtime.execute_episode(e['id'])
    result=store.get(e['id'],False)
    assert len(calls)==4 and not result.get('continuity_repair_history')


def test_explicit_scene_retry_uses_new_generation_and_saved_feedback(isolated, monkeypatch):
    p,e,calls = setup_runtime(monkeypatch)
    store.update(e['id'], {'status':'blocked','unit_ids':[], 'scene_generation':1,
        'needs_decision':{'type':'episode_scenes','issues':['场景时长不符']}})
    root=dramas.snapshot(p['id'])
    dramas.control(p['id'], {'expected_revision':root['revision'],'request_id':'scene-retry',
        'action':'retry','episode_id':e['id']})
    resumed=store.get(e['id'],False)
    assert resumed['scene_generation']==2
    assert resumed['scene_feedback']=='场景时长不符'
    stages=[]
    original=long_episodes.call
    def call(ep,cfg,role,stage,instructions,payload,*args):
        stages.append(stage)
        if role=='screenwriter':assert payload['producer_feedback']=='场景时长不符'
        return original(ep,cfg,role,stage,instructions,payload,*args)
    monkeypatch.setattr(long_episodes,'call',call)
    long_episodes.plan_units(resumed, resumed['execution_config'])
    assert stages==['episode-scenes-2-1','episode-scenes-review-2-1']
