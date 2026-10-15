"""Content-addressed ComfyUI API graphs with declarative input/output mappings."""
from __future__ import annotations
import copy
import json
import re
from studio.core import fingerprint, read_json, write_json
from .config import DATA

SOURCES = {'prompt', 'negative_prompt', 'seed', 'width', 'height', 'frames', 'fps'}


def validate(value):
    if not isinstance(value, dict) or set(value) != {'name', 'kind', 'graph', 'mappings', 'output'}:
        raise ValueError('模板需要 name、kind、graph、mappings、output')
    if value['kind'] not in {'image', 'video'} or not isinstance(value['name'], str) or not 1 <= len(value['name']) <= 120:
        raise ValueError('模板名称或输出类型无效')
    from .providers import _reject_secret_fields
    _reject_secret_fields(value)
    graph = value['graph']
    if not isinstance(graph, dict) or not 1 <= len(graph) <= 300:
        raise ValueError('请导入 API 格式图，而非编辑器的 nodes/links 格式')
    for ident, node in graph.items():
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', ident): raise ValueError('节点编号无效')
        if not isinstance(node, dict) or set(node) - {'class_type', 'inputs', '_meta'} or not isinstance(node.get('class_type'), str) or not isinstance(node.get('inputs'), dict):
            raise ValueError('API 节点须包含 class_type 和 inputs')
        for item in node['inputs'].values():
            if isinstance(item, list):
                if len(item) != 2 or not isinstance(item[0], str) or item[0] not in graph or type(item[1]) is not int or item[1] < 0:
                    raise ValueError('节点连接无效；列表常量暂不支持')
    mappings = value['mappings']
    if not isinstance(mappings, list) or not 1 <= len(mappings) <= 100: raise ValueError('输入映射数量无效')
    used, sources = set(), set()
    for m in mappings:
        if not isinstance(m, dict) or set(m) != {'node', 'input', 'source'}: raise ValueError('输入映射需要 node、input、source')
        if m['node'] not in graph or m['input'] not in graph[m['node']]['inputs']: raise ValueError('映射节点或输入不存在')
        source = m['source']
        if not isinstance(source, str) or (source not in SOURCES and not re.fullmatch(r'reference:[0-9]{1,2}', source)): raise ValueError('不支持的输入映射来源')
        pair = (m['node'], m['input'])
        if pair in used or isinstance(graph[m['node']]['inputs'][m['input']], list): raise ValueError('映射重复或覆盖了节点连线')
        used.add(pair); sources.add(source)
    if 'prompt' not in sources: raise ValueError('模板必须映射 prompt')
    indexes = sorted({int(s.split(':')[1]) for s in sources if s.startswith('reference:')})
    if indexes != list(range(len(indexes))): raise ValueError('参考图槽位必须从0连续编号')
    output = value['output']
    if not isinstance(output, dict) or set(output) != {'node', 'field', 'index'} or output['node'] not in graph or output['field'] not in {'images', 'gifs', 'videos'} or type(output['index']) is not int or not 0 <= output['index'] <= 100:
        raise ValueError('输出映射无效')
    # Disallow cycles before a job can be submitted.
    visiting, done = set(), set()
    def visit(ident):
        if ident in visiting: raise ValueError('工作流包含循环依赖')
        if ident in done: return
        visiting.add(ident)
        for v in graph[ident]['inputs'].values():
            if isinstance(v, list): visit(v[0])
        visiting.remove(ident); done.add(ident)
    for ident in graph: visit(ident)
    json.dumps(value, allow_nan=False)
    return copy.deepcopy(value)


def import_template(value):
    clean = validate(value)
    ident = fingerprint(clean)
    path = DATA / 'workflow_templates' / (ident + '.json')
    write_json(path, clean)
    path.chmod(0o600)
    return {'id': ident, **clean}


def load_template(ident):
    if not isinstance(ident, str) or not re.fullmatch(r'[a-f0-9]{64}', ident): raise ValueError('工作流模板编号无效')
    value = read_json(DATA / 'workflow_templates' / (ident + '.json'))
    if fingerprint(value) != ident: raise ValueError('工作流模板校验失败')
    return validate(value)


def templates():
    return [{'id': p.stem, 'name': v['name'], 'kind': v['kind']} for p in sorted((DATA / 'workflow_templates').glob('*.json'))
            for v in [load_template(p.stem)]]


def bind(template, values, references):
    graph = copy.deepcopy(template['graph'])
    slots = {m['source'] for m in template['mappings'] if m['source'].startswith('reference:')}
    if len(references) != len(slots):
        raise ValueError(f'工作流需要 {len(slots)} 张参考图，实际为 {len(references)}；请配置匹配的模板')
    for m in template['mappings']:
        source = m['source']
        graph[m['node']]['inputs'][m['input']] = references[int(source.split(':')[1])] if source.startswith('reference:') else values[source]
    return graph


def check_nodes(template, info):
    for node in template['graph'].values():
        spec = info.get(node['class_type'])
        if not isinstance(spec, dict): raise ValueError('ComfyUI 缺少节点：' + node['class_type'])
        required = spec.get('input', {}).get('required', {})
        if set(required) - node['inputs'].keys(): raise ValueError('工作流缺少节点必填输入：' + node['class_type'])
