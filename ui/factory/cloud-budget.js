import { h, list, dateText } from './format.js';

export const money = (micros, currency = 'CNY') => micros == null ? '未确认' : (micros / 1000000).toFixed(6).replace(/0+$/, '').replace(/\.$/, '') + ' ' + currency;
export function parseMoney(value) {
  if (!/^\d{1,9}(\.\d{1,6})?$/.test(String(value))) throw new Error('费用请输入非负金额，最多六位小数。');
  const [whole, fraction = ''] = String(value).split('.');
  const result = Number(whole) * 1000000 + Number(fraction.padEnd(6, '0'));
  if (!Number.isSafeInteger(result)) throw new Error('费用金额超出范围。');
  return result;
}
export function cloudAuthorizationPayload(form, values) {
  const expected = Number(form.dataset.revision), count = Number(values.max_submissions);
  if (!Number.isInteger(expected) || expected < 1 || !Number.isInteger(count) || count < 1 || count > 1000) throw new Error('请刷新项目并填写有效的授权次数。');
  const unknown = form.dataset.unknown === 'true';
  if (unknown && values.confirm_unknown_cost !== 'on') throw new Error('必须明确确认未知费用风险。');
  return { batch_id: form.dataset.batch, expected_revision: expected, max_submissions: count,
    max_estimated_cost_micros: unknown ? null : parseMoney(values.max_cost), confirm_unknown_cost: unknown && values.confirm_unknown_cost === 'on' };
}
export function cloudBudgetPanel(project) {
  const ledger = project.cloud_budget || {}, quote = ledger.quote || project.cloud_quote;
  const tasks = list(ledger.tasks);
  const totals = Object.entries(ledger.totals || {}).map(([currency, total]) => '<p>' + h(currency) + '：累计估算 ' + h(money(total.estimated_cost_micros, currency)) + '；已确认 ' + h(money(total.confirmed_cost_micros, currency)) + '；仍未确认 ' + h(total.unconfirmed_tasks) + ' 项。</p>').join('');
  const table = '<details><summary>累计云任务账本 · ' + tasks.length + ' 项</summary><div class="cloud-table-wrap"><table><thead><tr><th>镜头 / 尝试</th><th>模型</th><th>任务 ID</th><th>状态</th><th>估价</th><th>确认费用</th></tr></thead><tbody>' + tasks.map(task => '<tr><td>' + h(task.shot_id) + ' / ' + h(task.attempt) + ' · ' + h(task.take) + '</td><td>' + h(task.provider) + ' / ' + h(task.model) + '</td><td>' + h(task.task_id || '结果未确认') + '</td><td>' + h(task.status) + '</td><td>' + h(money(task.estimated_cost_micros, task.currency)) + '</td><td>' + h(money(task.confirmed_cost_micros, task.currency)) + '</td></tr>').join('') + '</tbody></table></div></details>';
  if (!quote) return '<section class="panel"><h2>云视频预算与授权</h2><p>尚无待提交的云视频批次。镜头首帧和生成参数就绪后，系统会先展示估价并等待明确授权。</p>' + totals + table + '</section>';
  const price = quote.pricing || {}, unknown = price.unit_cost_micros == null;
  const auth = list(ledger.authorizations).filter(row => row.batch_id === quote.id).at(-1);
  const blocked = String(project.needs_decision?.code || '').startsWith('cloud_');
  const editable = !['approved', 'cancelled'].includes(project.status) && (!auth || blocked);
  const form = editable ? '<form data-form="cloud-authorize" data-batch="' + h(quote.id) + '" data-revision="' + h(project.revision) + '" data-unknown="' + unknown + '"><div class="form-row"><label>本批次累计最多提交次数<input name="max_submissions" type="number" min="1" max="1000" step="1" value="' + h(quote.estimated_submissions) + '" required></label>' + (unknown ? '<label><input type="checkbox" name="confirm_unknown_cost" required>我已了解单价尚未确认，仍授权本批次列明的请求；费用并无严格账单硬限额。</label>' : '<label>本批次累计估价额度 / ' + h(price.currency) + '<input name="max_cost" inputmode="decimal" value="' + h((quote.estimated_total_micros / 1000000).toFixed(6)) + '" required></label>') + '</div><button type="submit" class="button primary">明确授权本批次</button></form>' : auth ? '<p>已于 ' + h(dateText(auth.created, true)) + ' 授权；累计最多 ' + h(auth.limits.max_submissions) + ' 次。新的请求参数与返修候选仍需重新授权。</p>' : '';
  return '<section class="panel cloud-budget"><h2>云视频预算与授权</h2><p class="notice">费用标记为“' + (unknown ? '未确认' : '估算') + '”。这里只限制已授权的请求与估价预留，不冒充供应商真实账单硬限额。暂停、恢复与返修不会清空累计记录。</p><dl class="data-list"><div><dt>模型</dt><dd>' + h(quote.connection?.provider) + ' / ' + h(quote.connection?.model) + '</dd></div><div><dt>生产版本</dt><dd>' + h(quote.production_revision) + '</dd></div><div><dt>镜头 / 预计提交</dt><dd>' + h(quote.shot_count) + ' 镜 / ' + h(quote.estimated_submissions) + ' 次</dd></div><div><dt>分辨率 / 单次生成时长</dt><dd>' + h(quote.resolution) + ' / ' + h(quote.duration) + ' 秒</dd></div><div><dt>单次估价</dt><dd>' + h(money(price.unit_cost_micros, price.currency)) + '</dd></div><div><dt>批次总估价</dt><dd>' + h(money(quote.estimated_total_micros, price.currency)) + '</dd></div><div><dt>估价依据</dt><dd>' + h(price.source || '未提供可靠计价依据') + '</dd></div></dl><details><summary>查看授权绑定的镜头与参数</summary>' + list(quote.lines).map(line => '<article><h3>' + h(line.shot_id) + ' · ' + h(line.take) + '</h3><p>' + h(line.prompt) + '</p><small>时间线 ' + h(line.timeline_seconds) + ' 秒 · 参数签名 ' + h(line.request_signature) + '</small></article>').join('') + '</details>' + form + totals + table + '</section>';
}
