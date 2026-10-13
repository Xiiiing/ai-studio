import { api } from './api.js';
import { list, isRunning } from './format.js';
import { dashboard, projectView, renderSidebar, connectionError, capabilitiesDialog } from './views.js';
import { bindActions } from './actions.js';
import { bindModelActions, ingestInventory, modelsView } from './models.js';
import { ingestProjectFlow } from './team.js';

const state = { overview: null, projectId: '', project: null, view: 'workflow', tab: 'overview', busy: false, epoch: 0, models: null, modelsDraft: null, modelsDirty: false, modelTests: {}, workflow: null, projectFlow: null, projectFlowError: '' };
const workspace = document.getElementById('workspace');
const validTabs = ['overview', 'team', 'script', 'quality', 'delivery'];
let timer, toastTimer, generation = 0, lastPaint = '', lastError = '', lastCapabilities = '';
function toast(message, error = false) {
  const element = document.getElementById('toast');
  clearTimeout(toastTimer);
  element.textContent = message;
  element.className = 'toast' + (error ? ' error' : '');
  element.hidden = false;
  toastTimer = setTimeout(() => { element.hidden = true; }, error ? 10000 : 5500);
}
function paint(force = false) {
  const key = JSON.stringify({ view: state.view, id: state.projectId, tab: state.tab, project: state.project, overview: state.projectId || state.view === 'models' ? null : state.overview, models: state.models, draft: state.modelsDraft, dirty: state.modelsDirty, tests: state.modelTests, pending: state.modelTestPending, saving: state.modelsSaving, modelError: state.modelsError, saveError: state.modelSaveError, remoteChanged: state.modelsRemoteChanged, workflow: state.workflow, flow: state.projectFlow, flowError: state.projectFlowError });
  if (!force && key === lastPaint) return;
  const video = workspace.querySelector('video');
  if (!force && video && !video.paused && !video.ended) return;
  const preserve = new Map(Array.from(workspace.querySelectorAll('[data-preserve]')).map(element => [element.dataset.preserve, { value: element.value, checked: element.type === 'checkbox' ? element.checked : null }]));
  const active = workspace.contains(document.activeElement) ? document.activeElement : null;
  const focusKey = active?.dataset.preserve;
  const selection = focusKey ? [active.selectionStart, active.selectionEnd] : null;
  const details = new Map(Array.from(workspace.querySelectorAll('details[data-flow-detail]')).map(element => [element.dataset.flowDetail, element.open]));
  workspace.innerHTML = state.view === 'models' ? modelsView(state) : state.projectId ? projectView(state) : dashboard(state);
  for (const [key, value] of preserve) {
    const field = Array.from(workspace.querySelectorAll('[data-preserve]')).find(element => element.dataset.preserve === key);
    if (field) {
      field.value = value.value;
      if (value.checked !== null) field.checked = value.checked;
      if (focusKey === key) { field.focus({ preventScroll: true }); if (selection && Number.isInteger(selection[0])) field.setSelectionRange(...selection); }
    }
  }
  for (const detail of workspace.querySelectorAll('details[data-flow-detail]')) {
    if (details.has(detail.dataset.flowDetail)) detail.open = details.get(detail.dataset.flowDetail);
  }
  if (state.busy) workspace.querySelectorAll('[data-action="decide"],[data-action="project-action"],form button').forEach(button => { button.disabled = true; });
  lastPaint = key;
}
function updateHash() {
  const params = new URLSearchParams();
  if (state.view === 'models') params.set('view', 'models');
  else if (state.projectId) { params.set('project', state.projectId); if (state.tab !== 'overview') params.set('tab', state.tab); }
  history.replaceState(null, '', location.pathname + location.search + (params.size ? '#' + params : ''));
}
function schedule() {
  clearTimeout(timer);
  const active = state.project ? isRunning(state.project) : list(state.overview?.projects).some(isRunning);
  timer = setTimeout(() => { if (document.hidden || state.busy) schedule(); else refresh(); }, document.hidden ? 25000 : active ? 5000 : 15000);
}
async function refresh({ force = false, notify = false } = {}) {
  clearTimeout(timer);
  const request = ++generation, epoch = state.epoch, projectId = state.projectId;
  document.getElementById('sync-state').textContent = '正在同步';
  const results = await Promise.allSettled([api.overview(), projectId ? api.project(projectId) : Promise.resolve(null), api.models(), api.workflow(), projectId ? api.flow(projectId) : Promise.resolve(null)]);
  if (request !== generation || epoch !== state.epoch) { schedule(); return; }
  const [overviewResult, projectResult, modelsResult, workflowResult, flowResult] = results;
  let error = '';
  if (overviewResult.status === 'fulfilled') state.overview = overviewResult.value;
  else error = overviewResult.reason.message;
  if (projectId && projectId === state.projectId && projectResult.status === 'fulfilled') state.project = projectResult.value?.project || projectResult.value;
  if (projectId && projectResult.status === 'rejected') error = projectResult.reason.message;
  if (modelsResult.status === 'fulfilled') ingestInventory(state, modelsResult.value);
  else state.modelsError = modelsResult.reason.message;
  if (workflowResult.status === 'fulfilled') state.workflow = workflowResult.value;
  if (projectId && flowResult.status === 'fulfilled') ingestProjectFlow(state, flowResult.value, projectId);
  if (projectId && flowResult.status === 'rejected') state.projectFlowError = flowResult.reason.message;
  if (state.overview || state.view === 'models') {
    renderSidebar(state);
    if (state.view === 'models' || !projectId || state.project) paint(force);
    else if (error) workspace.innerHTML = connectionError(error);
    const capabilities = document.getElementById('capabilities-dialog');
    if (capabilities.open) {
      const content = capabilitiesDialog(state);
      if (content !== lastCapabilities) {
        const hadFocus = capabilities.contains(document.activeElement), scroll = capabilities.scrollTop;
        capabilities.innerHTML = content; capabilities.scrollTop = scroll;
        if (hadFocus) capabilities.querySelector('[data-action="close-dialog"]')?.focus({ preventScroll: true });
        lastCapabilities = content;
      }
    } else lastCapabilities = '';
  } else if (error) workspace.innerHTML = connectionError(error);
  if (error) {
    document.getElementById('sync-state').textContent = '同步中断';
    if (error !== lastError || notify) toast(error, true);
    lastError = error;
  } else {
    document.getElementById('sync-state').textContent = '已同步 · ' + new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date());
    lastError = '';
    if (notify) toast(state.modelsDirty ? '项目状态已更新；未保存的模型草稿仍保留。' : '状态已更新。');
  }
  schedule();
}
async function selectProject(id, project = null) {
  state.epoch += 1; state.view = 'workflow'; state.projectId = id || ''; state.project = project; state.projectFlow = null; state.projectFlowError = ''; state.tab = 'overview';
  lastPaint = ''; updateHash(); workspace.innerHTML = '';
  if (state.overview) { renderSidebar(state); paint(true); }
  await refresh({ force: true });
  workspace.focus({ preventScroll: true }); window.scrollTo({ top: 0, behavior: 'instant' });
}
async function selectView(view) {
  if (view !== 'models') return selectProject('');
  state.epoch += 1; state.view = 'models'; state.projectId = ''; state.project = null; state.projectFlow = null; state.projectFlowError = ''; state.tab = 'overview';
  updateHash(); renderSidebar(state); paint(true);
  await refresh({ force: true });
  workspace.focus({ preventScroll: true }); window.scrollTo({ top: 0, behavior: 'instant' });
}
function selectTab(tab) {
  if (!validTabs.includes(tab)) return;
  state.tab = tab; updateHash(); paint(true);
}
const ui = { refresh, selectProject, selectView, selectTab, toast, paint };
bindActions(state, ui);
bindModelActions(state, ui);
function readLocation() {
  const params = new URLSearchParams(location.hash.slice(1));
  state.view = params.get('view') === 'models' ? 'models' : 'workflow';
  const id = params.get('project') || '';
  state.projectId = state.view !== 'models' && /^[a-f0-9]{12}$/.test(id) ? id : '';
  state.project = null; state.projectFlow = null; state.projectFlowError = '';
  state.tab = validTabs.includes(params.get('tab')) ? params.get('tab') : 'overview';
}
window.addEventListener('hashchange', async () => {
  if (location.hash === '#workspace') return;
  state.epoch += 1; readLocation(); workspace.innerHTML = '';
  await refresh({ force: true });
});
document.addEventListener('visibilitychange', () => { if (!document.hidden && !state.busy) refresh(); });
readLocation();
refresh();

