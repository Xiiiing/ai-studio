import { h, list, dateText } from './format.js';
import { api } from './api.js';
import { compactModel, roleLabel } from './workflow.js';

const clone = value => JSON.parse(JSON.stringify(value));
const NETWORKS = { loopback: '本机运行', lan: '局域网接口', remote: '远程 API' };
const PROVIDERS = { llama_cpp: 'llama.cpp', ollama: 'Ollama', vllm: 'vLLM', openai_compatible: 'OpenAI 兼容接口' };
export function ingestInventory(state, inventory, reset = false) {
  if (!reset && state.modelsDirty && state.models?.revision !== inventory.revision) {
    state.modelsRemoteChanged = true;
    return;
  }
  const changed = state.models?.revision !== inventory.revision;
  state.models = inventory;
  if (!state.modelsDirty || reset) {
    state.modelsDraft = { profiles: clone(inventory.editable_profiles || {}), roles: clone(inventory.role_assignments || {}) };
    state.modelsDirty = false;
    state.modelsRemoteChanged = false;
  }
  if (changed || reset) state.modelTests = {};
  state.modelsError = '';
}
export function registryPayload(state) {
  if (!state.models?.revision || !state.modelsDraft) throw new Error('请先读取当前模型配置。');
  return { profiles: clone(state.modelsDraft.profiles), roles: clone(state.modelsDraft.roles), expected_revision: state.models.revision };
}
const notice = (text, error = false) => '<div class="notice ' + (error ? 'error' : '') + '" role="' + (error ? 'alert' : 'status') + '"><div>' + h(text) + '</div></div>';
function profileCard(id, profile, state) {
  const observed = list(state.models?.profiles).find(item => item.id === id) || {};
  const test = state.modelTests?.[id];
  const pending = state.modelTestPending === id;
  const roles = Object.entries(state.modelsDraft?.roles || {}).filter(([, value]) => value === id).map(([role]) => roleLabel(role));
  const status = profile.enabled === false ? '已停用' : pending ? '检测中' : state.modelsDirty ? '待保存 / 未测试' : test?.status === 'ready' ? '连接通过' : test ? '连接未通过' : '尚未测试';
  const statusClass = profile.enabled === false ? 'neutral' : !state.modelsDirty && test?.status === 'ready' ? 'success' : !state.modelsDirty && test ? 'danger' : 'neutral';
  const auth = profile.api_key_env ? (observed.api_key_env === profile.api_key_env && observed.auth_configured ? '环境变量已配置' : '环境变量待配置或待同步') : '未设置鉴权变量';
  return '<article class="model-card"><div class="model-card-heading"><div class="model-symbol" aria-hidden="true">' + (profile.network_mode === 'remote' ? '↗' : '◉') + '</div><div><h3>' + h(id) + '</h3><p>' + h(PROVIDERS[profile.provider] || profile.provider || '兼容接口') + ' · ' + h(NETWORKS[profile.network_mode] || profile.network_mode || '本机运行') + '</p></div><span class="badge ' + statusClass + '">' + h(status) + '</span></div><strong class="model-name" title="' + h(profile.model) + '">' + h(compactModel(profile.model)) + '</strong><code class="endpoint">' + h(profile.base_url || '尚未填写地址') + '</code><div class="model-flags"><span>文本</span>' + (profile.vision ? '<span>图像输入</span>' : '') + '<span>' + h(profile.context_tokens || '—') + ' 上下文</span></div><div class="model-assigned">' + (roles.length ? roles.map(role => '<span>' + h(role) + '</span>').join('') : '<span class="muted">尚未分配角色</span>') + '</div><div class="model-auth"><span>' + h(auth) + '</span>' + (profile.api_key_env ? '<code>' + h(profile.api_key_env) + '</code>' : '') + '</div>' + (test && !state.modelsDirty ? '<div class="probe-result ' + (test.status === 'ready' ? '' : 'error') + '" role="status">' + h(test.status === 'ready' ? 'GET /models 成功，已匹配配置模型。' : test.error?.message || '服务未返回匹配的模型。') + '<small>' + h(test.elapsed_ms ?? '—') + ' ms · ' + h(dateText(test.checked_at, true)) + ' · 未发起生成</small></div>' : '') + '<div class="model-card-actions"><button class="button small" data-model-action="edit" data-profile="' + h(id) + '">编辑连接</button><button class="button small" data-model-action="test" data-profile="' + h(id) + '" ' + (profile.enabled === false || state.modelsDirty || state.modelTestPending || state.modelsSaving ? 'disabled' : '') + ' title="' + (state.modelsDirty ? '请先保存配置，再测试已保存的连接' : '只请求服务模型列表，不生成内容') + '">' + (pending ? '正在测试…' : '测试连接') + '</button></div></article>';
}
export function modelsView(state) {
  const heading = '<section class="page-heading"><div><div class="eyebrow">MODELS & CONNECTIONS</div><h1>模型与连接</h1><p>模型在各自的服务中运行，工作流通过统一接口调用。</p></div><button class="button primary" data-model-action="add" ' + (!state.models ? 'disabled' : '') + '>＋ 添加连接</button></section>';
  if (!state.models) return heading + (state.modelsError ? notice(state.modelsError, true) + '<button class="button" data-action="refresh">重新读取</button>' : '<section class="loading-panel"><div class="loader"></div><h2>正在读取模型配置</h2></section>');
  const profiles = Object.entries(state.modelsDraft?.profiles || {});
  const roles = state.modelsDraft?.roles || {};
  const roleRows = list(state.models.roles);
  const assignments = Object.entries(roles).map(([role, selected]) => {
    const meta = roleRows.find(item => (item.role || item.id) === role) || {};
    return '<div class="role-assignment"><label for="role-' + h(role) + '"><strong>' + h(meta.label || roleLabel(role)) + '</strong><code>' + h(role) + '</code></label><select id="role-' + h(role) + '" data-role-assignment="' + h(role) + '" ' + (state.modelsSaving ? 'disabled' : '') + '>' + profiles.map(([id, profile]) => '<option value="' + h(id) + '" ' + (selected === id ? 'selected' : '') + (profile.enabled === false || (meta.requires_vision && !profile.vision) ? ' disabled' : '') + '>' + h(id + ' · ' + compactModel(profile.model) + (profile.enabled === false ? '（已停用）' : meta.requires_vision && !profile.vision ? '（不支持图像输入）' : '')) + '</option>').join('') + '</select></div>';
  }).join('');
  return heading + (state.modelsError ? notice(state.modelsError, true) : '') + (state.modelSaveError ? notice(state.modelSaveError, true) : '') + (state.modelsRemoteChanged ? notice('其他终端已更新配置。当前草稿仍保留；请先记下修改，再重新载入，避免覆盖他人的配置。', true) : '') +
    '<div class="connection-explainer"><div><span class="explain-index">01</span><strong>运行模型服务</strong><p>llama.cpp、Ollama 或 vLLM 加载本地权重，也可连接已获授权的远程服务。</p></div><div><span class="explain-index">02</span><strong>登记接口与模型</strong><p>设置地址、模型名和能力；密钥只从服务器环境变量读取。</p></div><div><span class="explain-index">03</span><strong>分配工作流角色</strong><p>编剧、审核、导演可使用不同模型。保存后，当前执行批次结束，下一次执行使用新配置。</p></div></div>' +
    '<div class="section-heading"><h2>模型连接 <span class="inline-count">' + profiles.length + '</span></h2><span>连接测试与生成质量验收分别记录</span></div><div class="model-grid">' + (profiles.length ? profiles.map(([id, profile]) => profileCard(id, profile, state)).join('') : '<section class="empty-state"><h3>还没有模型连接</h3><p>先添加一个已运行的模型服务，再为角色分配模型。</p></section>') + '</div>' +
    '<section class="panel roles-panel"><div class="panel-title"><div><h2>角色分工</h2><p class="muted tiny">这里选择具体连接。视觉质检需要支持图像输入的模型。</p></div><span class="badge neutral">' + Object.keys(roles).length + ' 个角色</span></div><div class="role-assignment-grid">' + (assignments || '<p class="muted">服务暂未返回可分配的角色。</p>') + '</div></section>' +
    '<div class="config-savebar" aria-live="polite"><div><strong>' + (state.modelsSaving ? '正在保存…' : state.modelsDirty ? '有尚未保存的配置修改' : '当前配置已同步') + '</strong><p>' + (state.modelsDirty ? '修改只保存在页面草稿中，保存后供下一次执行批次使用。' : '不会自动测试接口或运行模型；点击测试连接可检查服务。') + '</p></div><div><button class="button small" data-model-action="reload" ' + (state.modelsSaving ? 'disabled' : '') + '>' + (state.modelsDirty ? '丢弃草稿并重新载入' : '重新载入') + '</button><button class="button primary" data-model-action="save" ' + (!state.modelsDirty || state.modelsSaving ? 'disabled' : '') + '>保存配置</button></div></div>' +
    '<details class="runtime-note"><summary>图像、视频和配音如何调用？</summary><p>创作角色统一使用模型接口。图像和视频由现有 GPU 调度器提交给 ComfyUI，配音通过本地音频适配器执行，FFmpeg 负责剪辑。它们的实际可用情况在“制作能力与资源”中查看。</p><button class="button small" data-action="capabilities">查看制作能力</button></details>';
}
export function modelEditor(id, profile = {}) {
  const fresh = !id;
  const value = key => h(profile[key] ?? '');
  const option = (key, items, fallback) => Object.entries(items).map(([item, label]) => '<option value="' + h(item) + '" ' + ((profile[key] || fallback) === item ? 'selected' : '') + '>' + h(label) + '</option>').join('');
  return '<div class="dialog-head"><div><div class="eyebrow">MODEL CONNECTION</div><h2 id="model-editor-title">' + (fresh ? '添加模型连接' : '编辑模型连接') + '</h2><p>登记已经运行的服务；此表单不会下载或启动模型。</p></div><button type="button" class="icon-button" data-action="close-dialog" data-dialog="model-dialog" aria-label="关闭">×</button></div><form data-model-form="profile" data-profile="' + h(id || '') + '" class="dialog-body"><div class="form-row"><div class="field"><label for="model-id">连接编号</label><input id="model-id" name="id" value="' + h(id || '') + '" placeholder="writer_local" pattern="[A-Za-z0-9][A-Za-z0-9_.-]{0,63}" maxlength="64" required ' + (fresh ? '' : 'readonly') + '><small>用字母、数字、短横线或下划线命名。</small></div><div class="field"><label for="model-provider">服务类型</label><select id="model-provider" name="provider">' + option('provider', PROVIDERS, 'openai_compatible') + '</select></div></div><div class="field"><label for="model-endpoint">接口地址</label><input id="model-endpoint" name="base_url" type="url" value="' + value('base_url') + '" placeholder="http://127.0.0.1:8090/v1" maxlength="2048" required><small>填写 OpenAI 兼容接口的基础地址，通常包含 /v1。</small></div><div class="field"><label for="model-name">模型标识</label><input id="model-name" name="model" value="' + value('model') + '" placeholder="与服务 /models 返回的 id 一致" maxlength="512" required></div><div class="form-row"><div class="field"><label for="model-network">连接范围</label><select id="model-network" name="network_mode">' + option('network_mode', NETWORKS, 'loopback') + '</select></div><div class="field"><label for="model-context">上下文 Token 上限</label><input id="model-context" name="context_tokens" type="number" min="1024" max="2000000" step="1" value="' + h(profile.context_tokens || 32768) + '" required></div></div><div class="field"><label for="model-hosts">允许访问的主机名</label><input id="model-hosts" name="allowed_hosts" value="' + h(list(profile.allowed_hosts).join(', ')) + '" placeholder="局域网或远程连接必填，用逗号分隔"><small>精确填写接口地址中的 hostname；远程接口需要 HTTPS。</small></div><div class="field"><label for="model-key-env">密钥环境变量名称 <span class="muted">/ 可选</span></label><input id="model-key-env" name="api_key_env" value="' + value('api_key_env') + '" pattern="[A-Za-z_][A-Za-z0-9_]*" maxlength="128" placeholder="STUDIO_API_KEY" autocomplete="off" spellcheck="false"><small>只填写变量名称。实际密钥保存在服务器环境中，不在网页内填写。</small></div><div class="form-row"><div class="field"><label for="model-timeout">请求超时 / 秒</label><input id="model-timeout" name="request_timeout_seconds" type="number" min="1" max="600" step="1" value="' + h(profile.request_timeout_seconds || 120) + '" required></div><div class="field"><label for="model-response">结构化输出</label><select id="model-response" name="response_format">' + option('response_format', { llama_json_schema: 'llama.cpp JSON Schema', openai_json_schema: '标准 JSON Schema', json_object: 'JSON 对象', prompt: '提示词约束 + 本地校验' }, 'json_object') + '</select></div></div><div class="model-checks"><label><input name="vision" type="checkbox" ' + (profile.vision ? 'checked' : '') + '>支持图像输入</label><label><input name="enabled" type="checkbox" ' + (profile.enabled !== false ? 'checked' : '') + '>启用此连接</label></div><p class="form-note">现有连接的高级参数会保留。先加入页面草稿，再点击“保存配置”生效；编辑连接不会自动重做已完成的镜头。</p><div id="model-form-error" class="form-error" role="alert" hidden></div><div class="dialog-actions"><button type="button" class="button" data-action="close-dialog" data-dialog="model-dialog">取消</button><button type="submit" class="button primary">加入待保存修改</button></div></form>';
}
export function profileFromForm(values, previous = {}) {
  return { ...clone(previous), context_validation: previous.provider === values.provider && previous.context_validation ? previous.context_validation : values.provider === 'llama_cpp' ? 'llama_tokenize' : 'conservative_estimate', provider: String(values.provider), enabled: values.enabled === 'on', network_mode: String(values.network_mode), allowed_hosts: String(values.allowed_hosts || '').split(',').map(x => x.trim()).filter(Boolean), base_url: String(values.base_url || '').trim(), model: String(values.model || '').trim(), context_tokens: Number(values.context_tokens), vision: values.vision === 'on', api_key_env: String(values.api_key_env || '').trim(), response_format: String(values.response_format), request_timeout_seconds: Number(values.request_timeout_seconds) };
}
export function bindModelActions(state, ui) {
  const dialog = document.getElementById('model-dialog');
  function markDirty() { state.modelsDirty = true; state.modelsError = ''; state.modelSaveError = ''; state.modelTests = {}; ui.paint(true); }
  document.addEventListener('change', event => {
    if (event.target.id === 'model-provider') {
      const formats = { llama_cpp: 'llama_json_schema', ollama: 'openai_json_schema', vllm: 'openai_json_schema', openai_compatible: 'json_object' };
      event.target.form.elements.response_format.value = formats[event.target.value] || 'json_object';
    }
    const select = event.target.closest('[data-role-assignment]');
    if (!select || !state.modelsDraft || state.modelsSaving) return;
    const role = select.dataset.roleAssignment;
    if (!(role in state.modelsDraft.roles)) return;
    state.modelsDraft.roles[role] = select.value;
    markDirty();
    document.getElementById('role-' + role)?.focus({ preventScroll: true });
  });
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-model-action]');
    if (!button || button.disabled || state.modelsSaving) return;
    const action = button.dataset.modelAction, id = button.dataset.profile;
    if (action === 'edit' || action === 'add') {
      dialog.innerHTML = modelEditor(action === 'edit' ? id : '', state.modelsDraft?.profiles?.[id] || {});
      if (!dialog.open) dialog.showModal();
      dialog.querySelector(action === 'edit' ? '#model-endpoint' : '#model-id')?.focus();
    }
    if (action === 'test') {
      if (state.modelsDirty || state.modelTestPending) return;
      const revision = state.models.revision;
      state.modelTestPending = id; ui.paint(true);
      try {
        const result = await api.testModel(id);
        if (!state.modelsDirty && state.models.revision === revision) {
          state.modelTests ||= {};
          state.modelTests[id] = { ...result, checked_at: new Date().toISOString() };
        }
      } catch (error) { ui.toast(error.message, true); }
      finally { state.modelTestPending = ''; ui.paint(true); }
    }
    if (action === 'reload') {
      try { ingestInventory(state, await api.models(), true); state.modelSaveError = ''; ui.paint(true); ui.toast('已读取服务器配置。'); }
      catch (error) { state.modelsError = error.message; ui.paint(true); }
    }
    if (action === 'save') {
      if (!state.modelsDirty || state.busy) return;
      state.modelsSaving = true; state.busy = true; state.epoch += 1; ui.paint(true);
      try {
        const result = await api.saveModels(registryPayload(state));
        ingestInventory(state, result, true); state.modelSaveError = '';
        ui.toast('配置已保存；当前执行批次结束后，下一次执行使用新的角色分配。');
      } catch (error) { state.modelSaveError = error.message; ui.toast(error.message, true); }
      finally { state.modelsSaving = false; state.busy = false; ui.paint(true); await ui.refresh(); }
    }
  });
  document.addEventListener('submit', event => {
    const form = event.target.closest('form[data-model-form="profile"]');
    if (!form) return;
    event.preventDefault();
    if (state.modelsSaving || !form.reportValidity()) return;
    const values = Object.fromEntries(new FormData(form)), id = String(values.id || '').trim();
    const error = document.getElementById('model-form-error');
    if (!form.dataset.profile && Object.hasOwn(state.modelsDraft.profiles, id)) { error.textContent = '此连接编号已存在，请使用其他编号。'; error.hidden = false; return; }
    state.modelsDraft.profiles[id] = profileFromForm(values, state.modelsDraft.profiles[id]);
    dialog.close(); markDirty(); ui.toast('已更新页面草稿，保存配置后生效。');
  });
  window.addEventListener('beforeunload', event => { if (state.modelsDirty) { event.preventDefault(); event.returnValue = ''; } });
}
