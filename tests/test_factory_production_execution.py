import copy
import json

import pytest

from factory import drama_automation, dramas, store
from factory.production_execution import DEFAULT_POLICY
from studio import core
from test_factory_dramas import isolated
from test_factory_drama_automation import create, reviewed, complete_jobs, action


def legacy_run():
    p=create();reviewed(p['id']);complete_jobs(p['id']);drama_automation.recover()
    p=dramas.snapshot(p['id']);p=action(p,'pause')
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        root=store._read(db,p['id']);root['automation'].pop('production_recovery',None);store._write(db,root)
        for eid in root['episodes']:
            e=store._read(db,eid);e['execution_config'].setdefault('production',{}).pop('production_recovery',None)
            e['production']={'gpu_seconds':600,'elapsed_seconds':700,'remote_gpu_receipts':{'old':600}}
            e['budget'].update(gpu_minutes=200 if e['episode_number']==1 else 120,spent_gpu_minutes=10)
            store._write(db,e)
    return dramas.snapshot(p['id'])


def test_new_automation_freezes_recovery_policy_without_waiting_for_browser(isolated):
    p=create();assert p['automation']['production_recovery']==DEFAULT_POLICY
    reviewed(p['id']);complete_jobs(p['id']);drama_automation.recover()
    root=dramas.snapshot(p['id'])
    for eid in [e['id'] for e in root['episodes']]:
        assert store.get(eid,False)['execution_config']['production']['production_recovery']==DEFAULT_POLICY


def test_explicit_upgrade_keeps_mixed_caps_costs_snapshots_and_status(isolated):
    p=legacy_run();before={e['id']:store.get(e['id'],False) for e in p['episodes']}
    upgraded=action(p,'enable_autonomous_repair',key='apply-policy')
    assert upgraded['status']=='paused' and upgraded['automation']['suspended']
    assert upgraded['automation']['production_recovery']==DEFAULT_POLICY
    for eid,old in before.items():
        new=store.get(eid,False);assert new['budget']==old['budget'] and new['production']==old['production']
        expected=copy.deepcopy(old['execution_config']);expected['production']['production_recovery']=DEFAULT_POLICY
        assert new['execution_config']==expected
        assert new['status']==old['status']
    assert 'never-return-this' not in json.dumps(upgraded)
    again=dramas.control(p['id'],{'action':'enable_autonomous_repair','request_id':'apply-policy','expected_revision':p['revision']})
    assert len(again['execution_policy_authorizations'])==1
    with store.connection() as db:assert db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]==0


@pytest.mark.parametrize('condition',['running','unresolved','not_paused'])
def test_upgrade_requires_safe_boundary_and_does_not_start_work(isolated,condition):
    p=legacy_run();eid=p['episodes'][0]['id']
    if condition=='running':
        with store.connection() as db:store._enqueue(db,eid,'drama_episode')
    elif condition=='unresolved':
        store.update(eid,{'production':{'resource_wait':{'status':'waiting','job_id':'owned-original'}}})
    else:
        with store.connection() as db:
            root=store._read(db,p['id']);root['pause_requested']=False;store._write(db,root)
        p=dramas.snapshot(p['id'])
    with pytest.raises(ValueError):action(p,'enable_autonomous_repair')
    assert not dramas.snapshot(p['id'])['automation'].get('production_recovery')


def test_upgrade_rejects_unrequested_budget_or_scope_fields(isolated):
    p=legacy_run()
    with pytest.raises(ValueError,match='未知字段'):
        action(p,'enable_autonomous_repair',budget={'gpu_minutes':999})


def journal_for(p, *, root=False):
    pid = p['id'] if root else p['episodes'][0]['id']
    folder = core.ROOT / 'production' / ('factory-' + pid) / 'renders/S02/take_001'
    folder.mkdir(parents=True, exist_ok=True)
    return pid, folder


def failed_business_job(pid):
    with store.connection() as db:
        store._enqueue(db,pid,'workflow')
        db.execute("UPDATE jobs SET status='failed' WHERE project_id=?",(pid,))


@pytest.mark.parametrize('state', ['dispatching', 'running', 'unresolved', 'missing_receipt', 'wrong_id'])
def test_lost_business_job_does_not_authorize_upgrade_over_unknown_gpu(isolated,state,monkeypatch):
    p=legacy_run();pid,folder=journal_for(p);ident='a'*64
    failed_business_job(pid)
    core.write_json(folder/'submission.json',{'state':'running','gpu_service_job_id':ident})
    if state != 'dispatching':
        answer={'id':ident,'status':state if state in {'running','unresolved'} else 'failed','receipt':{'gpu_seconds':12}}
        if state=='missing_receipt':answer.pop('receipt')
        if state=='wrong_id':answer['id']='b'*64
        core.write_json(folder/'remote_result.json',answer)
    # A local exception file alone never proves remote termination or billing.
    core.write_json(folder/'submission_error.json',{'job_id':ident,'status':'failed'})
    before={eid:store.get(eid,False) for eid in [p['id'],*(e['id'] for e in p['episodes'])]}
    journals={path:path.read_bytes() for path in folder.glob('*.json')}
    monkeypatch.setattr('factory.gpu_client.GPUClient.request',lambda *a,**k:pytest.fail('Upgrade must remain read-only'))
    with pytest.raises(ValueError,match='对账'):action(p,'enable_autonomous_repair')
    assert all(store.get(eid,False)==old for eid,old in before.items())
    assert all(path.read_bytes()==old for path,old in journals.items())


@pytest.mark.parametrize('terminal', ['completed','failed'])
def test_proven_gpu_terminal_receipt_overrides_stale_rendering_journal(isolated,terminal,monkeypatch):
    p=legacy_run();pid,folder=journal_for(p);ident='a'*64
    failed_business_job(pid)
    core.write_json(folder/'attempt.json',{'state':'rendering','attempt':1})
    core.write_json(folder/'submission.json',{'state':'running','gpu_service_job_id':ident})
    core.write_json(folder/'remote_result.json',{'id':ident,'status':terminal,'result':{'receipt':{'gpu_seconds':12}}})
    store.update(pid,{'production':{**store.get(pid,False)['production'],
                                  'remote_gpu_execution':{'status':'waiting','job_id':ident}}})
    old=store.get(pid,False)
    journals={path:path.read_bytes() for path in folder.glob('*.json')}
    monkeypatch.setattr('factory.gpu_client.GPUClient.request',lambda *a,**k:pytest.fail('No reconciliation network calls'))
    upgraded=action(p,'enable_autonomous_repair')
    assert upgraded['status']=='paused'
    new=store.get(pid,False)
    assert new['production']==old['production'] and new['budget']==old['budget']
    assert all(path.read_bytes()==data for path,data in journals.items())


@pytest.mark.parametrize('valid', [True, False])
def test_gpu_downloaded_media_receipt_requires_matching_bytes_and_billing(isolated,valid):
    p=legacy_run();_,folder=journal_for(p);ident='a'*64
    core.write_json(folder/'submission.json',{'state':'running','gpu_service_job_id':ident})
    media=folder/'image.png';media.write_bytes(b'completed media fixture')
    core.write_json(folder/'result.json',core.media_receipt(media,gpu_service_job_id=ident,gpu_seconds=12))
    if not valid:media.write_bytes(b'changed bytes')
    if valid:
        assert action(p,'enable_autonomous_repair')['status']=='paused'
    else:
        with pytest.raises(ValueError,match='对账'):action(p,'enable_autonomous_repair')


def test_terminal_display_does_not_settle_an_explicitly_unfinished_bill(isolated):
    p=legacy_run();_,folder=journal_for(p);ident='a'*64
    core.write_json(folder/'submission.json',{'state':'completed','gpu_service_job_id':ident})
    core.write_json(folder/'remote_result.json',{'id':ident,'status':'completed',
        'result':{'receipt':{'gpu_seconds':0,'usage_final':False}}})
    with pytest.raises(ValueError,match='对账'):action(p,'enable_autonomous_repair')


@pytest.mark.parametrize('proof', ['none','valid_media','tampered_media','wrong_prompt','error_history','confirmed_cancel'])
def test_external_comfy_requires_actual_terminal_proof(isolated,proof):
    p=legacy_run();pid,folder=journal_for(p)
    state={'state':'accepted','prompt_id':'owned-prompt'}
    if proof=='confirmed_cancel':
        state.update(state='cancelled',stop_confirmed_at=123,owned_queue_state='absent',owned_usage={'execution_seconds':2})
    core.write_json(folder/'submission.json',state)
    if proof in {'valid_media','tampered_media','wrong_prompt'}:
        media=folder/'video.mp4';media.write_bytes(b'owned completed fixture media')
        core.write_json(folder/'result.json',core.media_receipt(media,prompt_id='different' if proof=='wrong_prompt' else state['prompt_id']))
        if proof=='tampered_media':media.write_bytes(b'tampered')
    if proof=='error_history':core.write_json(folder/'history.json',{'status':{'status_str':'error'}})
    if proof in {'valid_media','error_history','confirmed_cancel'}:
        assert action(p,'enable_autonomous_repair')['automation']['production_recovery']==DEFAULT_POLICY
    else:
        with pytest.raises(ValueError,match='对账'):action(p,'enable_autonomous_repair')


def test_unknown_shared_drama_request_is_guarded_even_without_unit_job(isolated):
    p=legacy_run();_,folder=journal_for(p,root=True)
    core.write_json(folder/'submission.json',{'gpu_service_job_id':'a'*64,'state':'dispatching'})
    with pytest.raises(ValueError,match='对账'):action(p,'enable_autonomous_repair')


@pytest.mark.parametrize('status', ['submit_unknown','running','downloaded','failed'])
def test_optional_cloud_ledger_blocks_unresolved_provider_tasks(isolated,status):
    p=legacy_run();pid=p['episodes'][0]['id']
    with store.connection() as db:
        db.execute('INSERT INTO cloud_tasks(intent_id,project_id,shot_id,attempt,take,provider,model,request_signature,status,currency,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   ('fixture-intent',pid,'S02',1,'take_001','fixture-provider','fixture-model','fixture-signature',status,'CNY',1,1))
    if status in {'downloaded','failed'}:
        assert action(p,'enable_autonomous_repair')['status']=='paused'
    else:
        with pytest.raises(ValueError,match='对账'):action(p,'enable_autonomous_repair')


def test_missing_optional_cloud_table_does_not_block_clean_legacy_project(isolated):
    from factory.production_execution import _guard_remote_evidence
    import sqlite3
    with sqlite3.connect(':memory:') as db:
        _guard_remote_evidence(db,{'id':'abcdef123456','production':{}})
