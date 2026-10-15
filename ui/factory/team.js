import { teamCockpit } from './team-cockpit.js';
import { h, list, words, safeUrl, dateText } from './format.js';
import { compactModel, roleLabel } from './workflow.js';

const STATES = {
  pending: ['待执行', 'neutral'], running: ['执行中', 'attention'],
  succeeded: ['已完成', 'success'], failed: ['执行失败', 'danger'],
  waiting: ['等待决策', 'attention'], paused: ['已暂停', 'neutral'],
  cancelled: ['已取消', 'neutral']
};
export function flowBadge(status, prefix = '') {
  const [label, tone] = STATES[status] || ['状态未记录', 'neutral'];
  return '<span class="badge ' + tone + '"><span class="dot"></span>' + h(prefix) + label + '</span>';
}
export function ingestProjectFlow(state, flow, projectId) {
  if (!projectId || state.projectId !== projectId) return false;
  if (!flow || flow.project_id !== projectId || !Array.isArray(flow.nodes) || !Array.isArray(flow.teams) || !Array.isArray(flow.edges)) {
    state.projectFlowError = '工作流响应与当前项目不匹配，等待重新同步。';
    return false;
  }
  if (typeof flow.revision === 'number' && typeof state.project?.revision === 'number' && flow.revision < state.project.revision) {
    state.projectFlowError = '项目已更新，工作数据流正在重新同步。';
    return false;
  }
  state.projectFlow = flow;
  state.projectFlowError = '';
  return true;
}
function currentFlow(state) {
  return state.projectFlow?.project_id === state.project?.id ? state.projectFlow : null;
}
export function teamFlowSummary(state) {
  const flow = currentFlow(state);
  const active = list(flow?.nodes).filter(node => ['running', 'waiting', 'failed', 'paused'].includes(node.status));
  const text = state.projectFlowError ? '工作数据流同步中断' : active.length ? active.slice(0, 3).map(node => node.label || node.id).join(' · ') : flow ? '查看团队分工、执行记录与输入输出' : '正在读取团队执行状态';
  return '<div class="team-flow-entry"><div><strong>团队与数据流</strong><p>' + h(text) + '</p></div><button class="button small" data-action="tab" data-tab="team">查看工作数据流 ↗</button></div>';
}
function teamCard(team) {
  const human = team.assignment_source === 'human' || team.provider === 'human';
  const model = human ? '人工确认' : team.model ? compactModel(team.model) : team.role === 'orchestrator' ? '自动编排' : '尚未配置模型';
  return '<article class="team-member"><div><strong>' + h(team.label || roleLabel(team.role) || team.id) + '</strong>' + flowBadge(team.status) + '</div><p class="team-role">' + h(team.role || team.id) + '</p><span class="tiny muted">' + (human ? '执行方式' : '当前分配') + '</span><code title="' + h(team.model || '') + '">' + h(model) + '</code><small>' + h([team.profile_id, team.provider].filter(Boolean).join(' · ')) + '</small></article>';
}
function datum(item) {
  const url = safeUrl(item.artifact_url);
  const hasValue = item.value !== undefined && item.value !== null && item.value !== '';
  return '<li><strong>' + h(item.label || '数据') + '</strong>' + (hasValue ? '<pre class="flow-data-value">' + h(words(item.value)) + '</pre>' : '') + (url ? '<a href="' + h(url) + '" target="_blank" rel="noopener noreferrer">查看产物 ↗</a>' : '') + (!hasValue && !url ? '<span class="muted">尚未记录内容</span>' : '') + '</li>';
}
function ioPanel(label, values, empty) {
  const rows = list(values);
  return '<section class="flow-io"><h4>' + label + ' <span>' + rows.length + '</span></h4>' + (rows.length ? '<ul>' + rows.map(datum).join('') + '</ul>' : '<p class="muted">' + empty + '</p>') + '</section>';
}
function executionCard(execution) {
  const receiptUrl = typeof execution.receipt === 'string' && /^\/media\/production\//.test(execution.receipt) ? safeUrl(execution.receipt) : '';
  const model = execution.model ? compactModel(execution.model) : '未记录模型调用';
  const route = [execution.profile_id, execution.provider].filter(Boolean).join(' · ');
  const timestamps = [
    execution.started_at ? '开始 ' + dateText(execution.started_at, true) : '',
    execution.finished_at ? '结束 ' + dateText(execution.finished_at, true) : ''
  ].filter(Boolean).join(' · ');
  const attempt = Number.isInteger(Number(execution.attempt)) && Number(execution.attempt) > 0 ? '第 ' + Number(execution.attempt) + ' 次' : '';
  return '<article class="flow-execution"><div class="flow-execution-title"><strong title="' + h(execution.model || '') + '">' + h(model) + '</strong>' + flowBadge(execution.status) + '</div>' + (route ? '<p>' + h(route) + '</p>' : '') + '<div class="flow-execution-meta">' + (execution.scope === 'project_history' ? '<span>项目历史记录</span>' : '') + (execution.model_source === 'request' ? '<span>请求模型</span>' : execution.model_source === 'response' ? '<span>服务返回模型</span>' : '') + (attempt ? '<span>' + h(attempt) + '</span>' : '') + (execution.id ? '<code>' + h(execution.id) + '</code>' : '') + (timestamps ? '<span>' + h(timestamps) + '</span>' : '') + '</div>' + (execution.receipt ? '<div class="flow-receipt"><span>执行回执</span>' + (receiptUrl ? '<a href="' + h(receiptUrl) + '" target="_blank" rel="noopener noreferrer">' + h(execution.receipt) + ' ↗</a>' : '<code>' + h(typeof execution.receipt === 'string' ? execution.receipt : words(execution.receipt)) + '</code>') + '</div>' : '') + '</article>';
}
function handoffs(node, flow, direction) {
  const incoming = direction === 'incoming';
  const rows = list(flow.edges).filter(edge => (incoming ? edge.target : edge.source) === node.id);
  if (!rows.length) return '';
  const title = incoming ? '接收自' : '交给';
  return '<div class="flow-handoff"><span>' + title + '</span><ul>' + rows.map(edge => {
    const id = incoming ? edge.source : edge.target;
    const peer = list(flow.nodes).find(item => item.id === id);
    return '<li><strong>' + h(peer?.label || id) + '</strong>' + (edge.label ? '<small>' + h(edge.label) + '</small>' : '') + (edge.status ? flowBadge(edge.status, '上游 · ') : '') + '</li>';
  }).join('') + '</ul></div>';
}
function nodeCard(node, flow, index) {
  const executions = list(node.executions);
  const team = list(flow.teams).find(item => item.role === node.role);
  const detailKey = 'executions:' + String(node.id);
  return '<li class="flow-node" data-node-id="' + h(node.id) + '"><div class="flow-node-heading"><span class="flow-order">' + String(index + 1).padStart(2, '0') + '</span><div><h3>' + h(node.label || node.id) + '</h3><p>' + h(team?.label || roleLabel(node.role) || '工作流总控') + '</p></div>' + flowBadge(node.status) + '</div>' + handoffs(node, flow, 'incoming') + '<div class="flow-io-grid">' + ioPanel('输入', node.inputs, '尚未记录输入') + ioPanel('输出', node.outputs, '尚未记录输出产物') + '</div>' + (executions.length ? '<details class="flow-executions" data-flow-detail="' + h(detailKey) + '"><summary>执行记录 · ' + executions.length + ' 次 <span>查看实际模型与回执</span></summary>' + executions.map(executionCard).join('') + '</details>' : '<p class="flow-no-execution">尚无已保存的模型调用记录</p>') + handoffs(node, flow, 'outgoing') + '</li>';
}
export function teamFlowView(state) {
  const flow = currentFlow(state);
  const error = state.projectFlowError ? '<div class="notice error" role="alert"><div><strong>工作数据流同步中断</strong>' + h(state.projectFlowError) + (flow ? ' 当前显示上一次成功读取的记录。' : '') + '</div><button class="button small" data-action="refresh">重新读取</button></div>' : '';
  const header = '<div class="section-heading team-flow-heading"><div><h2>团队与数据流</h2><p class="muted tiny">从任务输入到产物交接，查看每位成员的实际执行记录。</p></div><button class="button small" data-action="models">调整角色模型 ↗</button></div>';
  if (!flow) return '<section class="team-flow">' + header + error + (!error ? '<div class="loading-panel"><div class="loader"></div><h3>正在读取工作数据流</h3></div>' : '') + '</section>';
  const evidence = flow.evidence || {};
  const bounded = evidence.truncated || evidence.unreadable_receipts ? '<div class="flow-evidence-note" role="status">' + h(evidence.truncated ? '当前仅显示部分历史记录。' : '') + h(evidence.unreadable_receipts ? ' ' + evidence.unreadable_receipts + ' 份回执暂时无法读取。' : '') + '</div>' : '';
  const nodes = list(flow.nodes);
  const completed = nodes.filter(node => node.status === 'succeeded').length;
  const running = nodes.filter(node => node.status === 'running').length;
  const waiting = nodes.filter(node => ['waiting', 'paused'].includes(node.status)).length;
  const failed = nodes.filter(node => node.status === 'failed').length;
  return '<section class="team-flow">' + header + error + bounded + teamCockpit(flow, { stale: !!state.projectFlowError || (Number.isFinite(state.project?.revision) && flow.revision < state.project.revision) }) + '<div class="flow-statusbar"><span class="badge neutral">' + h(flow.assignment_mode === 'orchestrator' ? '总控自动分工' : flow.assignment_mode || '分工方式未记录') + '</span><span>已完成 <b>' + completed + '</b></span><span>执行中 <b>' + running + '</b></span><span>等待 / 暂停 <b>' + waiting + '</b></span><span>失败 <b>' + failed + '</b></span>' + (flow.revision != null ? '<small>项目版本 ' + h(flow.revision) + '</small>' : '') + '</div><section class="team-roster" aria-label="当前团队模型分配">' + list(flow.teams).map(teamCard).join('') + '</section><div class="flow-section-label"><h3>任务与交接</h3><span>成员卡显示当前分配；回执保留历史实际模型，交接标注上游状态。</span></div>' + (nodes.length ? '<ol class="flow-node-list">' + nodes.map((node, index) => nodeCard(node, flow, index)).join('') + '</ol>' : '<section class="empty-state"><h3>尚无任务记录</h3><p>项目推进后，输入、输出与执行回执将在这里显示。</p></section>') + '</section>';
}