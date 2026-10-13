/** Native UI contract tests. Run with Node 20+: node --test tests/test_factory_ui.mjs */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { api } from '../ui/factory/api.js';
import { bindModelActions, ingestInventory, modelEditor, modelsView, profileFromForm, registryPayload } from '../ui/factory/models.js';
import { workflowBoard } from '../ui/factory/workflow.js';
import { projectView, capabilities, capabilityStatus } from '../ui/factory/views.js';
import { dateText } from '../ui/factory/format.js';
import { videoModelEditor, videoProfileFromForm } from '../ui/factory/video-models.js';
import { endpointHelp, switchTextProvider } from '../ui/factory/connections.js';
import { ingestProjectFlow, teamFlowView } from '../ui/factory/team.js';

globalThis.location = { origin: 'http://127.0.0.1:8787' };
const clone = x => JSON.parse(JSON.stringify(x));
function inventory(revision = 'config-a') {
  const profiles = {
    writer: { provider: 'llama_cpp', network_mode: 'loopback', enabled: true, base_url: 'http://127.0.0.1:8090/v1', model: 'writer-model', context_tokens: 32768, vision: true, response_format: 'llama_json_schema', context_validation: 'llama_tokenize', chat_template_kwargs: { enable_thinking: false }, temperature_override: 0.4, api_key_env: 'WRITER_KEY', allowed_hosts: [] },
    critic: { provider: 'openai_compatible', network_mode: 'remote', enabled: true, base_url: 'https://models.example.test/v1', model: 'critic-model', context_tokens: 32768, vision: false, response_format: 'json_object', allowed_hosts: ['models.example.test'], api_key_env: 'CRITIC_KEY' }
  };
  return { revision, profiles: Object.entries(profiles).map(([id, p]) => ({ id, ...clone(p), auth_configured: id === 'writer' })), editable_profiles: profiles, role_assignments: { screenwriter: 'writer', independent_critic: 'critic', visual_critic: 'writer' }, roles: [{ role: 'screenwriter', label: '编剧', profile: 'writer' }, { role: 'independent_critic', label: '独立审稿', profile: 'critic' }, { role: 'visual_critic', label: '画面质检', profile: 'writer', requires_vision: true }] };
}
function state() {
  const result = { epoch: 0, busy: false, modelTests: {} };
  ingestInventory(result, inventory());
  return result;
}
function browserHarness(s) {
  const listeners = {}, toasts = [], calls = [];
  const dialog = { innerHTML: '', open: false, showModal() { this.open = true; }, close() { this.open = false; }, querySelector() { return { focus() {} }; } };
  globalThis.document = { getElementById(id) { return id === 'model-dialog' ? dialog : { focus() {}, hidden: true, textContent: '' }; }, addEventListener(type, callback) { listeners[type] = callback; } };
  globalThis.window = { addEventListener() {} };
  const ui = { paint() { calls.push('paint'); }, toast(message) { toasts.push(message); }, async refresh() { calls.push('refresh'); } };
  bindModelActions(s, ui);
  const click = (action, profile = 'writer') => listeners.click({ target: { closest() { return { disabled: false, dataset: { modelAction: action, profile } }; } } });
  return { listeners, dialog, click, toasts, calls };
}
test('model inventory is copied and preserves advanced profile fields', () => {
  const s = state();
  s.modelsDraft.profiles.writer.chat_template_kwargs.enable_thinking = true;
  assert.equal(s.models.editable_profiles.writer.chat_template_kwargs.enable_thinking, false);
  const payload = registryPayload(s);
  assert.equal(payload.expected_revision, 'config-a');
  assert.equal(payload.profiles.writer.temperature_override, 0.4);
  assert.deepEqual(Object.keys(payload).sort(), ['expected_revision', 'profiles', 'roles']);
});
test('refresh never overwrites a dirty draft or its optimistic version', () => {
  const s = state();
  s.modelsDirty = true; s.modelsDraft.roles.screenwriter = 'critic';
  ingestInventory(s, inventory('config-b'));
  assert.equal(s.models.revision, 'config-a');
  assert.equal(s.modelsDraft.roles.screenwriter, 'critic');
  assert.equal(s.modelsRemoteChanged, true);
});
test('explicit reload drops draft and stale probe results', () => {
  const s = state();
  s.modelsDirty = true; s.modelTests.writer = { status: 'ready' };
  ingestInventory(s, inventory('config-b'), true);
  assert.equal(s.modelsDirty, false);
  assert.equal(s.models.revision, 'config-b');
  assert.deepEqual(s.modelTests, {});
});
test('profile edits retain custom options while translating form types', () => {
  const previous = inventory().editable_profiles.writer;
  const next = profileFromForm({ provider: 'llama_cpp', enabled: 'on', network_mode: 'loopback', base_url: previous.base_url, model: 'new-model', context_tokens: '65536', vision: 'on', allowed_hosts: '', api_key_env: 'WRITER_KEY', response_format: 'llama_json_schema', request_timeout_seconds: '480' }, previous);
  assert.equal(next.model, 'new-model');
  assert.equal(next.context_tokens, 65536);
  assert.equal(next.context_validation, 'llama_tokenize');
  assert.deepEqual(next.chat_template_kwargs, { enable_thinking: false });
  assert.equal(next.temperature_override, 0.4);
  assert.equal(previous.model, 'writer-model');
});
test('provider switches stop using an incompatible llama tokenizer', () => {
  const next = profileFromForm({ provider: 'vllm', enabled: 'on', network_mode: 'lan', base_url: 'http://192.168.1.7:8000/v1', model: 'qwen', context_tokens: '32768', allowed_hosts: '192.168.1.7', response_format: 'openai_json_schema', request_timeout_seconds: '120' }, inventory().editable_profiles.writer);
  assert.equal(next.context_validation, 'conservative_estimate');
  assert.deepEqual(next.allowed_hosts, ['192.168.1.7']);
  assert.equal(next.vision, false);
});
test('model page displays true endpoints, role bindings and environment names only', () => {
  const html = modelsView(state());
  for (const text of ['writer-model', 'critic-model', 'http://127.0.0.1:8090/v1', 'WRITER_KEY', 'CRITIC_KEY', '尚未测试', 'data-role-assignment="visual_critic"']) assert.ok(html.includes(text));
  assert.ok(!html.includes('type="password"'));
  assert.ok(!html.includes('连接通过'));
});
test('model metadata is escaped and a failed probe is not rendered as ready', () => {
  const s = state();
  s.modelsDraft.profiles.writer.model = '<img src=x onerror=alert(1)>';
  s.modelTests.writer = { status: 'error', elapsed_ms: 5, error: { message: '<script>bad</script>' } };
  const html = modelsView(s);
  assert.ok(html.includes('&lt;img'));
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(!html.includes('<script>bad'));
  assert.ok(!html.includes('连接通过'));
});
test('unsaved profiles disable probes and retain an explicit save action', () => {
  const s = state(); s.modelsDirty = true;
  const html = modelsView(s);
  assert.match(html, /data-model-action="test"[^>]+disabled/);
  assert.match(html, /data-model-action="save"(?![^>]*disabled)/);
  assert.ok(html.includes('有尚未保存'));
});
test('profile form supports all transport formats and only a key variable name', () => {
  const html = modelEditor('', {});
  for (const value of ['openai_json_schema', 'llama_json_schema', 'json_object', 'prompt', 'network_mode', 'allowed_hosts', 'api_key_env']) assert.ok(html.includes(value));
  assert.ok(html.includes('max="600"'));
  assert.ok(html.includes('max="2000000"'));
  assert.ok(!/name="api_key"/.test(html));
});
test('workflow uses current role models and actual returned graph topology', () => {
  const html = workflowBoard(inventory(), { image: { model: 'image-engine' } }, { orchestrator: 'LangGraph', version: '3.0.0', nodes: [{ id: 'research' }, { id: 'story_decision' }], edges: [{ source: 'research', target: 'story_decision', conditional: true }] });
  for (const text of ['writer-model', 'critic-model', 'image-engine', 'research', 'story_decision', '2 节点', '不代表历史任务已调用']) assert.ok(html.includes(text));
});
test('workflow empty states never invent an available model', () => {
  const html = workflowBoard(null, {});
  assert.ok(html.includes('等待配置'));
  assert.ok(html.includes('等待能力检测'));
  assert.ok(html.includes('执行图接口尚未返回'));
});
test('existing final review remains a real decision form without autoapproval', () => {
  const s = { project: { id: '012345abcdef', title: '待审项目', format: 'motion_comic', duration_seconds: 60, status: 'awaiting_final', stage: 'final_decision', delivery: { video_url: '/media/production/test/release/movie.mp4' }, decisions: [{ id: 'gate-1', type: 'final', status: 'pending' }], shots: [], budget: {} }, models: inventory(), overview: { capabilities: {} }, tab: 'overview', busy: false };
  const html = projectView(s);
  assert.ok(html.includes('data-decision-id="gate-1"'));
  assert.ok(html.includes('data-form="final"'));
  assert.ok(html.includes('value="approve"'));
  assert.ok(html.includes('movie.mp4'));
});
test('role selection only updates a draft; explicit save sends a versioned registry', async () => {
  const s = state(), harness = browserHarness(s), requests = [];
  globalThis.fetch = async (url, options) => { requests.push({ url, options }); return new Response(JSON.stringify(inventory('config-b')), { status: 200 }); };
  harness.listeners.change({ target: { closest() { return { dataset: { roleAssignment: 'screenwriter' }, value: 'critic' }; } } });
  assert.equal(s.modelsDirty, true);
  assert.equal(requests.length, 0);
  await harness.click('save');
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, '/api/factory/models');
  const payload = JSON.parse(requests[0].options.body);
  assert.equal(payload.expected_revision, 'config-a');
  assert.equal(payload.roles.screenwriter, 'critic');
  assert.equal(s.models.revision, 'config-b');
  assert.equal(s.modelsDirty, false);
});
test('a conflicting save retains the edited draft and exposes the error', async () => {
  const s = state(); s.modelsDirty = true; s.modelsDraft.roles.screenwriter = 'critic';
  const harness = browserHarness(s);
  globalThis.fetch = async () => new Response(JSON.stringify({ error: 'Configuration changed; reload' }), { status: 409 });
  await harness.click('save');
  assert.equal(s.modelsDirty, true);
  assert.equal(s.modelsDraft.roles.screenwriter, 'critic');
  assert.equal(s.modelSaveError, 'Configuration changed; reload');
  assert.equal(s.busy, false);
});
test('connection testing is explicit and calls only the saved-profile route', async () => {
  const s = state(), harness = browserHarness(s), requests = [];
  globalThis.fetch = async (url, options) => { requests.push({ url, options }); return new Response(JSON.stringify({ profile: 'writer', status: 'ready', model_match: true, generation: false, elapsed_ms: 4 }), { status: 200 }); };
  assert.equal(requests.length, 0);
  await harness.click('test');
  assert.equal(requests[0].url, '/api/factory/models/test');
  assert.deepEqual(JSON.parse(requests[0].options.body), { profile_id: 'writer' });
  assert.equal(s.modelTests.writer.status, 'ready');
  assert.equal(s.modelTests.writer.generation, false);
});
test('a late probe cannot claim success for an edited connection', async () => {
  const s = state(), harness = browserHarness(s);
  let resolve;
  globalThis.fetch = () => new Promise(done => { resolve = done; });
  const pending = harness.click('test');
  s.modelsDirty = true;
  resolve(new Response(JSON.stringify({ profile: 'writer', status: 'ready' }), { status: 200 }));
  await pending;
  assert.deepEqual(s.modelTests, {});
});
test('model and workflow reads use GET without request bodies', async () => {
  const requests = [];
  globalThis.fetch = async (url, options) => { requests.push({ url, options }); return new Response('{}', { status: 200 }); };
  await api.models(); await api.workflow();
  assert.deepEqual(requests.map(r => r.url), ['/api/factory/models', '/api/factory/workflow']);
  assert.ok(requests.every(r => r.options.method === 'GET' && r.options.body === undefined));
});
test('all deployed native JavaScript files parse', async () => {
  const files = ['api.js', 'format.js', 'panels.js', 'views.js', 'actions.js', 'app.js', 'models.js', 'workflow.js', 'connections.js', 'video-models.js', 'team.js'];
  const { SourceTextModule } = await import('node:vm');
  if (SourceTextModule) {
    for (const file of files) new SourceTextModule(await readFile(new URL('../ui/factory/' + file, import.meta.url), 'utf8'));
  } else {
    const { spawnSync } = await import('node:child_process');
    for (const file of files) {
      const result = spawnSync(process.execPath, ['--check', new URL('../ui/factory/' + file, import.meta.url).pathname], { encoding: 'utf8' });
      assert.equal(result.status, 0, file + ': ' + result.stderr);
    }
  }
});
test('visual review selector disables a text-only connection', () => {
  const html = modelsView(state());
  const row = html.split('id="role-visual_critic"')[1].split('</select>')[0];
  assert.match(row, /value="critic"[^>]*disabled/);
  assert.ok(row.includes('不支持图像输入'));
});
test('the real app navigates workflow, models and a pending project through read-only APIs', async () => {
  const { parseHTML } = await import('linkedom');
  const workspaceDocument = parseHTML('<html><body><main id="workspace"></main></body></html>').document;
  const previous = { document: globalThis.document, window: globalThis.window, location: globalThis.location, history: globalThis.history, fetch: globalThis.fetch, setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout };
  const events = new Map(), windowEvents = new Map(), nodes = new Map(), requests = [];
  nodes.set('workspace', workspaceDocument.getElementById('workspace'));
  class Element {
    constructor() { this.innerHTML = ''; this.textContent = ''; this.dataset = {}; this.open = false; this.classList = { toggle() {} }; }
    querySelector() { return null; }
    querySelectorAll() { return []; }
    contains() { return false; }
    focus() {}
    setAttribute() {}
    addEventListener(type, listener) { (events.get('element:' + type) || events.set('element:' + type, []).get('element:' + type)).push(listener); }
  }
  const node = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  globalThis.document = { hidden: false, activeElement: null, getElementById: node, querySelector: node, querySelectorAll() { return []; }, addEventListener(type, listener) { (events.get(type) || events.set(type, []).get(type)).push(listener); } };
  globalThis.window = { scrollTo() {}, addEventListener(type, listener) { (windowEvents.get(type) || windowEvents.set(type, []).get(type)).push(listener); } };
  globalThis.location = { origin: 'http://127.0.0.1:8787', pathname: '/', search: '', hash: '' };
  globalThis.history = { replaceState(_state, _title, value) { globalThis.location.hash = value.includes('#') ? '#' + value.split('#')[1] : ''; } };
  let timer = 0;
  globalThis.setTimeout = () => ++timer;
  globalThis.clearTimeout = () => {};
  const project = { id: '012345abcdef', title: '待审项目', format: 'motion_comic', duration_seconds: 60, status: 'awaiting_final', stage: 'final_decision', decisions: [{ id: 'gate-app', type: 'final', status: 'pending' }], shots: [], budget: {}, delivery: {} };
  globalThis.fetch = async (url, options) => {
    requests.push({ url, method: options.method });
    const result = url === '/api/factory/models' ? inventory() : url === '/api/factory/workflow' ? { nodes: [{ id: 'research' }], edges: [], orchestrator: 'LangGraph', version: '3.0.0' } : url === '/api/factory/overview' ? { projects: [project], capabilities: {}, queue: {} } : url === '/api/factory/projects/012345abcdef' ? project : url === '/api/factory/projects/012345abcdef/flow' ? flowFixture() : {};
    return new Response(JSON.stringify(result), { status: 200 });
  };
  try {
    await import('../ui/factory/app.js?ui-contract-smoke');
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(node('workspace').innerHTML.includes('一个方向，一支制作团队'));
    const click = { target: { closest(selector) { return selector === '[data-action]' ? { dataset: { action: 'models' }, disabled: false } : null; } } };
    for (const listener of events.get('click') || []) await listener(click);
    assert.ok(node('workspace').innerHTML.includes('模型与连接'));
    assert.ok(node('workspace').innerHTML.includes('writer-model'));
    globalThis.location.hash = '#project=012345abcdef';
    for (const listener of windowEvents.get('hashchange') || []) await listener();
    assert.ok(node('workspace').innerHTML.includes('待审项目'));
    assert.ok(node('workspace').innerHTML.includes('data-decision-id="gate-app"'));
    const teamClick = { target: { closest(selector) { return selector === '[data-action]' ? { dataset: { action: 'tab', tab: 'team' }, disabled: false } : null; } } };
    for (const listener of events.get('click') || []) await listener(teamClick);
    assert.ok(node('workspace').innerHTML.includes('actual-history-model'));
    assert.ok(node('workspace').innerHTML.includes('经审核的剧本'));
    assert.ok(requests.some(request => request.url === '/api/factory/projects/012345abcdef/flow'));
    assert.ok(requests.every(request => request.method === 'GET'));
  } finally {
    Object.assign(globalThis, previous);
  }
});

function videoInventory(revision = 'video-config-a') {
  const value = inventory(revision);
  value.video_profiles = {
    local_wan: { provider: 'comfyui_wan', enabled: true, model: 'Wan2.2-TI2V-5B' },
    cloud_video: { provider: 'minimax', enabled: true, model: 'test-video-model', base_url: 'https://api.example.test/v1', network_mode: 'remote', allowed_hosts: ['api.example.test'], download_hosts: ['assets.example.test'], api_key_env: 'VIDEO_TEST_KEY', duration: 6, resolution: '768P', request_timeout_seconds: 60, poll_interval_seconds: 5, max_wait_seconds: 1800, prompt_optimizer: false }
  };
  value.video_roles = { video_generator: 'local_wan' };
  value.video_providers = [{ id: 'comfyui_wan', label: 'ComfyUI / Wan' }, { id: 'minimax', label: 'MiniMax Video' }];
  value.video_role_options = [{ role: 'video_generator', label: '视频生成' }];
  return value;
}
function flowFixture(projectId = '012345abcdef') {
  return {
    project_id: projectId, revision: 7, assignment_mode: 'orchestrator',
    teams: [{ id: 'screenwriter', role: 'screenwriter', label: '编剧', profile_id: 'new-default', model: 'current-default-model', provider: 'vllm', status: 'succeeded' }, { id: 'storyboard_director', role: 'storyboard_director', label: '分镜导演', profile_id: 'director', model: 'director-model', provider: 'ollama_native', status: 'running' }],
    nodes: [
      { id: 'screenwriter', role: 'screenwriter', label: '剧本创作', status: 'succeeded', inputs: [{ label: '创作方向', value: '一场雨中的重逢' }], outputs: [{ label: '剧本', value: '序场：车站', artifact_url: '/media/production/factory-' + projectId + '/script.json' }], executions: [{ id: 'receipt-1', profile_id: 'old-writer', provider: 'llama_cpp', model: 'actual-history-model', model_source: 'response', scope: 'project_history', status: 'succeeded', attempt: 1, started_at: null, finished_at: null, receipt: '/media/production/factory-' + projectId + '/llm/receipt.json' }] },
      { id: 'storyboard_director', role: 'storyboard_director', label: '分镜设计', status: 'running', inputs: [{ label: '剧本', value: '序场：车站' }], outputs: [], executions: [] }
    ],
    edges: [{ source: 'screenwriter', target: 'storyboard_director', label: '经审核的剧本', status: 'succeeded' }]
  };
}
test('video and text assignments share one revisioned payload without mutating inventory', () => {
  const s = state(), original = videoInventory();
  ingestInventory(s, original, true);
  s.modelsDraft.video_roles.video_generator = 'cloud_video';
  s.modelsDraft.video_profiles.cloud_video.download_hosts.push('second.example.test');
  const payload = registryPayload(s);
  assert.equal(payload.expected_revision, 'video-config-a');
  assert.equal(payload.video_roles.video_generator, 'cloud_video');
  assert.deepEqual(original.video_profiles.cloud_video.download_hosts, ['assets.example.test']);
  assert.equal(original.video_roles.video_generator, 'local_wan');
  assert.equal(payload.profiles.writer.temperature_override, 0.4);
  assert.equal(payload.video_profiles.cloud_video.prompt_optimizer, false);
});
test('conflicting inventory refresh preserves a dirty video draft', () => {
  const s = state();
  ingestInventory(s, videoInventory(), true);
  s.modelsDraft.video_roles.video_generator = 'cloud_video'; s.modelsDirty = true;
  ingestInventory(s, videoInventory('video-config-b'));
  assert.equal(s.models.revision, 'video-config-a');
  assert.equal(s.modelsDraft.video_roles.video_generator, 'cloud_video');
  assert.equal(s.modelsRemoteChanged, true);
});
test('video role changes remain drafts and video preflight uses its own non-generation route', async () => {
  const s = state();
  ingestInventory(s, videoInventory(), true);
  const harness = browserHarness(s), requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    return new Response(JSON.stringify({ status: 'configured', message: '配置预检通过，尚未验证视频生成', generation: false }), { status: 200 });
  };
  harness.listeners.change({ target: { closest() { return { dataset: { roleAssignment: 'video_generator', roleKind: 'video' }, value: 'cloud_video' }; } } });
  assert.equal(s.modelsDraft.video_roles.video_generator, 'cloud_video');
  assert.equal(s.modelsDraft.roles.screenwriter, 'writer');
  assert.equal(requests.length, 0);
  assert.equal(s.modelsDirty, true);
  await harness.click('video-test', 'cloud_video');
  assert.equal(requests.length, 0);
  ingestInventory(s, videoInventory(), true);
  await harness.click('video-test', 'cloud_video');
  assert.deepEqual(JSON.parse(requests[0].options.body), { profile_id: 'cloud_video', kind: 'video' });
  assert.equal(s.modelTests['video:cloud_video'].generation, false);
  const html = modelsView(s);
  assert.ok(html.includes('配置预检通过'));
  assert.ok(!html.includes('连接通过'));
  assert.ok(!html.includes('已生成视频'));
});
test('video form preserves transport options and removes cloud fields when switching to managed local video', () => {
  const previous = videoInventory().video_profiles.cloud_video;
  const next = videoProfileFromForm({ provider: 'minimax', enabled: 'on', model: 'replacement-video-model', base_url: previous.base_url, api_key_env: previous.api_key_env, allowed_hosts: 'api.example.test', download_hosts: 'assets.example.test', duration: '10', resolution: '1080P', request_timeout_seconds: '90', poll_interval_seconds: '10', max_wait_seconds: '2400' }, previous);
  assert.equal(next.duration, 10);
  assert.equal(next.request_timeout_seconds, 90);
  assert.equal(next.max_wait_seconds, 2400);
  assert.equal(next.prompt_optimizer, false);
  assert.deepEqual(next.download_hosts, ['assets.example.test']);
  const local = videoProfileFromForm({ provider: 'comfyui_wan', enabled: 'on', model: 'Wan2.2-TI2V-5B' }, previous);
  assert.deepEqual(local, { provider: 'comfyui_wan', enabled: true, model: 'Wan2.2-TI2V-5B' });
  const html = videoModelEditor('local_wan', local);
  assert.match(html, /id="video-model"[^>]*readonly/);
  assert.match(html, /class="video-remote-fields"[^>]*hidden disabled/);
  assert.ok(!/name="api_key"/.test(html));
});
test('unverified model-directory skips never claim an online connection', () => {
  const s = state();
  s.modelTests.writer = { status: 'unverified', model_discovery: 'skip', generation: false, message: '尚未验证连接' };
  const html = modelsView(s);
  assert.ok(html.includes('尚未验证连接'));
  assert.ok(!html.includes('连接通过'));
  assert.ok(!html.includes('模型目录返回了配置模型'));
});
test('native and cloud provider controls preserve exact API prefixes and remove incompatible options', () => {
  const html = modelEditor('', { provider: 'ollama_native' });
  for (const provider of ['ollama_native', 'lm_studio', 'deepseek', 'dashscope']) assert.ok(html.includes('value="' + provider + '"'));
  assert.ok(html.includes('不需要 /v1'));
  assert.ok(endpointHelp('dashscope').includes('/compatible-mode/v1'));
  const previous = { ...inventory().editable_profiles.writer, thinking: true };
  const next = profileFromForm({ provider: 'ollama_native', enabled: 'on', network_mode: 'loopback', base_url: 'http://127.0.0.1:11434/proxy/api', model: 'qwen-local', context_tokens: '32768', request_timeout_seconds: '120', response_format: 'openai_json_schema', model_discovery: 'skip', thinking: 'false' }, previous);
  assert.equal(next.base_url, 'http://127.0.0.1:11434/proxy/api');
  assert.equal(next.thinking, false);
  assert.equal(next.model_discovery, 'required');
  assert.equal(next.chat_template_kwargs, undefined);
  assert.equal(next.context_validation, 'conservative_estimate');
  const generic = profileFromForm({ provider: 'vllm', base_url: 'http://127.0.0.1:8000/v1', model: 'qwen', context_tokens: '32768', request_timeout_seconds: '120', response_format: 'openai_json_schema' }, previous);
  assert.equal(generic.thinking, undefined);
});
test('provider presets never overwrite a custom proxy URL', () => {
  const help = {}, skip = {};
  const field = value => ({ value });
  const form = { elements: { provider: { value: 'dashscope', dataset: { previousProvider: 'vllm' } }, base_url: field('https://gateway.example.test/team/compatible-mode/v1'), network_mode: field('remote'), allowed_hosts: field('gateway.example.test'), response_format: field('json_object'), model_discovery: { value: 'required', querySelector() { return skip; } }, thinking: field('') }, querySelector() { return help; } };
  switchTextProvider(form);
  assert.equal(form.elements.base_url.value, 'https://gateway.example.test/team/compatible-mode/v1');
  assert.equal(form.elements.allowed_hosts.value, 'gateway.example.test');
  assert.equal(form.elements.model_discovery.value, 'skip');
  assert.equal(form.elements.thinking.value, 'false');
  assert.ok(help.textContent.includes('完整兼容 API 前缀'));
});
test('flow APIs are read-only and a response from another project cannot replace current evidence', async () => {
  const s = { projectId: '012345abcdef', project: { id: '012345abcdef' } };
  assert.equal(ingestProjectFlow(s, flowFixture(), s.projectId), true);
  const retained = s.projectFlow;
  assert.equal(ingestProjectFlow(s, flowFixture('ffffffffffff'), s.projectId), false);
  assert.equal(s.projectFlow, retained);
  assert.ok(s.projectFlowError.includes('不匹配'));
  assert.equal(ingestProjectFlow(s, flowFixture('ffffffffffff'), 'ffffffffffff'), false);
  const requests = [];
  globalThis.fetch = async (url, options) => { requests.push({ url, options }); return new Response(JSON.stringify(flowFixture()), { status: 200 }); };
  await api.flow('012345abcdef');
  assert.equal(requests[0].url, '/api/factory/projects/012345abcdef/flow');
  assert.equal(requests[0].options.method, 'GET');
  assert.equal(requests[0].options.body, undefined);
});
test('team flow shows real input, output, dependencies and historical models independently of current assignment', () => {
  const s = { project: { id: '012345abcdef' }, projectFlow: flowFixture() };
  const html = teamFlowView(s);
  for (const text of ['总控自动分工', 'current-default-model', 'actual-history-model', '一场雨中的重逢', '序场：车站', '经审核的剧本', '接收自', '交给', '项目历史记录', '服务返回模型', '执行回执', '尚无已保存的模型调用记录']) assert.ok(html.includes(text), text);
  assert.match(html, /href="http:\/\/127\.0\.0\.1:8787\/media\/production\/factory-012345abcdef\/llm\/receipt\.json"/);
  assert.ok(!html.includes('开始 尚未记录'));
  const executionArea = html.split('class="flow-execution"')[1].split('</details>')[0];
  assert.ok(executionArea.includes('actual-history-model'));
  assert.ok(!executionArea.includes('current-default-model'));
  assert.ok(!/style="width:\d+%"/.test(html));
});
test('flow data is escaped, unsafe artifact URLs are omitted and stale snapshots stay explicit', () => {
  const flow = flowFixture();
  flow.nodes[0].inputs[0].value = '<script>alert(1)</script>';
  flow.nodes[0].outputs[0].artifact_url = 'javascript:alert(1)';
  flow.nodes[0].executions[0].receipt = '<img src=x onerror=alert(1)>';
  const html = teamFlowView({ project: { id: flow.project_id }, projectFlow: flow, projectFlowError: '连接失败' });
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(html.includes('&lt;img'));
  assert.ok(!html.includes('<script>alert'));
  assert.ok(!html.includes('href="javascript:'));
  assert.ok(html.includes('上一次成功读取的记录'));
  const mismatched = teamFlowView({ project: { id: 'ffffffffffff' }, projectFlow: flow });
  assert.ok(!mismatched.includes('actual-history-model'));
  assert.ok(mismatched.includes('正在读取工作数据流'));
});

test('configured and unverified capability rows stay distinct from an unavailable service', () => {
  const rows = capabilities({ llm: { status: 'unverified', available: null, model: 'manual-model' }, video: { status: 'configured', available: null, model: 'video-model' }, audio: { available: false } });
  assert.equal(capabilityStatus(rows.find(row => row.key === 'llm')), '未验证');
  assert.equal(capabilityStatus(rows.find(row => row.key === 'video')), '已配置');
  assert.equal(capabilityStatus(rows.find(row => row.key === 'audio')), '不可用');
});
test('flow timestamps render seconds and ISO UTC consistently without inventing missing times', () => {
  const flow = flowFixture();
  flow.nodes[0].executions[0].started_at = 1700000000;
  flow.nodes[0].executions[0].finished_at = '2023-11-14T22:13:20Z';
  const html = teamFlowView({ project: { id: flow.project_id }, projectFlow: flow });
  assert.ok(html.includes('开始 ' + dateText('2023-11-14T22:13:20Z', true)));
  assert.ok(html.includes('结束 ' + dateText('2023-11-14T22:13:20Z', true)));
});
test('older project revisions cannot replace current task evidence after a retry', () => {
  const s = { projectId: '012345abcdef', project: { id: '012345abcdef', revision: 8 } };
  assert.equal(ingestProjectFlow(s, flowFixture(), s.projectId), false);
  assert.equal(s.projectFlow, undefined);
  assert.ok(s.projectFlowError.includes('项目已更新'));
  const current = flowFixture(); current.revision = 8;
  assert.equal(ingestProjectFlow(s, current, s.projectId), true);
  assert.equal(s.projectFlowError, '');
});
test('bounded history and human participation are displayed honestly', () => {
  const flow = flowFixture();
  flow.evidence = { truncated: true, unreadable_receipts: 2 };
  flow.teams.push({ id: 'producer', role: 'producer', label: '出品人', provider: 'human', assignment_source: 'human', status: 'waiting', model: null });
  const html = teamFlowView({ project: { id: flow.project_id }, projectFlow: flow });
  assert.ok(html.includes('当前仅显示部分历史记录'));
  assert.ok(html.includes('2 份回执暂时无法读取'));
  assert.ok(html.includes('人工确认'));
  assert.ok(html.includes('上游 · 已完成'));
});
test('changing a preset host does not forward the prior service credential variable', () => {
  const skip = {}, help = {};
  const form = { elements: { provider: { value: 'ollama_native', dataset: { previousProvider: 'deepseek' } }, base_url: { value: 'https://api.deepseek.com' }, api_key_env: { value: 'DEEPSEEK_API_KEY' }, network_mode: { value: 'remote' }, allowed_hosts: { value: 'api.deepseek.com' }, response_format: {}, model_discovery: { querySelector() { return skip; } }, thinking: {} }, querySelector() { return help; } };
  switchTextProvider(form);
  assert.equal(form.elements.base_url.value, 'http://127.0.0.1:11434');
  assert.equal(form.elements.api_key_env.value, '');
  assert.equal(form.elements.network_mode.value, 'loopback');
  form.elements.provider.value = 'ollama';
  form.elements.api_key_env.value = 'LOCAL_AUTH_KEY';
  switchTextProvider(form);
  assert.equal(form.elements.base_url.value, 'http://127.0.0.1:11434/v1');
  assert.equal(form.elements.api_key_env.value, 'LOCAL_AUTH_KEY');
});
