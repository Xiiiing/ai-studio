import { applyTeamMode } from './team-cockpit.js';
import { cloudAuthorizationPayload } from './cloud-budget.js';
import { applyProductionProfile } from './production-profile.js';
import { api } from './api.js';
import { h, list, numeric, safeUrl, pendingDecisions, canReviseProduction } from './format.js';
import { createForm, capabilitiesDialog, budgetForm } from './views.js';
function requestId() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = bytes[6] & 15 | 64; bytes[8] = bytes[8] & 63 | 128;
  return Array.from(bytes, (byte, i) => ([4, 6, 8, 10].includes(i) ? '-' : '') + byte.toString(16).padStart(2, '0')).join('');
}
export function bindActions(state, ui) {
  let createDraft = null;
  const dialog = id => document.getElementById(id);
  const open = (id, content) => { const element = dialog(id); element.innerHTML = content; if (!element.open) element.showModal(); };
  const setBusy = busy => {
    state.busy = busy;
    if (busy) state.epoch += 1;
    document.querySelectorAll('[data-action="decide"],[data-action="project-action"],[data-action="budget"],form button[type="submit"],form button:not([type])').forEach(button => { button.disabled = busy || button.dataset.locked === 'true'; });
  };
  async function mutate(work, message) {
    if (state.busy) return;
    setBusy(true);
    try { await work(); ui.toast(message); }
    catch (error) { ui.toast(error.message, true); }
    finally { setBusy(false); await ui.refresh({ force: true }); }
  }
  function openCreate() {
    if (state.busy) return;
    open('project-dialog', createForm(state.productionProfiles, state.models));
    const form = document.getElementById('create-form');
    if (createDraft) {
      form.dataset.requestId = createDraft.requestId;
      for (const [name, value] of Object.entries(createDraft.values)) {
        const field = form.elements.namedItem(name);
        if (field) field.value = value;
      }
    } else form.dataset.requestId = requestId();
    document.getElementById('project-brief').focus();
  }
  function openBudget() {
    open('project-dialog', budgetForm(state.project));
  }
  function preview(id) {
    const shot = list(state.project?.shots).find(item => String(item.id) === id);
    if (!shot) return;
    const video = safeUrl(shot.video_url);
    const image = safeUrl(shot.image_url);
    if (!video && !image) { ui.toast('这个镜头暂时没有可预览素材。'); return; }
    open('preview-dialog', '<div class="preview-header"><span>' + h(shot.id) + ' · 镜头预览</span><button class="icon-button" data-action="close-dialog" data-dialog="preview-dialog" aria-label="关闭">×</button></div>' + (video ? '<video class="preview-media" controls autoplay playsinline src="' + h(video) + '"></video>' : '<img class="preview-media" src="' + h(image) + '" alt="' + h(shot.description || shot.id) + '">'));
  }
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-action]');
    if (!button || button.disabled) return;
    const action = button.dataset.action;
    if (action === 'new-project') openCreate();
    if (action === 'close-dialog') dialog(button.dataset.dialog)?.close();
    if (action === 'dashboard') await ui.selectProject('');
    if (action === 'models') await ui.selectView('models');
    if (action === 'select-project') await ui.selectProject(button.dataset.id);
    if (action === 'refresh') await ui.refresh({ force: true, notify: true });
    if (action === 'tab') ui.selectTab(button.dataset.tab);
    if (action === 'capabilities') open('capabilities-dialog', capabilitiesDialog(state));
    if (action === 'preview') preview(button.dataset.shot);
    if (action === 'budget') openBudget();
    if (action === 'decide') {
      const displayedId = button.closest('[data-decision-id]')?.dataset.decisionId;
      const decision = pendingDecisions(state.project).find(item => item.type === button.dataset.type && item.id === displayedId);
      if (!decision) { ui.toast('该决策状态已变化，请刷新后再选择。', true); await ui.refresh({ force: true }); return; }
      await mutate(() => api.decide(state.projectId, { type: button.dataset.type, choice: button.dataset.choice, decision_id: decision.id }), '选择已提交，制作团队继续推进。');
    }
    if (action === 'project-action') await mutate(() => api.action(state.projectId, { action: button.dataset.command }), { pause: '暂停请求已提交，正在执行的安全步骤会收尾。', resume: '项目已恢复。', retry: '重试请求已提交。' }[button.dataset.command] || '操作已提交。');
  });
  document.addEventListener('submit', async event => {
    const form = event.target.closest('form[data-form]');
    if (!form) return;
    event.preventDefault();
    if (state.busy || !form.reportValidity()) return;
    const values = Object.fromEntries(new FormData(form));
    if (form.dataset.form === 'cloud-authorize') {
      try {
        const payload = cloudAuthorizationPayload(form, values);
        await mutate(() => api.cloudAuthorize(state.projectId, payload), '批次授权已记录，累计用量继续保留。');
      } catch (error) { ui.toast(error.message, true); }
      return;
    }
    if (form.dataset.form === 'create') {
      const errorBox = document.getElementById('create-error');
      errorBox.hidden = true;
      const sources = String(values.sources || '').split(/\r?\n/).map(url => url.trim()).filter(Boolean);
      if (sources.some(url => !/^https?:\/\//i.test(url) || !safeUrl(url))) { errorBox.textContent = '资料来源请填写完整的 http:// 或 https:// 链接，每行一条。'; errorBox.hidden = false; return; }
      if (sources.length > 12) { errorBox.textContent = '每个项目最多添加 12 条素材来源。'; errorBox.hidden = false; return; }
      const payload = { request_id: form.dataset.requestId || requestId(), title: String(values.title || '').trim(), brief: String(values.brief || '').trim(), format: values.format, duration_seconds: Number(values.duration_seconds), budget: { gpu_minutes: Number(values.gpu_minutes), wall_minutes: Number(values.wall_minutes), max_attempts: Number(values.max_attempts) }, sources: sources.map(url => ({ url, title: new URL(url).hostname, rights: values.rights })) };
      try { applyTeamMode(payload, values, state.models); applyProductionProfile(payload, values, state.productionProfiles); }
      catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; return; }
      if (String(values.series_key || '').trim()) payload.series_key = String(values.series_key).trim();
      createDraft = { requestId: payload.request_id, values };
      setBusy(true);
      try {
        const result = await api.create(payload);
        const project = result.project || result;
        if (!project.id) throw new Error('服务未返回项目编号，请刷新查看是否已建立项目。');
        createDraft = null;
        dialog('project-dialog').close();
        ui.toast('制作项目已建立，开始自主策划。');
        setBusy(false);
        await ui.selectProject(project.id, project);
      } catch (error) {
        errorBox.textContent = error.message; errorBox.hidden = false;
        ui.toast(error.message, true);
      } finally { setBusy(false); }
      return;
    }
    const feedback = String(values.feedback || '').trim();
    if (form.dataset.form === 'revise-production') {
      if (!canReviseProduction(state.project) || form.dataset.projectId !== state.projectId) { ui.toast('项目已离开可局部返修的阻断状态，请刷新后查看。', true); await ui.refresh({ force: true }); return; }
      const shotIds = [...new Set(new FormData(form).getAll('shot_ids').map(String))];
      const known = new Set(list(state.project.shots).map(shot => String(shot.id)));
      if (!shotIds.length || shotIds.some(id => !known.has(id))) { ui.toast('请至少选择一个当前项目中的镜头。', true); form.querySelector('input[name="shot_ids"]')?.focus(); return; }
      if (!feedback) { ui.toast('请填写所选镜头的具体修改说明。', true); form.elements.feedback.focus(); return; }
      const revision = Number(form.dataset.projectRevision);
      if (!Number.isInteger(revision) || revision < 1) { ui.toast('项目版本尚未同步，请刷新后提交。', true); await ui.refresh({ force: true }); return; }
      await mutate(() => api.action(state.projectId, { action: 'revise', expected_revision: revision, shot_ids: shotIds, feedback }), '局部返修已提交，系统继续评审与制作。');
      return;
    }
    if (form.dataset.form === 'final') {
      const choice = event.submitter?.value || 'approve';
      if (['revise', 'reedit'].includes(choice) && !feedback) { ui.toast('请填写具体审片意见，再提交修改或重剪。', true); form.elements.feedback.focus(); return; }
      const displayedId = form.closest('[data-decision-id]')?.dataset.decisionId;
      const decision = pendingDecisions(state.project).find(item => item.type === 'final' && item.id === displayedId);
      if (!decision) { ui.toast('当前审片决策已变化，请刷新。', true); await ui.refresh({ force: true }); return; }
      await mutate(() => api.decide(state.projectId, { type: 'final', choice, feedback, decision_id: decision.id }), choice === 'approve' ? '成片已通过，正在更新交付状态。' : '审片意见已提交，系统继续处理。');
    }
    if (form.dataset.form === 'repropose') {
      const displayedId = form.closest('[data-decision-id]')?.dataset.decisionId;
      const decision = pendingDecisions(state.project).find(item => item.type === 'story' && item.id === displayedId);
      if (!decision) { ui.toast('故事决策已变化，请刷新。', true); await ui.refresh({ force: true }); return; }
      await mutate(() => api.decide(state.projectId, { type: 'story', choice: '__repropose__', feedback, decision_id: decision.id }), '已提交重新提案。');
    }
    if (form.dataset.form === 'retry') await mutate(() => api.action(state.projectId, { action: 'retry', feedback }), '调整意见已记录，正在重试。');
    if (form.dataset.form === 'budget') {
      const payload = { action: 'resume' };
      for (const key of ['gpu_minutes', 'wall_minutes']) if (Number(values[key]) > numeric(state.project?.budget?.[key])) payload[key] = Number(values[key]);
      await mutate(async () => { await api.action(state.projectId, payload); dialog('project-dialog').close(); }, '预算已更新，项目继续制作。');
    }
  });
  dialog('project-dialog').addEventListener('close', () => {
    const form = document.getElementById('create-form');
    if (form && createDraft) createDraft.values = Object.fromEntries(new FormData(form));
  });
  dialog('preview-dialog').addEventListener('close', () => { dialog('preview-dialog').innerHTML = ''; });
}
