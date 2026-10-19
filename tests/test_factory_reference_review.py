"""Independent same-pixel checks can refute invented canon, never real defects."""
import copy
import json
from pathlib import Path

import pytest
from PIL import Image

from factory import llm, reference_review
from studio.core import read_json, sha256, media_receipt, write_json
from test_factory_reference_repairs import engine

CANON = {'code': 'identity', 'severity': 'blocking', 'detail': '与原作标志形象不一致，缺少帝王古风威严'}
WEAPON = {'code': 'off_prompt', 'severity': 'blocking', 'detail': '手里是短棍而非批准的宽厚重尺'}


def verdict(issues=(), score=95):
    return {'status': 'fail' if issues else 'pass', 'score': score, 'summary': '独立候选参考图审核',
            'issues': copy.deepcopy(list(issues))}


def finding(issue_id='I1', status='refuted', basis='unsupported_requirement', citations=None):
    return {'issue_id': issue_id, 'status': status, 'basis_status': basis,
            'fact_citations': citations or [], 'evidence_ids': ['F0'],
            'detail': 'F0中可见批准的蓝衣与宽厚矩形装备，实际批准事实没有古风帝王装饰或原作人脸要求'}


def review(tmp_path, monkeypatch, *, answer=None, initial=None, small=False, missing=False):
    path = tmp_path / 'image.png'
    Image.new('RGB', (288, 512), 'navy').save(path)
    calls = []
    initial = copy.deepcopy(initial or verdict([CANON], 35))

    def request(config, role, system, payload, schema, output_dir, **kwargs):
        assert role == 'visual_critic'
        calls.append({'system': system, 'context': json.loads(kwargs['content'][0]['text']),
                      'content': copy.deepcopy(kwargs['content']), 'folder': str(output_dir)})
        if len(calls) == 1:
            return copy.deepcopy(initial)
        if isinstance(answer, Exception):
            raise answer
        return copy.deepcopy(answer or {'review': verdict(), 'findings': [finding()]})

    monkeypatch.setattr(llm, '_request', request)
    identity = {'model': 'fixture', 'profile': 'fixture'}
    if small:
        identity['context_tokens'] = 100
        monkeypatch.setattr('factory.providers.context_budget', lambda *args: {'fits': False})
    monkeypatch.setattr(llm, 'resolve_llm', lambda *args, **kwargs: identity)
    monkeypatch.setattr('factory.config.resolve_llm', lambda *args, **kwargs: identity)
    shot = {'id': 'reference', 'review_purpose': 'character_reference',
        'prompt': 'Original approved facts, not a generated prompt.',
        'approved_reference_requirements': {'entity': {'name': '林青', 'identity_constraints': '成年女性，短发',
            'default_wardrobe': '蓝衣，携带宽厚重尺'},
            'related_known_props': [{'name': '重尺', 'description': '宽厚矩形尺体'}],
            'visual_rules': '水彩画风', 'purpose': 'Single static identity reference'}}
    result = llm.review_media({'id': 'fixture', 'slug': 'factory-reference', 'format': 'motion_comic'},
                             shot, path, [], {'quality': {'minimum_score': 60}}, output_dir=tmp_path / 'qa')
    return result, calls, path


def test_invented_canon_requires_actual_independent_same_pixel_review(tmp_path, monkeypatch):
    result, calls, path = review(tmp_path, monkeypatch)
    assert len(calls) == 2 and result['status'] == 'pass'
    audit = result['reference_verification']
    assert audit['initial_review']['issues'] == [CANON]
    assert audit['review']['issues'] == [] and audit['status'] == 'verified'
    assert audit['source_sha256'] == sha256(path)
    payload = calls[1]['context']
    assert payload['reference_evidence']['supplied_image_count'] == 0
    assert payload['candidate_reference_claims_untrusted'][0]['claim'] == CANON
    assert payload['approved_facts'] and all('fact_id' in item for item in payload['approved_facts'])
    assert [item['image_url']['url'] for item in calls[0]['content'] if item['type'] == 'image_url'] == [
        item['image_url']['url'] for item in calls[1]['content'] if item['type'] == 'image_url']
    assert read_json(tmp_path / 'qa' / audit['verification_record']) == audit
    assert '原著' in calls[1]['system'] and '短棍/短剑' in calls[1]['system']
    assert 'reference_evidence' in Path(calls[1]['folder']).parts


def test_supported_wrong_weapon_stays_blocking_even_if_followup_omits_it(tmp_path, monkeypatch):
    check = finding(status='confirmed', basis='supported_requirement', citations=[{'fact_id': 'A003', 'quote': '宽厚重尺'}])
    result, calls, path = review(tmp_path, monkeypatch,
        initial=verdict([WEAPON], 35), answer={'review': verdict(), 'findings': [check]})
    assert len(calls) == 2 and result['status'] == 'fail'
    assert WEAPON in result['issues'] and path.is_file()


@pytest.mark.parametrize('problem', ['false_quote', 'no_basis', 'missing_issue', 'duplicate_issue', 'fake_pixels', 'contradiction', 'downgraded_claim', 'transport'])
def test_invalid_or_unavailable_second_check_preserves_original_and_stops_for_review(tmp_path, monkeypatch, problem):
    checks = [finding()]
    response = {'review': verdict(), 'findings': checks}
    if problem == 'false_quote':
        checks[0].update(status='confirmed', basis_status='supported_requirement',
                         fact_citations=[{'fact_id': 'A001', 'quote': '原著帝王古装'}])
    elif problem == 'no_basis':
        checks[0].update(status='confirmed', basis_status='supported_requirement')
    elif problem == 'missing_issue':
        response['findings'] = []
    elif problem == 'duplicate_issue':
        response['findings'] = [finding(), finding()]
    elif problem == 'fake_pixels':
        checks[0]['evidence_ids'] = ['F9']
    elif problem == 'contradiction':
        response['review'] = verdict([CANON])
    elif problem == 'downgraded_claim':
        response['review'] = verdict([{**CANON, 'severity': 'minor'}])
    else:
        response = RuntimeError('fixture unavailable')
    result, calls, path = review(tmp_path, monkeypatch, answer=response)
    assert len(calls) == 2 and result['status'] == 'unavailable'
    assert result['issues'] == [CANON] and path.is_file()
    assert result['reference_verification']['initial_review']['issues'] == [CANON]
    assert result['reference_verification']['status'] == 'unavailable'


def test_uncertain_second_check_keeps_original_issue_and_pixels_without_approval(tmp_path, monkeypatch):
    response = {'review': verdict(), 'findings': [finding(status='uncertain', basis='insufficient_evidence')]}
    result, _, path = review(tmp_path, monkeypatch, answer=response)
    assert result['status'] == 'unavailable' and CANON in result['issues'] and path.exists()
    assert result['reference_verification']['status'] == 'uncertain'


def test_confirmed_real_problem_cannot_be_cleared_while_other_canon_claim_is_uncertain(tmp_path, monkeypatch):
    response = {'review': verdict(), 'findings': [
        finding(status='uncertain', basis='insufficient_evidence'),
        finding('I2', 'confirmed', 'supported_requirement', [{'fact_id': 'A003', 'quote': '宽厚重尺'}])]}
    result, _, _ = review(tmp_path, monkeypatch, initial=verdict([CANON, WEAPON]), answer=response)
    assert result['status'] == 'fail' and WEAPON in result['issues']


def test_context_limit_keeps_all_facts_and_preserves_original_instead_of_resubmitting(tmp_path, monkeypatch):
    result, calls, path = review(tmp_path, monkeypatch, small=True)
    assert result['status'] == 'unavailable' and len(calls) == 1 and path.exists()
    assert result['reference_verification']['reason'] == 'reference_review_context_too_small'


def test_minor_unsupported_comparison_is_also_rechecked(tmp_path, monkeypatch):
    minor = {**CANON, 'severity': 'minor'}
    result, calls, _ = review(tmp_path, monkeypatch, initial=verdict([minor]))
    assert len(calls) == 2 and result['status'] == 'pass'
    assert result['reference_verification']['initial_review']['issues'] == [minor]


def test_clean_reference_does_not_add_model_call(tmp_path, monkeypatch):
    result, calls, _ = review(tmp_path, monkeypatch, initial=verdict())
    assert result['status'] == 'pass' and len(calls) == 1 and 'reference_verification' not in result


def test_policy_identity_changes_reference_qa_only_even_with_explicit_policy(engine, monkeypatch):
    from test_factory_reference_repairs import reference_target, CASES
    from factory.production import _Run
    run = _Run(engine.project, {**engine.config, 'semantic_review_revision': 'explicit-unchanged-policy'},
               None, None, engine.evaluator)
    first = reference_target(run, 'character_reference', CASES[0][1])
    plain1 = run._target('plain', run.folder / 'plain', 'Ordinary shot prompt')
    monkeypatch.setattr(reference_review, 'POLICY', 'approved-reference-evidence.next')
    second = reference_target(run, 'character_reference', CASES[0][1])
    plain2 = run._target('plain', run.folder / 'plain', 'Ordinary shot prompt')
    assert first['signature'] == second['signature']
    assert first['review_signature'] != second['review_signature']
    assert plain1['review_signature'] == plain2['review_signature']
    changed_purpose = run._target(first['key'], first['folder'], first['prompt'], review=True,
        review_context={**first['review_context'], 'review_purpose': 'prop_reference'})
    assert changed_purpose['signature'] == second['signature']
    assert changed_purpose['review_signature'] != second['review_signature']


@pytest.mark.parametrize('confirmed_blocking', [False, True])
def test_unordered_findings_map_severity_by_issue_id_not_position(tmp_path, monkeypatch, confirmed_blocking):
    minor = {**CANON, 'severity': 'minor'}
    check_minor = finding('I1', 'refuted' if confirmed_blocking else 'confirmed',
        'unsupported_requirement' if confirmed_blocking else 'supported_requirement',
        [] if confirmed_blocking else [{'fact_id': 'A003', 'quote': '宽厚重尺'}])
    check_weapon = finding('I2', 'confirmed' if confirmed_blocking else 'uncertain',
        'supported_requirement' if confirmed_blocking else 'insufficient_evidence',
        [{'fact_id': 'A003', 'quote': '宽厚重尺'}] if confirmed_blocking else [])
    result, _, _ = review(tmp_path, monkeypatch, initial=verdict([minor, WEAPON]),
        answer={'review': verdict(), 'findings': [check_weapon, check_minor]})
    assert result['status'] == ('fail' if confirmed_blocking else 'unavailable')
    assert WEAPON in result['issues']


def test_failed_existing_reference_reuses_same_take_for_new_policy_without_gpu(engine, monkeypatch):
    from test_factory_reference_repairs import reference_target, CASES
    run = engine
    original = reference_target(run, 'character_reference', CASES[0][1])
    attempt = run._new_attempt(original)
    folder = original['folder'] / attempt['take']
    path = folder / 'image.png'
    Image.new('RGB', run.image_size, 'navy').save(path)
    receipt = media_receipt(path, source_signature=original['signature'])
    write_json(folder / 'result.json', receipt)
    old_review = verdict([CANON], 35)
    attempt.update(state='failed', output=receipt, review_signature=original['review_signature'],
        qa={'technical': {'status': 'pass'}, 'semantic': old_review},
        semantic_attempts=[{'attempt': 1, 'assessment': copy.deepcopy(old_review)}])
    run._save_attempt(original, attempt)
    digest = sha256(path)
    monkeypatch.setattr(reference_review, 'POLICY', 'approved-reference-evidence.changed')
    run.evaluator = lambda *args: verdict()
    updated = reference_target(run, 'character_reference', CASES[0][1])
    assert updated['signature'] == original['signature']
    assert updated['review_signature'] != original['review_signature']
    run._render_targets([updated], 'shared_references')
    selected = updated['selected']
    assert selected['path'] == receipt['path'] and selected['sha256'] == digest
    assert selected['take'] == attempt['take'] and len(list(original['folder'].glob('take_*'))) == 1
    persisted = read_json(folder / 'attempt.json')
    assert persisted['semantic_attempts'][0]['assessment'] == old_review
    assert len(persisted['semantic_attempts']) == 2


def test_changed_candidate_during_verification_cannot_be_approved(tmp_path, monkeypatch):
    from factory.reference_review import verify_reference
    path = tmp_path / 'candidate.png'
    Image.new('RGB', (288, 512), 'navy').save(path)
    def transport(*args, **kwargs):
        Image.new('RGB', (288, 512), 'red').save(path)
        return {'review': verdict(), 'findings': [finding()]}
    monkeypatch.setattr(llm, '_request', transport)
    monkeypatch.setattr('factory.config.resolve_llm', lambda *args, **kwargs: {'model': 'fixture'})
    context = {'shot': {'review_purpose': 'character_reference', 'prompt': 'Adult woman in blue coat.'}}
    result = verify_reference({}, llm.REVIEW_SCHEMA, context, [], verdict([CANON]), path, tmp_path / 'qa')
    assert result['_reference_unavailable'] is True
    assert result['reference_verification']['reason'] == 'reference_review_source_changed'
    assert CANON in result['issues']


def test_explicit_3d_clarification_and_source_records_reach_independent_reference_check(tmp_path, monkeypatch):
    path = tmp_path / 'candidate.png'
    Image.new('RGB', (288, 512), 'navy').save(path)
    resolved = {'description': '本次制作方明确采用三维3D动画角色及场景，保留批准蓝衣和宽厚重尺，不增加剧情。',
                'source_records': [{'title': '批准的动画媒介来源记录', 'url': 'https://example.com/approved-animation',
                                    'excerpt': '本次媒介为三维动画', 'scope': 'medium_only'}]}
    original = copy.deepcopy(resolved)
    false_2d = {'code': 'off_prompt', 'severity': 'blocking',
                'detail': '该系列通常是2D或半写实，所以当前三维角色不符合原作'}
    calls = []
    def transport(config, role, system, payload, schema, output_dir, **kwargs):
        context = json.loads(kwargs['content'][0]['text'])
        calls.append(context)
        assert context['resolved_visual_style'] == resolved
        assert '系列名本身不能证明二维' in context['visual_style_resolution_rule']
        if len(calls) == 1:
            return verdict([false_2d], 35)
        assert '不能从某系列通常如何的记忆补出媒介要求' in system
        style_facts = [item for item in context['approved_facts'] if item['field'] == 'resolved_visual_style.description']
        assert style_facts == [{'fact_id': style_facts[0]['fact_id'],
                                'field': 'resolved_visual_style.description', 'text': resolved['description']}]
        assert any(item['field'] == 'resolved_visual_style.source_records[0].url'
                   and item['text'] == resolved['source_records'][0]['url'] for item in context['approved_facts'])
        check = finding(status='refuted', basis='supported_requirement', citations=[
            {'fact_id': style_facts[0]['fact_id'], 'quote': '三维3D动画角色及场景'}])
        check['detail'] = 'F0候选采用三维动画表现，符合本次制作方明确澄清的3D媒介；旧意见从系列名猜二维没有给定依据'
        return {'review': verdict(), 'findings': [check]}
    monkeypatch.setattr(llm, '_request', transport)
    monkeypatch.setattr(llm, 'resolve_llm', lambda *args, **kwargs: {'model': 'fixture', 'profile': 'fixture'})
    monkeypatch.setattr('factory.config.resolve_llm', lambda *args, **kwargs: {'model': 'fixture', 'profile': 'fixture'})
    result = llm.review_media({'id': 'fixture', 'slug': 'factory-reference', 'format': 'motion_comic',
            'resolved_visual_style': resolved},
        {'id': 'reference', 'review_purpose': 'character_reference',
         'approved_reference_requirements': {'visual_rules': '保持某系列视觉一致性',
             'entity': {'name': '林青', 'default_wardrobe': '蓝衣与宽厚重尺'}}},
        path, [], {'quality': {'minimum_score': 60}}, output_dir=tmp_path / 'qa')
    assert result['status'] == 'pass' and len(calls) == 2
    assert result['reference_verification']['initial_review']['issues'] == [false_2d]
    assert result['reference_verification']['policy'] == 'approved-reference-evidence.v2'
    assert resolved == original


def test_missing_visual_clarification_keeps_legacy_context_compatible(tmp_path, monkeypatch):
    result, calls, _ = review(tmp_path, monkeypatch)
    assert result['status'] == 'pass'
    assert all('resolved_visual_style' not in item['context'] for item in calls)
    assert not any(item['field'].startswith('resolved_visual_style') for item in calls[1]['context']['approved_facts'])


def test_visual_clarification_changes_reference_qa_identity_without_changing_image_or_other_targets(engine):
    from test_factory_reference_repairs import reference_target, CASES
    run = engine
    first = reference_target(run, 'character_reference', CASES[0][1])
    plain1 = run._target('plain', run.folder / 'plain', 'Ordinary prompt')
    run.project['resolved_visual_style'] = {'description': '本次采用三维3D动画',
        'source_records': [{'title': '制作方媒介澄清', 'scope': 'medium_only'}]}
    second = reference_target(run, 'character_reference', CASES[0][1])
    plain2 = run._target('plain', run.folder / 'plain', 'Ordinary prompt')
    assert first['signature'] == second['signature']
    assert first['review_signature'] != second['review_signature']
    assert plain1['signature'] == plain2['signature'] and plain1['review_signature'] == plain2['review_signature']


def test_candidate_and_repeat_verification_records_do_not_overwrite_each_other(tmp_path, monkeypatch):
    from factory.reference_review import verify_reference
    path = tmp_path / 'candidate.png'
    Image.new('RGB', (288, 512), 'navy').save(path)
    folders = []
    def transport(*args, **kwargs):
        folders.append(Path(args[5]))
        return {'review': verdict(), 'findings': [finding()]}
    monkeypatch.setattr(llm, '_request', transport)
    monkeypatch.setattr('factory.config.resolve_llm', lambda *args, **kwargs: {'model': 'fixture'})
    context = {'shot': {'review_purpose': 'character_reference', 'prompt': 'Adult woman in blue coat.'}}
    first = verify_reference({}, llm.REVIEW_SCHEMA, context, [], verdict([CANON]), path, tmp_path / 'qa')
    second = verify_reference({}, llm.REVIEW_SCHEMA, context, [], verdict([CANON]), path, tmp_path / 'qa')
    Image.new('RGB', (288, 512), 'red').save(path)
    third = verify_reference({}, llm.REVIEW_SCHEMA, context, [], verdict([CANON]), path, tmp_path / 'qa')
    audits = [item['reference_verification'] for item in (first, second, third)]
    assert len({item['verification_id'] for item in audits}) == 3
    assert audits[0]['verification_basis_sha256'] == audits[1]['verification_basis_sha256']
    assert audits[0]['verification_basis_sha256'] != audits[2]['verification_basis_sha256']
    assert len(set(folders)) == 3
    for audit in audits:
        saved = read_json(tmp_path / 'qa' / audit['verification_record'])
        assert saved == audit and saved['initial_review']['issues'] == [CANON]
