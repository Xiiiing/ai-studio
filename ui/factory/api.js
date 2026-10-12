const PREFIX = '/api/factory';
async function request(path, body, timeout = 30000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(PREFIX + path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? { Accept: 'application/json' } : { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      cache: 'no-store'
    });
    const raw = await response.text();
    let data;
    try { data = raw ? JSON.parse(raw) : {}; }
    catch { throw new Error(response.ok ? '服务返回了无法识别的数据，请刷新重试。' : '制片服务暂时无法响应，请稍后重试。'); }
    if (!response.ok) throw new Error(typeof data.error === 'string' ? data.error : data.error?.message || data.message || '操作失败（HTTP ' + response.status + '）');
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('请求超时。请先刷新查看实际状态，避免重复提交。');
    if (error instanceof TypeError) throw new Error('无法连接制片服务。请检查网络连接后重试。');
    throw error;
  } finally { clearTimeout(timer); }
}
export const api = {
  overview: () => request('/overview'),
  workflow: () => request('/workflow'),
  models: () => request('/models'),
  testModel: profile_id => request('/models/test', { profile_id }, 15000),
  saveModels: payload => request('/models', payload),
  project: id => request('/projects/' + encodeURIComponent(id)),
  create: payload => request('/projects', payload, 45000),
  decide: (id, payload) => request('/projects/' + encodeURIComponent(id) + '/decision', payload),
  action: (id, payload) => request('/projects/' + encodeURIComponent(id) + '/action', payload)
};
