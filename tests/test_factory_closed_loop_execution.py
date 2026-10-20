"""Real renderer/handoff/role protocols with CPU media and simulated model/GPU I/O.

No testsrc verdict here certifies a model's visual quality or a finished episode.
"""
import copy
import json
from pathlib import Path

import pytest

from factory import production
from factory.production_execution import DEFAULT_POLICY, apply_configuration
from factory.scheduler import ProductionNeedsDecision, ProductionPaused
from studio import core
from tests.test_factory_autonomous_execution import autonomous, reviewer, h3_setup
from tests.test_factory_production_strategy import candidate, observe
from tests.test_factory_role_routing import mock_transport, routing_config


def failed_review():
    return {'status': 'fail', 'score': 35, 'issues': [{'code': 'action', 'severity': 'blocking',
            'detail': 'Synthetic actual-media action failure, not a new production instruction.',
            'evidence': {'seconds': 2.5, 'source': 'cpu_testsrc_not_model_quality'}}]}


def role_transport(monkeypatch):
    def respond(request):
        content = request['messages'][-1]['content']
        if isinstance(content, list):
            result = observe()
        else:
            payload = json.loads(content)['project']
            result = candidate(payload['approved_contract'], payload['strategy'])
        return {'model': request['model'], 'choices': [{'finish_reason': 'stop',
                'message': {'content': json.dumps(result)}}]}
    return mock_transport(monkeypatch, respond)[0]


@pytest.fixture
def historical(autonomous, monkeypatch):
    p, config, _, events, compiles, executions, frame = autonomous
    # Produce all three old failed attempts through the real renderer. The old
    # legacy loop has no new recovery strategy authorization and no role calls.
    config['production_recovery'] = {'enabled': False}
    run = production._Run(p, config, None, None, evaluator=lambda *args: failed_review())
    target = run._video_target(run.shots[0])
    with pytest.raises(ProductionNeedsDecision) as error:
        run._render_targets([target], 'diffusion_video')
    assert error.value.reason == 'retry_limit'
    assert len(executions) == 3 and all(kind == 'video' for kind, _ in executions)
    old = [core.read_json(path) for path in sorted(target['folder'].glob('take_*/attempt.json'))]
    assert [r['attempt'] for r in old] == [1, 2, 3]
    assert {r['source_signature'] for r in old} == {target['signature']}
    assert all(r['state'] == 'failed' for r in old)
    config = apply_configuration({'production': config}, DEFAULT_POLICY)['production']
    models = routing_config()
    models['llm_roles']['storyboard_director'] = 'qwen'
    config['generation_pipeline_config'] = models
    calls = role_transport(monkeypatch)
    # The old outputs must first fail the current independent policy. A newer
    # review policy may legitimately recover an old clip without any generation.
    upgraded = production._Run(run.project, config, None, None, evaluator=reviewer(True)[0])
    upgraded._transport_review_only = True
    for record in old:
        upgraded.evaluator = reviewer(True)[0]
        current = upgraded._video_target(upgraded.shots[0])
        current['review_candidate'] = (record, core.read_json(target['folder'] / record['take'] / 'result.json'))
        assert upgraded._render_targets([current], 'semantic_review_pending') == []
    assert len(executions) == 3 and not calls
    old = [core.read_json(path) for path in sorted(target['folder'].glob('take_*/attempt.json'))]
    return upgraded.project, config, target, old, calls, executions, compiles, frame


def documents(run):
    return [core.read_json(path)['document'] for path in
            (run.folder / 'recovery' / 'handoffs').glob('*.json')
            if not path.name.startswith('upstream-')]


def test_old_three_failures_upgrade_runs_real_director_strategy_fourth_take_and_restart(historical):
    p, config, old_target, old, calls, executions, compiles, frame = historical
    evaluate, reviews = reviewer(fail_first=False)
    run = production._Run(p, config, None, None, evaluator=evaluate)
    target = run._video_target(run.shots[0])
    assert target['signature'] == old_target['signature']
    result = run._render_targets([target], 'diffusion_video')[0]
    assert result['take'] == 'take_004'
    assert len(executions) == 4 and len(reviews) == 1 and len(calls) == 2
    repaired = core.read_json(target['folder'] / 'take_004' / 'attempt.json')
    assert repaired['attempt'] == 4 and repaired['source_signature'] == old_target['signature']
    assert repaired['production_strategy']['strategy'] == 'silent_visual_action'
    actual = executions[-1][1]['6']['inputs']['prompt']
    assert actual == repaired['production_strategy']['parameters']['prompt']
    assert actual != old[-1]['prompt']
    assert 'Synthetic actual-media action failure' not in actual
    assert isinstance(calls[0]['request']['messages'][-1]['content'], list)
    assert json.loads(calls[1]['request']['messages'][-1]['content'])['project']['strategy'] == 'silent_visual_action'
    cases = documents(run)
    assert len(cases) == 1 and cases[0]['status'] == 'resolved'
    assert {'diagnosing', 'planning', 'repairing', 'reviewing', 'resolved'} <= {e['status'] for e in cases[0]['events']}
    assert cases[0]['events'][-1]['artifact']['sha256'] == result['sha256']
    for index, original in enumerate(old, 1):
        assert core.read_json(target['folder'] / f'take_{index:03d}' / 'attempt.json') == original
    receipts = [core.read_json(path) for path in (run.folder / 'recovery').rglob('receipt.json')]
    assert {r['role'] for r in receipts} == {'visual_critic', 'storyboard_director'}
    before = (len(executions), len(calls), len(compiles), len(reviews))
    resumed = production._Run(run.project, config, None, None, evaluator=evaluate)
    assert resumed._render_targets([resumed._video_target(resumed.shots[0])], 'diffusion_video')[0]['sha256'] == result['sha256']
    assert before == (len(executions), len(calls), len(compiles), len(reviews))


def test_failed_first_strategy_uses_phase_isolated_fifth_take_then_no_sixth(historical):
    p, config, _, old, calls, executions, compiles, frame = historical
    evaluate, reviews = reviewer(fail_first=True)
    run = production._Run(p, config, None, None, evaluator=evaluate)
    target = run._video_target(run.shots[0])
    selected = run._render_targets([target], 'diffusion_video')[0]
    assert selected['take'] == 'take_005' and len(executions) == 5 and len(reviews) == 2
    records = [core.read_json(target['folder'] / f'take_{n:03d}' / 'attempt.json') for n in (4, 5)]
    assert [r['production_strategy']['strategy'] for r in records] == ['silent_visual_action', 'phase_isolated_action']
    assert records[0]['prompt'] != records[1]['prompt']
    assert '0.00s-0.60s:' in records[1]['prompt'] and '0.60s-2.40s:' in records[1]['prompt']
    assert [r['attempt'] for r in records] == [4, 5]
    assert len(calls) == 4 and p['budget']['max_attempts'] == 3
    with pytest.raises(ProductionNeedsDecision) as error:
        run._new_attempt(target)
    assert error.value.reason == 'retry_limit'
    assert len(executions) == 5 and len(calls) == 4
    assert not (target['folder'] / 'take_006').exists()


def test_pause_before_strategy_submits_no_role_or_gpu(historical):
    p, config, _, old, calls, executions, compiles, frame = historical
    run = production._Run(p, config, None, lambda: True, evaluator=reviewer(False)[0])
    with pytest.raises(ProductionPaused):
        run._render_targets([run._video_target(run.shots[0])], 'diffusion_video')
    assert len(executions) == 3 and not calls


def test_unknown_existing_submission_blocks_new_strategy_and_generation(historical):
    p, config, target, old, calls, executions, compiles, frame = historical
    folder = target['folder'] / 'take_004'
    core.write_json(folder / 'attempt.json', {'take': 'take_004', 'attempt': 4, 'stage': 'video',
                    'state': 'rendering', 'source_signature': target['signature']})
    core.write_json(folder / 'submission.json', {'state': 'submitted', 'prompt_id': 'owned-unknown-result'})
    run = production._Run(p, config, None, None, evaluator=reviewer(False)[0])
    with pytest.raises(ProductionNeedsDecision) as error:
        run._render_targets([run._video_target(run.shots[0])], 'diffusion_video')
    assert error.value.reason == 'unresolved_submission'
    assert len(executions) == 3 and not calls


@pytest.mark.parametrize('pass_review', [True, False])
def test_transport_recovery_only_reviews_exact_old_candidate_before_any_strategy(historical, pass_review):
    p, config, target, old, calls, executions, compiles, frame = historical
    evaluate, reviews = reviewer(fail_first=not pass_review)
    run = production._Run(p, config, None, None, evaluator=evaluate)
    saved = core.read_json(target['folder'] / 'take_003' / 'result.json')
    run.production['review_transport_recovery'] = {'status': 'waiting', 'pending': [
        {'asset': 'S01', 'stage': 'video', 'take': 'take_003', 'path': saved['path']}]}
    other = copy.deepcopy(run.shots[0])
    other['id'] = 'S02'
    run.shots.append(other)
    before = (len(executions), len(compiles), len(calls))
    run._review_transport_pending()
    assert before == (len(executions), len(compiles), len(calls))
    assert len(reviews) == 1 and reviews[0]['path'] == str(core.ROOT / saved['path'])
    assert run.production['review_transport_recovery']['status'] == 'resolved'
    assert run.production['review_transport_recovery']['pending'] == []
    assert not (run.folder / 'renders' / 'S02').exists()
    record = core.read_json(target['folder'] / 'take_003' / 'attempt.json')
    assert record['state'] == ('passed' if pass_review else 'failed')
    if not pass_review:
        selected = run._render_targets([run._video_target(run.shots[0])], 'diffusion_video')[0]
        assert selected['take'] == 'take_004' and len(executions) == 4
        assert len(calls) == 2


@pytest.mark.parametrize('tamper', ['wrong_take', 'wrong_path'])
def test_transport_marker_never_silently_reviews_a_different_file(historical, tamper):
    p, config, target, old, calls, executions, compiles, frame = historical
    evaluate, reviews = reviewer(False)
    run = production._Run(p, config, None, None, evaluator=evaluate)
    receipt = core.read_json(target['folder'] / 'take_003' / 'result.json')
    marker = {'asset': 'S01', 'stage': 'video', 'take': 'take_003', 'path': receipt['path']}
    if tamper == 'wrong_take':
        marker['take'] = 'take_002'
    else:
        different = core.ROOT / receipt['path']
        different = different.with_name('another-candidate.mp4')
        different.write_bytes((core.ROOT / receipt['path']).read_bytes())
        marker['path'] = str(different.relative_to(core.ROOT))
    run.production['review_transport_recovery'] = {'status': 'waiting', 'pending': [marker]}
    with pytest.raises(ProductionNeedsDecision) as error:
        run._review_transport_pending()
    assert error.value.reason == 'production_review_scope_changed'
    assert len(executions) == 3 and not calls and not reviews


def test_upstream_image_role_rebuilds_real_reference_and_preserves_video_quota_and_other_media(historical):
    p, config, old_target, old, calls, executions, compiles, frame = historical
    video_review, decoded_reviews = reviewer(False)
    image_reviews = []
    def independent(project, shot, path, refs):
        if Path(path).suffix == '.png':
            from PIL import Image
            with Image.open(path) as image:
                assert image.size == (160, 288)
            image_reviews.append(str(path))
            return {'status': 'pass', 'score': 93, 'issues': [],
                    'summary': 'Synthetic independent inspection of actual CPU image fixture.'}
        if int(Path(path).parent.name.split('_')[-1]) <= 3:
            return failed_review()
        return video_review(project, shot, path, refs)
    run = production._Run(p, config, None, None, evaluator=independent)
    # Establish a real reusable identity asset before the upstream handoff.
    run._characters_and_images(roles=['lead'], shot_ids=[])
    identity = copy.deepcopy(run.assets['characters']['lead'])
    unaffected = copy.deepcopy(run.shots[0])
    unaffected['id'] = 'S02'
    run.shots.append(unaffected)
    unaffected_image = copy.deepcopy(unaffected['image'])
    sound = run.folder / 'audio' / 'approved.wav'
    sound.parent.mkdir(parents=True, exist_ok=True)
    sound.write_bytes(b'SYNTHETIC already approved audio fixture; not speech inference')
    run.shots[0]['audio'] = core.media_receipt(sound, status='approved_fixture')
    old_audio = copy.deepcopy(run.shots[0]['audio'])
    run.shots[0]['image']['qa'] = {'status': 'fail', 'eligible_for_selection': False,
        'semantic': {'status': 'fail', 'issues': [{'code': 'composition', 'severity': 'blocking'}]}}
    target = run._video_target(run.shots[0])
    before = len(executions)
    selected = run._render_targets([target], 'diffusion_video')[0]
    assert [kind for kind, _ in executions[before:]] == ['image', 'video']
    assert selected['take'] == 'take_004' and selected['attempt'] == 4
    assert target['signature'] != old_target['signature']
    assert run.production['recovery_lineage']['video:S01'] == [old_target['signature']]
    assert len(run._quota_attempts(target)) == 4
    assert run.shots[0]['image']['sha256'] != core.sha256(frame)
    assert run.shots[0]['audio'] == old_audio and run.assets['characters']['lead'] == identity
    assert run.shots[1]['image'] == unaffected_image
    assert len(image_reviews) == 1 and len(decoded_reviews) == 1
    assert not (run.folder / 'renders' / 'S02').exists()
    cases = documents(run)
    assert len(cases) == 3 and all(case['status'] == 'resolved' for case in cases)
    assert sorted(case['identity']['stage'] for case in cases) == ['image', 'video', 'video']
    upstream = list((run.folder / 'recovery' / 'handoffs').glob('upstream-*.json'))
    assert len(upstream) == 1
    receipt = core.read_json(upstream[0])
    assert receipt['operation'] == 'revise_first_frame'
    assert receipt['previous_source_signature'] == old_target['signature']
    assert receipt['frame_sha256'] == run.shots[0]['image']['sha256']
    assert receipt['first_frame_assessment']['status'] == 'pass'
    assert len(calls) == 4
    role_receipts = [core.read_json(path) for path in (run.folder / 'recovery').rglob('receipt.json')]
    assert sorted(item['role'] for item in role_receipts) == [
        'storyboard_director', 'storyboard_director', 'visual_critic', 'visual_critic']
    strategies = [json.loads(call['request']['messages'][-1]['content'])['project']['strategy']
                  for call in calls if isinstance(call['request']['messages'][-1]['content'], str)]
    assert strategies == ['grounded_single_instant', 'silent_visual_action']
    before_restart = (len(executions), len(calls), len(image_reviews), len(decoded_reviews))
    resumed = production._Run(run.project, config, None, None, evaluator=independent)
    rebuilt = resumed._video_target(resumed.shots[0])
    assert rebuilt['signature'] == target['signature'], 'Restart must retain the independently repaired first frame'
    assert resumed._render_targets([rebuilt], 'diffusion_video')[0]['sha256'] == selected['sha256']
    assert before_restart == (len(executions), len(calls), len(image_reviews), len(decoded_reviews))


def test_image_transport_recovery_reviews_exact_old_take_even_if_a_newer_selection_exists(autonomous):
    p, config, _, events, compiles, executions, frame = autonomous
    config['production_recovery'] = {'enabled': False}
    reviewed = []
    def image_reviewer(project, shot, path, refs):
        reviewed.append(str(path))
        return failed_review() if len(reviewed) == 1 else {'status': 'pass', 'score': 92, 'issues': []}
    run = production._Run(p, config, None, None, evaluator=image_reviewer)
    run._characters_and_images(shot_ids=['S01'])
    folder = run.folder / 'assets' / 'shots' / 'S01'
    old = core.read_json(folder / 'take_001' / 'result.json')
    assert run.shots[0]['image']['take'] == 'take_002'
    assert len(reviewed) == 2
    run.production['review_transport_recovery'] = {'status': 'waiting', 'pending': [
        {'asset': 'S01', 'stage': 'image', 'take': 'take_001', 'path': old['path']}]}
    before = len(executions)
    run._review_transport_pending()
    assert len(executions) == before
    assert reviewed[-1] == str(core.ROOT / old['path']) and len(reviewed) == 3
    assert run.production['review_transport_recovery']['status'] == 'resolved'
    assert run.production['review_transport_recovery']['pending'] == []


def test_missing_first_frame_verdict_reviews_existing_pixels_without_remaking_frame_or_identity(historical):
    p, config, old_target, old, calls, executions, compiles, frame = historical
    accepted, decoded = reviewer(False)
    image_reviews = []
    def independent(project, shot, path, refs):
        if Path(path).suffix == '.png':
            image_reviews.append(str(path))
            return {'status': 'pass', 'score': 92, 'issues': [],
                    'summary': 'Synthetic independent review of the existing actual frame pixels.'}
        if int(Path(path).parent.name.split('_')[-1]) <= 3:
            return failed_review()
        return accepted(project, shot, path, refs)
    run = production._Run(p, config, None, None, evaluator=independent)
    original = copy.deepcopy(run.shots[0]['image'])
    run.shots[0]['image']['qa'] = {'status': 'unavailable', 'eligible_for_selection': False,
                                 'semantic': {'status': 'unavailable'}}
    before = len(executions)
    target = run._video_target(run.shots[0])
    selected = run._render_targets([target], 'diffusion_video')[0]
    assert selected['take'] == 'take_004'
    assert [kind for kind, _ in executions[before:]] == ['video']
    assert image_reviews == [str(frame)]
    assert run.shots[0]['image']['sha256'] == original['sha256']
    assert target['signature'] == old_target['signature']
    upstream = list((run.folder / 'recovery' / 'handoffs').glob('upstream-*.json'))
    assert len(upstream) == 1
    assert core.read_json(upstream[0])['operation'] == 'review_first_frame'


def test_started_v2_render_without_submission_or_result_never_reenters_worker(historical):
    p, config, old_target, old, calls, executions, compiles, frame = historical
    run = production._Run(p, config, None, None, evaluator=reviewer(False)[0])
    target = run._video_target(run.shots[0])
    record = run._new_attempt(target)
    assert record['take'] == 'take_004'
    record['prepared_only'] = False
    run._save_attempt(target, record)
    folder = target['folder'] / record['take']
    assert not (folder / 'submission.json').exists() and not (folder / 'result.json').exists()
    counts = (len(executions), len(compiles), len(calls))
    resumed = production._Run(run.project, config, None, None, evaluator=reviewer(False)[0])
    with pytest.raises(ProductionNeedsDecision) as error:
        resumed._render_targets([resumed._video_target(resumed.shots[0])], 'diffusion_video')
    assert error.value.reason == 'unresolved_submission'
    assert counts == (len(executions), len(compiles), len(calls))
    assert core.read_json(folder / 'attempt.json')['prepared_only'] is False
    assert not (target['folder'] / 'take_005').exists()
