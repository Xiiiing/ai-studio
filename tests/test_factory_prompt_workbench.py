"""Prompt workbench contracts exercise actual selection, cache, HTTP and recovery."""
import copy
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from factory import prompt_workbench as w, store
from factory.prompt_registry import load_prompt, prompt_receipt, derive_prompt
from factory.profile_creative import adapt_system
from test_factory_team_ledger import project, http_client


def edition(key='creator@1.0.0', label='动作因果试验', **updates):
    content = w.template(key)['sections']
    content.update(updates)
    return w.save({'request_id': uuid.uuid4().hex, 'key': key, 'label': label, 'sections': content})


def idle(p):
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='cancelled' WHERE project_id=?", (p['id'],))
    return store.update(p['id'], {'status': 'paused', 'pause_requested': True})


def apply(p, e=None, overrides=None):
    return w.apply(p['id'], {'request_id': uuid.uuid4().hex, 'expected_revision': p['revision'],
        'versions': {e['key']: e['id']} if e else {}, 'overrides': overrides or w.EMPTY})


@pytest.fixture
def model(monkeypatch):
    from factory import config
    value = {'llm': {'base_url': 'http://127.0.0.1:8090/v1', 'model': 'fixture', 'context_tokens': 32768,
                    'provider': 'llama_cpp', 'context_validation': 'conservative_estimate',
                    'model_discovery': 'required', 'network_mode': 'loopback'}}
    monkeypatch.setattr(config, 'load_config', lambda: copy.deepcopy(value))
    return value


def test_http_inventory_preview_and_immutable_save(project, http_client, model):
    url = f"/api/factory/projects/{project['id']}/prompts"
    before = store.get(project['id'])
    status, inventory = http_client('GET', url)
    assert status == 200 and len(inventory['catalog']) == 16
    payload = {'key': 'creator@1.0.0', 'sections': w.template('creator@1.0.0')['sections'], 'input': {}}
    status, rendered = http_client('POST', url + '/preview', payload)
    assert status == 200 and '恰好' not in rendered['system']
    assert '{{' not in rendered['system'] and '15镜' in rendered['system']
    assert rendered['model']['model'] == 'fixture'
    assert rendered['prompt']['assembly']['segments'][0]['source'].startswith('edition:')
    assert store.get(project['id']) == before
    saved = {'request_id': uuid.uuid4().hex, 'key': payload['key'], 'sections': payload['sections'], 'label': '初版'}
    assert http_client('POST', url + '/save', saved) == http_client('POST', url + '/save', saved)
    assert len(w.inventory(project['id'])['editions']) == 1
    assert http_client('POST', url + '/save', {**saved, 'label': '另一版'})[0] == 409
    assert store.get(project['id']) == before


def test_applied_parameters_and_sources_are_used_by_real_cache_boundary(project, monkeypatch, tmp_path, model):
    from factory import schemas, llm
    from factory.production_profiles import normalize
    p = idle(project)
    store.update(p['id'], {'production_profile': normalize({'preset': 'compact6'}), 'duration_seconds': 30})
    p = store.get(p['id'], False)
    e = edition(role='你是故事作者。共{{shot_count}}镜，{{duration_seconds}}秒，人物{{character_ids}}。', repair='不得恢复已删除的雨伞')
    apply(p, e, {**w.EMPTY, 'project': '保留交接手套的因果'})
    p = store.get(p['id'], False)
    assert 'prompt_snapshot' not in schemas.project_context(p)
    assert schemas.project_context(p)['prompt_snapshot_sha256']
    calls = []
    monkeypatch.setattr(schemas, 'ROOT', tmp_path)
    monkeypatch.setattr(llm, 'json_call', lambda **kw: calls.append(kw) or {'ok': True})
    output = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok']}
    for _ in range(2):
        assert schemas.cached_json_call(p, model, 'creator', 'fixture', load_prompt('creator'), {}, output) == {'ok': True}
    assert len(calls) == 1
    actual = calls[0]['system']
    assert '共6镜，30秒，人物char_lead/char_partner' in actual
    assert '不得恢复已删除的雨伞' in actual and '保留交接手套的因果' in actual
    assert '15镜' not in actual and '{{' not in actual
    meta = prompt_receipt(actual, 'creator', {}, output)
    assert meta['assembly']['edition_id'] == e['id']
    assert 'production_profile' in meta['transforms']
    # Same rendered instructions in a distinct saved edition still invalidate cache.
    e2 = edition(role=e['sections']['role'], repair=e['sections']['repair'], label='同文新版本')
    apply(p, e2, {**w.EMPTY, 'project': '保留交接手套的因果'})
    p2 = store.get(p['id'], False)
    schemas.cached_json_call(p2, model, 'creator', 'fixture', load_prompt('creator'), {}, output)
    assert len(calls) == 2
    assert store.get(project['id'], False)['production_revision'] == project['production_revision']


def simulated_trial_success(pid, edition):
    # Protocol fixture only: never asserts that a real model ran.
    with store.connection() as db:
        data = {'edition_id': edition['id'], 'evidence_mode': 'simulation'}
        db.execute('INSERT INTO prompt_trials VALUES(?,?,?,?,?)', (uuid.uuid4().hex, pid, 'succeeded', store.encoded(data), 1.0))


def test_project_snapshot_never_changes_with_defaults_and_can_roll_back(project, model):
    p = idle(project)
    first = edition(role='版本一 {{shot_count}}')
    apply(p, first)
    p = store.get(p['id'], False)
    validation = w.validate(p['id'], {'request_id': uuid.uuid4().hex, 'edition_id': first['id'], 'input': {}, 'schema_index': 0})
    request = {'request_id': uuid.uuid4().hex, 'key': first['key'], 'edition_id': first['id'], 'expected_revision': 0,
        'validation_id': validation['id'], 'confirmed': True}
    with pytest.raises(ValueError, match="试运行"):
        w.activate(request)
    simulated_trial_success(p["id"], first)
    w.activate(request)
    second = edition(role='版本二 {{shot_count}}')
    v2 = w.validate(p['id'], {'request_id': uuid.uuid4().hex, 'edition_id': second['id'], 'input': {}, 'schema_index': 0})
    simulated_trial_success(p['id'], second)
    w.activate({**request, 'request_id': uuid.uuid4().hex, 'expected_revision': 1, 'edition_id': second['id'], 'validation_id': v2['id']})
    assert str(adapt_system(load_prompt('creator'), p)).startswith('版本一 15')
    new = store.create({'brief': '另外一份有效的离线测试需求', 'prepare_materials': True})
    assert str(adapt_system(load_prompt('creator'), new)).startswith('版本二 15')
    apply(p)
    reverted = store.get(p['id'], False)
    assert adapt_system(load_prompt('creator'), reverted) == load_prompt('creator')
    w.activate({**request, 'request_id': uuid.uuid4().hex, 'expected_revision': 2, 'edition_id': '', 'validation_id': ''})
    with store.connection() as db:
        assert w.pin_defaults(db)['versions'] == {}


def test_apply_rejects_inflight_stale_and_preserves_approved_assets(project):
    e = edition()
    with pytest.raises(ValueError, match='在途'):
        apply(project, e)
    p = idle(project)
    p = store.update(p['id'], {'shots': [{'id': 'S01', 'selected_image': 'approved.png'}], 'delivery': {'path': 'final.mp4'}})
    with pytest.raises(ValueError, match='项目已更新'):
        apply(project, e)
    before = store.get(p['id'], False)
    result = apply(p, e)
    after = store.get(p['id'], False)
    for field in ('shots', 'delivery', 'production_revision', 'script_generation'):
        assert after[field] == before[field]
    request = {'request_id': uuid.uuid4().hex, 'expected_revision': after['revision'], 'versions': {}, 'overrides': w.EMPTY}
    first = w.apply(p['id'], request)
    store.update(p['id'], {'title': '后来的标题'})
    assert w.apply(p['id'], request) == first
    assert result['snapshot']['versions'][e['key']]['id'] == e['id']


def test_public_default_requires_matching_validation_and_manual_confirmation(project, model):
    e = edition()
    base = {'request_id': uuid.uuid4().hex, 'key': e['key'], 'edition_id': e['id'], 'expected_revision': 0,
            'validation_id': 'missing', 'confirmed': True}
    with pytest.raises(ValueError, match='验证'):
        w.activate(base)
    with pytest.raises(ValueError, match='人工确认'):
        w.activate({**base, 'confirmed': False})
    other = edition(label='另一个版本')
    v = w.validate(project['id'], {'request_id': uuid.uuid4().hex, 'edition_id': other['id'], 'input': {}, 'schema_index': 0})
    with pytest.raises(ValueError, match='验证'):
        w.activate({**base, 'validation_id': v['id']})


@pytest.mark.parametrize('change', [{'role': '{{unknown}}'}, {'project': '{{format'}, {'repair': 'x' * 4001}, {'tools': 'shell'}])
def test_invalid_template_cannot_expand_contract_or_tools(project, change):
    with pytest.raises(ValueError):
        edition(**change)


def test_specialized_contracts_and_derived_provenance_are_isolated(project):
    p = idle(project)
    e = edition('storyboard_director@3.1.0', role='H3 {{format}}')
    apply(p, e)
    p = store.get(p['id'], False)
    assert adapt_system(load_prompt('storyboard_director', '1.1.0'), p) == load_prompt('storyboard_director', '1.1.0')
    selected = adapt_system(load_prompt('storyboard_director', '3.1.0'), p)
    derived = derive_prompt(selected, selected + '\n额外规则', 'extra_rules')
    assert copy.deepcopy(derived).assembly == derived.assembly
    assert derived.assembly['segments'][:-1] == selected.assembly['segments']
    assert derived.assembly['segments'][-1]['source'] == 'renderer:extra_rules'
    mutation = selected.assembly
    mutation['segments'].clear()
    assert selected.assembly['segments']


def trial_body(p, e):
    rendered = w.preview(p['id'], {'key': e['key'], 'edition_id': e['id'], 'input': {}})
    return {'request_id': uuid.uuid4().hex, 'expected_revision': p['revision'], 'max_tokens': 128, 'confirmed': True, 'model_config_revision': rendered['model'].get('config_revision'),
            'preview': {'key': e['key'], 'edition_id': e['id'], 'input': {'project': {'brief': '测试'}}, 'schema_index': 0, 'overrides': w.EMPTY}}


def test_trial_one_call_pinned_inputs_and_no_project_mutation(project, model, monkeypatch):
    from factory import llm
    p = idle(project)
    e = edition()
    request = trial_body(p, e)
    before = store.get(p['id'])
    queued = w.trial(p['id'], request)
    assert w.trial(p['id'], request) == queued
    calls = []
    def complete(config, role, system, payload, schema, output_dir, **kwargs):
        assert config['llm']['max_schema_attempts'] == 1
        assert kwargs['max_tokens'] == 128
        assert prompt_receipt(system, role, payload, schema)['assembly']['edition_id'] == e['id']
        calls.append(payload)
        return {'proposals': []}
    monkeypatch.setattr(llm, 'json_call', complete)
    assert w.run_next_trial() and not w.run_next_trial()
    assert len(calls) == 1 and store.get(p['id']) == before
    assert w.inventory(p['id'])['trials'][0]['state'] == 'succeeded'
    assert w.trial(p['id'], request) == queued


def test_crash_unknown_trial_is_not_automatically_replayed(project, model, monkeypatch):
    from factory import llm
    p = idle(project)
    e = edition()
    first = trial_body(p, e)
    w.trial(p['id'], first)
    with store.connection() as db:
        db.execute("UPDATE prompt_trials SET state='running'")
    w.recover_trials()
    monkeypatch.setattr(llm, 'json_call', lambda *a, **k: pytest.fail('Unknown result must not be resubmitted'))
    assert not w.run_next_trial()
    with pytest.raises(ValueError, match='结果未知'):
        w.trial(p['id'], trial_body(p, e))
    w.settle_trial(p['id'], {'request_id': uuid.uuid4().hex, 'trial_id': first['request_id'], 'confirmed': True})
    assert w.inventory(p['id'])['trials'][0]['state'] == 'closed'


def test_trial_budget_and_unpriced_cloud_boundaries(project, model, monkeypatch):
    p = idle(project)
    e = edition()
    request = trial_body(p, e)
    for budget in (0, 127, 16385, True):
        with pytest.raises(ValueError, match='额度'):
            w.trial(p['id'], {**request, 'max_tokens': budget})
    model['llm'].update(base_url='https://api.deepseek.com', provider='deepseek', network_mode='public_api')
    with pytest.raises(ValueError):
        w.trial(p['id'], request)


def test_actual_call_evidence_legacy_gap_and_path_boundaries(project, tmp_path):
    folder = tmp_path / 'production' / project['slug'] / 'planning' / 'llm' / 'calls' / 'fixture'
    folder.mkdir(parents=True)
    assert w.calls(project['id'], root=tmp_path)['calls'] == []
    value = {'schema': 'ai-studio.prompt-assembly.v1', 'role': 'creator', 'system': '当时的提示词',
             'prompt': {'version': '1.0.0'}, 'input': {'project': {'brief': '当时的输入'}}}
    (folder / 'assembly.json').write_text(json.dumps(value))
    (folder / 'receipt.json').write_text(json.dumps({'status': 'succeeded'}))
    ident = w.calls(project['id'], root=tmp_path)['calls'][0]['id']
    store.update(project['id'], {'brief': '此后变更的要求不改变证据'})
    assert w.calls(project['id'], ident, root=tmp_path)['system'] == '当时的提示词'
    assert w.calls(project['id'], ident, root=tmp_path)['input'] == value['input']
    (folder / 'assembly.json').unlink()
    (folder / 'assembly.json').symlink_to(folder / 'receipt.json')
    assert w.calls(project['id'], root=tmp_path)['calls'] == []


def test_concurrent_save_same_request_creates_one_edition(project):
    body = {'request_id': uuid.uuid4().hex, 'key': 'creator@1.0.0', 'label': '并发请求', 'sections': w.template('creator@1.0.0')['sections']}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(w.save, [body, body]))
    assert first == second and len(w.inventory(project['id'])['editions']) == 1


def test_different_requests_in_same_second_are_distinct_immutable_versions(project, monkeypatch):
    monkeypatch.setattr(store, 'now', lambda: '2026-10-19T03:00:00Z')
    first, second = edition(), edition()
    assert first['id'] != second['id']


def test_edition_comparison_uses_selected_baseline_without_mutating_project(project, model):
    first = edition(role='初稿 {{shot_count}}')
    second = edition(role='修订稿 {{shot_count}}')
    before = store.get(project['id'])
    diff = w.preview(project['id'], {'key': first['key'], 'edition_id': second['id'], 'baseline_id': first['id'], 'input': {}})['diff']
    assert '-  "role": "初稿 {{shot_count}}"' in diff and '+  "role": "修订稿 {{shot_count}}"' in diff
    assert store.get(project['id']) == before


def test_trial_late_completion_is_retained_without_automatic_adoption(project, model, monkeypatch):
    from factory import llm
    p = idle(project)
    e = edition()
    w.trial(p['id'], trial_body(p, e))
    before = store.get(p['id'])
    def late(*args, **kwargs):
        w.recover_trials()
        return {'late': 'evidence'}
    monkeypatch.setattr(llm, 'json_call', late)
    assert w.run_next_trial()
    value = w.inventory(p['id'])['trials'][0]
    assert value['state'] == 'unknown' and value['result'] == {'late': 'evidence'}
    assert store.get(p['id']) == before


def test_http_query_invalid_ids_and_cross_origin_mutations_are_rejected(project, http_client, model):
    base = f"/api/factory/projects/{project['id']}/prompts"
    assert http_client('GET', base + '?unknown=x')[0] == 400
    assert http_client('POST', base + '/preview?unknown=x', {})[0] == 400
    assert http_client('POST', base + '/preview', {'key': 'creator@1.0.0', 'edition_id': ['invalid']})[0] == 409
    assert http_client('POST', base + '/save', {}, headers={'Origin': 'https://cross-origin.example'})[0] == 403


def test_llm_receipt_and_assembly_preserve_the_final_workbench_identity(project, model, monkeypatch, tmp_path):
    from factory import llm
    p = idle(project)
    e = edition(role='保留动作因果 {{shot_count}}')
    apply(p, e)
    p = store.get(p['id'], False)
    system = adapt_system(load_prompt('creator'), p)
    monkeypatch.setattr(llm, 'check_model', lambda _: {'data': [{'id': 'fixture'}]})
    monkeypatch.setattr(llm, 'context_budget', lambda *a: {'fits': True})
    sent = []
    def response(*args, **kwargs):
        sent.append(args[2])
        return {'model': 'fixture', 'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}]}
    monkeypatch.setattr(llm, 'request_json', response)
    schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok'], 'additionalProperties': False}
    assert llm.json_call(model, 'creator', system, {}, schema, tmp_path) == {'ok': True}
    path = next(tmp_path.glob('calls/*/assembly.json'))
    evidence = json.loads(path.read_text())
    receipt = json.loads(path.with_name('receipt.json').read_text())
    assert evidence['system'] == system
    assert evidence['wire_system'] == sent[0]['messages'][0]['content']
    assert receipt['prompt']['assembly']['edition_id'] == e['id']
    assert evidence['prompt']['assembly'] == receipt['prompt']['assembly']
    assert evidence['output_schema'] == schema


def test_project_overrides_without_a_custom_edition_use_parameterized_builtin(project):
    from factory.production_profiles import normalize
    p = idle(project)
    p = store.update(p['id'], {'production_profile': normalize({'preset': 'compact6'}), 'duration_seconds': 30})
    apply(p, overrides={**w.EMPTY, 'repair': '不得恢复已删除的雨伞'})
    selected = adapt_system(load_prompt('creator'), store.get(p['id'], False))
    assert '6镜' in selected and '15镜' not in selected
    assert '不得恢复已删除的雨伞' in selected
