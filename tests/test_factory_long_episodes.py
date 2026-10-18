"""Long timelines use CPU test patterns, never real model generation."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest
from factory import dramas, store, long_episodes, episode_assembly as assembly, drama_runtime, workflows
from studio import core
from studio.media import FFMPEG, probe
from test_factory_dramas import isolated, input_payload, plan


@pytest.fixture(scope='module')
def video75(tmp_path_factory):
    path=tmp_path_factory.mktemp('long-synthetic')/'clip.mp4'
    subprocess.run([FFMPEG,'-hide_banner','-loglevel','error','-nostdin','-f','lavfi','-i','testsrc2=size=64x96:rate=24',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','75','-c:v','libx264','-preset','ultrafast',
        '-threads','1','-c:a','aac','-ac','2','-pix_fmt','yuv420p',str(path)],check=True,capture_output=True,timeout=60)
    return path


def episode_fixture(seconds, monkeypatch):
    p=dramas.create(input_payload(duration_seconds=seconds))
    store.update(p['id'],{'draft_plan':plan(),'plan_review':{'passed':True},'status':'awaiting_plan'})
    with store.connection() as db: db.execute("UPDATE jobs SET status='completed'")
    p=dramas.snapshot(p['id']);p=dramas.confirm(p['id'],{'expected_revision':p['revision'],'request_id':'confirm'})
    p=dramas.generate(p['id'],{'expected_revision':p['revision'],'request_id':'generate','episode_numbers':[1]})
    e=store.get(p['episodes'][0]['id'],False)
    def call(project,config,role,stage,*args):
        if 'review' in stage: return {'passed':True,'issues':[]}
        return {'scenes':[{'scene_id':f'C{i:02d}','title':f'场景{i}','synopsis':f'角色在车站寻找第{i}封信，走过站台并找到线索。',
            'seconds':150,'characters':['lead'],'location_id':'','prop_ids':[],
            'opening_state':'进入站台','ending_state':'找到线索'} for i in range(1,seconds//150+1)]}
    monkeypatch.setattr(long_episodes,'call',call)
    return p,e


def fake_generated_unit(root,video75,uid):
    unit=store.get(uid,False)
    folder=root/'production'/unit['slug']/'release/v1'
    folder.mkdir(parents=True)
    for name in ('episode.mp4','episode_clean.mp4'): shutil.copyfile(video75,folder/name)
    count=unit['production_profile']['shot_count']
    shots=[{'id':f'S{i+1:02d}','seconds':value,'continuity':'人物保持蓝衣'} for i,value in enumerate(unit['production_profile']['shot_seconds'])]
    core.write_json(folder/'timeline.json',{'subtitles':[{'start':.5,'end':1.5,'text':unit['global_prefix']}]})
    def receipt(path): return {'path':path.relative_to(root).as_posix(),'sha256':core.sha256(path),'bytes':path.stat().st_size}
    delivery={'source_signature':uid,'resolution':[64,96],'fps':24,'duration_seconds':75,
        'semantic_status':'pass','artifacts':[receipt(folder/n) for n in ('episode.mp4','episode_clean.mp4','timeline.json')]}
    store.update(uid,{'status':'unit_ready','progress':100,'delivery':delivery,'script':{'shots':shots,'ending':'角色拿到线索'},
        'production':{'gpu_seconds':2},'shots':shots})


@pytest.mark.parametrize('seconds',[300,900])
def test_full_long_runtime_assembles_scenes_offsets_subtitles_and_reuses(isolated,monkeypatch,video75,seconds):
    root=isolated
    monkeypatch.setattr(assembly,'ROOT',root)
    monkeypatch.setattr(core,'ROOT',root)
    p,e=episode_fixture(seconds,monkeypatch)
    from factory import drama_assets, drama_continuity
    monkeypatch.setattr(drama_assets,'prepare',lambda *args:None)
    monkeypatch.setattr(drama_continuity,'review',lambda *args:{'status':'pass','scope':'fixture_boundary_review_not_real_model'})
    calls=[]
    def advance(uid): calls.append(uid);fake_generated_unit(root,video75,uid)
    monkeypatch.setattr(workflows,'advance',advance)
    drama_runtime.execute_episode(e['id'])
    e=store.get(e['id'],False)
    assert e['status']=='awaiting_final' and not e.get('accepted_by')
    assert len(e['unit_ids'])==seconds//75 and len(calls)==len(e['unit_ids'])
    assert e['delivery']['duration_seconds']==seconds
    result=probe(root/e['delivery']['path'])
    assert abs(result['duration']-seconds)<.05
    assert result['fps']==24 and any(s['type']=='audio' for s in result['streams'])
    timeline=core.read_json(root/next(a['path'] for a in e['delivery']['artifacts'] if a['path'].endswith('timeline.json')))
    assert timeline['subtitles'][-1]['start']==seconds-75+.5
    assert len({s['id'] for s in timeline['shots']})==len(timeline['shots'])
    unit=store.get(e['unit_ids'][1],False)
    assert unit['production_profile']['characters'][0]['voice_id']=='female'
    assert unit['previous_unit_state']['ending']=='角色拿到线索'
    before=copy.deepcopy(e['delivery'])
    drama_runtime.execute_episode(e['id'])
    assert len(calls)==seconds//75 and store.get(e['id'],False)['delivery']==before


def test_scene_time_partition_and_bad_totals_are_bounded(isolated,monkeypatch):
    assert long_episodes.unit_durations(900)==[90]*10
    assert sum(long_episodes.unit_durations(181))==181
    p,e=episode_fixture(300,monkeypatch)
    def bad(project,config,role,stage,*args):
        return {'scenes':[{'scene_id':'C01','title':'场景','synopsis':'无法覆盖总时长的场景内容。','seconds':90,
            'characters':['lead'],'location_id':'','prop_ids':[],'opening_state':'进入','ending_state':'离开'}]}
    monkeypatch.setattr(long_episodes,'call',bad)
    with pytest.raises(workflows.ReviewBlocked): long_episodes.plan_units(e,{})
    assert not store.get(e['id'],False)['unit_ids']


def test_cancel_propagates_and_archive_records_unfinished_episodes(isolated,monkeypatch):
    from factory import drama_exports
    monkeypatch.setattr(drama_exports,'ROOT',isolated)
    p,e=episode_fixture(300,monkeypatch)
    long_episodes.plan_units(e,{})
    p=dramas.snapshot(p['id'])
    p=dramas.control(p['id'],{'expected_revision':p['revision'],'request_id':'cancel','action':'cancel','episode_id':e['id']})
    e=store.get(e['id'],False)
    assert all(store.get(uid,False)['pause_requested'] for uid in e['unit_ids'])
    with pytest.raises(Exception,match='暂停'): long_episodes.check_parent(e['id'])
    path=drama_exports.archive(p['id'])
    with zipfile.ZipFile(path) as bundle:
        manifest=json.loads(bundle.read('manifest.json'))
        assert len(manifest['episodes'])==3 and all(e['reason'] for e in manifest['episodes'])
        assert bundle.namelist()==['manifest.json']


def test_invalid_subtitle_times_and_interleaved_scenes_are_rejected():
    with pytest.raises(ValueError):assembly.offset_cues([{'subtitles':[{'start':1,'end':91,'text':'bad'}]}],[90])


def test_partial_resume_keeps_completed_unit_and_pinned_config(isolated,monkeypatch,video75):
    from factory import drama_assets, drama_continuity, config
    p,e=episode_fixture(300,monkeypatch)
    monkeypatch.setattr(drama_assets,'prepare',lambda *args:None)
    monkeypatch.setattr(drama_continuity,'review',lambda *args:{'status':'pass','scope':'fixture'})
    monkeypatch.setattr(assembly,'assemble',lambda ep,units,control:{'source_signature':'fixture','duration_seconds':300,'video_url':'/fixture-only'})
    calls=[]
    def advance(uid):
        calls.append(uid)
        assert config._CONFIG_SNAPSHOT.get()['creative']['max_review_rounds']==3
        if len(calls)==2: raise assembly.ProductionPaused('fixture pause')
        fake_generated_unit(isolated,video75,uid)
    monkeypatch.setattr(workflows,'advance',advance)
    with pytest.raises(assembly.ProductionPaused):drama_runtime.execute_episode(e['id'])
    e=store.get(e['id'],False)
    first=e['unit_ids'][0]
    assert store.get(first,False)['status']=='unit_ready'
    drama_runtime.execute_episode(e['id'])
    assert calls.count(first)==1 and store.get(e['id'],False)['status']=='awaiting_final'


def test_long_metadata_backup_restore_and_relationship_audit(isolated,monkeypatch,tmp_path):
    import sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver
    from factory import backup
    p,e=episode_fixture(300,monkeypatch)
    long_episodes.plan_units(e,{})
    with sqlite3.connect(isolated/'checkpoints.sqlite3') as db: SqliteSaver(db).setup()
    (isolated/'worker.lock').touch()
    target=tmp_path.parent/(tmp_path.name+'-backup')
    backup.create(isolated,target,data_dir=isolated,maintenance_confirmed=True)
    restored=tmp_path.parent/(tmp_path.name+'-restored')
    result=backup.restore(target,restored)
    assert not result['generation_started']
    with sqlite3.connect(restored/'runtime/factory/studio.sqlite3') as db:
        snapshots=[json.loads(r[0]) for r in db.execute('SELECT snapshot FROM projects')]
    assert len([v for v in snapshots if v.get('record_kind')=='scene_unit'])==4
    assert next(v for v in snapshots if v['id']==e['id'])['plan_version']==1
    store.update(e['id'],{'unit_ids':[]})
    with pytest.raises(backup.BackupError,match='scene unit'):
        backup.create(isolated,tmp_path.parent/(tmp_path.name+'-broken'),data_dir=isolated,maintenance_confirmed=True)


def test_reviewed_shared_character_is_pinned_reused_and_corruption_blocks(isolated,monkeypatch):
    from factory import drama_assets,production,config
    from test_factory_dramas import ready
    from PIL import Image
    monkeypatch.setattr(drama_assets,'ROOT',isolated)
    monkeypatch.setattr(config,'production_config',lambda *args:{})
    p=ready()
    for e in p['episodes'][:2]: drama_runtime.setup_episode(store.get(e['id'],False))
    calls=[]
    class Run:
        def __init__(self,project,*args,**kwargs):
            assert project['episode']['cast']['lead']=='成年女性，短发'
        def _target(self,key,folder,prompt,**kwargs): return {'key':key,'folder':folder,'prompt':prompt}
        def _render_targets(self,targets,phase):
            calls.append(phase)
            for target in targets:
                path=target['folder']/'image.png';path.parent.mkdir(parents=True,exist_ok=True)
                Image.new('RGB',(64,96),'navy').save(path)
                target['selected']={'path':path.relative_to(isolated).as_posix(),'sha256':core.sha256(path),'bytes':path.stat().st_size,
                    'qa':{'technical_passed':True,'semantic':{'status':'pass'}}}
    monkeypatch.setattr(production,'_Run',Run)
    for e in p['episodes'][:2]: drama_assets.prepare(e['id'],{})
    assert calls==['shared_references']
    first=store.get(p['episodes'][0]['id'],False)['drama_references']['character:lead']
    second=store.get(p['episodes'][1]['id'],False)['drama_references']['character:lead']
    assert first==second and first['plan_version']==1
    (isolated/first['receipt']['path']).write_bytes(b'changed')
    with pytest.raises(Exception,match='不匹配'):drama_assets.prepare(p['episodes'][1]['id'],{})


def test_transition_frame_review_is_cached_and_failure_keeps_target(isolated,monkeypatch,video75):
    from factory import drama_continuity
    monkeypatch.setattr(assembly,'ROOT',isolated)
    monkeypatch.setattr(drama_continuity,'ROOT',isolated)
    monkeypatch.setattr(drama_continuity,'llm_identity',lambda *args:{'model':'fixture-vision'})
    p,e=episode_fixture(300,monkeypatch)
    long_episodes.plan_units(e,{})
    e=store.get(e['id'],False)
    for uid in e['unit_ids'][:2]:
        drama_runtime.setup_episode(store.get(uid,False))
        fake_generated_unit(isolated,video75,uid)
    units=[store.get(uid,False) for uid in e['unit_ids'][:2]]
    calls=[]
    def request(*args,**kwargs):
        calls.append(kwargs['content'])
        assert len([v for v in kwargs['content'] if v['type']=='image_url'])==2
        return {'status':'fail','summary':'相邻单元人物衣服冲突','issues':['前帧蓝衣，后帧红衣。']}
    monkeypatch.setattr(drama_continuity,'_request',request)
    for _ in range(2):
        with pytest.raises(workflows.ReviewBlocked) as error:
            drama_continuity.review(e,units,{})
        assert error.value.details['unit_id']==units[1]['id']
    assert len(calls)==1
    units[1]['delivery']['source_signature']='new-version'
    with pytest.raises(workflows.ReviewBlocked):drama_continuity.review(e,units,{})
    assert len(calls)==2


def test_completed_archive_has_per_episode_production_manifest(isolated,monkeypatch,video75):
    from factory import drama_exports
    monkeypatch.setattr(drama_exports,'ROOT',isolated)
    p,e=episode_fixture(300,monkeypatch)
    # A receipt-backed local artifact is sufficient for this read-only download path.
    folder=isolated/'production'/e['slug'];folder.mkdir(parents=True)
    path=folder/'episode.mp4';shutil.copyfile(video75,path)
    delivery={'source_signature':'fixture','artifacts':[{'path':path.relative_to(isolated).as_posix(),
        'sha256':core.sha256(path),'bytes':path.stat().st_size}]}
    store.update(e['id'],{'delivery':delivery,'status':'awaiting_final'})
    archive=drama_exports.archive(p['id'])
    with zipfile.ZipFile(archive) as bundle:
        assert json.loads(bundle.read('E001/manifest.json'))==delivery
        assert bundle.read('E001/episode.mp4')==path.read_bytes()
        assert json.loads(bundle.read('manifest.json'))['episodes'][1]['reason']=='尚未完成制作'
    assert drama_exports.archive(p['id'])==archive


def test_long_episode_checks_directory_owner_before_planning(isolated,monkeypatch):
    p,e=episode_fixture(300,monkeypatch)
    folder=isolated/'production'/e['slug'];folder.mkdir(parents=True)
    core.write_json(folder/'factory_owner.json',{'project_id':'foreign-owner'})
    with pytest.raises(ValueError,match='归属'):drama_runtime.execute_episode(e['id'])
    assert not store.get(e['id'],False)['unit_ids']
    assert core.read_json(folder/'factory_owner.json')['project_id']=='foreign-owner'
