import copy
import pytest
from jsonschema import validate, ValidationError
from factory import creative_contracts as policy, schemas


def project():
    return policy.initialize({'id': '111122223333'})


def episode():
    return {'shots': [{'id': 'S01', 'action': '甲把钥匙交给乙', 'dialogue': []}, {'id': 'S02', 'action': '乙拿着钥匙离开', 'dialogue': []}]}


def issue(nature='OBJECTIVE', **kwargs):
    return {'severity': 'major', 'code': 'ownership', 'description': '道具持有冲突', 'evidence': '钥匙已被交出',
        'suggestion': '修复钥匙的归属', 'shot_ids': ['S02'], 'nature': nature, 'location': 'S02',
        'impact': '观众无法理解持物来源', 'repair_target': '保持上一镜交付后的钥匙归属', 'evidence_refs': ['S01', 'S02'], 'requires_choice': False, **kwargs}


def test_legacy_call_remains_identical():
    payload = {'episode': episode()}
    result = policy.review_call({}, 'independent_critic', 'review', payload, schemas.SCRIPT_REVIEW_SCHEMA)
    assert result == ('review', payload, schemas.SCRIPT_REVIEW_SCHEMA)


def test_review_schema_requires_classification_and_valid_source_ids():
    system, payload, schema = policy.review_call(project(), 'independent_critic', 'review', {'episode': episode()}, schemas.SCRIPT_REVIEW_SCHEMA)
    assert payload['project']['review_evidence_catalog'][0]['id'] == 'S01'
    assert 'AESTHETIC' in str(system)
    value = {'scores': {k: 8 for k in schemas.SCORE_KEYS}, 'summary': '审核当前两个镜头的道具事实', 'strengths': [], 'issues': [issue()]}
    validate(value, schema)
    value['issues'][0]['evidence_refs'] = ['S99']
    with pytest.raises(ValidationError): validate(value, schema)


def test_aesthetic_critic_cannot_force_rewrite_but_scores_still_gate():
    raw = {'scores': {k: 8 for k in schemas.SCORE_KEYS}, 'issues': [issue('AESTHETIC')]}
    review = policy.normalize(project(), raw, episode())
    assert review['issues'][0]['severity'] == 'minor'
    assert not policy.can_repair(project(), review)
    assert schemas.review_passes(review)
    review['scores']['hook'] = 5
    assert not schemas.review_passes(review)
    assert not policy.can_repair(project(), review)
    assert raw['issues'][0]['severity'] == 'major'


def test_real_objective_problem_has_frozen_evidence_and_scoped_feedback():
    review = policy.normalize(project(), {'issues': [issue(), issue('CRAFT')]}, episode())
    assert policy.can_repair(project(), review)
    assert review['repair_shot_ids'] == ['S02']
    assert len(policy.repair_feedback(project(), review)['issues']) == 1
    assert review['issues'][0]['evidence_sources'][0]['sha256']
    assert review['source_signature'] == policy.fingerprint(episode())


def test_invalid_evidence_or_creative_choice_cannot_auto_repair():
    invalid = issue(); invalid['evidence_refs'] = ['S99']
    assert not policy.can_repair(project(), policy.normalize(project(), {'issues': [invalid]}, episode()))
    choice = issue(); choice['requires_choice'] = True
    review = policy.normalize(project(), {'issues': [choice]}, episode())
    assert not policy.can_repair(project(), review)
    review['scores'] = {k: 8 for k in schemas.SCORE_KEYS}
    assert not schemas.review_passes(review)

@pytest.mark.parametrize('requires_choice,expected_pass', [(False, True), (True, False)])
def test_actual_creative_loop_preserves_aesthetic_choice_without_automatic_rewrite(requires_choice, expected_pass):
    from factory import creative
    from tests.test_factory_creative import CreativeContractTests
    case = CreativeContractTests('runTest'); case.setUp()
    try:
        p = policy.initialize(case.approved_project())
        p['workflow_version'] = 2
        def reply(**kw):
            value = case.reply(**kw)
            if kw['role'] == 'independent_critic' and 'episode' in kw['payload']:
                note = issue('AESTHETIC'); note['requires_choice'] = requires_choice
                value['issues'] = [note]
            return value
        case.model.side_effect = reply
        result = creative.create_script_and_styles(p, case.config, None)
        assert result['script_review']['passed'] is expected_pass
        assert len(result['script_review']['rounds']) == 1
        assert sum(r['role'] == 'screenwriter' for r in case.calls) == 1
        assert bool(result['needs_decision']) is (not expected_pass)
    finally:
        case.tearDown(); case.doCleanups()


def test_adopted_contract_blocks_production_before_model_or_media(monkeypatch):
    from factory import workflows
    p = project(); p.update(creative_quality_review_required=True, production_revision=1, script_review={'passed': True})
    monkeypatch.setattr(workflows, '_project', lambda state: p)
    with pytest.raises(workflows.ReviewBlocked, match='新创作契约'):
        workflows.production({'project_id': p['id']})
