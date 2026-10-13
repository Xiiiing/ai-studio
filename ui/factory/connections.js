import { h, list } from './format.js';

const DEFAULTS = {
  llama_cpp: { label: 'llama.cpp', protocol: 'openai', base_url_example: 'http://127.0.0.1:8090/v1', response_format: 'llama_json_schema', context_validation: 'llama_tokenize' },
  ollama: { label: 'Ollama · OpenAI 兼容', protocol: 'openai', base_url_example: 'http://127.0.0.1:11434/v1', response_format: 'openai_json_schema' },
  ollama_native: { label: 'Ollama · 原生 API', protocol: 'ollama', base_url_example: 'http://127.0.0.1:11434', response_format: 'openai_json_schema' },
  vllm: { label: 'vLLM', protocol: 'openai', base_url_example: 'http://127.0.0.1:8000/v1', response_format: 'openai_json_schema' },
  lm_studio: { label: 'LM Studio', protocol: 'openai', base_url_example: 'http://127.0.0.1:1234/v1', response_format: 'openai_json_schema' },
  deepseek: { label: 'DeepSeek · 官方 API', protocol: 'openai', base_url_example: 'https://api.deepseek.com', response_format: 'json_object' },
  dashscope: { label: '千问 / 百炼 · 兼容 API', protocol: 'openai', base_url_example: 'https://dashscope.aliyuncs.com/compatible-mode/v1', response_format: 'json_object' },
  openai_compatible: { label: 'OpenAI 兼容接口', protocol: 'openai', base_url_example: 'http://127.0.0.1:8000/v1', response_format: 'json_object' }
};
export function textProviders(inventory = null) {
  const result = Object.fromEntries(Object.entries(DEFAULTS).map(([id, value]) => [id, { id, ...value }]));
  for (const meta of list(inventory?.providers)) if (meta.id) result[meta.id] = { ...result[meta.id], ...meta };
  return result;
}
export function providerMeta(id, inventory = null) {
  return textProviders(inventory)[id] || { id, label: id, response_format: 'json_object' };
}
export function endpointHelp(provider) {
  if (provider === 'ollama_native') return 'Ollama 原生地址可填写服务根目录或完整 /api 前缀，不需要 /v1。';
  if (provider === 'dashscope') return '填写所用地域或工作空间的完整兼容 API 前缀；保留 /compatible-mode/v1。模型目录可选择跳过检测。';
  if (provider === 'deepseek') return '填写完整 API 前缀；官方服务根地址即可使用，无需强制追加 /v1。';
  return '填写服务实际的完整 API 前缀，保留 /v1 或自定义代理路径；系统不会强制追加 /v1。';
}
export function providerOptions(selected, inventory = null) {
  return Object.entries(textProviders(inventory)).map(([id, meta]) => '<option value="' + h(id) + '" ' + (id === selected ? 'selected' : '') + '>' + h(meta.label || id) + '</option>').join('');
}
export const supportsThinking = provider => ['ollama_native', 'dashscope'].includes(provider);
export const supportsSkippingDiscovery = provider => !['llama_cpp', 'ollama_native'].includes(provider);
export function switchTextProvider(form, inventory = null) {
  const provider = form.elements.provider.value;
  const meta = providerMeta(provider, inventory);
  const previous = form.elements.provider.dataset.previousProvider || 'openai_compatible';
  const oldMeta = providerMeta(previous, inventory);
  const endpoint = form.elements.base_url;
  if (!endpoint.value || endpoint.value === oldMeta.base_url_example) {
    let previousHost = '';
    try { previousHost = new URL(endpoint.value).host; } catch {}
    endpoint.value = meta.base_url_example || '';
    try {
      if (previousHost && new URL(endpoint.value).host !== previousHost && form.elements.api_key_env) form.elements.api_key_env.value = '';
    } catch {}
    const remote = endpoint.value.startsWith('https://');
    form.elements.network_mode.value = remote ? 'remote' : 'loopback';
    if (remote) {
      try { form.elements.allowed_hosts.value = new URL(endpoint.value).hostname; } catch {}
    } else form.elements.allowed_hosts.value = '';
  }
  endpoint.placeholder = meta.base_url_example || '完整 API 前缀';
  form.querySelector('[data-endpoint-help]').textContent = endpointHelp(provider);
  form.elements.response_format.value = meta.response_format || 'json_object';
  form.elements.model_discovery.value = provider === 'dashscope' ? 'skip' : 'required';
  form.elements.model_discovery.querySelector('option[value="skip"]').disabled = !supportsSkippingDiscovery(provider);
  form.elements.thinking.disabled = !supportsThinking(provider);
  form.elements.thinking.value = provider === 'dashscope' ? 'false' : '';
  form.elements.provider.dataset.previousProvider = provider;
}
