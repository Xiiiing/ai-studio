"""Evidence isolation and receipt integrity; fixtures do not certify aesthetics."""
import copy
import json
from pathlib import Path

import pytest

from factory import frame_observation as observation, llm
from studio.core import sha256


def row(description='One person seated in the center.'):
    return {'description': description, 'people': [{key: 'Visible fixture observation' for key in observation.FIELDS}]}


def test_each_image_is_observed_alone_and_cache_never_reuses_other_pixels(tmp_path):
    candidate, reference = tmp_path / 'candidate', tmp_path / 'reference'
    candidate.write_bytes(b'seated'); reference.write_bytes(b'standing')
    calls = []
    def request(*args, **kw):
        calls.append(kw['content'])
        return row()
    urls = lambda p: Path(p).read_text()
    first = observation.collect(candidate, [{'path': reference}], {}, tmp_path / 'cache', request, urls)
    assert len(calls) == 2
    assert [c[1]['image_url']['url'] for c in calls] == ['seated', 'standing']
    assert all(len(c) == 2 and c[0]['text'] == observation.TASK for c in calls)
    assert first['candidate_only_observation']['image_sha256'] == sha256(candidate)
    assert first['reference_only_observations'][0]['reference_index'] == 1
    assert observation.collect(candidate, [reference], {}, tmp_path / 'cache', request, urls) == first
    assert len(calls) == 2
    candidate.write_bytes(b'new pose')
    observation.collect(candidate, [reference], {}, tmp_path / 'cache', request, urls)
    assert len(calls) == 3


@pytest.mark.parametrize('mutation', ['changed_pixels', 'invalid_schema', 'corrupt_cache'])
def test_uncertain_or_changed_observation_never_becomes_verified_evidence(tmp_path, mutation):
    path = tmp_path / 'candidate'; path.write_bytes(b'original')
    def request(*a, **k):
        if mutation == 'changed_pixels': path.write_bytes(b'changed')
        return {} if mutation == 'invalid_schema' else row()
    if mutation == 'corrupt_cache':
        observation.observe(path, {}, tmp_path / 'cache', request, str)
        evidence = next((tmp_path / 'cache').rglob('observation.json'))
        saved = json.loads(evidence.read_text()); saved['observation'] = row('Tampered')
        evidence.write_text(json.dumps(saved))
    with pytest.raises(Exception):
        observation.observe(path, {}, tmp_path / 'cache', request, str)


@pytest.mark.parametrize('verdict', ['pass', 'fail'])
def test_static_critic_gets_current_contract_and_separate_pixel_evidence_without_waiving_failures(tmp_path, monkeypatch, verdict):
    path, ref = tmp_path / 'candidate.png', tmp_path / 'reference.png'
    path.write_bytes(b'candidate'); ref.write_bytes(b'reference')
    config = {'llm': {'model': 'fixture'}, 'production': {'production_recovery': {
        'version': 'ai-studio.production-recovery.v2', 'enabled': True,
        'max_asset_cycles': 2, 'max_project_cycles': 8, 'strategy_attempt_allowance': 2}}}
    shot = {'id': 'S01', 'prompt': 'Approved person sits centrally.', 'action': 'FUTURE_ACTION'}
    project = {'slug': 'factory-isolation', 'format': 'animation', 'episode': {'shots': [shot,
        {'id': 'S02', 'action': 'FUTURE_PROP', 'prompt': 'FUTURE_PROMPT'}]}}
    original = copy.deepcopy((project, shot))
    calls = []
    result = {'status': verdict, 'score': 90 if verdict == 'pass' else 20,
              'summary': 'Actual candidate reviewed', 'issues': [] if verdict == 'pass' else [
                  {'code': 'identity', 'severity': 'blocking', 'detail': 'Visible wrong hair color.'}]}
    def request(*args, **kwargs):
        content = kwargs['content']; calls.append(content)
        if args[4] == observation.SCHEMA: return row()
        if 'review' in args[4].get('properties', {}):
            return {'review': copy.deepcopy(result), 'findings': [{
                'issue_id': 'I1', 'status': 'confirmed', 'candidate_quote': 'Visible fixture observation',
                'reference_quotes': [{'reference_index': 1, 'quote': 'Visible fixture observation'}],
                'detail': 'The actual candidate and reference evidence confirm the mismatch.'}]}
        return copy.deepcopy(result)
    monkeypatch.setattr(llm, '_request', request)
    monkeypatch.setattr(llm, '_image_url', lambda p: str(p))
    answer = llm.review_media(project, shot, path, [ref], config, output_dir=tmp_path / 'review')
    assert len(calls) == (3 if verdict == 'pass' else 4)
    context = json.loads(calls[2][0]['text'])
    assert not context['approved_sequence'] and 'FUTURE_' not in json.dumps(context)
    assert context['shot']['prompt'] == shot['prompt']
    assert context['candidate_only_observation']['image_sha256'] == sha256(path)
    assert context['reference_only_observations'][0]['image_sha256'] == sha256(ref)
    assert answer['status'] == verdict and answer['issues'] == result['issues']
    assert (project, shot) == original


def test_changed_candidate_after_critic_cannot_pass(tmp_path, monkeypatch):
    path = tmp_path / 'candidate.png'; path.write_bytes(b'original')
    config = {'llm': {'model': 'fixture'}, 'production': {'production_recovery': {
        'enabled': True, 'version': 'ai-studio.production-recovery.v1'}}}
    def request(*args, **kwargs):
        if args[4] == observation.SCHEMA: return row()
        path.write_bytes(b'changed')
        return {'status': 'pass', 'score': 99, 'summary': 'Fixture', 'issues': []}
    monkeypatch.setattr(llm, '_request', request)
    monkeypatch.setattr(llm, '_image_url', str)
    result = llm.review_media({'slug': 'factory-changed', 'format': 'animation'}, {}, path, [], config,
                             output_dir=tmp_path / 'review')
    assert result['status'] == 'unavailable' and result['reason'] == 'static_review_pixels_changed'


@pytest.mark.parametrize('disposition', ['confirmed', 'refuted', 'uncertain', 'invented_quote'])
def test_same_pixel_claim_verification_preserves_failures_and_requires_real_citations(tmp_path, disposition):
    path = tmp_path / 'candidate'; path.write_bytes(b'actual candidate')
    context = {'candidate_only_observation': {'image_sha256': sha256(path), 'observation': row('A seated person.')},
               'reference_only_observations': [{'reference_index': 1, 'observation': row('A standing person.')} ]}
    issue = {'code': 'identity', 'severity': 'blocking', 'detail': 'Visible mismatch to check.'}
    initial = {'status': 'fail', 'score': 20, 'summary': 'Old claim', 'issues': [issue]}
    answer = {'review': {'status': 'pass', 'score': 90, 'summary': 'Independent candidate review', 'issues': []},
              'findings': [{'issue_id': 'I1', 'status': disposition if disposition != 'invented_quote' else 'refuted',
                'candidate_quote': 'Invented evidence' if disposition == 'invented_quote' else 'A seated person.',
                'reference_quotes': [{'reference_index': 1, 'quote': 'A standing person.'}],
                'detail': 'Actual candidate evidence compared to the independently observed reference.'}]}
    calls = []
    def request(*a, **kw): calls.append(kw['content']); return copy.deepcopy(answer)
    result = observation.verify_claims({}, context, llm.REVIEW_SCHEMA, initial, path, tmp_path, request, str)
    assert sum(x['type'] == 'image_url' for x in calls[0]) == 1
    assert result['static_verification']['initial_review'] == initial
    if disposition == 'confirmed': assert issue in result['issues']
    if disposition == 'refuted': assert not result['issues']
    assert result['static_verification']['status'] == ('unavailable' if disposition in {'uncertain', 'invented_quote'} else 'verified')


@pytest.mark.parametrize('routed', [False, True])
def test_reasoning_is_opt_in_and_keeps_observers_and_model_routing_unchanged(routed):
    profile = {'model': 'same-qwen', 'chat_template_kwargs': {'enable_thinking': False, 'existing': 'preserved'}}
    config = {'quality': {'static_review': {'thinking': True, 'max_tokens': 6000}},
              **({'llm_roles': {'visual_critic': 'vision'}, 'llm_profiles': {'vision': profile}}
                 if routed else {'llm': profile})}
    original = copy.deepcopy(config)
    result, tokens = observation.critic_configuration(config, 1600)
    actual = result['llm_profiles']['vision'] if routed else result['llm']
    assert tokens == 6000 and actual['model'] == 'same-qwen'
    assert actual['chat_template_kwargs'] == {'enable_thinking': True, 'existing': 'preserved'}
    assert config == original
    assert observation.critic_configuration({}, 1600) == ({}, 1600)


@pytest.mark.parametrize('setting', [{'thinking': 'yes'}, {'max_tokens': True}, {'max_tokens': 20000}])
def test_invalid_reasoning_settings_do_not_silently_change_quality_or_budget(setting):
    with pytest.raises(ValueError): observation.critic_configuration({'quality': {'static_review': setting}}, 1600)
