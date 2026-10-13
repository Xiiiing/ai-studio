import { h, list, dateText } from './format.js';
import { compactModel } from './workflow.js';

export const VIDEO_DEFAULTS = {
  comfyui_wan: { provider: 'comfyui_wan', model: 'Wan2.2-TI2V-5B', enabled: true },
  minimax: { provider: 'minimax', enabled: true, network_mode: 'remote', base_url: 'https://api.minimax.io/v1', model: '', api_key_env: 'MINIMAX_API_KEY', allowed_hosts: ['api.minimax.io'], download_hosts: [], duration: 6, resolution: '768P', poll_interval_seconds: 5, request_timeout_seconds: 60, max_wait_seconds: 1800 }
};
const LABELS = { comfyui_wan: 'ComfyUI · 本地视频', minimax: 'MiniMax · 视频 API' };
const providers = inventory => {
  const result = { ...LABELS };
  for (const item of list(inventory?.video_providers)) if (item.id) result[item.id] = item.label || item.id;
  return result;
};
const testKey = id => 'video:' + id;
function card(id, profile, state) {
  const result = state.modelTests?.[testKey(id)];
  const pending = state.modelTestPending === testKey(id);
  const passed = result && ['ready', 'configured', 'config_valid'].includes(result.status);
  const visibleResult = result && !state.modelsDirty;
  const enabled = profile.enabled !== false;
  const label = !enabled ? '已停用' : pending ? '预检中' : state.modelsDirty ? '待保存 / 未预检' : passed ? '配置预检通过' : result ? '预检未通过' : '尚未预检';
  const roles = Object.entries(state.modelsDraft.video_roles || {}).filter(([, value]) => value === id).map(([role]) => role === 'video_generator' ? '视频制作' : role);
  return '<article class="model-card video-model-card"><div class="model-card-heading"><div class="model-symbol" aria-hidden="true">▷</div><div><h3>' + h(id) + '</h3><p>' + h(providers(state.models)[profile.provider] || profile.provider || '视频服务') + '</p></div><span class="badge ' + (!enabled || state.modelsDirty || !result ? 'neutral' : passed ? 'success' : 'danger') + '">' + h(label) + '</span></div><strong class="model-name">' + h(compactModel(profile.model)) + '</strong><code class="endpoint">' + h(profile.base_url || '本地 GPU 调度器') + '</code><div class="model-flags"><span>视频生成</span>' + (profile.provider === 'minimax' ? '<span>' + h(profile.duration || 6) + ' 秒</span><span>' + h(profile.resolution || '768P') + '</span>' : '<span>ComfyUI 工作流</span>') + '</div><div class="model-assigned">' + (roles.length ? roles.map(role => '<span>' + h(role) + '</span>').join('') : '<span class="muted">尚未分配视频角色</span>') + '</div>' + (profile.api_key_env ? '<div class="model-auth"><span>密钥变量</span><code>' + h(profile.api_key_env) + '</code></div>' : '') + (visibleResult ? '<div class="probe-result ' + (passed ? '' : 'error') + '" role="status">' + h(result.message || result.error?.message || (passed ? '配置检查通过；尚未验证视频生成。' : '视频配置预检未通过。')) + '<small>' + h(dateText(result.checked_at, true)) + ' · 仅检查配置，不提交生成任务</small></div>' : '') + '<div class="model-card-actions"><button class="button small" data-model-action="video-edit" data-profile="' + h(id) + '">编辑连接</button><button class="button small" data-model-action="video-test" data-profile="' + h(id) + '" ' + (!enabled || state.modelsDirty || state.modelTestPending || state.modelsSaving ? 'disabled' : '') + '>' + (pending ? '正在预检…' : '预检配置') + '</button></div></article>';
}
export function videoModelsSection(state) {
  if (!state.modelsDraft?.video_profiles) return '';
  const profiles = Object.entries(state.modelsDraft.video_profiles);
  const roles = Object.entries(state.modelsDraft.video_roles || {});
  const roleRows = list(state.models.video_role_options);
  return '<section class="video-models" aria-labelledby="video-models-title"><div class="section-heading"><div><h2 id="video-models-title">视频模型 <span class="inline-count">' + profiles.length + '</span></h2><p class="muted tiny">为视频制作单独选择本地工作流或云端生成服务。</p></div><button class="button small" data-model-action="video-add" ' + (state.modelsSaving ? 'disabled' : '') + '>＋ 添加视频连接</button></div><div class="model-grid">' + (profiles.length ? profiles.map(([id, profile]) => card(id, profile, state)).join('') : '<div class="empty-state"><h3>尚无视频连接</h3><p>添加连接后，为视频制作分配模型。</p></div>') + '</div><div class="role-assignment-grid video-role-grid">' + roles.map(([role, selected]) => {
    const meta = roleRows.find(item => (item.role || item.id) === role) || {};
    const found = profiles.some(([id]) => id === selected);
    return '<div class="role-assignment"><label for="role-video-' + h(role) + '"><strong>' + h(meta.label || (role === 'video_generator' ? '视频制作' : role)) + '</strong><code>' + h(role) + '</code></label><select id="role-video-' + h(role) + '" data-role-assignment="' + h(role) + '" data-role-kind="video" ' + (state.modelsSaving ? 'disabled' : '') + '>' + (!found ? '<option value="" selected disabled>请选择有效的视频连接</option>' : '') + profiles.map(([id, profile]) => '<option value="' + h(id) + '" ' + (selected === id ? 'selected' : '') + (profile.enabled === false ? ' disabled' : '') + '>' + h(id + ' · ' + compactModel(profile.model) + (profile.enabled === false ? '（已停用）' : '')) + '</option>').join('') + '</select></div>';
  }).join('') + '</div></section>';
}
export function videoModelEditor(id, profile = {}, inventory = null) {
  const fresh = !id;
  const provider = profile.provider || 'minimax';
  const current = { ...VIDEO_DEFAULTS[provider], ...profile };
  const value = key => h(current[key] ?? '');
  const choices = (items, selected) => Object.entries(items).map(([key, label]) => '<option value="' + h(key) + '" ' + (selected === key ? 'selected' : '') + '>' + h(label) + '</option>').join('');
  const cloud = provider === 'minimax';
  return '<div class="dialog-head"><div><div class="eyebrow">VIDEO CONNECTION</div><h2 id="model-editor-title">' + (fresh ? '添加视频连接' : '编辑视频连接') + '</h2><p>视频模型独立于编剧、导演等文本角色。</p></div><button type="button" class="icon-button" data-action="close-dialog" data-dialog="model-dialog" aria-label="关闭">×</button></div><form data-model-form="video" data-profile="' + h(id || '') + '" class="dialog-body"><div class="form-row"><div class="field"><label for="video-id">连接编号</label><input id="video-id" name="id" value="' + h(id || '') + '" placeholder="video_minimax" pattern="[A-Za-z0-9][A-Za-z0-9_.-]{0,63}" maxlength="64" required ' + (fresh ? '' : 'readonly') + '></div><div class="field"><label for="video-provider">服务类型</label><select id="video-provider" name="provider">' + choices(providers(inventory), provider) + '</select></div></div><div class="field"><label for="video-model">模型标识</label><input id="video-model" name="model" value="' + value('model') + '" maxlength="128" placeholder="填写所选服务支持的模型 ID" ' + (!cloud ? 'readonly' : '') + ' required></div><fieldset class="video-remote-fields" ' + (!cloud ? 'hidden disabled' : '') + '><legend class="sr-only">云端视频接口</legend><div class="field"><label for="video-endpoint">接口地址</label><input id="video-endpoint" name="base_url" type="url" value="' + value('base_url') + '" placeholder="https://api.minimax.io/v1" maxlength="2048" required><small>完整 API 前缀。中国站可使用 https://api.minimax.cn/v1。</small></div><div class="field"><label for="video-key-env">密钥环境变量名称</label><input id="video-key-env" name="api_key_env" value="' + value('api_key_env') + '" pattern="[A-Za-z_][A-Za-z0-9_]*" maxlength="128" placeholder="MINIMAX_API_KEY" autocomplete="off" spellcheck="false" required><small>实际密钥由服务器读取；此处只登记变量名称。</small></div><div class="field"><label for="video-hosts">允许访问的 API 主机名</label><input id="video-hosts" name="allowed_hosts" value="' + h(list(current.allowed_hosts).join(', ')) + '" placeholder="api.minimax.io" required></div><div class="field"><label for="video-download-hosts">允许下载视频的主机名</label><input id="video-download-hosts" name="download_hosts" value="' + h(list(current.download_hosts).join(', ')) + '" placeholder="填写服务实际使用的下载域名，逗号分隔"><small>可留空以接收该服务返回的公网 HTTPS 下载地址；填写后只接受这些域名。</small></div><div class="form-row"><div class="field"><label for="video-duration">单次生成时长</label><select id="video-duration" name="duration">' + choices({ 6: '6 秒', 10: '10 秒' }, String(current.duration || 6)) + '</select></div><div class="field"><label for="video-resolution">分辨率</label><select id="video-resolution" name="resolution">' + choices({ '512P': '512P', '768P': '768P', '1080P': '1080P' }, current.resolution || '768P') + '</select></div></div><div class="form-row"><div class="field"><label for="video-timeout">单次请求超时 / 秒</label><input id="video-timeout" name="request_timeout_seconds" type="number" min="1" max="300" value="' + h(current.request_timeout_seconds || 60) + '" required></div><div class="field"><label for="video-poll">任务查询间隔 / 秒</label><input id="video-poll" name="poll_interval_seconds" type="number" min="1" max="60" value="' + h(current.poll_interval_seconds || 5) + '" required></div></div><div class="field"><label for="video-max-wait">任务等待上限 / 秒</label><input id="video-max-wait" name="max_wait_seconds" type="number" min="1" max="7200" value="' + h(current.max_wait_seconds || 1800) + '" required><small>等待已提交的视频任务；超时后保留任务回执。</small></div></fieldset><p class="video-local-note tiny muted" ' + (cloud ? 'hidden' : '') + '>使用服务器管理的 ComfyUI 视频工作流和本地 GPU 资源。</p><div class="model-checks"><label><input name="enabled" type="checkbox" ' + (current.enabled !== false ? 'checked' : '') + '>启用此连接</label></div><p class="form-note">保存后供后续执行批次使用。配置预检不会创建视频任务；时长与分辨率须受所选模型支持。</p><div id="model-form-error" class="form-error" role="alert" hidden></div><div class="dialog-actions"><button type="button" class="button" data-action="close-dialog" data-dialog="model-dialog">取消</button><button type="submit" class="button primary">加入待保存修改</button></div></form>';
}
export function videoProfileFromForm(values, previous = {}) {
  const provider = String(values.provider || 'minimax');
  const result = { ...(previous.provider === provider ? previous : {}), provider, model: String(values.model || '').trim(), enabled: values.enabled === 'on' };
  if (provider === 'minimax') Object.assign(result, {
    network_mode: 'remote', base_url: String(values.base_url || '').trim(),
    api_key_env: String(values.api_key_env || '').trim(),
    allowed_hosts: String(values.allowed_hosts || '').split(',').map(x => x.trim()).filter(Boolean),
    download_hosts: String(values.download_hosts || '').split(',').map(x => x.trim()).filter(Boolean),
    duration: Number(values.duration), resolution: String(values.resolution || '768P'),
    poll_interval_seconds: Number(values.poll_interval_seconds), request_timeout_seconds: Number(values.request_timeout_seconds), max_wait_seconds: Number(values.max_wait_seconds || previous.max_wait_seconds || 1800)
  });
  return result;
}
export function switchVideoProvider(form, inventory) {
  const id = form.dataset.profile || '';
  const previous = Object.fromEntries(new FormData(form));
  const profile = { ...VIDEO_DEFAULTS[previous.provider], enabled: previous.enabled === 'on' };
  const host = form.parentElement;
  host.innerHTML = videoModelEditor(id, profile, inventory);
  const nextForm = host.querySelector('form');
  if (!id && previous.id) nextForm.elements.id.value = previous.id;
  nextForm.elements.provider.focus();
}
