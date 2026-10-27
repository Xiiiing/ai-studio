import copy
import pytest
from jsonschema import validate, ValidationError
from factory import creative_contracts, story_coverage as coverage, schemas


def imported():
    return creative_contracts.initialize({'id': '111122223333', 'source_text': '# 人物设定\n甲为成年邮差。\n# 正文\n第1集\n甲把钥匙交给乙。\n乙打开房门。\n第2集\n两人发现旧信。'})


def test_frozen_original_retains_offsets_and_excludes_reference_metadata():
    p = imported(); rows = coverage.source_catalog(p)
    required = [r for r in rows if r['required']]
    assert [r['text'] for r in required] == ['甲把钥匙交给乙。', '乙打开房门。', '两人发现旧信。']
    assert [r['episode_number'] for r in required] == [1, 1, 2]
    assert all(p['source_text'][r['start']:r['end']] == r['text'] for r in rows)
    p['source_text'] = '后来编辑的新文本'
    assert coverage.source_catalog(p) == rows
    p['creative_source_document']['text'] = '被篡改'
    with pytest.raises(ValueError, match='冻结来源哈希'): coverage.source_catalog(p)


def test_missing_source_is_detected_across_full_drama_plan():
    p = imported(); source = [r for r in coverage.source_catalog(p) if r['required']]
    plan = {'episodes': [{'number': 1, 'beats': [{'id': 'B1', 'description': '递交钥匙', 'source_refs': [source[0]['id']]}]}]}
    assert any('未被任何分集' in v for v in coverage.plan_issues(p, plan))
    plan['episodes'][0]['beats'][0]['source_refs'] = [r['id'] for r in source]
    assert not coverage.plan_issues(p, plan)
    plan['episodes'][0]['beats'][0]['source_refs'].append('src_missing')
    assert any('来源无效' in v for v in coverage.plan_issues(p, plan))


def test_beat_labels_do_not_certify_semantics_and_stale_reviews_do_not_apply():
    p = creative_contracts.initialize({'id': '111122223333', 'outline': {'beats': [{'id': 'B1', 'description': '甲把钥匙交给乙'}]}})
    ep = {'shots': [{'id': 'S01', 'action': '甲没有递交钥匙', 'beat_ids': ['B1']}]}
    assert coverage.inspect(p, ep)['status'] == 'mapped'
    assert coverage.inspect(p, ep)['semantic_status'] == 'not_reviewed'
    p['draft_review'] = {'source_signature': coverage.fingerprint(ep), 'coverage_checks': {'B1': {'status': 'fail', 'reason': '动作没有发生'}}}
    assert coverage.inspect(p, ep)['semantic_status'] == 'text_fail'
    ep['shots'][0]['action'] = '甲把钥匙交给乙'
    assert coverage.inspect(p, ep)['semantic_status'] == 'not_reviewed'


def test_schema_rejects_unknown_event_and_checker_finds_missing_duplicate():
    p = creative_contracts.initialize({'outline': {'beats': [{'id': 'B1', 'description': '甲把钥匙交给乙'}]}})
    schema = coverage.attach_schema({'properties': {'shots': {'items': {'properties': {'action': {}}, 'required': ['action']}}}}, p)
    assert schema['properties']['shots']['items']['properties']['beat_ids']['items']['enum'] == ['B1']
    assert coverage.inspect(p, {'shots': [{'id': 'S01', 'beat_ids': []}]})['missing'] == ['B1']
    bad = coverage.inspect(p, {'shots': [{'id': 'S01', 'beat_ids': ['B1', 'B1', 'B2']}]})
    assert bad['status'] == 'fail' and bad['invalid']


def test_source_model_context_contains_every_byte_segment_no_silent_truncation():
    text = '甲' * 6002
    p = creative_contracts.initialize({'id': '111122223333', 'source_text': text})
    rows = coverage.request_context(p)['source_catalog']
    assert ''.join(r['text'] for r in rows) == text
    assert len(rows) == 3


def test_independent_coverage_failure_yields_grounded_directed_repair():
    p = creative_contracts.initialize({'id': '111122223333', 'outline': {'beats': [{'id': 'B1', 'description': '递交钥匙'}]}})
    ep = {'shots': [{'id': 'S01', 'action': '两人站立', 'beat_ids': ['B1']}]}
    _, _, schema = creative_contracts.review_call(p, 'independent_critic', 'review', {'episode': ep}, schemas.SCRIPT_REVIEW_SCHEMA)
    assert schema['properties']['coverage_checks']['required'] == ['B1']
    result = creative_contracts.normalize(p, {'issues': [], 'coverage_checks': {'B1': {'status': 'fail', 'evidence_refs': ['S01'], 'reason': '正文没有交付动作'}}}, ep)
    assert creative_contracts.can_repair(p, result)
    assert result['repair_shot_ids'] == ['S01']
    result = creative_contracts.normalize(p, {'issues': [], 'coverage_checks': {'B1': {'status': 'uncertain', 'evidence_refs': ['S01'], 'reason': '正文无法确认'}}}, ep)
    assert not creative_contracts.can_repair(p, result)


def test_chinese_episode_ordinals_and_scene_slugs_are_scoped_without_inventing_events():
    p = creative_contracts.initialize({'id': '111122223333', 'source_text': '第十一集 夜归\n内景 车站 夜\n甲发现旧信。\n第十二集 真相\n乙归还钥匙。'})
    rows = [r for r in coverage.source_catalog(p) if r['required']]
    assert [(r['text'], r['episode_number']) for r in rows] == [('甲发现旧信。', 11), ('乙归还钥匙。', 12)]
    p['episode_number'] = 11
    assert '乙归还钥匙。' not in [r['text'] for r in coverage.scoped_sources(p)]


def test_semantic_evidence_must_belong_to_the_event_carrier():
    p = creative_contracts.initialize({'id': '111122223333', 'outline': {'beats': [{'id': 'B1', 'description': '递交钥匙'}]}})
    ep = {'shots': [{'id': 'S01', 'action': '递交钥匙', 'beat_ids': ['B1']}, {'id': 'S02', 'action': '无关空景', 'beat_ids': []}]}
    _, _, schema = creative_contracts.review_call(p, 'independent_critic', 'review', {'episode': ep}, schemas.SCRIPT_REVIEW_SCHEMA)
    assert schema['properties']['coverage_checks']['properties']['B1']['properties']['evidence_refs']['items']['enum'] == ['S01']
    result = creative_contracts.normalize(p, {'issues': [], 'coverage_checks': {'B1': {'status': 'pass', 'evidence_refs': ['S02'], 'reason': '空景正常'}}}, ep)
    assert result['coverage_checks']['B1']['status'] == 'uncertain'
    assert not creative_contracts.can_repair(p, result)


def test_full_versioned_plan_schema_accepts_outline_source_refs():
    from factory.drama_planning import plan_schema
    from tests.test_factory_dramas import plan
    p = imported(); source = [r['id'] for r in coverage.source_catalog(p) if r['required']]
    value = plan()
    for outline in value['episodes']:
        outline['beats'] = [{'id': f'B{outline["number"]}_{i}', 'description': '人物完成当前必要事件', 'source_refs': source} for i in (1, 2)]
    validate(value, plan_schema(len(value['episodes']), p))
    assert not coverage.plan_issues(p, value)
