import test from 'node:test';
import assert from 'node:assert/strict';
import { cloudBudgetPanel, cloudAuthorizationPayload, parseMoney } from '../ui/factory/cloud-budget.js';

test('cloud consent requires explicit unknown-price acknowledgement and exact integer money', () => {
  const form = { dataset: { revision: '7', batch: 'batch-1', unknown: 'true' } };
  assert.throws(() => cloudAuthorizationPayload(form, { max_submissions: '2' }), /未知费用/);
  const unknown = cloudAuthorizationPayload(form, { max_submissions: '2', confirm_unknown_cost: 'on' });
  assert.equal(unknown.confirm_unknown_cost, true);
  assert.equal(unknown.max_estimated_cost_micros, null);
  form.dataset.unknown = 'false';
  const known = cloudAuthorizationPayload(form, { max_submissions: '2', max_cost: '1.000001' });
  assert.equal(known.max_estimated_cost_micros, 1000001);
  assert.equal(known.expected_revision, 7);
  for (const value of ['NaN', '-1', '0.0000001', '1e5', '']) assert.throws(() => parseMoney(value));
});
test('cloud panel distinguishes quotes from real bills and escapes untrusted metadata', () => {
  const project = { revision: 7, status: 'blocked', cloud_quote: { id: 'batch-1', production_revision: 2, shot_count: 2, estimated_submissions: 2, resolution: '768P', duration: 6, connection: { provider: 'minimax', model: '<img src=x onerror=alert(1)>' }, pricing: { unit_cost_micros: null, currency: 'CNY' }, lines: [] } };
  const html = cloudBudgetPanel(project);
  assert.ok(html.includes('未确认') && html.includes('不冒充供应商真实账单硬限额'));
  assert.ok(html.includes('data-revision="7"') && html.includes('confirm_unknown_cost'));
  assert.ok(!html.includes('<img src=x'));
  assert.ok(cloudBudgetPanel({}).includes('尚无待提交'));
});
