"""Immutable prompt editions, explicit project pins and isolated one-request trials.

The reviewed catalog owns contracts and capabilities. Workbench text can change
instructions, never schema, tool permissions, routing or approved artifacts.
"""
from __future__ import annotations

import copy
import difflib
import importlib
import ipaddress
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from . import store
from .creative_evidence_io import digest, read_json
from .prompt_registry import PROMPT_ROOT, PromptSpec, PromptText, load_prompt

SECTIONS = ('role', 'project', 'production', 'model', 'references', 'repair')
PARAMETERS = {'shot_count', 'duration_seconds', 'character_ids', 'speech_limit', 'format'}
TOKEN = re.compile(r'\{\{([a-z_]+)\}\}')
EMPTY = {name: '' for name in SECTIONS if name != 'role'}


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS prompt_editions(
      id TEXT PRIMARY KEY, prompt_key TEXT NOT NULL, document TEXT NOT NULL, created REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS prompt_defaults(
      prompt_key TEXT PRIMARY KEY, edition_id TEXT NOT NULL, revision INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS prompt_operations(
      id TEXT PRIMARY KEY, signature TEXT NOT NULL, result TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS prompt_trials(
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
      state TEXT NOT NULL, document TEXT NOT NULL, created REAL NOT NULL);
    ''')


def catalog():
    data = read_json(PROMPT_ROOT / 'catalog.json')
    return [{"key": row['role'] + '@' + row['version'], "role": row['role'],
             "base_version": row['version'], "responsibility": row['responsibility'],
             "sections": {"role": parameterize(str(load_prompt(row['role'], row['version']))), **EMPTY}}
            for row in data['prompts']]


def template(key):
    return next((row for row in catalog() if row['key'] == key), None) or _fail('未知提示词合约')


def _fail(message):
    raise ValueError(message)


def parameterize(text):
    """One-time scaffolding of old templates; runtime uses named parameters only."""
    replacements = {
        '60至90秒竖屏、15镜、两位虚构成年角色（female/male）': '{{duration_seconds}}秒、{{shot_count}}镜、稳定角色ID {{character_ids}}',
        'characters 恰好包含 female 和 male 各一位': 'characters 精确覆盖稳定角色ID {{character_ids}}',
        '两位虚构成年角色，female和male仅作为固定制作角色键；姓名自由。': '虚构成年角色的稳定ID为 {{character_ids}}，姓名沿用制作规格。',
        '两位虚构成年角色恰为female和male，15镜60至90秒能讲完': '{{shot_count}}镜、{{duration_seconds}}秒内讲完，角色ID {{character_ids}}',
        '15镜60至90秒、两名成年角色': '{{shot_count}}镜、{{duration_seconds}}秒、角色ID {{character_ids}}',
        '恰好15镜': '恰好{{shot_count}}镜',
        '最多10个字符': '最多{{speech_limit}}个字符',
        'female/male/two': '规格中的角色ID {{character_ids}} 或 ensemble/none（旧规格保留two）',
        'female或male的声音': '所选character_id绑定的voice_id声音',
        '不得将两位角色混合': '不得混合不同角色身份',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def sections(value, *, full=True):
    names = set(SECTIONS if full else EMPTY)
    if not isinstance(value, dict) or set(value) != names:
        raise ValueError('提示词段落字段不完整或存在未知字段')
    result = {}
    for name, text in value.items():
        if not isinstance(text, str) or len(text) > (16000 if name == 'role' else 4000) or '\x00' in text:
            raise ValueError('提示词段落超出长度或格式限制')
        if set(TOKEN.findall(text)) - PARAMETERS or '{{' in TOKEN.sub('', text) or '}}' in TOKEN.sub('', text):
            raise ValueError('只支持公布的制作规格参数')
        result[name] = text.strip()
    if full and not result['role']:
        raise ValueError('岗位职责提示词不能为空')
    return result


def _edition(db, ident, key=None):
    if not isinstance(ident, str) or not re.fullmatch('[a-f0-9]{64}', ident):
        raise ValueError('提示词版本标识无效')
    row = db.execute('SELECT document FROM prompt_editions WHERE id=?', (ident,)).fetchone()
    if row is None:
        raise ValueError('提示词版本不存在')
    value = json.loads(row[0])
    if value['id'] != digest({k: v for k, v in value.items() if k != 'id'}) or (key and value['key'] != key):
        raise ValueError('提示词版本完整性或合约不匹配')
    return value


def pin_defaults(db):
    return {"schema": "ai-studio.prompt-snapshot.v1", "versions": {
        row['prompt_key']: _edition(db, row['edition_id'], row['prompt_key'])
        for row in db.execute("SELECT * FROM prompt_defaults WHERE edition_id != ''")}, "overrides": copy.deepcopy(EMPTY)}


def inventory(pid):
    with store.connection() as db:
        project = store._read(db, pid)
        defaults = {row['prompt_key']: dict(row) for row in db.execute('SELECT * FROM prompt_defaults')}
        editions = [json.loads(row[0]) for row in db.execute('SELECT document FROM prompt_editions ORDER BY created DESC LIMIT 300')]
        verified = [row[0] for row in db.execute("SELECT DISTINCT json_extract(document,'$.edition_id') FROM prompt_trials WHERE state='succeeded'")]
        trials = [_trial_public(json.loads(row[0]), row[1]) for row in db.execute(
            'SELECT document,state FROM prompt_trials WHERE project_id=? ORDER BY created DESC LIMIT 30', (pid,))]
    return {"catalog": catalog(), "editions": editions, "defaults": defaults,
            "snapshot": project.get('prompt_snapshot'), "revision": project['revision'],
            "trials": trials, "verified_editions": verified, "parameters": sorted(PARAMETERS),
            "legacy": not bool(project.get('prompt_snapshot'))}


def _operation(db, payload):
    ident = payload.get('request_id')
    if not isinstance(ident, str) or not re.fullmatch('[a-f0-9]{32}', ident):
        raise ValueError('请求标识须为32位小写十六进制')
    signature = digest(payload)
    previous = db.execute('SELECT * FROM prompt_operations WHERE id=?', (ident,)).fetchone()
    if previous and previous['signature'] != signature:
        raise ValueError('相同请求标识不能用于不同输入')
    return ident, signature, json.loads(previous['result']) if previous else None


def _remember(db, operation, value):
    db.execute('INSERT INTO prompt_operations VALUES(?,?,?)', (*operation[:2], store.encoded(value)))
    return value


def save(payload):
    if set(payload) != {'request_id', 'key', 'label', 'sections'}:
        raise ValueError('保存版本字段无效')
    info = template(payload['key'])
    content = sections(payload['sections'])
    label = payload['label']
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
        raise ValueError('版本名称应为1–80字')
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        op = _operation(db, payload)
        if op[2] is not None:
            return op[2]
        if db.execute('SELECT count(*) FROM prompt_editions').fetchone()[0] >= 300:
            raise ValueError('提示词版本达到300份上限，请先归档数据库再继续')
        contract = load_prompt(info['role'], info['base_version']).spec.contract_sha256
        value = {"key": info['key'], "label": label.strip(), "sections": content,
                 "contract_sha256": contract, "created_at": store.now(), "request_id": payload["request_id"]}
        value['id'] = digest(value)
        db.execute('INSERT INTO prompt_editions VALUES(?,?,?,?)', (value['id'], info['key'], store.encoded(value), time.time()))
        return _remember(db, op, value)


def _parameters(project):
    from .production_profiles import resolve, roles, speech_limit
    p = resolve(project)
    return {'shot_count': str(p['shot_count']), 'duration_seconds': f"{p['duration_seconds']:g}",
            'character_ids': '/'.join(roles(project)), 'speech_limit': str(speech_limit(project)),
            'format': project.get('format', 'motion_comic')}


def _render(text, project):
    if not TOKEN.search(text):
        return text
    params = _parameters(project)
    return TOKEN.sub(lambda m: params[m[1]], text)


def select(system, project):
    """Select before renderer transforms; already-selected immutable text is stable."""
    if not isinstance(system, PromptText) or getattr(system, 'assembly', None):
        return system
    snapshot = project.get('prompt_snapshot') or {}
    key = system.spec.role + '@' + system.spec.version
    edition = snapshot.get('versions', {}).get(key)
    if not edition and not any(snapshot.get('overrides', {}).values()):
        return system
    if system.transforms:
        raise ValueError('提示词版本必须在角色渲染之前选择')
    if edition and (edition['key'] != key or edition['contract_sha256'] != system.spec.contract_sha256
                    or edition['id'] != digest({k: v for k, v in edition.items() if k != 'id'})):
        raise ValueError('项目提示词快照与岗位合约不匹配')
    content = sections(edition['sections']) if edition else {'role': parameterize(str(system)), **EMPTY}
    overrides = sections(snapshot.get('overrides', EMPTY), full=False)
    records = []
    parts = []
    for name in SECTIONS:
        for source, text in [('edition:' + edition['id'] if edition else 'catalog:' + key, content[name]),
                             ('project:' + project['id'], overrides.get(name, ''))]:
            if text:
                rendered = _render(text, project)
                parts.append(rendered if name == 'role' else '\n[' + name + ']\n' + rendered)
                records.append({'section': name, 'source': source, 'sha256': digest(rendered)})
    # Identity includes editions and overrides, even when their rendered text is equal.
    identity = digest({'key': key, 'edition': edition, 'overrides': overrides})
    spec = system.spec
    custom = PromptSpec(spec.role, spec.version, spec.responsibility, spec.input_schema_json,
                        spec.output_schema_refs, digest(content), digest({'catalog_contract': spec.contract_sha256, 'snapshot': identity}))
    return PromptText(''.join(parts), custom, ('prompt_workbench',), {'key': key, 'snapshot_sha256': identity,
        'edition_id': edition['id'] if edition else None, 'segments': records})


def _output_schema(info, project, index=0):
    prompt = load_prompt(info['role'], info['base_version'])
    refs = prompt.spec.output_schema_refs
    if type(index) is not int or not 0 <= index < len(refs):
        raise ValueError('未知输出合约')
    module, attr = refs[index].split(':')
    value = getattr(importlib.import_module(module), attr)
    if callable(value):
        from .production_profiles import resolve
        value = value(resolve(project)['shot_count'], project) if attr == 'episode_schema' else value(['S%02d' % (i + 1) for i in range(resolve(project)['shot_count'])], project)
    from .profile_creative import adapt_schema
    return adapt_schema(copy.deepcopy(value), project)


def preview(pid, payload):
    if set(payload) - {'key', 'edition_id', 'sections', 'overrides', 'input', 'schema_index', 'baseline_id'}:
        raise ValueError('预览字段无效')
    info = template(payload.get('key'))
    with store.connection() as db:
        project = store._read(db, pid)
        edition = _edition(db, payload['edition_id'], info['key']) if payload.get('edition_id') else None
        baseline = _edition(db, payload['baseline_id'], info['key']) if payload.get('baseline_id') else None
    if 'sections' in payload:
        edition = {'key': info['key'], 'label': '未保存预览', 'sections': sections(payload['sections']),
                   'contract_sha256': load_prompt(info['role'], info['base_version']).spec.contract_sha256}
        edition['id'] = digest(edition)
    snapshot = copy.deepcopy(project.get('prompt_snapshot') or {'versions': {}, 'overrides': EMPTY})
    if edition:
        snapshot['versions'][info['key']] = edition
    if 'overrides' in payload:
        snapshot['overrides'] = sections(payload['overrides'], full=False)
    project['prompt_snapshot'] = snapshot
    from .profile_creative import adapt_system
    system = adapt_system(load_prompt(info['role'], info['base_version']), project)
    output = _output_schema(info, project, payload.get('schema_index', 0))
    task = payload.get('input', {})
    if not isinstance(task, dict) or len(store.encoded(task).encode("utf-8")) > 80000:
        raise ValueError('试运行输入必须为不超过80000字节的对象')
    from .prompt_registry import prompt_receipt
    meta = prompt_receipt(system, info['role'], task, output)
    # Preview includes actual provider framing and a clear identity without credentials.
    from .config import load_config, resolve_llm
    from .team_planning import task_config
    from .llm import _system_rules
    from .providers import config_revision
    try:
        config = task_config(load_config(), info['role'])
        model = resolve_llm(config, info['role'])
        wire = _system_rules(model, system, output)
        model_info = {k: model.get(k) for k in ('model', 'profile', 'provider', 'system_prompt_mode', 'response_format')}
        model_info['trial_available'] = _local_trial(model)
        model_info['config_revision'] = config_revision(config)
    except ValueError:
        wire, model_info = None, {'trial_available': False, 'error': '请先为此岗位配置可用模型'}
    return {'key': info['key'], 'system': str(system), 'wire_system': wire, 'prompt': meta,
            'input': task, 'input_schema': json.loads(system.spec.input_schema_json), 'output_schema': output,
            'model': model_info, 'transforms': list(system.transforms),
            'diff': '\n'.join(difflib.unified_diff(
                json.dumps(baseline['sections'] if baseline else info['sections'], ensure_ascii=False, indent=2).splitlines(),
                json.dumps(edition['sections'] if edition else info['sections'], ensure_ascii=False, indent=2).splitlines(),
                fromfile=baseline['label'] if baseline else 'catalog', tofile=edition['label'] if edition else 'catalog', lineterm='')),
            'project_revision': project['revision'], 'quality_conclusion': 'not_established'}


def validate(pid, payload):
    if set(payload) != {'request_id', 'edition_id', 'input', 'schema_index'}:
        raise ValueError('验证字段无效')
    with store.connection() as db:
        edition = _edition(db, payload['edition_id'])
    result = preview(pid, {'key': edition['key'], 'edition_id': edition['id'], 'input': payload['input'], 'schema_index': payload['schema_index']})
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        op = _operation(db, payload)
        if op[2] is not None:
            return op[2]
        return _remember(db, op, {'id': payload['request_id'], 'kind': 'prompt_validation', 'edition_id': edition['id'],
            'project_id': pid, 'project_revision': result['project_revision'], 'input_sha256': digest(payload['input']),
            'rendered_sha256': result['prompt']['rendered_sha256'], 'passed': True,
            'scope': 'structure_and_assembly_only', 'quality_conclusion': 'not_established'})


def activate(payload):
    if set(payload) != {'request_id', 'key', 'edition_id', 'expected_revision', 'validation_id', 'confirmed'} or payload['confirmed'] is not True:
        raise ValueError('启用公共模板需要人工确认及验证记录')
    template(payload['key'])
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        op = _operation(db, payload)
        if op[2] is not None:
            return op[2]
        edition = _edition(db, payload['edition_id'], payload['key']) if payload['edition_id'] else None
        if edition:
            row = db.execute('SELECT result FROM prompt_operations WHERE id=?', (payload['validation_id'],)).fetchone()
            evidence = json.loads(row[0]) if row else {}
            if evidence.get('kind') != 'prompt_validation' or evidence.get('edition_id') != edition['id'] or evidence.get('passed') is not True:
                raise ValueError('此版本尚无有效的装配验证记录')
            if not db.execute("SELECT 1 FROM prompt_trials WHERE state='succeeded' AND json_extract(document,'$.edition_id')=?", (edition['id'],)).fetchone():
                raise ValueError('公共模板需要此版本的成功试运行记录，再人工确认启用')
        prior = db.execute('SELECT revision FROM prompt_defaults WHERE prompt_key=?', (payload['key'],)).fetchone()
        revision = prior[0] if prior else 0
        if type(payload['expected_revision']) is not int or revision != payload['expected_revision']:
            raise ValueError('公共默认已更新，请刷新后重试')
        db.execute('INSERT OR REPLACE INTO prompt_defaults VALUES(?,?,?)', (payload['key'], edition['id'] if edition else '', revision + 1))
        return _remember(db, op, {'key': payload['key'], 'edition_id': edition['id'] if edition else '', 'revision': revision + 1})


def apply(pid, payload):
    if set(payload) != {'request_id', 'expected_revision', 'versions', 'overrides'}:
        raise ValueError('项目版本快照字段无效')
    overrides = sections(payload['overrides'], full=False)
    if not isinstance(payload['versions'], dict) or len(payload['versions']) > 20:
        raise ValueError('项目版本选择无效')
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        op = _operation(db, {**payload, 'project_id': pid})
        if op[2] is not None:
            return op[2]
        from .series_bible import project_cas
        project = project_cas(db, pid, payload['expected_revision'])
        # Applying while a graph checkpoint or a child can still submit is unsafe.
        related = [pid] + [row[0] for row in db.execute("SELECT id FROM projects WHERE json_extract(snapshot,'$.drama_id')=? OR json_extract(snapshot,'$.episode_id')=?", (pid, pid))]
        slots = ','.join('?' for _ in related)
        if db.execute(f"SELECT 1 FROM jobs WHERE project_id IN ({slots}) AND status IN ('running','queued','waiting_remote')", related).fetchone():
            raise ValueError('请先暂停项目并等待全部在途任务结束，再应用提示词版本')
        if db.execute(f"SELECT 1 FROM material_tasks WHERE json_extract(data,'$.project_id') IN ({slots}) AND status IN ('queued','running','pause_requested')", related).fetchone():
            raise ValueError('请等待项目素材任务结束后应用提示词版本')
        if db.execute(f"SELECT 1 FROM cloud_tasks WHERE project_id IN ({slots}) AND status NOT IN ('downloaded','failed','rejected','cancelled')", related).fetchone():
            raise ValueError('请先核实并结束未完成的云任务，再应用提示词版本')
        if project['status'] in store.ACTIVE:
            raise ValueError('请先暂停项目后应用提示词版本')
        versions = {}
        for key, ident in payload['versions'].items():
            template(key)
            if ident:
                versions[key] = _edition(db, ident, key)
        snapshot = {'schema': 'ai-studio.prompt-snapshot.v1', 'versions': versions, 'overrides': overrides}
        snapshot['sha256'] = digest(snapshot)
        project['prompt_snapshot'] = snapshot
        store._write(db, project)
        store._event(db, pid, '已显式应用提示词快照；已有调用、批准素材及子项目保持原版本。', 'prompt_snapshot', {'sha256': snapshot['sha256']})
        return _remember(db, op, {'snapshot': snapshot, 'revision': project['revision']})


def _local_trial(model):
    """No unpriced vendor submissions. Existing private inference routes only."""
    try:
        host = urlsplit(model['base_url']).hostname
        address = ipaddress.ip_address(host) if host != 'localhost' else ipaddress.ip_address('127.0.0.1')
        return (address.is_private or address.is_loopback) and model.get('provider', 'llama_cpp') in {'llama_cpp', 'ollama_native'} and model.get('enabled', True)
    except (ValueError, KeyError):
        return False


def _trial_public(value, state):
    return {k: copy.deepcopy(value.get(k)) for k in ('id', 'key', 'edition_id', 'created_at', 'max_tokens', 'project_revision', 'model', 'result', 'error', 'quality_conclusion', 'rendered', 'late_result')} | {'state': state}


def trial(pid, payload):
    if set(payload) != {'request_id', 'preview', 'expected_revision', 'max_tokens', 'confirmed', 'model_config_revision'} or payload['confirmed'] is not True:
        raise ValueError('试运行需要明确确认一次模型请求及输出额度')
    if type(payload['max_tokens']) is not int or not 128 <= payload['max_tokens'] <= 16384:
        raise ValueError('试运行输出额度应为128–16384 tokens')
    # Trials execute saved immutable editions only, never mutable editor content.
    selection = payload['preview']
    if not isinstance(selection, dict) or set(selection) != {'key', 'edition_id', 'input', 'schema_index', 'overrides'} or not selection.get('edition_id'):
        raise ValueError('请先保存版本再试运行')
    with store.connection() as db:
        previous = _operation(db, {**payload, 'project_id': pid})
        if previous[2] is not None:
            return previous[2]
    rendered = preview(pid, selection)
    from .config import load_config, resolve_llm
    from .team_planning import task_config
    config = task_config(load_config(), template(selection['key'])['role'])
    model = resolve_llm(config, template(selection['key'])['role'])
    from .providers import config_revision
    if config_revision(config) != rendered['model'].get('config_revision') or payload['model_config_revision'] != config_revision(config):
        raise ValueError('模型配置已改变，请重新预览后授权试运行')
    if not _local_trial(model):
        raise ValueError('此试运行仅支持既有内网推理；费用未知的供应商调用尚未开放')
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        op = _operation(db, {**payload, 'project_id': pid})
        if op[2] is not None:
            return op[2]
        from .series_bible import project_cas
        project_cas(db, pid, payload['expected_revision'])
        if rendered['project_revision'] != payload['expected_revision']:
            raise ValueError('预览期间项目已改变，请重新预览')
        if db.execute("SELECT 1 FROM prompt_trials WHERE state IN ('queued','running','unknown')").fetchone():
            raise ValueError('已有试运行在途或结果未知，请先核实结果')
        value = {'id': payload['request_id'], 'key': selection['key'], 'edition_id': selection['edition_id'],
                 'created_at': store.now(), 'project_revision': payload['expected_revision'], 'max_tokens': payload['max_tokens'],
                 'model': rendered['model'], 'config': config, 'rendered': rendered, 'quality_conclusion': 'not_established'}
        db.execute('INSERT INTO prompt_trials VALUES(?,?,?,?,?)', (value['id'], pid, 'queued', store.encoded(value), time.time()))
        return _remember(db, op, _trial_public(value, 'queued'))


def recover_trials():
    with store.connection() as db:
        db.execute("UPDATE prompt_trials SET state='unknown' WHERE state='running'")


def run_next_trial():
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT * FROM prompt_trials WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
        if row is None:
            return False
        db.execute("UPDATE prompt_trials SET state='running' WHERE id=? AND state='queued'", (row['id'],))
        value = json.loads(row['document'])
    try:
        # Pin routing/configuration and force a single schema attempt. A crash or
        # unknown completion is never automatically resubmitted after recovery.
        from .config import resolve_llm
        from .llm import json_call
        info = template(value['key'])
        config = copy.deepcopy(value['config'])
        model = resolve_llm(config, info['role'])
        if model.get('configured'):
            config['llm_profiles'][model['profile']].update(max_schema_attempts=1, request_timeout_seconds=60)
        else:
            config.setdefault('llm', {}).update(max_schema_attempts=1, request_timeout_seconds=60)
        rendered = value['rendered']
        meta = rendered['prompt']
        base = load_prompt(info['role'], info['base_version']).spec
        spec = PromptSpec(base.role, base.version, base.responsibility, base.input_schema_json, base.output_schema_refs,
                          meta['template_sha256'], meta['contract_sha256'])
        system = PromptText(rendered['system'], spec, meta['transforms'], meta.get('assembly'))
        result = json_call(config, info['role'], system, rendered['input'], rendered['output_schema'],
                           store.DATA / 'prompt-trials' / value['id'], max_tokens=value['max_tokens'], temperature=.2)
        value['result'] = result
        state = 'succeeded'
    except Exception as exc:
        # Transport exceptions can hide a completed vendor request. No retries.
        value['error'] = '试运行未得到可用结果，已保留回执；请核实后结束此记录。' + type(exc).__name__
        state = 'unknown'
    with store.connection() as db:
        current = db.execute('SELECT state FROM prompt_trials WHERE id=?', (value['id'],)).fetchone()
        if current and current[0] == 'unknown':
            value['late_result'] = True
            state = 'unknown'
        db.execute("UPDATE prompt_trials SET state=?,document=? WHERE id=? AND state IN ('running','unknown')", (state, store.encoded(value), value['id']))
    return True


def settle_trial(pid, payload):
    if set(payload) != {'request_id', 'trial_id', 'confirmed'} or payload['confirmed'] is not True:
        raise ValueError('结束未知试运行需要确认不再自动重试')
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        op = _operation(db, {**payload, 'project_id': pid})
        if op[2] is not None:
            return op[2]
        row = db.execute('SELECT * FROM prompt_trials WHERE id=? AND project_id=?', (payload['trial_id'], pid)).fetchone()
        if not row or row['state'] not in {'unknown', 'queued'}:
            raise ValueError('只有待执行或结果未知的试运行可结束')
        db.execute("UPDATE prompt_trials SET state='closed' WHERE id=?", (row['id'],))
        return _remember(db, op, {'id': row['id'], 'state': 'closed'})


def calls(pid, call_id=None, *, root=None):
    from studio.core import ROOT
    project = store.get(pid, False)
    if not re.fullmatch('factory-[a-f0-9]{12}', project['slug']):
        raise ValueError('项目制作目录无效')
    base = Path(root or ROOT) / 'production' / project['slug']
    folders = (base / 'planning', base / 'quality' / 'dialogue_rewrites')
    records = []
    for folder in folders:
        if not folder.exists():
            continue
        for path in sorted(folder.glob('**/calls/*/assembly.json'), reverse=True)[:80]:
            ident = digest(str(path.relative_to(base)))[:32]
            if call_id and ident != call_id:
                continue
            try:
                data = read_json(path)
                receipt = read_json(path.parent / 'receipt.json')
            except (ValueError, OSError):
                continue
            if call_id:
                # Only allowlisted fields from bounded owned evidence; credentials
                # and private provider addresses are not part of the assembly.
                return {k: data.get(k) for k in ('schema', 'role', 'system', 'wire_system', 'input', 'output_schema', 'prompt', 'parameters', 'model')}
            records.append({'id': ident, 'role': data.get('role'), 'call_id': receipt.get('call_id'),
                'status': receipt.get('status'), 'prompt': data.get('prompt'), 'model': data.get('model')})
    if call_id:
        raise KeyError('此调用尚无装配记录；历史原始回执保持原样')
    return {'calls': records[:80], 'note': '只展示升级后留有装配证据的调用；旧回执不补写当前模板。'}
