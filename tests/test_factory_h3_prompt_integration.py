"""H3 automatic prompting exercises real receipts/QA with CPU media fixtures."""
import contextlib
import copy
import subprocess

from PIL import Image, ImageDraw
import pytest

from factory import production, scheduler, config as configuration, h3_prompting
from factory.video_providers import H3_PRODUCTION_PROFILE
from factory.shot_workbench import plan_signature
from studio import core


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    monkeypatch.setattr(production, 'ROOT', tmp_path)
    events, compiles, executions = [], [], []
    active = [False]
    def picture(path, variant=0):
        path.parent.mkdir(parents=True, exist_ok=True)
        im = Image.new('RGB', (160, 288), 'navy')
        draw = ImageDraw.Draw(im)
        for x in range(0, 160, 10):
            draw.rectangle((x, 0, x+4, 287), fill=(40+x, 80+variant, 180-x))
        im.save(path)
        return core.media_receipt(path)
    frame = tmp_path / 'frame.png'
    image = picture(frame)
    shot = {'id':'S01', 'seconds':3, 'prompt':'An adult in a blue shirt holds a white cup.',
            'action':'Turn toward the window and smile, keeping the cup on the table.',
            'continuity':'The cup stays on the table.', 'camera_motion':'hold', 'image':image}
    project = {'id':'h3automatic01', 'format':'live_action',
               'episode':{'cast':{'lead':'Adult in a blue shirt'}, 'shots':[shot]}}
    cfg = {'video_connection':{'profile':'local_h3', **H3_PRODUCTION_PROFILE},
           'h3_prompting':'automatic', 'h3_prompt_config':{'llm':{'model':'chosen-director'}},
           'image_width':160,'image_height':288,'video_width':160,'video_height':288,
           'semantic_enabled':False,'max_attempts':2}
    monkeypatch.setattr(h3_prompting, 'identity', lambda c: {'model':c['llm']['model'], 'version':'fixture-v1'})
    def compile_prompt(project, shot, **kwargs):
        assert active[0] is False, 'Director was called while a GPU worker was held'
        compiles.append((copy.deepcopy(shot), copy.deepcopy(kwargs)))
        events.append('compile')
        prompt = f'Compiled approved action, take {kwargs["attempt"]}: {shot["action"]}'
        return {'prompt':prompt, 'receipt':{'schema':'fixture-compiler',
                'first_frame_sha256':kwargs['references'][0]['sha256'],
                'quality_status':'pending_actual_media_review'}}
    monkeypatch.setattr(h3_prompting, 'compile_prompt', compile_prompt)
    class Worker:
        def copy_input(self, path):
            return str(path)
        def execute(self, graph, destination, kind, **kwargs):
            executions.append((kind, copy.deepcopy(graph)))
            if kind == 'image':
                path = destination / 'image.png'
                receipt = picture(path, variant=20)
            else:
                from factory.audio_editor import FFMPEG
                path = destination / 'video.mp4'
                path.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run([FFMPEG,'-hide_banner','-loglevel','error','-nostdin','-y',
                    '-f','lavfi','-i','testsrc=size=160x288:rate=24',
                    '-frames:v',str(graph['6']['inputs']['length']),'-an','-c:v','libx264',
                    '-threads','1','-pix_fmt','yuv420p',str(path)],check=True,capture_output=True,timeout=15)
                receipt = core.media_receipt(path, origin='cpu_fixture_not_h3_inference')
            core.write_json(destination / 'result.json', receipt)
            return path
    @contextlib.contextmanager
    def worker(self, tag, control=None, **kwargs):
        assert not active[0]
        active[0] = True
        events.append('gpu_enter')
        try:
            yield Worker()
        finally:
            active[0] = False
            events.append('gpu_exit')
    monkeypatch.setattr(scheduler.Scheduler, 'worker', worker)
    def target(run):
        s = run.shots[0]
        return run._target('S01', run.folder / 'renders/S01', s['motion_prompt'], [frame],
                           kind='video', seconds=3, shot=s, review=run.semantic_enabled)
    return project, cfg, target, events, compiles, executions, frame


def test_workflow_generates_prompt_without_manual_motion_text_and_reuses_output(setup):
    project, cfg, target, events, compiles, executions, frame = setup
    assert 'motion_prompt' not in project['episode']['shots'][0]
    run = production._Run(project, cfg, None, None)
    selected = run._render_targets([target(run)], 'diffusion_video')[0]
    assert events.index('compile') < events.index('gpu_enter')
    assert executions[0][1]['6']['inputs']['prompt'].startswith('Compiled approved action')
    assert compiles[0][0]['action'] == project['episode']['shots'][0]['action']
    assert selected['h3_prompt']['first_frame_sha256'] == core.sha256(frame)
    again = production._Run(run.project, cfg, None, None)
    assert again._render_targets([target(again)], 'diffusion_video')[0]['sha256'] == selected['sha256']
    assert len(compiles) == len(executions) == 1


def test_compiler_failure_consumes_no_take_or_gpu(setup, monkeypatch):
    project, cfg, target, events, compiles, executions, _ = setup
    def unavailable(*args, **kwargs):
        raise scheduler.ProductionNeedsDecision('Director unavailable', 'h3_prompt_unavailable')
    monkeypatch.setattr(h3_prompting, 'compile_prompt', unavailable)
    run = production._Run(project, cfg, None, None)
    current = target(run)
    with pytest.raises(scheduler.ProductionNeedsDecision) as error:
        run._render_targets([current], 'diffusion_video')
    assert error.value.reason == 'h3_prompt_unavailable'
    assert not executions and 'gpu_enter' not in events
    assert not list(current['folder'].glob('take_*/attempt.json'))


def test_failed_video_repairs_prompt_after_replacement_frame_gpu_is_released(setup):
    project, cfg, target, events, compiles, executions, frame = setup
    cfg['semantic_enabled'] = True
    reviews = iter([{'status':'fail','score':40,'issues':[{'code':'action','severity':'blocking','detail':'Wrong action'}]},
                    {'status':'pass','score':90,'issues':[]}])
    run = production._Run(project, cfg, None, None, evaluator=lambda *a: next(reviews))
    selected = run._render_targets([target(run)], 'diffusion_video')[0]
    assert [kind for kind, _ in executions] == ['video','image','video']
    assert len(compiles) == 2
    assert compiles[1][1]['previous_prompt'].startswith('Compiled approved action, take 1')
    assert compiles[1][1]['repair']['diagnosis']['primary_code'] == 'action'
    assert compiles[1][1]['references'][0]['sha256'] != core.sha256(frame)
    assert selected['h3_prompt']['first_frame_sha256'] == compiles[1][1]['references'][0]['sha256']
    assert events == ['compile','gpu_enter','gpu_exit','gpu_enter','gpu_exit','compile','gpu_enter','gpu_exit']


def test_pending_repair_prompt_resumes_same_take_without_extra_frame_generation(setup, monkeypatch):
    project, cfg, target, events, compiles, executions, _ = setup
    cfg['semantic_enabled'] = True
    original = h3_prompting.compile_prompt
    unavailable = [True]
    def compile_prompt(*args, **kwargs):
        if kwargs['attempt'] == 2 and unavailable[0]:
            raise scheduler.ProductionNeedsDecision('Temporary director outage','h3_prompt_unavailable')
        return original(*args, **kwargs)
    monkeypatch.setattr(h3_prompting, 'compile_prompt', compile_prompt)
    run = production._Run(project, cfg, None, None, evaluator=lambda *a: {
        'status':'fail','score':40,'issues':[{'code':'action','severity':'blocking'}]})
    with pytest.raises(scheduler.ProductionNeedsDecision):
        run._render_targets([target(run)], 'diffusion_video')
    assert [kind for kind, _ in executions] == ['video','image']
    assert core.read_json(run.folder/'renders/S01/take_002/attempt.json')['state'] == 'prompt_preparing'
    unavailable[0] = False
    resumed = production._Run(run.project, cfg, None, None, evaluator=lambda *a:{'status':'pass','score':90,'issues':[]})
    selected = resumed._render_targets([target(resumed)], 'diffusion_video')[0]
    assert selected['take'] == 'take_002'
    assert [kind for kind, _ in executions] == ['video','image','video']


def test_prepared_frozen_prompt_resumes_without_recompilation(setup, monkeypatch):
    project, cfg, target, events, compiles, executions, _ = setup
    run = production._Run(project, cfg, None, None)
    current = target(run)
    record = run._new_attempt(current)
    assert len(compiles) == 1 and not executions
    monkeypatch.setattr(h3_prompting, 'compile_prompt', lambda *a, **k: pytest.fail('Frozen prompt was recompiled'))
    resumed = production._Run(run.project, cfg, None, None)
    selected = resumed._render_targets([target(resumed)], 'diffusion_video')[0]
    assert selected['take'] == record['take']
    assert executions[0][1]['6']['inputs']['prompt'] == record['prompt']


def test_unresolved_remote_submission_blocks_prompt_and_new_generation(setup):
    project, cfg, target, events, compiles, executions, _ = setup
    run = production._Run(project, cfg, None, None)
    current = target(run)
    folder = current['folder'] / 'take_001'
    core.write_json(folder/'submission.json', {'state':'submitted'})
    core.write_json(folder/'attempt.json', {'source_signature':'old-compiler-inputs','state':'rendering'})
    with pytest.raises(scheduler.ProductionNeedsDecision) as error:
        run._render_targets([current], 'diffusion_video')
    assert error.value.reason == 'unresolved_submission'
    assert not compiles and not executions


@pytest.mark.parametrize('field,value', [('action','Pick up the cup.'),('continuity','The cup is empty.'),
                                        ('camera_motion','pan_left'),('lip_sync',True)])
def test_approved_input_changes_invalidate_generation_and_plan(setup, field, value):
    project, cfg, target, *_ = setup
    run = production._Run(project, cfg, None, None)
    before = target(run)['signature']
    old_plan = plan_signature(run.project, run.shots[0], 'video')
    run.shots[0][field] = value
    assert target(run)['signature'] != before
    assert plan_signature(run.project, run.shots[0], 'video') != old_plan


def test_director_model_change_invalidates_generation(setup):
    project, cfg, target, *_ = setup
    before = target(production._Run(project, cfg, None, None))['signature']
    cfg['h3_prompt_config']['llm']['model'] = 'another-configured-model'
    assert target(production._Run(project, cfg, None, None))['signature'] != before


def test_first_frame_reassessment_changes_review_but_not_video_inputs(setup):
    project, cfg, target, *_ = setup
    cfg.update(semantic_enabled=True, semantic_model_identity={'context_tokens':8192})
    project['episode']['shots'][0]['image']['qa'] = {'status':'pass', 'semantic':{'summary':'First assessment'}}
    before = target(production._Run(project, cfg, None, None))
    project['episode']['shots'][0]['image']['qa']['semantic'] = {'summary':'New assessment', 'score':95}
    cfg['semantic_model_identity']['context_tokens'] = 32768
    after = target(production._Run(project, cfg, None, None))
    assert before['signature'] == after['signature']
    assert before['review_signature'] != after['review_signature']


def test_video_plan_matches_renderer_duration_normalization(setup):
    project, cfg, target, *_ = setup
    raw = project['episode']['shots'][0]
    raw['seconds'] = 6
    raw['motion_prompt'] = raw['prompt']
    run = production._Run(project, cfg, None, None)
    assert plan_signature(project, raw, 'video') == plan_signature(project, run.shots[0], 'video')
    raw['seconds'] = 6.1
    assert plan_signature(project, raw, 'video') != plan_signature(project, run.shots[0], 'video')


def legacy_pending_video(setup, monkeypatch):
    """Create a real CPU clip with the pre-fix QA-bound signature on disk."""
    project, cfg, target, events, compiles, executions, frame = setup
    cfg.update(semantic_enabled=True, semantic_max_attempts=1)
    old_qa = {'status':'pass', 'semantic':{'status':'pass', 'summary':'Original keyframe assessment'}}
    project['episode']['shots'][0]['image']['qa'] = old_qa
    run = production._Run(project, cfg, None, None, evaluator=lambda *a:{'status':'unavailable','issues':[]})
    original_fingerprint = production.fingerprint
    def old_signature(value):
        if 'h3_prompt_compiler' in value and 'first_frame_review' not in value:
            value = {**value, 'first_frame_review':old_qa}
        return original_fingerprint(value)
    with monkeypatch.context() as old:
        old.setattr(production, 'fingerprint', old_signature)
        current = target(run)
    with pytest.raises(scheduler.ProductionNeedsDecision, match='semantic reviewer'):
        run._render_targets([current], 'diffusion_video')
    for name in ('attempt.json', 'result.json', 'pending_review.json'):
        path = current['folder']/name if name == 'pending_review.json' else current['folder']/'take_001'/name
        value = core.read_json(path)
        value.pop('generation_signature', None)
        core.write_json(path, value)
    return run, cfg, target, current


@pytest.mark.parametrize('with_contract', [False, True])
def test_locked_legacy_pending_video_is_reviewed_without_director_or_gpu(setup, monkeypatch, with_contract):
    if with_contract:
        setup[0]['creative_contract'] = {'schema_version':1, 'visual_style':'photorealistic',
            'story_mode':'standalone', 'pacing':'short_drama', 'motion':'generated_video'}
    run, cfg, target, old = legacy_pending_video(setup, monkeypatch)
    _, _, _, _, compiles, executions, frame = setup
    before = core.sha256(old['folder']/'take_001/video.mp4')
    run.project['episode']['shots'][0]['image']['qa'] = {'status':'pass','semantic':{'summary':'Reassessed'}}
    cfg['semantic_model_identity'] = {'context_tokens':32768}
    def locked(pid, kind, scope, *, check_locks=True):
        if kind == 'video' and check_locks:
            raise production.team_control.TeamInterventionPending('Locked: no new generation')
    monkeypatch.setattr(production.team_control, 'admit', locked)
    reviews = []
    def review(project, shot, path, refs):
        reviews.append(path)
        return {'status':'pass','score':95,'issues':[]}
    resumed = production._Run(run.project, cfg, None, None, evaluator=review)
    current = target(resumed)
    assert old['signature'] in current['compatible_source_signatures']
    selected = resumed._render_targets([current], 'diffusion_video')[0]
    assert selected['source_signature'] == old['signature']  # provenance/locks unchanged
    assert selected['generation_signature'] == current['signature']
    assert selected['take'] == 'take_001' and selected['sha256'] == before
    assert len(reviews) == len(compiles) == len(executions) == 1
    again = production._Run(resumed.project, cfg, None, None, evaluator=review)
    again._render_targets([target(again)], 'diffusion_video')
    assert len(reviews) == len(compiles) == len(executions) == 1


@pytest.mark.parametrize('changed', ['action', 'frame', 'director', 'recipe', 'retake'])
def test_legacy_compatibility_never_ignores_real_generation_changes(setup, monkeypatch, changed):
    run, cfg, target, old = legacy_pending_video(setup, monkeypatch)
    if changed == 'action':
        run.project['episode']['shots'][0]['action'] = 'Leave the cup and walk out.'
    elif changed == 'frame':
        from PIL import Image
        Image.new('RGB',(160,288),'red').save(setup[-1])
    elif changed == 'director':
        cfg['h3_prompt_config']['llm']['model'] = 'different-director'
    elif changed == 'recipe':
        cfg['video_connection']['shift_video'] = 11.0
    else:
        run.project['episode']['shots'][0]['render_revisions'] = {'video':1}
    current = target(production._Run(run.project, cfg, None, None))
    assert old['signature'] not in current['compatible_source_signatures']
    assert current['signature'] != old['signature']


def test_legacy_reuse_still_checks_video_bytes(setup, monkeypatch):
    run, cfg, target, old = legacy_pending_video(setup, monkeypatch)
    (old['folder']/'take_001/video.mp4').write_bytes(b'corrupt')
    resumed = production._Run(run.project, cfg, None, None)
    with pytest.raises(scheduler.ProductionNeedsDecision, match='checksum'):
        resumed._render_targets([target(resumed)], 'diffusion_video')
    assert len(setup[4]) == len(setup[5]) == 1


def test_explicit_manual_mode_preserves_existing_motion_prompt(setup, monkeypatch):
    project, cfg, target, events, compiles, executions, _ = setup
    cfg['h3_prompting'] = 'manual'
    monkeypatch.setattr(h3_prompting,'compile_prompt', lambda *a, **k: pytest.fail('Manual mode called director'))
    run = production._Run(project, cfg, None, None)
    current = target(run)
    run._render_targets([current], 'diffusion_video')
    assert executions[0][1]['6']['inputs']['prompt'] == current['prompt']
    assert not compiles


def test_application_defaults_to_automatic_and_preserves_model_configuration():
    import json
    from pathlib import Path
    cfg = json.loads(Path('config/factory.example.json').read_text())
    cfg['video_roles'] = {'video_generator':'h3'}
    cfg['video_profiles'] = {'h3':copy.deepcopy(H3_PRODUCTION_PROFILE)}
    cfg['production'].pop('h3_prompting', None)
    project = {'budget':{'gpu_minutes':100,'max_attempts':2}}
    settings = configuration.production_config(project, cfg)
    assert settings['h3_prompting'] == 'automatic'
    assert settings['h3_prompt_config']['llm_roles']['storyboard_director'] == cfg['llm_roles']['storyboard_director']
    assert settings['h3_prompt_config']['llm_profiles'] == cfg['llm_profiles']
    assert 'production' not in settings['h3_prompt_config']
    cfg['production']['h3_prompting'] = 'manual'
    assert 'h3_prompt_config' not in configuration.production_config(project,cfg)
    cfg['production']['h3_prompting'] = 'invalid'
    with pytest.raises(ValueError, match='h3_prompting'):
        configuration.production_config(project,cfg)


def test_non_h3_application_does_not_require_prompt_compiler():
    import json
    from pathlib import Path
    cfg = json.loads(Path('config/factory.example.json').read_text())
    cfg['production'].pop('h3_prompting', None)
    cfg.pop('video_roles', None)
    cfg.pop('video_profiles', None)
    settings = configuration.production_config({'budget':{'gpu_minutes':10,'max_attempts':1}},cfg)
    assert 'h3_prompt_config' not in settings and 'h3_prompting' not in settings


def test_prompt_preparation_phase_exposes_director_and_readable_label():
    from factory import team_flow, operator_console, runtime_observations, team_tasks
    assert team_flow.PHASE_ROLES['h3_prompt_preparation'] == 'storyboard_director'
    assert operator_console.PHASES['h3_prompt_preparation'] == '自动编写视频镜头提示词'
    assert runtime_observations.PHASE_TASKS['h3_prompt_preparation'][1] == 'storyboard_director'
    assert 'storyboard_director' in team_tasks.TASKS['production']['members']


def frozen_compiler_video(setup, monkeypatch):
    """An archived registered compiler created a real immutable CPU clip."""
    from factory.prompt_registry import load_prompt
    project,cfg,target,*_=setup
    def compiler(version,digest):
        spec=load_prompt('storyboard_director',version).spec
        return {'protocol':h3_prompting.PROTOCOL,'role':h3_prompting.ROLE,'version':version,
                'template_sha256':spec.template_sha256,'contract_sha256':spec.contract_sha256,
                'implementation_sha256':digest*64,'model_identity':{'model':'chosen-director'}}
    old=compiler('3.0.0','a');new=compiler('3.1.0','b');active=[old]
    monkeypatch.setattr(h3_prompting,'identity',lambda c:copy.deepcopy(active[0]))
    original=h3_prompting.compile_prompt
    def compile_with_receipt(*a,**kw):
        result=original(*a,**kw);result['receipt']['compiler']=copy.deepcopy(active[0]);return result
    monkeypatch.setattr(h3_prompting,'compile_prompt',compile_with_receipt)
    cfg.update(semantic_enabled=True,semantic_max_attempts=1)
    run=production._Run(project,cfg,None,None,evaluator=lambda *a:{'status':'unavailable','issues':[]})
    before=target(run)
    with pytest.raises(scheduler.ProductionNeedsDecision):run._render_targets([before],'diffusion_video')
    active[0]=new
    return run,cfg,target,before,active


def test_new_writer_policy_reviews_locked_old_video_without_writer_or_gpu(setup,monkeypatch):
    run,cfg,target,old,active=frozen_compiler_video(setup,monkeypatch)
    before=core.sha256(old['folder']/'take_001/video.mp4')
    def locked(pid,kind,scope,*,check_locks=True):
        if kind=='video' and check_locks:raise production.team_control.TeamInterventionPending('Locked original')
    monkeypatch.setattr(production.team_control,'admit',locked)
    resumed=production._Run(run.project,cfg,None,None,evaluator=lambda *a:{'status':'pass','score':95,'issues':[]})
    current=target(resumed)
    assert old['signature']!=current['signature'] and old['signature'] in current['compatible_source_signatures']
    selected=resumed._render_targets([current],'diffusion_video')[0]
    assert selected['sha256']==before and selected['h3_prompt']['compiler']['version']=='3.0.0'
    assert len(setup[4])==len(setup[5])==1


@pytest.mark.parametrize('changed',['action','frame','director','recipe','retake','template'])
def test_writer_policy_compatibility_still_rejects_changed_inputs(setup,monkeypatch,changed):
    run,cfg,target,old,active=frozen_compiler_video(setup,monkeypatch)
    if changed=='action':run.project['episode']['shots'][0]['action']='Leave the cup and walk away.'
    elif changed=='frame':Image.new('RGB',(160,288),'red').save(setup[-1])
    elif changed=='director':active[0]['model_identity']={'model':'different-director'}
    elif changed=='recipe':cfg['video_connection']['shift_video']=11.0
    elif changed=='retake':run.project['episode']['shots'][0]['render_revisions']={'video':1}
    else:
        path=old['folder']/'take_001/attempt.json';record=core.read_json(path)
        record['h3_prompt']['compiler']['template_sha256']='f'*64;core.write_json(path,record)
    current=target(production._Run(run.project,cfg,None,None))
    assert old['signature'] not in current['compatible_source_signatures']


@pytest.mark.parametrize('kind', ['image', 'video'])
def test_failed_media_is_reviewed_once_under_changed_policy_without_new_take(setup, monkeypatch, kind):
    project,cfg,video_target,*_=setup
    cfg.update(semantic_enabled=True,semantic_max_attempts=1,max_attempts=1)
    def target(run):
        if kind=='video':return video_target(run)
        shot=run.shots[0]
        return run._target('S01',run.folder/'assets/shots/S01',shot['prompt'],[],
                           kind='image',shot=shot,review=True)
    run=production._Run(project,cfg,None,None,evaluator=lambda *a:{'status':'unavailable','issues':[]})
    old=target(run)
    with pytest.raises(scheduler.ProductionNeedsDecision):run._render_targets([old],'diffusion_'+kind)
    path=old['folder']/'take_001/attempt.json';record=core.read_json(path)
    record.update(state='failed',qa={**record['qa'],'status':'fail','semantic':{'status':'fail','issues':[]}})
    core.write_json(path,record)
    original=core.read_json(path.parent/'result.json');digest=core.sha256(core.ROOT/original['path'])
    assert run._unfinished_candidate(old) is None  # Same rules cannot loop on a failed review.
    cfg['semantic_model_identity']={'context_tokens':32768}
    def locked(pid,unit,scope,*,check_locks=True):
        if unit==kind and check_locks:raise production.team_control.TeamInterventionPending('Preserve original')
    monkeypatch.setattr(production.team_control,'admit',locked)
    calls=[]
    def review(*args):
        calls.append(args[2]);return {'status':'pass','score':95,'issues':[]}
    resumed=production._Run(run.project,cfg,None,None,evaluator=review)
    selected=resumed._render_targets([target(resumed)],'diffusion_'+kind)[0]
    assert selected['take']=='take_001' and selected['sha256']==digest
    assert len(calls)==1 and len(setup[5])==1 and len(list(old['folder'].glob('take_*')))==1
    again=production._Run(resumed.project,cfg,None,None,evaluator=review)
    again._render_targets([target(again)],'diffusion_'+kind)
    assert len(calls)==1 and len(setup[5])==1


@pytest.mark.parametrize('invalid', ['technical', 'source', 'receipt', 'no_review'])
def test_policy_upgrade_never_revives_unusable_failed_media(setup,monkeypatch,invalid):
    run,cfg,target,old,_=frozen_compiler_video(setup,monkeypatch)
    path=old['folder']/'take_001/attempt.json';record=core.read_json(path)
    record['state']='failed'
    if invalid=='technical':record['qa']['technical']['status']='fail'
    elif invalid=='source':record['source_signature']='unrelated';record.pop('generation_signature',None)
    elif invalid=='no_review':record.pop('review_signature',None)
    else:
        receipt=core.read_json(path.parent/'result.json');receipt['source_signature']='unrelated'
        receipt.pop('generation_signature',None);core.write_json(path.parent/'result.json',receipt)
    core.write_json(path,record)
    resumed=production._Run(run.project,cfg,None,None)
    assert resumed._unfinished_candidate(target(resumed)) is None
