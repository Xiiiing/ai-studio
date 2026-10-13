import { api } from './api.js';

export function seriesPayload(form, values) {
  const number = (value, label, minimum = 0) => {
    const result = Number(value);
    if (!Number.isSafeInteger(result) || result < minimum) throw new Error(label + ' 无效，请刷新。');
    return result;
  };
  const action = form.dataset.seriesOperation;
  const payload = { action, expected_revision: number(form.dataset.revision, '项目版本', 1) };
  if (action === 'save_bible') return { ...payload, expected_version: number(form.dataset.expectedVersion, '设定版本'), document: JSON.parse(values.document) };
  if (action === 'plan') {
    const [kind, entity_id] = String(values.entity || '').split(':');
    if (!['character', 'scene', 'prop'].includes(kind) || !entity_id) throw new Error('请选择系列实体。');
    return { ...payload, kind, entity_id, expected_version: number(form.dataset.expectedVersion, '资产版本'), ...(form.dataset.asset ? { asset_id: form.dataset.asset } : {}) };
  }
  Object.assign(payload, { asset_id: form.dataset.asset, version: number(form.dataset.version, '资产版本', 1) });
  if (action === 'bind') {
    if (!Array.isArray(values.shot_ids) || !values.shot_ids.length) throw new Error('请至少明确选择一个镜头。');
    return { ...payload, role: values.role, shot_ids: [...new Set(values.shot_ids)] };
  }
  payload.expected_asset_revision = number(form.dataset.assetRevision, '资产修订', 1);
  if (action === 'register') payload.source_key = values.source_key;
  else if (action === 'approve') {
    if (values.confirmed !== 'on') throw new Error('正式复用必须明确确认。');
    payload.confirmed = true;
  } else if (action !== 'review') throw new Error('不支持的系列操作。');
  return payload;
}

export function bindSeriesActions(state, ui) {
  state.seriesDrafts ||= {};
  const valuesFor = form => {
    const data = new FormData(form);
    return { ...Object.fromEntries(data), shot_ids: data.getAll('shot_ids').map(String) };
  };
  document.addEventListener('input', event => {
    const form = event.target.closest('form[data-series-operation]');
    if (!form) return;
    state.seriesDrafts[form.dataset.seriesKey] = {
      expected_revision: Number(form.dataset.revision), expected_asset_revision: Number(form.dataset.assetRevision),
      expected_version: Number(form.dataset.expectedVersion), values: valuesFor(form)
    };
  });
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-series-discard]');
    if (!button || state.busy) return;
    delete state.seriesDrafts[button.dataset.seriesDiscard];
    await ui.refresh({ force: true });
  });
  document.addEventListener('submit', async event => {
    const form = event.target.closest('form[data-series-operation]');
    if (!form) return;
    event.preventDefault();
    if (state.busy || !form.reportValidity()) return;
    try {
      const payload = seriesPayload(form, valuesFor(form));
      const projectId = state.projectId;
      if (!form.dataset.seriesKey.startsWith(projectId + ':')) throw new Error('项目已切换，请重新查看素材。');
      state.busy = true; state.epoch += 1;
      form.querySelectorAll('button').forEach(button => { button.disabled = true; });
      await api.seriesAction(projectId, payload);
      delete state.seriesDrafts[form.dataset.seriesKey];
      ui.toast(payload.action === 'bind' ? '固定引用已保存。旧素材保留，恢复制作后只处理变更依赖。' : '系列资产操作已记录。');
    } catch (error) { ui.toast(error.message, true); }
    finally { state.busy = false; await ui.refresh({ force: true }); }
  });
}
