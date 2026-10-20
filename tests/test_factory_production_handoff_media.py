"""First-frame review/restart evidence, using real image bytes and offline verdicts."""
import copy

import pytest

from factory import production, production_handoffs as handoffs, production_recovery as recovery
from factory.production_execution import DEFAULT_POLICY
from factory.creative_evidence_io import digest
from factory.scheduler import ProductionNeedsDecision
from studio import core
from tests.test_factory_autonomous_execution import autonomous, h3_setup


@pytest.fixture
def media_case(autonomous):
    p, config, _, events, compiles, executions, frame = autonomous
    config['production_recovery'] = copy.deepcopy(DEFAULT_POLICY)
    reviews = []
    verdict = [{'status': 'pass', 'score': 94, 'issues': []}]
    def evaluate(project, shot, path, refs):
        reviews.append((shot, path, refs))
        assert path == str(frame) and refs == []
        if isinstance(verdict[0], BaseException):
            raise verdict[0]
        return copy.deepcopy(verdict[0])
    run = production._Run(p, config, None, None, evaluator=evaluate)
    shot = run.shots[0]
    shot['image']['qa'] = {'status': 'unavailable', 'semantic': {'status': 'unavailable'}}
    target = run._video_target(shot)
    checked = {**target, 'first_frame_assessment': copy.deepcopy(shot['image']['qa'])}
    directed = recovery.prepare_repair(run.project, config, checked,
        {'status': 'fail', 'issues': [{'code': 'action', 'severity': 'blocking'}]},
        previous_attempt={'take': 'take_003', 'attempt': 3, 'state': 'failed', 'source_signature': target['signature']}, root=core.ROOT)
    case = handoffs.start(run, target, directed)
    run.production['upstream_contracts'] = {shot['id']: digest(handoffs._approved_facts(shot))}
    return run, target, case, verdict, reviews, executions, frame


def test_review_original_frame_independently_preserves_pixels_audit_and_cached_result(media_case):
    run, target, case, verdict, reviews, executions, frame = media_case
    original = copy.deepcopy(target['shot']['image'])
    assert handoffs.review_first_frame(run, case, target) == 'pass'
    assert len(reviews) == 1 and not executions
    assert target['shot']['image']['sha256'] == original['sha256'] == core.sha256(frame)
    assert target['shot']['image']['qa']['semantic']['status'] == 'pass'
    evidence = next(case['path'].parent.glob('frame-review-*.json'))
    document = core.read_json(evidence)['document']
    assert document['original_qa'] == original['qa']
    assert document['qa']['technical']['status'] == 'pass'
    assert handoffs.review_first_frame(run, case, target) == 'pass'
    assert len(reviews) == 1 and not executions


@pytest.mark.parametrize('verdict_status', ['fail', 'unavailable'])
def test_unavailable_or_failed_first_frame_never_regenerates_any_media(media_case, verdict_status):
    run, target, case, verdict, reviews, executions, frame = media_case
    verdict[0] = (TimeoutError('Synthetic model response timeout') if verdict_status == 'unavailable' else
                  {'status': verdict_status, 'score': 30, 'issues': []})
    original = copy.deepcopy(target['shot']['image'])
    if verdict_status == 'fail':
        assert handoffs.review_first_frame(run, case, target) == 'fail'
        assert len(reviews) == 1
    else:
        with pytest.raises(ProductionNeedsDecision) as error:
            handoffs.review_first_frame(run, case, target)
        assert error.value.reason == 'semantic_unavailable'
        assert error.value.details['pending'][0]['upstream_case_id'] == case['id']
        assert error.value.details['pending'][0]['retryable_transport'] is True
        assert len(reviews) == run.semantic_max_attempts
    if verdict_status == 'unavailable':
        assert target['shot']['image'] == original
    else:
        assert target['shot']['image']['qa']['status'] == 'fail'
        assert target['shot']['image']['sha256'] == original['sha256']
    assert not executions


def test_v2_restores_verified_reviewed_frame_only_for_same_approved_contract(media_case):
    run, target, case, verdict, reviews, executions, frame = media_case
    raw = copy.deepcopy(target['shot'])
    assert handoffs.review_first_frame(run, case, target) == 'pass'
    current = copy.deepcopy(target['shot'])
    restored = handoffs.restore_media(run, raw, current)
    assert restored['image'] == current['image']
    assert not ({'prompt', 'action', 'seconds', 'dialogue'} & set(restored))
    for field, value in [('prompt', 'Changed approved framing'), ('action', 'Changed approved action'), ('seconds', 4)]:
        changed = {**raw, field: value}
        assert handoffs.restore_media(run, changed, current) == {}
    run.strategy_recovery = False
    assert handoffs.restore_media(run, raw, current) == {}


@pytest.mark.parametrize('tamper', ['media', 'review_receipt'])
def test_corrupt_upstream_media_stops_instead_of_silently_reverting_to_old_frame(media_case, tamper):
    run, target, case, verdict, reviews, executions, frame = media_case
    raw = copy.deepcopy(target['shot'])
    handoffs.review_first_frame(run, case, target)
    current = copy.deepcopy(target['shot'])
    if tamper == 'media':
        frame.write_bytes(b'changed image bytes')
    else:
        review = current['image']['upstream_review']
        (core.ROOT / review['path']).write_text('{}')
    with pytest.raises(ProductionNeedsDecision) as error:
        handoffs.restore_media(run, raw, current)
    assert error.value.reason == 'production_handoff_corrupt' and not executions


def test_pause_after_new_frame_before_upstream_receipt_restores_verified_result(media_case):
    from factory.quality import inspect_image, combine
    from PIL import Image
    run, target, case, verdict, reviews, executions, frame = media_case
    raw = copy.deepcopy(target['shot'])
    handoffs.advance(run, case, 'handing_off', 'image_generator')
    generated = run.folder / 'assets' / 'shots' / raw['id'] / 'take_002' / 'image.png'
    generated.parent.mkdir(parents=True)
    with Image.open(frame) as image:
        image.transpose(Image.Transpose.FLIP_LEFT_RIGHT).save(generated)
    qa = combine(inspect_image(generated, run.image_size),
                 {'status': 'pass', 'issues': [], 'scope': 'synthetic independent CPU-image fixture review'}, True)
    output = core.media_receipt(generated, qa=qa, take='take_002', stage='image')
    core.write_json(generated.parent / 'result.json', output)
    current = {**copy.deepcopy(raw), 'image': output}
    assert not list(case['path'].parent.glob('upstream-*.json'))
    restored = handoffs.restore_media(run, raw, current)
    assert restored['image']['sha256'] == output['sha256'] != raw['image']['sha256']
    assert not executions


def test_forged_runtime_verdict_without_durable_review_is_not_reused(media_case):
    run, target, case, verdict, reviews, executions, frame = media_case
    raw = copy.deepcopy(target['shot'])
    handoffs.advance(run, case, 'handing_off', 'visual_critic')
    current = copy.deepcopy(raw)
    current['image']['qa'] = {'status': 'pass', 'technical': {'status': 'pass'},
                              'semantic': {'status': 'pass'}, 'eligible_for_selection': True}
    with pytest.raises(ProductionNeedsDecision) as error:
        handoffs.restore_media(run, raw, current)
    assert error.value.reason == 'production_handoff_corrupt'


@pytest.mark.parametrize('outcome', ['pass', 'fail', 'unavailable'])
def test_real_run_transport_resume_handles_legacy_first_frame_without_attempt_or_result(media_case, outcome):
    run, target, case, verdict, reviews, executions, frame = media_case
    assert not (frame.parent / 'attempt.json').exists()
    assert not (frame.parent / 'result.json').exists()
    original_sha = core.sha256(frame)
    verdict[0] = TimeoutError('Synthetic upstream vision timeout')
    with pytest.raises(ProductionNeedsDecision) as first:
        handoffs.review_first_frame(run, case, target)
    assert first.value.reason == 'semantic_unavailable'
    pending = copy.deepcopy(first.value.details['pending'])
    run.production['review_transport_recovery'] = {'status': 'waiting', 'pending': pending}
    verdict[0] = (TimeoutError('Synthetic still unavailable') if outcome == 'unavailable' else
                  {'status': outcome, 'score': 93 if outcome == 'pass' else 30, 'issues': []})
    before = len(reviews)
    if outcome == 'unavailable':
        with pytest.raises(ProductionNeedsDecision) as error:
            run._review_transport_pending()
        assert error.value.reason == 'semantic_unavailable'
        assert run.production['review_transport_recovery']['status'] == 'waiting'
        assert run.production['review_transport_recovery']['pending'] == pending
        assert len(reviews) == before + run.semantic_max_attempts
    else:
        run._review_transport_pending()
        assert run.production['review_transport_recovery']['status'] == 'resolved'
        assert run.production['review_transport_recovery']['pending'] == []
        assert run.shots[0]['image']['qa']['semantic']['status'] == outcome
        assert len(reviews) == before + 1
    assert all(path == str(frame) and refs == [] for _, path, refs in reviews)
    assert core.sha256(frame) == original_sha
    assert not executions
    assert not (frame.parent / 'attempt.json').exists()
    assert not (frame.parent / 'result.json').exists()
