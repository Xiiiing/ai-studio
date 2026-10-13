export const h = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
export const list = value => Array.isArray(value) ? value : [];
export const numeric = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
export const clamp = (value, min = 0, max = 100) => Math.min(max, Math.max(min, numeric(value)));
export const words = value => value == null ? '' : typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
export function safeUrl(value) {
  if (typeof value !== 'string' || !value.trim()) return '';
  try {
    const url = new URL(value, location.origin);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
  } catch { return ''; }
}
export function dateText(value, time = false) {
  if (!value) return '尚未记录';
  const date = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value);
  if (Number.isNaN(date.getTime())) return '时间待同步';
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', ...(time ? { hour: '2-digit', minute: '2-digit', hour12: false } : {}) }).format(date);
}
export const formatName = value => ({ motion_comic: '动态漫剧', live_action: '真人影视', animation: '二维动画' }[value] || value || '制作形式待定');
export const stageNames = { remote_submit: '云任务提交', remote_poll: '云端等待', remote_download: '下载云素材', local_render: '本地制作', quality_review: '质量复核', script_creation: '剧本创作', script_review: '剧本评审', story_review: '提案评审', scripting: '剧本开发', visual_development: '视觉开发', producing: '镜头制作', final_technical_review: '成片技术质检', delivery_ready: '等待成片审核', screenplay: '剧本开发', style_previews: '视觉开发', story_decision: '故事决策', style_decision: '视觉决策', final_decision: '成片决策', revision: '返修制作', delivered: '成片交付', created: '项目建立', queued: '等待调度', research: '选题研究', researching: '选题研究', proposals: '创意提案', story: '故事决策', story_selection: '故事决策', awaiting_story: '故事决策', writing: '剧本开发', script: '剧本开发', screenwriting: '剧本开发', visual: '视觉开发', styles: '视觉决策', style: '视觉决策', awaiting_style: '视觉决策', storyboard: '分镜设计', production: '镜头制作', rendering: '镜头制作', images: '首帧制作', video: '镜头生成', audio: '声音制作', editing: '自动剪辑', quality: '质量评审', review: '成片评审', final: '成片决策', awaiting_final: '成片决策', delivery: '成片交付', completed: '已交付', paused: '已暂停', failed: '需要处理', blocked: '等待处理' };
export const statusNames = { waiting_remote: '云端等待 · 执行器已让出', planned: '待制作', generated: '已生成', pass: '通过', fail: '未通过', creating: '创意开发', scripting: '剧本创作', visual_development: '视觉开发', revising: '自动返修', created: '已建立', queued: '待调度', running: '进行中', researching: '研究中', writing: '创作中', producing: '制作中', rendering: '生成中', reviewing: '评审中', waiting: '等待决策', awaiting_decision: '等待决策', awaiting_story: '等待选故事', awaiting_style: '等待定视觉', awaiting_final: '等待审成片', paused: '已暂停', failed: '需要处理', blocked: '等待处理', completed: '已交付', approved: '已通过', done: '已完成', pending: '待处理', passed: '通过', retrying: '自动返修', cancelled: '已取消', ready: '就绪', rejected: '未通过', error: '异常' };
export const pendingDecisions = project => { const pending = list(project?.decisions).filter(item => item.status === 'pending'); return pending.length ? pending : project?.needs_decision && typeof project.needs_decision === 'object' ? [{ status: 'pending', ...project.needs_decision }] : []; };
export const canReviseProduction = project => project?.status === 'blocked' && project?.needs_decision?.type === 'production' && !!project.selected_story && !!project.selected_style && !!project.script && list(project.shots).length > 0;
export const isRunning = project => ['waiting_remote', 'running', 'queued', 'researching', 'writing', 'producing', 'rendering', 'reviewing', 'retrying', 'creating', 'scripting', 'visual_development', 'revising'].includes(project?.status);
export function badge(status, label) {
  const color = ['failed', 'error', 'rejected', 'blocked'].includes(status) ? 'danger' : ['completed', 'approved', 'done', 'passed', 'ready'].includes(status) ? 'success' : ['waiting', 'pending', 'awaiting_decision', 'awaiting_story', 'awaiting_style', 'awaiting_final'].includes(status) ? 'attention' : 'neutral';
  return '<span class="badge ' + color + '"><span class="dot"></span>' + h(label || statusNames[status] || status || '待开始') + '</span>';
}
export const fieldNames = { title: '标题', logline: '一句话故事', synopsis: '故事梗概', theme: '主题', genre: '题材', hook: '开场钩子', ending: '结局', characters: '人物设定', character: '人物', name: '姓名', description: '描述', motivation: '动机', conflict: '冲突', scenes: '场景', scene: '场景', scene_id: '场景编号', location: '地点', time: '时间', action: '动作', dialogue: '对白', speaker: '角色', text: '内容', voiceover: '旁白', narration: '旁白', duration_seconds: '时长（秒）', duration: '时长', seconds: '秒', beats: '情绪节拍', notes: '备注', screenplay: '剧本正文', visual_style: '视觉风格', wardrobe: '服装', personality: '性格', arc: '人物弧线', setting: '设定', production_notes: '制作说明', continuity: '连续性', technical: '技术检查', semantic: '剧情检查', identity: '人物一致性', score: '评分', verdict: '结论', issues: '发现问题', repair: '修复策略', reason: '原因', measured: '实测', passed: '通过', status: '状态' };
export function readable(value, depth = 0) {
  if (value === null || value === undefined || value === '') return '<span class="muted">待生成</span>';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value !== 'object') return '<span class="preserve-lines">' + h(value) + '</span>';
  if (depth > 5) return '<pre class="raw-text">' + h(words(value)) + '</pre>';
  if (Array.isArray(value)) return value.length ? '<div class="value-list">' + value.slice(0, 100).map(item => '<div>' + readable(item, depth + 1) + '</div>').join('') + '</div>' : '<span class="muted">暂无</span>';
  return '<dl class="data-list">' + Object.entries(value).filter(([key]) => !/^(prompt|negative_prompt|seed|model|api_key|token|secret)$/i.test(key)).slice(0, 70).map(([key, item]) => '<div><dt>' + h(fieldNames[key] || key) + '</dt><dd>' + readable(item, depth + 1) + '</dd></div>').join('') + '</dl>';
}
