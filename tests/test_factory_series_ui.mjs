import test from 'node:test';
import assert from 'node:assert/strict';
import { seriesPanel, seriesFormKey } from '../ui/factory/series.js';
import { seriesPayload, bindSeriesActions } from '../ui/factory/series-actions.js';
import { api } from '../ui/factory/api.js';
import { createForm, projectView } from '../ui/factory/views.js';

globalThis.location = { origin: 'http://127.0.0.1:8787' };
const asset = { asset_id: 'asset_test', version: 2, revision: 4, kind: 'character', entity_id: 'char_mei', status: 'approved', data: { entity: { name: '<script>unsafe</script>' }, receipt: { path: 'production/series-assets/a/reference.png', sha256: 'checksum' } } };
const bible = { version: 1, document: { schema_version: 1, world: '虚构世界', visual_rules: '朴素照明', characters: [{ character_id: 'char_mei', name: '梅', identity_constraints: '固定成年面孔', default_wardrobe: '灰外套' }], scenes: [], props: [] } };
function project() {
  return { id: '012345abcdef', revision: 7, series_key: 'station', status: 'paused', shots: [{ id: 'S01' }, { id: 'S02' }], episode: { cast: { female: 'Adult actor' } }, series_library: { bible, pinned_bible: bible, assets: [asset], sources: [], bindings: {} } };
}
function form(operation = 'approve') {
  return { dataset: { seriesOperation: operation, revision: '7', asset: 'asset_test', version: '2', assetRevision: '4', expectedVersion: '2', seriesKey: '012345abcdef:approve:asset_test:2' } };
}
test('series panel keeps legacy projects standalone and adds an optional creation field', () => {
  assert.ok(seriesPanel({}).includes('原有单集素材格式'));
  assert.ok(createForm().includes('name="series_key"'));
  const html = projectView({ project: project(), tab: 'series' });
  assert.ok(html.includes('系列资产') && html.includes('Series Bible'));
});
test('asset controls require separate human approval and escape entity metadata', () => {
  const p = project();
  let html = seriesPanel(p);
  assert.ok(html.includes('&lt;script&gt;unsafe&lt;/script&gt;') && !html.includes('<script>unsafe'));
  assert.ok(html.includes('data-series-operation="bind"') && !html.includes('data-series-operation="approve"'));
  p.series_library.assets = [{ ...asset, status: 'reviewed', data: { review: { technical: { status: 'fail' } } } }];
  assert.ok(!seriesPanel(p).includes('data-series-operation="approve"'));
  p.series_library.assets[0].data.review.technical.status = 'pass';
  html = seriesPanel(p);
  assert.ok(html.includes('name="confirmed" required') && html.includes('技术检查不能保证'));
});
test('drafts retain their original CAS versions instead of adopting background updates', () => {
  const p = project();
  const key = seriesFormKey(p, 'save_bible');
  const drafts = { [key]: { expected_revision: 3, expected_version: 0, values: { document: '{"world":"my unsaved draft"}' } } };
  const html = seriesPanel(p, drafts);
  assert.ok(html.includes('data-revision="3"') && html.includes('data-expected-version="0"'));
  assert.ok(html.includes('my unsaved draft') && html.includes('放弃草稿并读取新版本'));
});
test('approval payload is explicit and exact-version bound', () => {
  assert.throws(() => seriesPayload(form(), {}), /明确确认/);
  assert.deepEqual(seriesPayload(form(), { confirmed: 'on' }), { action: 'approve', expected_revision: 7, asset_id: 'asset_test', version: 2, expected_asset_revision: 4, confirmed: true });
  const stale = form(); stale.dataset.revision = 'NaN';
  assert.throws(() => seriesPayload(stale, { confirmed: 'on' }), /版本/);
});
test('binding requires a nonempty explicit shot scope and does not send arbitrary file paths', () => {
  assert.throws(() => seriesPayload(form('bind'), { role: 'female', shot_ids: [] }), /选择/);
  assert.deepEqual(seriesPayload(form('bind'), { role: 'female', shot_ids: ['S01', 'S01'], path: '/etc/passwd' }), { action: 'bind', expected_revision: 7, asset_id: 'asset_test', version: 2, role: 'female', shot_ids: ['S01'] });
  const planned = form('plan'); planned.dataset.expectedVersion = '0'; planned.dataset.asset = '';
  assert.equal(seriesPayload(planned, { entity: 'prop:ticket' }).entity_id, 'ticket');
});
test('interaction reports a conflict once and preserves the producer draft without automatic resubmission', async () => {
  const listeners = {}, notices = [], calls = [];
  const originalDocument = globalThis.document, originalFormData = globalThis.FormData, originalApi = api.seriesAction;
  const f = { ...form(), reportValidity: () => true, querySelectorAll: () => [] };
  globalThis.document = { addEventListener(type, callback) { listeners[type] = callback; } };
  globalThis.FormData = class { constructor() { this.values = [['confirmed', 'on']]; } [Symbol.iterator]() { return this.values[Symbol.iterator](); } getAll() { return []; } };
  const state = { projectId: '012345abcdef', busy: false, epoch: 0 };
  api.seriesAction = async (pid, payload) => { calls.push({ pid, payload }); throw new Error('资产版本已更新'); };
  try {
    bindSeriesActions(state, { toast(message) { notices.push(message); }, async refresh() { calls.push('refresh'); } });
    listeners.input({ target: { closest() { return f; } } });
    await listeners.submit({ preventDefault() {}, target: { closest() { return f; } } });
    assert.equal(calls.filter(value => typeof value === 'object').length, 1);
    assert.equal(state.busy, false);
    assert.ok(state.seriesDrafts[f.dataset.seriesKey]);
    assert.ok(notices.includes('资产版本已更新'));
  } finally { globalThis.document = originalDocument; globalThis.FormData = originalFormData; api.seriesAction = originalApi; }
});
