import { api } from './api.js';

export function shotPayload(form, values) {
  const revision = Number(form.dataset.revision);
  if (!Number.isSafeInteger(revision) || revision < 1) throw new Error('项目版本无效，请刷新。');
  const base = { action: form.dataset.shotOperation, expected_revision: revision };
  if (base.action === 'select') {
    if (values.confirmed !== 'on') throw new Error('选片必须明确确认。');
    return { ...base, stage: form.dataset.stage, take: form.dataset.take, confirmed: true };
  }
  if (base.action !== 'edit') throw new Error('不支持的镜头操作。');
  return { ...base, changes: { prompt: String(values.prompt || '').trim(), motion_prompt: String(values.motion_prompt || '').trim(), camera_motion: values.camera_motion },
    regenerate: values.regenerate === 'on', stage: values.stage || 'image' };
}
export function bindShotActions(state, ui) {
  state.shotDrafts ||= {};
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-shot-open],[data-shot-discard]');
    if (!button || state.busy) return;
    if (button.dataset.shotOpen) state.shotId = button.dataset.shotOpen;
    else {
      delete state.shotDrafts[button.dataset.shotDiscard];
      for (const input of document.querySelectorAll('[data-preserve]')) {
        if (input.dataset.preserve.startsWith('shot:' + button.dataset.shotDiscard + ':')) input.removeAttribute('data-preserve');
      }
    }
    ui.paint(true);
  });
  document.addEventListener('input', event => {
    const form = event.target.closest('form[data-shot-operation]');
    if (!form) return;
    const key = form.dataset.project + ':' + form.dataset.shot + (form.dataset.shotOperation === 'select' ? ':' + form.dataset.stage + ':' + form.dataset.take : '');
    state.shotDrafts[key] = { revision: Number(form.dataset.revision), values: Object.fromEntries(new FormData(form)) };
  });
  document.addEventListener('submit', async event => {
    const form = event.target.closest('form[data-shot-operation]');
    if (!form) return;
    event.preventDefault();
    if (state.busy || !form.reportValidity()) return;
    const projectId = state.projectId, shotId = form.dataset.shot;
    try {
      if (form.dataset.project !== projectId) throw new Error('项目已切换，请重新打开镜头。');
      const payload = shotPayload(form, Object.fromEntries(new FormData(form)));
      state.busy = true; state.epoch += 1;
      form.querySelectorAll('button').forEach(button => { button.disabled = true; });
      await api.shotAction(projectId, shotId, payload);
      delete state.shotDrafts[projectId + ':' + shotId + (payload.action === 'select' ? ':' + payload.stage + ':' + payload.take : '')];
      ui.toast(payload.action === 'select' ? '选片已记录，旧素材保留。恢复制作后更新成片。' : '局部版本已保存。恢复制作后处理变更镜头。');
    } catch (error) { ui.toast(error.message, true); }
    finally { state.busy = false; await ui.refresh({ force: true }); }
  });
}
