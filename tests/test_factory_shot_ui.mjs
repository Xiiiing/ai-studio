import test from 'node:test';
import assert from 'node:assert/strict';
import { parseHTML } from 'linkedom';
import { patchWorkspace } from '../ui/factory/dom-patch.js';
import { shotPayload, bindShotActions } from '../ui/factory/shot-actions.js';
import { shotWorkbenchView } from '../ui/factory/shot-workbench.js';
import { api } from '../ui/factory/api.js';

globalThis.location = { origin: 'http://127.0.0.1:8787' };
const state = () => ({ project: { id: '012345abcdef', workbench: { revision: 7, production_revision: 2, editable: true, shots: [
  { id: 'S01', seconds: 4, shot_revision: 1, prompt: 'A station', motion_prompt: 'Slow camera', candidates: [
    { id: 'video:take_001', stage: 'video', take: 'take_001', media_url: '/media/shot.mp4', qa: { score: 7 }, model: '<img src=x onerror=alert(1)>', selectable: true, plan_current: true, cost: { currency: 'CNY', estimated_cost_micros: 1000000 } },
    { id: 'image:take_002', stage: 'image', take: 'take_002', media_url: 'javascript:alert(1)', error: 'identity failed', selectable: false }
  ] }, { id: 'S02', seconds: 6, prompt: 'Another shot', candidates: [] }
] } } });

test('shot details compare candidates with costs, failures and escaped metadata', () => {
  const html = shotWorkbenchView(state());
  assert.ok(html.includes('S01') && html.includes('S02') && html.includes('1 CNY'));
  assert.ok(html.includes('identity failed') && html.includes('data-shot-operation="select"'));
  assert.ok(!html.includes('<img src=x') && !html.includes('src="javascript:'));
  assert.ok(html.includes('data-revision="7"') && html.includes('required'));
});
test('drafts retain the original revision after a concurrent refresh', () => {
  const s = state();
  s.shotDrafts = { '012345abcdef:S01': { revision: 5, values: { prompt: 'unsaved text', motion_prompt: 'camera', stage: 'video', regenerate: 'on' } } };
  const html = shotWorkbenchView(s);
  assert.ok(html.includes('data-revision="5"') && html.includes('unsaved text') && html.includes('项目已更新'));
  assert.match(html, /value="video" selected/);
});
test('selection consent and whitelisted edit payloads cannot smuggle arbitrary fields', () => {
  const form = { dataset: { revision: '7', shotOperation: 'select', stage: 'video', take: 'take_001' } };
  assert.throws(() => shotPayload(form, {}), /明确确认/);
  assert.deepEqual(shotPayload(form, { confirmed: 'on', model: 'unapproved' }), { action: 'select', expected_revision: 7, stage: 'video', take: 'take_001', confirmed: true });
  form.dataset.shotOperation = 'edit';
  assert.deepEqual(Object.keys(shotPayload(form, { prompt: 'x', motion_prompt: 'y', camera_motion: 'hold', seconds: 100 }).changes).sort(), ['camera_motion', 'motion_prompt', 'prompt']);
});
test('DOM refresh updates siblings without replacing or loading a playing video', () => {
  const { document } = parseHTML('<html><body><main><section><p>10%</p><video src="/media/shot.mp4"></video><span>old</span></section></main></body></html>');
  const root = document.querySelector('main'), video = root.querySelector('video');
  Object.assign(video, { currentTime: 3.2, paused: false, ended: false, load() { throw new Error('must not reload'); } });
  const mutations = [];
  const remove = video.remove;
  video.remove = () => { mutations.push('remove'); return remove.call(video); };
  patchWorkspace(root, '<section><p>60%</p><video src="/media/shot.mp4"></video><span>new</span><strong>progress</strong></section>');
  assert.equal(root.querySelector('video'), video);
  assert.equal(video.currentTime, 3.2);
  assert.equal(video.paused, false);
  assert.deepEqual(mutations, []);
  assert.match(root.textContent, /60%newprogress/);
});
test('keyed media survives insertion and removal of unrelated regions', () => {
  const { document } = parseHTML('<html><body><main><article data-dom-key="clip"><video src="/media/a.mp4"></video></article></main></body></html>');
  const root = document.querySelector('main'), video = root.querySelector('video');
  Object.assign(video, { paused: false, ended: false, currentTime: 2 });
  patchWorkspace(root, '<p data-dom-key="error">服务暂时不可用</p><article data-dom-key="clip"><video src="/media/a.mp4"></video></article>');
  assert.equal(root.querySelector('video'), video);
  assert.ok(root.textContent.includes('服务暂时不可用'));
  patchWorkspace(root, '<article data-dom-key="clip"><video src="/media/a.mp4"></video></article>');
  assert.equal(root.querySelector('video'), video);
});
test('DOM form updates preserve node identity and reset discarded values', () => {
  const { document } = parseHTML('<html><body><main><input value="old"><textarea>draft</textarea><select><option value="a" selected>A</option><option value="b">B</option></select></main></body></html>');
  const root = document.querySelector('main'), input = root.querySelector('input');
  input.value = 'dirty';
  patchWorkspace(root, '<input value="new"><textarea>saved</textarea><select><option value="a">A</option><option value="b" selected>B</option></select>');
  assert.equal(root.querySelector('input'), input);
  assert.equal(input.value, 'new');
  assert.equal(root.querySelector('textarea').value, 'saved');
  assert.equal(root.querySelector('select').value, 'b');
});
test('shot navigation and failed CAS retain draft and show a failure message', async () => {
  const previous = { document: globalThis.document, FormData: globalThis.FormData };
  const listeners = new Map(), messages = [], s = state();
  s.projectId = s.project.id; s.shotDrafts = { [s.projectId + ':S01']: { revision: 7, values: { prompt: 'draft' } } };
  globalThis.document = { addEventListener(type, callback) { (listeners.get(type) || listeners.set(type, []).get(type)).push(callback); } };
  const ui = { paint() {}, refresh: async () => {}, toast: message => messages.push(message) };
  const original = api.shotAction;
  api.shotAction = async () => { throw new Error('项目已更新'); };
  globalThis.FormData = class { constructor() { return new Map([['prompt', 'draft'], ['motion_prompt', 'camera'], ['camera_motion', 'hold']]); } };
  try {
    bindShotActions(s, ui);
    await listeners.get('click')[0]({ target: { closest: () => ({ dataset: { shotOpen: 'S02' } }) } });
    assert.equal(s.shotId, 'S02');
    const form = { dataset: { shotOperation: 'edit', project: s.projectId, shot: 'S01', revision: '7' }, reportValidity: () => true, querySelectorAll: () => [] };
    await listeners.get('submit')[0]({ preventDefault() {}, target: { closest: () => form } });
    assert.deepEqual(messages, ['项目已更新']);
    assert.equal(s.shotDrafts[s.projectId + ':S01'].values.prompt, 'draft');
    assert.equal(s.busy, false);
  } finally { Object.assign(globalThis, previous); api.shotAction = original; }
});
