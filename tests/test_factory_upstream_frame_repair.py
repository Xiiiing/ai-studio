"""Real renderer/handoff cycle, synthetic CPU pixels and mocked model verdicts."""
import copy

import pytest

from factory import llm, production, production_handoffs as handoffs, production_recovery as recovery
from factory.production_execution import DEFAULT_POLICY
from factory.quality import combine, inspect_image
from factory.scheduler import ProductionNeedsDecision
from studio import core
from tests.test_factory_autonomous_execution import autonomous, h3_setup, reviewer
from tests.test_factory_production_strategy import candidate
from tests.test_factory_role_routing import routing_config


@pytest.fixture
def returned_frame(autonomous, monkeypatch):
    project, config, make_target, events, compiles, executions, frame = autonomous
    config['production_recovery'] = copy.deepcopy(DEFAULT_POLICY)
    config['generation_pipeline_config'] = routing_config()
    config['generation_pipeline_config']['llm_roles']['storyboard_director'] = 'qwen'
    evaluate_video, video_reviews = reviewer(fail_first=False)
    image_reviews = []
    def evaluate(project, shot, path, refs):
        if str(path).endswith('.png'):
            image_reviews.append(core.sha256(path))
            return {'status': 'pass', 'score': 94, 'issues': [], 'scope': 'synthetic CPU image fixture independent QA'}
        return evaluate_video(project, shot, path, refs)
    run = production._Run(project, config, None, None, evaluator=evaluate)
    shot = run.shots[0]
    original_qa = combine(inspect_image(frame, run.image_size), {'status': 'pass', 'score': 94, 'issues': []}, True)
    shot['image']['qa'] = original_qa
    run.assets['characters'] = {'lead': copy.deepcopy(shot['image'])}
    # A legacy approved selection exists, without any tracked image attempt.
    prompt = (run.style_prompt + '\nPreserve the exact identity, age, hairstyle and clothing of each corresponding person in the character reference images. '
              + 'Create a single clear story frame: ' + shot['prompt'])
    image_target = run._target(shot['id'], run.folder/'assets/shots'/shot['id'], prompt,
                              [frame], shot=shot, review=True, reference_roles=['lead'])
    old_image = {**copy.deepcopy(shot['image']), 'source_signature': image_target['signature'],
                 'review_signature': image_target['review_signature']}
    shot['image'] = copy.deepcopy(old_image)
    core.write_json(image_target['folder']/'selected.json', old_image)
    target = run._video_target(shot)
    failed_qa = {'status': 'fail', 'issues': [{'code': 'action', 'severity': 'blocking',
                  'detail': 'Synthetic video failed its already approved motion requirement.'}]}
    for index in range(1, 4):
        core.write_json(target['folder']/f'take_{index:03d}'/'attempt.json',
                        {'take': f'take_{index:03d}', 'attempt': index, 'stage': 'video', 'state': 'failed',
                         'source_signature': target['signature'], 'qa': copy.deepcopy(failed_qa)})
    calls = []
    def model(config, role, system, payload, schema, output, **kwargs):
        assert not events or events[-1] != 'gpu_enter', 'Role model was called while a GPU worker was held'
        calls.append((role, copy.deepcopy(payload)))
        if role == 'visual_critic':
            old = payload['reference_sha256'] == old_image['sha256']
            return {'alignment': 'conflict' if old else 'match',
                    'summary': 'Synthetic compatibility check on actual supplied fixture bytes.',
                    'observations': [{'subject': 'lead', 'visible_state': 'Synthetic source pixel fixture state.',
                                      'image_region': 'center', 'certainty': 'visible'}],
                    'conflicts': ['Synthetic old frame violates the approved starting state.'] if old else []}
        approved = payload['project']['approved_contract']
        result = candidate(approved, payload['project']['strategy'])
        if payload.get('phase') == 'image':
            result.pop('performance', None)
            result['plan'] = {k: v for k, v in result['plan'].items() if k in {'approved_action', 'camera_motion', 'image'}}
        return result
    monkeypatch.setattr(llm, '_request', model)
    return run, target, old_image, calls, image_reviews, video_reviews, executions, frame


def test_old_three_attempts_frame_conflict_runs_real_image_return_then_video_strategy(returned_frame):
    run, target, original, calls, image_reviews, video_reviews, executions, frame = returned_frame
    old_sha = core.sha256(frame)
    selected = run._render_targets([target], 'diffusion_video')[0]
    assert [kind for kind, _ in executions] == ['image', 'video']
    assert image_reviews and old_sha not in image_reviews
    assert run.shots[0]['image']['sha256'] != old_sha
    assert core.sha256(frame) == old_sha
    assert selected['attempt'] == 4
    assert selected['qa']['status'] == 'pass'
    image_attempt = core.read_json(run.folder/'assets/shots/S01/take_001/attempt.json')
    assert image_attempt['attempt'] == 1
    assert image_attempt['production_strategy']['strategy'] == 'grounded_single_instant'
    source = recovery._load(run.folder/'recovery'/(image_attempt['production_recovery']['record_id']+'.json'))['source']
    assert source['failed_take'] == {}
    assert source['upstream_return']['assessment']['issues'][0]['evidence']['media_sha256'] == old_sha
    assert source['upstream_return']['original_qa'] == original['qa']
    assert original['qa']['status'] == 'pass'
    video_attempt = core.read_json(run.folder/'renders/S01'/selected['take']/'attempt.json')
    assert video_attempt['production_strategy']['strategy'] == 'silent_visual_action'
    upstream = list((run.folder/'recovery/handoffs').glob('upstream-*.json'))
    assert len(upstream) == 1
    receipt = core.read_json(upstream[0])
    assert receipt['first_frame_compatibility']['sha256'] == run.shots[0]['image']['sha256']
    assert receipt['first_frame_compatibility']['observation']['alignment'] == 'match'
    assert not run.production.get('upstream_returns')
    assert [row['attempt'] for row in run._quota_attempts(target)] == [1, 2, 3, 4]


def test_rejected_old_selection_and_attempt_result_cannot_bypass_real_image_return(returned_frame):
    run, target, original, calls, image_reviews, video_reviews, executions, frame = returned_frame
    image_folder = run.folder/'assets/shots/S01'
    core.write_json(image_folder/'take_001/attempt.json', {'take': 'take_001', 'attempt': 1, 'stage': 'image',
        'state': 'passed', 'source_signature': original['source_signature'], 'qa': copy.deepcopy(original['qa'])})
    core.write_json(image_folder/'take_001/result.json', original)
    old_attempt_bytes = (image_folder/'take_001/attempt.json').read_bytes()
    selected = run._render_targets([target], 'diffusion_video')[0]
    assert [kind for kind, _ in executions] == ['image', 'video']
    assert (image_folder/'take_001/attempt.json').read_bytes() == old_attempt_bytes
    actual = core.read_json(image_folder/'take_002/attempt.json')
    assert actual['attempt'] == 2
    assert selected['attempt'] == 4


def test_same_compatibility_contract_must_pass_before_upstream_can_resolve(returned_frame, monkeypatch):
    run, target, original, calls, image_reviews, video_reviews, executions, frame = returned_frame
    request = llm._request
    def incompatible(config, role, system, payload, schema, output, **kwargs):
        answer = request(config, role, system, payload, schema, output, **kwargs)
        if role == 'visual_critic':
            answer.update(alignment='conflict', conflicts=['Synthetic incompatibility remains after real image regeneration.'])
        return answer
    monkeypatch.setattr(llm, '_request', incompatible)
    with pytest.raises(ProductionNeedsDecision) as error:
        run._render_targets([target], 'diffusion_video')
    assert error.value.reason == 'generation_frame_conflict'
    assert [kind for kind, _ in executions] == ['image']
    assert not list((run.folder/'recovery/handoffs').glob('upstream-*.json'))
    assert run.production['upstream_returns']['S01']['assessment']['issues'][0]['evidence']['media_sha256'] == original['sha256']
    assert len(run._quota_attempts(target)) == 3


def test_runtime_restore_rejects_changed_new_hard_requirements(returned_frame):
    run, target, original, calls, image_reviews, video_reviews, executions, frame = returned_frame
    run._render_targets([target], 'diffusion_video')
    current = copy.deepcopy(run.shots[0])
    raw = copy.deepcopy(run.episode['shots'][0])
    raw['seconds'] = float(raw['seconds'])
    assert handoffs.restore_media(run, raw, current)['image']['sha256'] == current['image']['sha256']
    for field in ('camera_constraints', 'hard_constraints', 'requirements', 'audio_requirements', 'reference_shots', 'render_revisions'):
        changed = {**raw, field: {'new_requirement': 'must preserve changed approval'}}
        assert handoffs.restore_media(run, changed, current) == {}


def test_pause_after_real_frame_qa_resumes_same_return_without_second_image_generation(returned_frame, monkeypatch):
    from factory.scheduler import ProductionPaused
    run, target, original, calls, image_reviews, video_reviews, executions, frame = returned_frame
    verify = production._Run._verify_frame_compatibility
    pause = [True]
    def paused(self, case, checked):
        if pause[0] and self.shots[0]['image']['sha256'] != original['sha256']:
            raise ProductionPaused()
        return verify(self, case, checked)
    monkeypatch.setattr(production._Run, '_verify_frame_compatibility', paused)
    with pytest.raises(ProductionPaused): run._render_targets([target], 'diffusion_video')
    assert [kind for kind, _ in executions] == ['image']
    current_frame = run.shots[0]['image']['sha256']
    assert run.production['upstream_returns']['S01']['case_id']
    pause[0] = False
    resumed = production._Run(run.project, run.config, None, None, evaluator=run.evaluator)
    current = resumed._video_target(resumed.shots[0])
    selected = resumed._render_targets([current], 'diffusion_video')[0]
    assert [kind for kind, _ in executions] == ['image', 'video']
    assert resumed.shots[0]['image']['sha256'] == current_frame
    assert selected['attempt'] == 4
    assert not resumed.production.get('upstream_returns')
    actual = core.read_json(resumed.folder/'renders/S01'/selected['take']/'attempt.json')
    assert actual['production_strategy']['strategy'] == 'silent_visual_action'
