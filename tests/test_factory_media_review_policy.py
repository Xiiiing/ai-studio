"""035 media boundaries using actual critic/QA/recovery code and offline media."""
import copy
import json

import pytest
from PIL import Image

from factory import llm, quality, media_review_policy as policy, production_recovery as recovery
from studio.core import sha256
from test_factory_production_recovery import data


def classified_issue(nature='OBJECTIVE', **updates):
    return {'code': 'composition', 'severity': 'blocking', 'detail': 'The approved cup is absent in the actual candidate.',
            'nature': nature, 'location': 'candidate:0 at 0 seconds', 'impact': 'The required cup is missing.',
            'repair_target': 'Restore the approved cup, retaining all other approved content.',
            'evidence_refs': ['candidate:0'], 'requires_choice': False, **updates}


def marked_project():
    return {'id': 'abc123', 'slug': 'factory-abc123', 'format': 'motion_comic', 'production_revision': 4,
            'creative_quality_contract': {'version': 'ai-studio.creative-quality.v1'}}


@pytest.mark.parametrize('nature,score,status', [('CRAFT', 95, 'pass'), ('AESTHETIC', 95, 'pass'),
                                              ('CRAFT', 15, 'unavailable'), ('AESTHETIC', 15, 'unavailable'),
                                              ('OBJECTIVE', 95, 'fail'), ('STRUCTURAL', 95, 'fail')])
def test_actual_media_critic_classifies_current_pixels_and_preserves_advice(tmp_path, monkeypatch, nature, score, status):
    path = tmp_path / 'frame.png'
    Image.new('RGB', (128, 128), 'red').save(path)
    references = []
    for i in range(5):
        ref = tmp_path / f'reference-{i}.png'
        Image.new('RGB', (128, 128), 'blue').save(ref)
        references.append(ref)
    answer = {'status': 'fail', 'score': score, 'summary': 'Current fixture observation',
              'issues': [classified_issue(nature)]}
    captured = {}
    def request(config, role, system, payload, schema, output, **kwargs):
        captured.update(schema=schema, context=json.loads(kwargs['content'][0]['text']), content=kwargs['content'])
        return copy.deepcopy(answer)
    monkeypatch.setattr(llm, '_request', request)
    result = llm.review_media(marked_project(), {'id': 'S01', 'prompt': 'Adult with the required cup.'},
                              path, references, {'llm': {'model': 'fixture'}})
    assert result['status'] == status and result['model_status'] == 'fail'
    assert result['issues'] == answer['issues']
    fields = captured['schema']['properties']['issues']['items']['required']
    assert {'nature', 'location', 'impact', 'repair_target', 'evidence_refs', 'requires_choice'} <= set(fields)
    contract = captured['context']['media_review_contract']
    assert contract['media_sha256'] == sha256(path) and contract['production_revision'] == 4
    assert len(contract['evidence_catalog']) == 6
    assert sum(p['type'] == 'image_url' for p in captured['content']) == 6
    assert result['media_review_decision']['automatic_repair'] == (status == 'fail')


@pytest.mark.parametrize('issue,status', [(classified_issue(requires_choice=True), 'unavailable'),
    (classified_issue('AESTHETIC'), 'pass'), (classified_issue(evidence_refs=['made-up:1']), 'unavailable'),
    ({'code': 'low_quality', 'severity': 'blocking', 'detail': 'Make it more cinematic.'}, 'unavailable')])
def test_actual_qa_boundary_does_not_trust_severity_or_invent_missing_evidence(tmp_path, issue, status):
    path = tmp_path / 'candidate.png'; Image.new('RGB', (128, 128), 'red').save(path)
    result = quality.semantic_review(lambda *a: {'status': 'fail', 'score': 95, 'issues': [issue]},
        marked_project(), {'id': 'S01'}, path, [])
    assert result['status'] == status
    assert result['media_review_decision']['automatic_repair'] is False
    combined = quality.combine({'status': 'pass', 'issues': []}, result, semantic_required=True)
    assert combined['eligible_for_selection'] == (status == 'pass')


def test_pending_independent_evidence_cannot_be_promoted_by_classified_issue(tmp_path):
    path = tmp_path / 'candidate.png'; path.write_bytes(b'actual candidate')
    result = policy.normalize(marked_project(), {'status': 'unavailable', 'issues': [classified_issue()],
        'detail': 'Temporal samples do not prove this concern.'}, path, {'id': 'S01'})
    assert result['status'] == 'unavailable'
    assert not result['media_review_decision']['automatic_repair']
    assert result['detail'] == 'Temporal samples do not prove this concern.'


def reviewed_case(data, issues):
    p, config, target, qa, previous, root = data
    p['creative_quality_contract'] = {'version': 'ai-studio.creative-quality.v1'}
    from factory.production_execution import DEFAULT_POLICY
    config['production_recovery'] = copy.deepcopy(DEFAULT_POLICY)
    media = target['references'][0]
    contract = policy.binding(p, target['shot'], media)
    previous['output'] = {'sha256': sha256(media)}
    semantic = {'status': 'fail', 'issues': issues, 'media_review_contract': contract}
    qa.update(status='fail', issues=issues, technical={'status': 'pass', 'issues': []}, semantic=semantic)
    return p, config, target, qa, previous, root


@pytest.mark.parametrize('kind', ['image', 'video'])
@pytest.mark.parametrize('nature', ['CRAFT', 'AESTHETIC'])
def test_recovery_rejects_subjective_low_quality_even_with_blocking_code(data, kind, nature):
    p, config, target, qa, previous, root = reviewed_case(data, [classified_issue(nature, code='low_quality')])
    target['kind'] = kind
    before = copy.deepcopy(qa)
    result = recovery.prepare_repair(p, config, target, qa, previous_attempt=previous, root=root)
    assert result['action'] == 'stop' and result['reason'] == 'media_objective_evidence_missing'
    assert qa == before


@pytest.mark.parametrize('mutation', ['none', 'choice', 'minor_choice', 'stale_media', 'stale_revision', 'stale_shot', 'stale_camera', 'missing_location'])
def test_recovery_accepts_only_current_objective_major_evidence(data, mutation):
    items = [classified_issue(code='action'), classified_issue('AESTHETIC', code='low_quality')]
    p, config, target, qa, previous, root = reviewed_case(data, items)
    if mutation == 'choice': items[0]['requires_choice'] = True
    if mutation == 'minor_choice': items[1].update(requires_choice=True, severity='minor')
    if mutation == 'stale_media': qa['semantic']['media_review_contract']['media_sha256'] = '0' * 64
    if mutation == 'stale_revision': qa['semantic']['media_review_contract']['production_revision'] -= 1
    if mutation == 'stale_shot': target['shot']['action'] = 'A newly approved different action.'
    if mutation == 'stale_camera': target['shot']['camera_motion'] = 'pan_left'
    if mutation == 'missing_location': items[0].pop('location')
    result = recovery.prepare_repair(p, config, target, qa, previous_attempt=previous, root=root)
    if mutation == 'none':
        assert result['action'] == 'refine_motion'
        assert result['reason'].endswith('_action') and 'low_quality' not in result['reason']
        assert result['source']['assessment']['issues'] == items
    else:
        assert result['action'] == 'stop'
        assert result['reason'] == ('media_goal_requires_choice' if mutation in {'choice', 'minor_choice'} else 'media_objective_evidence_missing')


def test_measured_objective_failure_keeps_blocking_and_repair_with_subjective_advice(data):
    p, config, target, qa, previous, root = reviewed_case(data, [classified_issue('AESTHETIC')])
    defect = {'code': 'black_frame', 'severity': 'error', 'detail': 'Measured luminance is zero.'}
    qa['technical'] = {'status': 'fail', 'issues': [defect]}
    qa['issues'].append(defect)
    result = recovery.prepare_repair(p, config, target, qa, previous_attempt=previous, root=root)
    assert result['action'] == 'refine_motion' and result['reason'].endswith('_black_frame')
    combined = quality.combine(qa['technical'], {'status': 'pass', 'issues': [classified_issue('AESTHETIC')]}, True)
    assert combined['status'] == 'fail' and not combined['eligible_for_selection']


def test_objective_character_reference_repair_uses_its_actual_review_context(data):
    p, config, target, qa, previous, root = reviewed_case(data, [classified_issue(code='identity')])
    target['kind'] = 'image'
    context = target.pop('shot')
    context.update(review_purpose='character_reference', approved_reference_requirements={'identity': 'An adult woman with the approved white cup.'})
    target['review_context'] = context
    qa['semantic']['media_review_contract'] = policy.binding(p, context, target['references'][0])
    result = recovery.prepare_repair(p, config, target, qa, previous_attempt=previous, root=root)
    assert result['action'] == 'refine_start_frame'
    assert result['source']['media_review_target']['shot_id'] == context['id']


def test_unmarked_legacy_media_keeps_original_review_contract(tmp_path):
    raw = {'status': 'fail', 'score': 15, 'issues': [classified_issue('AESTHETIC')]}
    assert policy.normalize({}, raw, tmp_path / 'absent.png', {}) is raw


@pytest.mark.parametrize('check', ['mouth', 'camera'])
def test_existing_independent_objective_checks_still_block_new_contract(tmp_path, monkeypatch, check):
    path = tmp_path / 'candidate.mp4'; path.write_bytes(b'actual video fixture')
    sampling = {'timestamps': [0., 1., 2., 3.]}
    monkeypatch.setattr('factory.evaluation_sampling.sample_video', lambda *a, **k:
        ([{'image': str(i)} for i in range(4)], sampling))
    monkeypatch.setattr(llm, '_image_url', lambda _: 'data:image/png;base64,ZmFrZQ==')
    calls = []
    def request(*args, **kwargs):
        calls.append(args[4])
        if len(calls) == 1:
            return {'status': 'pass', 'score': 99, 'summary': 'General review passes', 'issues': []}
        if check == 'mouth':
            return {'observations': [{'sample': i, 'mouth': 'open' if i == 4 else 'closed',
                                     'evidence': 'A visible mouth gap.' if i == 4 else 'Visible lips touch.'}
                                    for i in range(1, 5)]}
        return {'motion': 'push_in', 'from_sample': 1, 'to_sample': 4,
                'evidence': 'The fixed background column grows larger across these samples.'}
    monkeypatch.setattr(llm, '_request', request)
    shot = {'id': 'S01', **({'prompt': 'Visible mouths stay closed.'} if check == 'mouth' else {'camera_motion': 'hold'})}
    config = {'llm': {'model': 'fixture'}, 'production': {
        'production_recovery': {'version': recovery.VERSION, 'enabled': True}}}
    result = llm.review_media(marked_project(), shot, path, [], config, output_dir=tmp_path / 'review')
    assert result['status'] == 'fail' and result['media_review_decision']['automatic_repair']
    objective = [i for i in result['issues'] if policy.repairable(i, result['media_review_contract'])]
    assert objective and objective[-1]['evidence_refs'] == (['candidate:3'] if check == 'mouth' else ['candidate:0', 'candidate:3'])
    assert len(calls) == 2


@pytest.mark.parametrize('entry', ['critic', 'qa'])
def test_media_byte_change_during_review_never_authorizes_a_retake(tmp_path, monkeypatch, entry):
    path = tmp_path / 'candidate.png'; Image.new('RGB', (128, 128), 'red').save(path)
    original_sha = sha256(path)
    def changed(*args, **kwargs):
        Image.new('RGB', (128, 128), 'blue').save(path)
        return {'status': 'fail', 'score': 20, 'summary': 'Objective fixture', 'issues': [classified_issue()]}
    if entry == 'critic':
        monkeypatch.setattr(llm, '_request', changed)
        result = llm.review_media(marked_project(), {'id': 'S01'}, path, [], {'llm': {'model': 'fixture'}})
        assert result['media_review_contract']['media_sha256'] == original_sha
    else:
        result = quality.semantic_review(changed, marked_project(), {'id': 'S01'}, path, [])
    assert result['status'] == 'unavailable' and not result['media_review_decision']['automatic_repair']
