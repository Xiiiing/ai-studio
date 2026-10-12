/** Native UI contract tests. Run with Node 20+: node --test tests/test_factory_ui.mjs */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { api } from '../ui/factory/api.js';
import { bindModelActions, ingestInventory, modelEditor, modelsView, profileFromForm, registryPayload } from '../ui/factory/models.js';
import { workflowBoard } from '../ui/factory/workflow.js';
import { projectView } from '../ui/factory/views.js';

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
  const click = action => listeners.click({ target: { closest() { return { disabled: false, dataset: { modelAction: action, profile: 'writer' } }; } } });
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
  const files = ['api.js', 'format.js', 'panels.js', 'views.js', 'actions.js', 'app.js', 'models.js', 'workflow.js'];
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
  const previous = { document: globalThis.document, window: globalThis.window, location: globalThis.location, history: globalThis.history, fetch: globalThis.fetch, setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout };
  const events = new Map(), windowEvents = new Map(), nodes = new Map(), requests = [];
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
    const result = url === '/api/factory/models' ? inventory() : url === '/api/factory/workflow' ? { nodes: [{ id: 'research' }], edges: [], orchestrator: 'LangGraph', version: '3.0.0' } : url === '/api/factory/overview' ? { projects: [project], capabilities: {}, queue: {} } : url === '/api/factory/projects/012345abcdef' ? project : {};
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
    assert.ok(requests.every(request => request.method === 'GET'));
  } finally {
    Object.assign(globalThis, previous);
  }
});

