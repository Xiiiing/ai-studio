"""Exercise actual scene planning and persisted units; no model or GPU calls."""
import copy
from jsonschema import validate
from factory import creative_contracts, dramas, long_episodes, store, story_coverage
from test_factory_dramas import isolated, input_payload, plan


def test_imported_long_units_keep_approved_event_sources_and_isolate_other_episodes(isolated, monkeypatch):
    text = '# 人物设定\n甲为成年邮差。\n# 正文\n第一集\n甲找到钥匙并交给乙。\n乙用钥匙打开房门。\n第二集\n乙发现完全不同的支线。'
    root = dramas.create(input_payload(duration_seconds=300, episode_count=2, source_text=text))
    current = store.get(root['id'], False)
    creative_contracts.initialize(current)
    store.update(root['id'], {'creative_quality_contract': current['creative_quality_contract'],
        'creative_source_document': current['creative_source_document']})
    document = plan(2)
    sources = [s for s in story_coverage.source_catalog(current) if s['required'] and s['episode_number'] == 1]
    beats = [{'id': 'B1', 'description': '甲把原来的钥匙明确交给乙', 'source_refs': [sources[0]['id']]},
             {'id': 'B2', 'description': '乙使用同一把钥匙打开房门', 'source_refs': [sources[1]['id']]}]
    document['episodes'][0]['beats'] = copy.deepcopy(beats)
    episode = dramas.record('episode', '第一集', document['episodes'][0]['synopsis'],
        drama_id=current['id'], parent_id=current['id'], episode_number=1, series_key='coverage-series',
        plan_version=1, duration_seconds=300, plan=document, outline=document['episodes'][0], unit_ids=[],
        source_excerpt=dramas.source_excerpt(text, 1, 2))
    creative_contracts.initialize(episode, parent=current)
    with store.connection() as db:
        dramas.insert(db, episode)
    calls = []
    def model(project, config, role, stage, instructions, payload, schema, *args):
        calls.append(role)
        if role == 'independent_critic':
            for scene in payload['scenes']:
                approved = beats[int(scene['scene_id'][-1]) - 1]
                assert scene['beats'] == [approved]
            value = {'passed': True, 'issues': []}
        else:
            beat_schema = schema['properties']['scenes']['items']['properties']['beats']['items']
            assert set(beat_schema['properties']) == {'id', 'description'}
            value = {'scenes': [{'scene_id': f'C{i:02d}', 'title': f'场景{i}',
                'synopsis': '甲乙在房门前寻找线索，发现钥匙后共同打开房门。', 'seconds': 150,
                'characters': ['lead'], 'location_id': '', 'prop_ids': [],
                'opening_state': '两人在门前', 'ending_state': '打开房门',
                'beats': [{'id': event['id'], 'description': '模型擅自改写的事件描述应被丢弃'}]} for i, event in enumerate(beats, 1)]}
        validate(value, schema)
        return value
    monkeypatch.setattr(long_episodes, 'call', model)
    long_episodes.plan_units(store.get(episode['id'], False), {})
    saved = store.get(episode['id'], False)
    assert calls == ['screenwriter', 'independent_critic']
    assert len(saved['unit_ids']) == 4
    assigned = []
    for uid in saved['unit_ids']:
        unit = store.get(uid, False)
        assert unit['creative_quality_contract'] == current['creative_quality_contract']
        events = unit['outline']['beats']
        for event in events:
            assert event == next(b for b in beats if b['id'] == event['id'])
        assigned.extend(event['id'] for event in events)
        expected_refs = {ref for event in events for ref in event['source_refs']}
        actual_sources = story_coverage.request_context(unit)['source_catalog']
        assert {source['id'] for source in actual_sources if source['required']} == expected_refs
        assert all(source['episode_number'] in (None, 1) for source in actual_sources)
        assert '完全不同的支线' not in unit['source_excerpt']
        if not expected_refs: assert unit['source_excerpt'] == ''
        ep = {'shots': [{'id': 'S01', 'action': '只检查来源映射，非真实语义证据', 'beat_ids': [event['id'] for event in events]}]}
        report = story_coverage.inspect(unit, ep)
        assert report['status'] in {'mapped', 'not_applicable'}
        assert not report['missing'] and not report['invalid']
        assert report['semantic_status'] == 'not_reviewed'
    assert assigned == ['B1', 'B2']
