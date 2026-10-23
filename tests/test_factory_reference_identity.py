"""Review-only changes must reuse checked pixels, never reset paid work."""
import copy
from contextlib import nullcontext

from PIL import Image
import pytest

from factory import generation_pipeline as pipeline, production as renderer
from factory import team_ledger, team_control
from factory.reference_identity import generation_identity
from factory.scheduler import ProductionNeedsDecision
from studio import core
from test_factory_generation_pipeline import production, transport, shot
from test_factory_team_ledger import project


def composition(root, frame, *, review='old', requirements=None):
    requirements = copy.deepcopy(requirements or {
        'kind': 'composition', 'plan_version': 1, 'visual_rules': 'Approved style',
        'entities': [{'kind': 'character', 'entity_id': 'female', 'plan_version': 1,
                      'approved_facts': {'wardrobe': 'blue'}}],
        'reference_bindings': [{'reference_index': 1, 'path': str(frame.relative_to(root)),
                               'sha256': core.sha256(frame), 'entity_id': 'female'}]})
    output = {'path': str(frame.relative_to(root)), 'sha256': core.sha256(frame),
              'review_signature': review, 'qa_sha256': 'qa-' + review}
    document = {'schema_version': 1, 'requirements': requirements, 'output': output}
    manifest = root / 'manifests' / (core.fingerprint(document) + '.json')
    core.write_json(manifest, document)
    return {'kind': 'composition', 'entity_id': 'composition_S01_1', 'version': 1,
            **output, 'entities': requirements['entities'],
            'composition_manifest': {'path': str(manifest.relative_to(root)), 'sha256': core.sha256(manifest)}}


def test_review_metadata_projection_keeps_original_evidence_and_checks_manifests(tmp_path):
    frame = tmp_path / 'reference.png'
    Image.new('RGB', (64, 64), 'blue').save(frame)
    old, new = composition(tmp_path, frame), composition(tmp_path, frame, review='new')
    original = copy.deepcopy(old)
    assert generation_identity(old, root=tmp_path) == generation_identity(new, root=tmp_path)
    assert old == original and old != new
    # These names are not stripped from arbitrary approved facts.
    fact = {'review_signature': 'meaningful custom fact'}
    assert generation_identity(fact, root=tmp_path) == fact


def test_nested_composition_review_changes_preserve_input_mapping(tmp_path):
    frame = tmp_path / 'reference.png'
    Image.new('RGB', (64, 64), 'blue').save(frame)
    combined = tmp_path / 'combined.png'
    Image.new('RGB', (64, 64), 'green').save(combined)
    def outer(review):
        inner = composition(tmp_path, frame, review=review)
        inner['reference_index'] = 1
        return composition(tmp_path, combined, review=review,
                           requirements={'kind': 'composition', 'reference_bindings': [inner],
                                         'entities': copy.deepcopy(inner['entities'])})
    assert generation_identity(outer('old'), root=tmp_path) == generation_identity(outer('new'), root=tmp_path)


@pytest.mark.parametrize('change', ['version', 'entity', 'facts', 'order', 'requirements', 'pixels'])
def test_real_reference_changes_invalidate_generation_identity(tmp_path, change):
    frame = tmp_path / 'reference.png'
    Image.new('RGB', (64, 64), 'blue').save(frame)
    original = composition(tmp_path, frame)
    changed = copy.deepcopy(original)
    if change == 'version': changed['version'] = 2
    elif change == 'entity': changed['entity_id'] = 'different_composition'
    elif change == 'facts': changed['entities'][0]['approved_facts']['wardrobe'] = 'red'
    elif change == 'order': changed['reference_index'] = 2
    elif change == 'requirements':
        document = core.read_json(tmp_path / original['composition_manifest']['path'])
        document['requirements']['reference_bindings'][0]['reference_index'] = 2
        changed = composition(tmp_path, frame, requirements=document['requirements'])
    else:
        other = tmp_path / 'other.png'
        Image.new('RGB', (64, 64), 'red').save(other)
        changed = composition(tmp_path, other)
    assert generation_identity(original, root=tmp_path) != generation_identity(changed, root=tmp_path)


@pytest.mark.parametrize('damage', ['checksum', 'wrong_output', 'pixels', 'missing', 'cycle'])
def test_damaged_composition_evidence_fails_closed(tmp_path, damage):
    frame = tmp_path / 'reference.png'
    Image.new('RGB', (64, 64), 'blue').save(frame)
    binding = composition(tmp_path, frame)
    manifest = tmp_path / binding['composition_manifest']['path']
    if damage == 'checksum': manifest.write_text('{}')
    elif damage == 'wrong_output': binding['sha256'] = '0' * 64
    elif damage == 'pixels': Image.new('RGB', (64, 64), 'red').save(frame)
    elif damage == 'missing': manifest.unlink()
    else:
        # A manifest chain is bounded even if an untrusted caller points back
        # at a previously visited journal.
        with pytest.raises(ProductionNeedsDecision):
            generation_identity(binding, root=tmp_path, _manifests=(manifest.resolve(),))
        return
    with pytest.raises(ProductionNeedsDecision) as error:
        generation_identity(binding, root=tmp_path)
    assert error.value.reason == 'reference_identity_invalid'


def test_compiler_cache_ignores_only_checked_review_changes(production, monkeypatch):
    p, frame, config = production
    calls = transport(monkeypatch)
    old, new = composition(pipeline.ROOT, frame), composition(pipeline.ROOT, frame, review='new')
    plan = pipeline.prepare(p, shot(), [frame], config=config, phase='image', reference_bindings=[old])
    documents = {path: path.read_bytes() for path in pipeline.folder(p).glob('*.json')}
    again = pipeline.prepare(p, shot(), [frame], config=config, phase='image', reference_bindings=[new])
    assert again == plan and len(calls) == 1
    assert plan['source']['reference_bindings'] == [old]
    assert all(path.read_bytes() == data for path, data in documents.items())


def test_review_change_cannot_bypass_an_unknown_compiler_submission(production, monkeypatch):
    p, frame, config = production
    calls = []
    def unknown(*args, **kwargs):
        calls.append(True)
        raise RuntimeError('Transport outcome unknown')
    monkeypatch.setattr('factory.schemas.cached_json_call', unknown)
    with pytest.raises(RuntimeError, match='unknown'):
        pipeline.prepare(p, shot(), [frame], config=config, phase='image',
                         reference_bindings=[composition(pipeline.ROOT, frame)])
    original = {path: path.read_bytes() for path in pipeline.folder(p).glob('*.json')}
    with pytest.raises(ProductionNeedsDecision) as error:
        pipeline.prepare(p, shot(), [frame], config=config, phase='image',
                         reference_bindings=[composition(pipeline.ROOT, frame, review='new')])
    assert error.value.reason == 'generation_plan_unknown' and calls == [True]
    assert all(path.read_bytes() == data for path, data in original.items())


def old_selection(production, monkeypatch):
    p, frame, config = production
    p = copy.deepcopy(p)
    p['production'] = {'gpu_seconds': 651.07, 'elapsed_seconds': 400,
                       'remote_gpu_receipts': {'b0' * 32: 651.07}}
    monkeypatch.setattr(renderer, 'ROOT', pipeline.ROOT)
    calls = transport(monkeypatch)
    config = {**config, 'image_width': 288, 'image_height': 512,
              'video_connection': {'provider': 'comfyui_wan', 'model': 'Wan2.2-TI2V-5B', 'enabled': True},
              'semantic_enabled': True}
    run = renderer._Run(p, config, None, None, evaluator=lambda *a: {'status': 'pass', 'score': 90, 'issues': []})
    binding = composition(pipeline.ROOT, frame)
    def target(engine, value):
        return engine._target('S01', engine.folder / 'assets/shots/S01', 'Approved staging', [frame],
                              shot=engine.shots[0], review=True, asset_references=[value])
    original = target(run, binding)
    folder = original['folder']
    image = folder / 'take_003/image.png'
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(frame.read_bytes())
    receipt = {**core.media_receipt(image), 'source_signature': 'c6' * 32,
               'generation_plan': copy.deepcopy(original['generation_plan']),
               'review_signature': original['review_signature'], 'take': 'take_003', 'attempt': 3,
               'qa': {'status': 'pass', 'technical': {'status': 'pass'},
                      'semantic': {'status': 'pass'}, 'eligible_for_selection': True}}
    core.write_json(folder / 'selected.json', receipt)
    core.write_json(folder / 'take_003/result.json', receipt)
    core.write_json(folder / 'take_003/attempt.json', {
        'take': 'take_003', 'attempt': 3, 'stage': 'image', 'state': 'passed', 'source_signature': receipt['source_signature'],
        'generation_plan': original['generation_plan'], 'qa': receipt['qa'], 'review_signature': original['review_signature'],
        'semantic_attempts': [{'assessment': {'status': 'pass'}, 'review_signature': original['review_signature']}]})
    return run, config, frame, binding, target, receipt, calls


def test_historical_approved_image_rechecks_qa_without_compiling_or_drawing(production, monkeypatch):
    run, config, frame, old, target, receipt, calls = old_selection(production, monkeypatch)
    old_plan = copy.deepcopy(receipt['generation_plan'])
    old_attempt = (run.folder / 'assets/shots/S01/take_003/attempt.json').read_bytes()
    refreshed = composition(pipeline.ROOT, frame, review='new')
    checks = []
    resumed = renderer._Run(run.project, config, None, None,
                           evaluator=lambda *args: checks.append(core.sha256(args[2])) or {'status': 'pass', 'score': 90, 'issues': []})
    current = target(resumed, refreshed)
    assert current['signature'] == receipt['source_signature']
    assert current['generation_plan'] == old_plan
    assert current['review_signature'] != receipt['review_signature']
    assert current['asset_references'] == [refreshed]
    assert len(calls) == 1
    assert (run.folder / 'assets/shots/S01/take_003/attempt.json').read_bytes() == old_attempt
    # Exercise the actual production review loop; any new take is a failure.
    monkeypatch.setattr(resumed, '_new_attempt', lambda *a: pytest.fail('Must not redraw approved pixels'))
    monkeypatch.setattr(resumed, '_team_review_unit', lambda *a: nullcontext(None))
    monkeypatch.setattr(resumed, '_team_publish_target', lambda *a: None)
    monkeypatch.setattr(resumed, '_team_after_target', lambda *a: None)
    monkeypatch.setattr(renderer.team_control, 'assert_publishable', lambda *a, **kw: None)
    monkeypatch.setattr(renderer.team_control, 'after_unit', lambda *a, **kw: None)
    monkeypatch.setattr(renderer, 'inspect_image', lambda *a: {'status': 'pass', 'issues': []})
    monkeypatch.setattr(renderer.team_ledger, 'finish_unit', lambda *a, **kw: None)
    selected = resumed._render_targets([current], 'story_frames')[0]
    assert checks == [receipt['sha256']] and selected['take'] == 'take_003'
    assert selected['sha256'] == receipt['sha256'] and len(calls) == 1
    assert selected['generation_plan'] == old_plan
    assert (run.folder / 'assets/shots/S01/take_003/attempt.json').read_bytes() != old_attempt  # New review is appended normally.
    saved = core.read_json(current['folder'] / 'take_003/attempt.json')
    assert saved['semantic_attempts'][0]['review_signature'] == receipt['review_signature']
    assert len(saved['semantic_attempts']) == 2
    assert resumed.budget.snapshot()['gpu_seconds'] == 651.07
    assert resumed.budget.snapshot()['remote_gpu_receipts'] == {'b0' * 32: 651.07}
    assert len(list(current['folder'].glob('take_*'))) == 1


@pytest.mark.parametrize('change', [None, 'facts', 'model'])
def test_selected_pixels_cross_only_equivalent_ledger_versions(production, monkeypatch, change):
    run, config, frame, old, target, receipt, calls = old_selection(production, monkeypatch)
    refreshed = composition(pipeline.ROOT, frame, review='new')
    sibling = core.read_json(run.folder / 'assets/shots/S01/take_003/attempt.json')
    sibling.update(take='take_004', source_signature='ab' * 32, state='failed')
    plan = sibling['generation_plan']
    plan['source']['reference_bindings'] = [refreshed]
    # A different compiler wording does not change the approved source facts.
    plan['parameters']['prompt'] += ' Preserve the approved staging.'
    if change == 'facts':
        plan['source']['approved_shot']['prompt'] = 'A different approved scene.'
    elif change == 'model':
        plan['capabilities']['model'] = 'different-model'
    core.write_json(run.folder / 'assets/shots/S01/take_004/attempt.json', sibling)
    token = team_ledger.begin_unit(run.ident, 'image', 'S01', input_version=sibling['source_signature'])
    team_ledger.finish_unit(token, status='completed')
    current = target(run, refreshed)
    if change:
        with pytest.raises(team_control.TeamInterventionPending, match='输入版本'):
            run._team_publish_target(current)
        return
    run._team_publish_target(current)
    assert current['signature'] == receipt['source_signature']
    assert sibling['source_signature'] not in current['compatible_source_signatures']
    assert [a['take'] for a in run._attempts(current)] == ['take_003']
    # Run the actual ledger-backed review/adoption path without a new render.
    monkeypatch.setattr(run, '_new_attempt', lambda *a: pytest.fail('Must not redraw approved pixels'))
    monkeypatch.setattr(renderer, 'inspect_image', lambda *a: {'status': 'pass', 'issues': []})
    selected = run._render_targets([current], 'story_frames')[0]
    assert selected['sha256'] == receipt['sha256']
    team_control.assert_publishable(run.ident, 'image', 'S01', input_version=current['signature'])
    assert len(calls) == 1 and run.budget.snapshot()['gpu_seconds'] == 651.07
    # A real concurrent input change still closes the publication fence.
    team_ledger.begin_unit(run.ident, 'image', 'S01', input_version='changed-by-producer')
    with pytest.raises(team_control.TeamInterventionPending, match='输入版本'):
        run._team_publish_target(current)


@pytest.mark.parametrize('change', ['version', 'mapping', 'dimensions', 'model', 'shot'])
def test_legacy_plan_compatibility_rejects_real_changes(production, monkeypatch, change):
    run, config, frame, binding, target, receipt, calls = old_selection(production, monkeypatch)
    updated = copy.deepcopy(binding)
    if change == 'version': updated['version'] += 1
    elif change == 'mapping': updated['entities'][0]['entity_id'] = 'other'
    elif change == 'dimensions': config['image_width'] = 320
    elif change == 'model': config['image_connection'] = {**config['image_connection'], 'model': 'different-model'}
    else: run.project['script']['shots'][0]['prompt'] = 'A different approved scene.'
    resumed = renderer._Run(run.project, config, None, None)
    current = target(resumed, updated)
    assert current['signature'] != receipt['source_signature']
    assert current['generation_plan']['signature'] != receipt['generation_plan']['signature']


def test_pending_request_retains_its_frozen_signature_after_review_refresh(production, monkeypatch):
    run, config, frame, binding, target, receipt, calls = old_selection(production, monkeypatch)
    folder = run.folder / 'assets/shots/S01'
    (folder / 'selected.json').unlink()
    attempt_path = folder / 'take_003/attempt.json'
    attempt = core.read_json(attempt_path)
    attempt.update(state='rendering', source_signature='a0' * 32)
    core.write_json(attempt_path, attempt)
    (attempt_path.parent / 'result.json').unlink()
    core.write_json(attempt_path.parent / 'submission.json', {'gpu_service_job_id': 'b0' * 32, 'state': 'running'})
    original = {path: path.read_bytes() for path in attempt_path.parent.glob('*.json')}
    monkeypatch.setattr(pipeline, 'prepare', lambda *a, **kw: pytest.fail('Resume must not compile'))
    resumed = renderer._Run(run.project, config, None, None)
    current = target(resumed, composition(pipeline.ROOT, frame, review='new'))
    assert current['signature'] == attempt['source_signature']
    assert current['generation_plan'] == attempt['generation_plan']
    assert len(calls) == 1
    assert all(path.read_bytes() == data for path, data in original.items())
