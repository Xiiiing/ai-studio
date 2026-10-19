"""Native function calls and constrained JSON actions share one tool executor.

No request retry here. The owner journals a turn before sending and keeps raw
responses for deterministic recovery. Provider reasoning stays in private wire
history; public events contain only explicit plans/actions and tool outcomes.
"""
from __future__ import annotations
import copy
import json
import time
from pathlib import Path

from studio.core import write_json
from .creative_evidence_io import digest, read_json
from .agent_policy import ACTION_SCHEMA
from .providers import chat_request, chat_response, model_matches, redact_secrets

NATIVE = {'llama_cpp', 'ollama', 'ollama_native', 'vllm', 'lm_studio', 'openai_compatible', 'dashscope', 'deepseek'}


def protocol(profile, selected):
    supported = profile.get('provider', 'llama_cpp') in NATIVE
    if selected == 'native' and not supported:
        raise ValueError('当前连接尚无原生工具适配，请选择结构化动作协议')
    return 'native' if selected == 'native' or selected == 'auto' and supported else 'structured'


def build_request(profile, messages, definitions, max_tokens, temperature):
    params = {'max_tokens': max_tokens, 'max_tokens_field': profile.get('max_tokens_field', 'max_tokens'),
        'send_sampling_parameters': profile.get('send_sampling_parameters', True),
        'temperature': profile.get('temperature_override', temperature), 'top_p': profile.get('top_p', .9),
        'chat_template_kwargs': profile.get('chat_template_kwargs', {})}
    adapted = copy.deepcopy(profile); adapted['response_format'] = 'prompt'
    wire_messages=[{**m,'content':m.get('content') or ''} for m in messages] if profile.get('provider')=='ollama_native' else messages
    route, request = chat_request(adapted, wire_messages, ACTION_SCHEMA, 'agent', params)
    request.pop('response_format', None); request.pop('format', None)
    request['tools'] = copy.deepcopy(definitions)
    if profile.get('provider') != 'ollama_native':
        request['tool_choice'] = 'auto'
        request['messages'] = [{k:v for k,v in m.items() if k!='name'} if m.get('role')=='tool' else m for m in request['messages']]
    else:
        converted=[]
        for message in messages:
            m=copy.deepcopy(message)
            if m.get('content') is None:m['content']=''
            if m.get('role')=='tool':m={'role':'tool','content':m['content'],'tool_name':m.get('name','')}
            for call in m.get('tool_calls',[]):
                if isinstance(call['function'].get('arguments'),str):
                    call['function']['arguments']=json.loads(call['function']['arguments'])
                call.pop('id',None);call.pop('type',None)
            if 'reasoning_content' in m:m['thinking']=m.pop('reasoning_content')
            converted.append(m)
        request['messages']=converted
    if profile.get('provider') == 'deepseek' and 'thinking' in profile:
        request['thinking'] = {'type': 'enabled' if profile['thinking'] else 'disabled'}
    return route, request


def decode(profile, response, turn):
    response = chat_response(profile, response)
    if not model_matches(profile, response.get('model')):
        raise ValueError('Agent返回模型与固定配置不一致')
    choices = response.get('choices')
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError('Agent模型返回格式无效')
    choice = choices[0]
    if choice.get('finish_reason') not in {'stop', 'tool_calls'}:
        raise ValueError('Agent模型输出未完整结束')
    message = choice.get('message')
    if not isinstance(message, dict) or not isinstance(message.get('tool_calls'), list) or not 1 <= len(message['tool_calls']) <= 4:
        raise ValueError('Agent必须使用显式工具动作，不能以普通回答冒充完成')
    # Keep the complete assistant message, including DeepSeek reasoning_content.
    assistant = {k: copy.deepcopy(message[k]) for k in ('content', 'tool_calls', 'reasoning_content', 'thinking') if k in message}
    assistant['role'] = 'assistant'
    actions, ids = [], set()
    for i, call in enumerate(assistant['tool_calls']):
        if not isinstance(call, dict) or call.get('type', 'function') != 'function':
            raise ValueError('Agent工具调用类型无效')
        ident = call.get('id') or (f'call_{turn}_{i}' if profile.get('provider')=='ollama_native' else None)
        if not isinstance(ident, str) or len(ident) > 200 or ident in ids:
            raise ValueError('Agent工具调用编号无效')
        call['id'] = ident; ids.add(ident)
        function = call.get('function', {})
        if not isinstance(function,dict) or not isinstance(function.get('name'),str) or len(function['name'])>128:
            raise ValueError('Agent工具名称无效')
        arguments = function.get('arguments')
        if isinstance(arguments, str):
            arguments = json.loads(arguments, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
        if not isinstance(arguments, dict):
            raise ValueError('Agent工具参数必须是对象')
        actions.append({'id': ident, 'tool': function.get('name'), 'arguments': arguments})
    return {'assistant': assistant, 'actions': actions, 'usage': response.get('usage') or {}}


def native_turn(config, role, system, payload, messages, definitions, directory, *, max_tokens, temperature, turn, evidence_payload=None):
    from .config import resolve_llm, llm_identity
    from . import llm
    from .prompt_registry import prompt_receipt
    from .context_manifest import build, completed
    from .execution_context import current_execution
    from .team_ledger import record_call
    from .role_handoffs import observe_call
    profile = resolve_llm(config, role)
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    response_path = directory / 'response-01.json'
    if response_path.exists():
        return decode(profile, read_json(response_path), turn)
    if (directory / 'intent.json').exists():
        raise TimeoutError('上次原生工具请求结果未知，未重投')
    route, request = build_request(profile, messages, definitions, max_tokens, temperature)
    # Include tool definitions in context accounting. Reject instead of dropping
    # mandatory project facts or provider-required reasoning/tool messages.
    accounting_profile={**profile, 'context_validation':'conservative_utf8_estimate'}
    budget = llm.context_budget(accounting_profile, [{'role': 'user', 'content': json.dumps(request, ensure_ascii=False)}], max_tokens)
    if not budget.get('fits'):
        raise ValueError('Agent上下文超出窗口；必需材料已保留，请拆分任务')
    llm.check_model(profile)
    prompt = prompt_receipt(system, role, payload, ACTION_SCHEMA)
    manifest = build(role, evidence_payload or payload, prompt, current_execution.get())
    manifest['content_sha256']=digest(request['messages'])
    manifest.update(delivery='attempted', request_sha256=digest(request), budget=budget)
    receipt = {'kind': 'agent_model_turn', 'role': role, 'call_id': directory.name, 'status': 'running',
        'configured_model': profile['model'], 'response_model': None, 'model': profile['model'], 'profile': profile['profile'],
        'provider': profile.get('provider'), 'model_identity': llm_identity(config, role), 'prompt': prompt,
        'parameters': {'max_tokens': max_tokens, 'temperature': temperature, 'protocol': 'native'},
        'started_at': time.time(), 'context_manifest': manifest, 'attempts': [], 'execution_context': current_execution.get()}
    def save(name, value):
        path = directory / name; write_json(path, redact_secrets(value, profile)); path.chmod(0o600)
    save('assembly.json', {'schema':'ai-studio.prompt-assembly.v1','role':role,'system':str(system),'wire_system':str(system),
        'input':evidence_payload or payload,'history_sha256':digest(messages),'output_schema':ACTION_SCHEMA,'prompt':prompt,'parameters':receipt['parameters'],
        'tool_contracts':definitions,'model':{'model':profile['model'],'profile':profile['profile']}})
    save('request-01.json', request); save('context-01.json', manifest)
    save('intent.json', {'request_sha256': digest(request), 'at': time.time()}); save('receipt.json', receipt)
    try:
        response = llm.request_json(profile['base_url'], route, request, timeout=profile.get('request_timeout_seconds', 480),
                                api_key_env=profile.get('api_key_env'), profile=profile)
        save('response-01.json', response)
        manifest['delivery'] = 'response_received'
        result = decode(profile, response, turn)
        receipt.update(status='succeeded', response_model=response.get('model'), output_sha256=digest(result['actions']))
        receipt['attempts'] = [{'attempt':1,'usage':result['usage'],'finish_reason':'tool_calls'}]
        save('parsed.json', {'actions':result['actions']})
        return result
    except Exception as exc:
        receipt.update(status='failed', error=type(exc).__name__)
        raise
    finally:
        receipt.update(finished_at=time.time(), elapsed_seconds=round(time.time()-receipt['started_at'],3))
        save('receipt.json', receipt)
        if receipt['status'] in {'succeeded','failed'}:
            record_call(current_execution.get(), role, receipt, directory/'receipt.json')
            completed(directory, receipt); observe_call(receipt)
