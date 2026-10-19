"""Actual renderer state machine with synthetic CPU video, mocked models/hardware.

These tests prove request, review, selection and restart behavior. FFmpeg testsrc
and synthetic reviewer verdicts are explicitly not evidence of artistic quality.
"""
import copy
import json
import shutil

import av
import pytest

from factory import generation_pipeline as pipeline, h3_prompting, production, schemas
from factory.production_recovery import VERSION
from factory.scheduler import ProductionNeedsDecision, ProductionPaused
from factory.visual_constraints import POLICY as CONSTRAINT_POLICY
from studio import core
from tests.test_factory_h3_prompt_integration import setup as h3_setup
from tests.test_factory_role_routing import mock_transport, routing_config


@pytest.fixture
def autonomous(h3_setup, tmp_path, monkeypatch):
    project, cfg, _target, events, compiles, executions, original_frame = h3_setup
    project.update(id='abcdef123456', slug='factory-abcdef123456', status='producing', pause_requested=False,
        production_revision=1, budget={'gpu_minutes':120, 'wall_minutes':180, 'max_attempts':3},
        production={'gpu_seconds':0, 'elapsed_seconds':0})
    frame = tmp_path/'production'/project['slug']/'approved-frame.png'
    frame.parent.mkdir(parents=True)
    shutil.copyfile(original_frame, frame)
    shot = project['episode']['shots'][0]
    shot['image'] = core.media_receipt(frame, qa={'status':'pass', 'eligible_for_selection':True,
        'semantic':{'status':'pass','summary':'Synthetic fixture independent first-frame review'}})
    shot['motion_prompt'] = 'Visible mouths remain naturally closed. Turn toward the window and smile, keeping the cup on the table.'
    cfg.update(max_attempts=3, semantic_enabled=True, review_only_existing_videos=False,
        max_gpu_seconds=7200, max_wall_seconds=10800,
        production_recovery={'version':VERSION,'enabled':True})
    # Only the compiler/model boundary is replaced. The renderer, persistent
    # planner, workflow graph, CPU encoding, decode QA and selection stay real.
    original_compiler = h3_prompting.compile_prompt
    def synthetic_director(*args, **kwargs):
        result = original_compiler(*args, **kwargs)
        appendix = (kwargs.get('repair') or {}).get('prompt_prefix')
        if appendix:
            result['prompt'] += '\n' + appendix
        return result
    monkeypatch.setattr(h3_prompting, 'compile_prompt', synthetic_director)
    monkeypatch.setattr(pipeline, 'ROOT', tmp_path)
    monkeypatch.setattr(schemas, 'ROOT', tmp_path)
    def target(run, index=0):
        shot = run.shots[index]
        return run._target(shot['id'], run.folder/'renders'/shot['id'], shot['motion_prompt'], [frame],
            kind='video', seconds=shot['seconds'], shot=shot, review=True)
    return project, cfg, target, events, compiles, executions, frame


def reviewer(fail_first=True, *, pause=None):
    calls = []
    def evaluate(project, shot, path, refs):
        # Review the actual generated candidate, outside the hardware lease.
        with av.open(path) as container:
            decoded = list(container.decode(video=0))
        assert len(decoded) >= 60
        calls.append({'shot':shot['id'], 'path':str(path), 'frames':len(decoded)})
        if fail_first and len(calls) == 1:
            if pause is not None:
                pause[0] = True
            return {'status':'fail', 'score':35, 'issues':[{'code':'action','severity':'blocking',
                'detail':'Synthetic semantic fixture: final mouth state violates the already approved closed-mouth contract.',
                'evidence':{'frame_index':len(decoded)-1, 'source':'actual_cpu_testsrc_fixture'}}]}
        indices = [round(i*(len(decoded)-1)/3) for i in range(4)]
        return {'status':'pass', 'score':92, 'issues':[],
            'summary':'Synthetic independent reviewer accepted corrected-request fixture; not real model quality.',
            'explicit_constraint_verification': {
                'policy':CONSTRAINT_POLICY, 'status':'pass', 'media_sha256':core.sha256(path),
                'timestamps':[float(decoded[i].time) for i in indices],
                'observations':[{'sample':sample, 'mouth':'not_visible',
                    'evidence':f'SYNTHETIC CPU testsrc frame {index}: color bars contain no visible human mouth.'}
                    for sample,index in enumerate(indices,1)],
                'fixture':'synthetic_testsrc_not_human_or_model_quality_certification'}}
    return evaluate, calls


def enable_real_compiled_pipeline(project, cfg, monkeypatch):
    project['generation_pipeline'] = {'version':pipeline.VERSION,'selection_id':'synthetic-selection'}
    routing = routing_config()
    routing['llm_roles']['storyboard_director'] = 'qwen'
    cfg['generation_pipeline_config'] = routing
    shot = project['episode']['shots'][0]
    def response(request):
        visual = isinstance(request['messages'][-1]['content'], list)
        value = ({'alignment':'match','summary':'Synthetic fixture starting-frame observation.',
                  'observations':[{'subject':'lead','visible_state':'fixture reference supplied unchanged',
                      'image_region':'center','certainty':'visible'}], 'conflicts':[]} if visual else
                 {'status':'ready','reason':'','approved_action':shot['action'],'camera_motion':shot['camera_motion'],
                  'image':{'moment':'start','description':shot['prompt']},
                  'video':{'initial_state':'The adult stands beside the table with mouth closed.',
                           'action':shot['action'],'end_state':'The adult faces the window with mouth closed.',
                           'camera':'Locked camera.','requires_body_motion':True}})
        return {'model':request['model'],'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]}
    return mock_transport(monkeypatch, response)[0]


@pytest.mark.parametrize('compiled', [False, True])
def test_system_executes_failed_review_directed_request_second_review_selection_and_restart(autonomous, monkeypatch, compiled):
    p, cfg, target, events, compiles, executions, frame = autonomous
    model_calls = enable_real_compiled_pipeline(p, cfg, monkeypatch) if compiled else []
    evaluate, reviews = reviewer()
    original_frame = core.sha256(frame)
    snapshots = []
    run = production._Run(p, cfg, snapshots.append, None, evaluator=evaluate)
    current = target(run)
    original_source = current['signature']
    selected = run._render_targets([current], 'diffusion_video')[0]
    assert [kind for kind, _ in executions] == ['video', 'video']
    assert len(reviews) == 2
    prompts = [graph['6']['inputs']['prompt'] for _, graph in executions]
    assert prompts[0] != prompts[1]
    assert 'START, MIDDLE AND END: lips stay together' in prompts[1]
    assert 'final mouth state violates' not in prompts[1]
    failed = core.read_json(current['folder']/'take_001/attempt.json')
    repaired = core.read_json(current['folder']/'take_002/attempt.json')
    assert failed['state'] == 'failed' and failed['qa']['semantic']['status'] == 'fail'
    assert failed['output']['path'] != selected['path']
    assert (core.ROOT/failed['output']['path']).is_file()
    assert repaired['state'] == 'passed' and repaired['qa']['semantic']['status'] == 'pass'
    assert [failed['attempt'], repaired['attempt']] == [1,2]
    assert failed['source_signature'] == repaired['source_signature'] == original_source
    assert repaired['prompt'] == prompts[1]
    assert repaired['production_recovery']['attempt_limit'] == 3
    assert selected['take'] == 'take_002'
    assert core.sha256(frame) == original_frame
    assert run.production['recovery']['status'] == 'resolved'
    assert 'execution_constraints' not in run.shots[0], 'adapter review contract must not mutate approved shot'
    assert any((s['production'].get('recovery') or {}).get('status') == 'repairing' for s in snapshots)
    if compiled:
        assert len(model_calls) == 2, 'real pipeline observes frame and compiles approved plan once'
        receipts = [r['document'] for r in pipeline.inventory(p) if r['document'].get('phase') == 'request']
        assert len(receipts) == 2
        second = next(r for r in receipts if r['take'] == 'take_002')
        assert second['parameters']['prompt'] == prompts[1]
        assert second['parameters']['seed'] == repaired['seed']
        assert second['repair']['recovery_record'] == repaired['production_recovery']['record_id']
    before = (len(executions),len(compiles),len(model_calls),len(reviews))
    resumed = production._Run(run.project, cfg, None, None, evaluator=evaluate)
    assert resumed._render_targets([target(resumed)], 'diffusion_video')[0]['sha256'] == selected['sha256']
    assert before == (len(executions),len(compiles),len(model_calls),len(reviews))


def test_pause_after_failed_review_resumes_next_slot_and_preserves_failed_media(autonomous):
    p, cfg, target, events, compiles, executions, frame = autonomous
    pause = [False]
    evaluate, reviews = reviewer(pause=pause)
    run = production._Run(p, cfg, None, lambda:pause[0], evaluator=evaluate)
    current = target(run)
    with pytest.raises(ProductionPaused):
        run._render_targets([current], 'diffusion_video')
    assert len(executions) == len(reviews) == 1
    saved = core.read_json(current['folder']/'take_001/attempt.json')
    assert saved['state'] == 'failed'
    pause[0] = False
    resumed = production._Run(run.project, cfg, None, lambda:pause[0], evaluator=evaluate)
    selected = resumed._render_targets([target(resumed)], 'diffusion_video')[0]
    assert selected['take'] == 'take_002' and len(executions) == 2
    assert resumed.production['recovery']['status'] == 'resolved'
    assert core.read_json(current['folder']/'take_001/attempt.json') == saved


def test_exhausted_authorized_slot_stops_without_new_revision_or_media_call(autonomous):
    p, cfg, target, events, compiles, executions, frame = autonomous
    cfg['max_attempts'] = p['budget']['max_attempts'] = 1
    evaluate, reviews = reviewer()
    run = production._Run(p, cfg, None, None, evaluator=evaluate)
    current = target(run)
    with pytest.raises(ProductionNeedsDecision) as exc:
        run._render_targets([current], 'diffusion_video')
    assert exc.value.reason == 'retry_limit'
    assert '直接继续不会获得新额度' in str(exc.value)
    assert len(executions) == len(reviews) == 1
    assert run.production['recovery']['status'] == 'stopped'
    assert run.production['recovery']['reason'] == 'retry_limit'
    assert len(list(current['folder'].glob('take_*/attempt.json'))) == 1
    assert not list((run.folder/'recovery').glob('*.json'))


def test_unaffected_shot_and_approved_frames_are_reused_during_local_repair(autonomous):
    p, cfg, target, events, compiles, executions, frame = autonomous
    second = copy.deepcopy(p['episode']['shots'][0])
    second['id'] = 'S02'
    p['episode']['shots'].append(second)
    evaluate, reviews = reviewer()
    original = core.sha256(frame)
    run = production._Run(p, cfg, None, None, evaluator=evaluate)
    first_target, second_target = target(run,0), target(run,1)
    selected = run._render_targets([first_target,second_target], 'diffusion_video')
    assert [item['take'] for item in selected] == ['take_002','take_001']
    assert len(executions) == 3 and all(kind == 'video' for kind, _ in executions)
    assert len(list(second_target['folder'].glob('take_*/attempt.json'))) == 1
    assert core.sha256(frame) == original
    assert [r['shot'] for r in reviews] == ['S01','S02','S01']


def test_unknown_submission_never_triggers_a_new_hardware_request(autonomous):
    p, cfg, target, events, compiles, executions, frame = autonomous
    evaluate, reviews = reviewer()
    run = production._Run(p, cfg, None, None, evaluator=evaluate)
    current = target(run)
    record = run._new_attempt(current)
    core.write_json(current['folder']/record['take']/'submission.json', {'state':'submitted','prompt_id':'owned-unknown-result'})
    existing = core.read_json(current['folder']/record['take']/'attempt.json')
    compile_count = len(compiles)
    with pytest.raises(ProductionNeedsDecision) as exc:
        run._render_targets([current], 'diffusion_video')
    assert exc.value.reason == 'unresolved_submission'
    assert not executions and not reviews
    assert len(compiles) == compile_count
    assert core.read_json(current['folder']/record['take']/'attempt.json') == existing


def test_high_score_without_actual_constraint_evidence_preserves_video_for_review_only_resume(autonomous):
    p, cfg, target, events, compiles, executions, frame = autonomous
    reviews = []
    def unsupported_pass(project, shot, path, refs):
        reviews.append(str(path))
        assert shot['execution_constraints']['visible_mouths_closed'] is True
        return {'status':'pass', 'score':100, 'issues':[],
                'summary':'Synthetic unsubstantiated general score: no actual per-sample constraint evidence.'}
    run = production._Run(p, cfg, None, None, evaluator=unsupported_pass)
    current = target(run)
    with pytest.raises(ProductionNeedsDecision) as exc:
        run._render_targets([current], 'diffusion_video')
    assert exc.value.reason == 'semantic_unavailable'
    assert len(executions) == 1 and len(reviews) == cfg.get('semantic_max_attempts',2)
    assert not (current['folder']/'selected.json').exists()
    record = core.read_json(current['folder']/'take_001/attempt.json')
    assert record['state'] == 'semantic_pending'
    assert record['qa']['semantic']['status'] == 'unavailable'
    assert (core.ROOT/record['output']['path']).is_file()
    assert 'execution_constraints' not in run.shots[0]
    evaluate, audited_reviews = reviewer(fail_first=False)
    resumed = production._Run(run.project, cfg, None, None, evaluator=evaluate)
    selected = resumed._render_targets([target(resumed)], 'diffusion_video')[0]
    assert selected['take'] == 'take_001'
    assert len(executions) == 1 and len(audited_reviews) == 1
    assert selected['qa']['semantic']['explicit_constraint_verification']['media_sha256'] == selected['sha256']
