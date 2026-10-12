import { h, list } from './format.js';

const LABELS = { researcher: '选题研究', creator: '故事策划', story_editor: '故事统筹', story_critic: '提案审核', screenwriter: '编剧', independent_critic: '剧本审核', storyboard_director: '分镜导演', visual_designer: '视觉设计', prompt_style_compiler: '提示词整理', visual_critic: '视觉质检', dialogue_editor: '对白编辑', edit_director: '剪辑统筹' };
export const roleLabel = role => LABELS[role] || role;
export const compactModel = model => { const name = String(model || '').split('/').filter(Boolean).pop() || '未配置模型'; return name.replace(/\.gguf$/i, ''); };
export function roleRoute(registry, role) {
  const row = list(registry?.roles).find(item => (item.role || item.id) === role) || {};
  const id = registry?.role_assignments?.[role] || row.profile;
  const profile = registry?.editable_profiles?.[id] || list(registry?.profiles).find(item => item.id === id);
  return { role, label: row.label || roleLabel(role), profile: id || '', model: profile?.model || row.model || '', network: profile?.network_mode || row.network_mode || 'loopback' };
}
export function modelChips(registry, roles) {
  return roles.map(role => {
    const row = roleRoute(registry, role);
    return '<span class="route-chip" title="' + h(row.model ? row.label + ' → ' + row.profile + ' / ' + row.model : '尚未读取角色连接') + '"><span>' + h(row.label) + '</span><code>' + h(row.model ? compactModel(row.model) : '等待配置') + '</code></span>';
  }).join('');
}
const NODES = [
  { name: '创作方向', sub: '题材、形式与生产预算', input: true },
  { name: '选题与提案', sub: '研究资料，比较候选故事', roles: ['researcher', 'creator', 'story_critic'], gate: '选故事' },
  { name: '剧本创作', sub: '完整剧本与独立审核', roles: ['screenwriter', 'independent_critic'], loop: '有限轮重写' },
  { name: '导演与分镜', sub: '把剧情落实为可制作镜头', roles: ['storyboard_director'], loop: '带着具体问题返修' },
  { name: '视觉开发', sub: '人物、场景与统一风格', roles: ['visual_designer', 'prompt_style_compiler'], gate: '定视觉' },
  { name: '镜头与声音', sub: '按资源排队，保留每次产物', engines: ['image', 'video', 'audio'] },
  { name: '质检与修复', sub: '检查剧情、画面和媒体完整性', roles: ['visual_critic'], loop: '只重做失败镜头' },
  { name: '剪辑与交付', sub: '完整 MP4 与可重建的工程', roles: ['edit_director'], engines: ['editor'], gate: '审成片' }
];
export function workflowBoard(registry, capabilities = {}, spec = null) {
  const content = NODES.map((node, index) => '<li class="workflow-node ' + (node.gate ? 'has-gate' : '') + '"><div class="workflow-node-head"><span class="workflow-number">' + String(index + 1).padStart(2, '0') + '</span>' + (node.gate ? '<span class="human-gate">出品人 · ' + h(node.gate) + '</span>' : '<span class="workflow-auto">' + (node.input ? '你来设定' : '自动执行') + '</span>') + '</div><h3>' + h(node.name) + '</h3><p>' + h(node.sub) + '</p><div class="node-routes">' + (node.roles ? modelChips(registry, node.roles) : '') + (node.engines || []).map(key => '<span class="engine-chip"><span>' + h({ image: '图像', video: '视频', audio: '配音', editor: '剪辑' }[key]) + '</span><code>' + h(capabilities[key]?.model || '等待能力检测') + '</code></span>').join('') + (node.input ? '<span class="input-token">BRIEF → 制作任务书</span>' : '') + '</div>' + (node.loop ? '<div class="workflow-loop">↶ ' + h(node.loop) + '</div>' : '') + '</li>').join('');
  return '<section class="workflow-section" aria-labelledby="workflow-title"><div class="section-heading"><div><h2 id="workflow-title">让不同模型，各司其职</h2><p class="muted tiny">创作阶段视图 · 总控保存状态，审核反馈驱动下一次尝试。</p></div><button class="button small" data-action="models">配置角色模型 ↗</button></div><ol class="workflow-board">' + content + '</ol><div class="workflow-legend"><span><i class="legend-gate"></i>3 个主要人工决策点</span><span>预算与失败上限触发时，暂停并保留进度</span><span>节点展示当前配置，不代表历史任务已调用</span></div>' + topologyDetails(spec) + '</section>';
}
function topologyDetails(spec) {
  if (!spec) return '<p class="tiny muted">执行图接口尚未返回；此处仅展示阶段概览。</p>';
  const nodes = list(spec.nodes), edges = list(spec.edges);
  return '<details class="workflow-topology"><summary>查看实际执行图 · ' + h(spec.orchestrator) + ' / v' + h(spec.version) + ' · ' + nodes.length + ' 节点</summary><p>以下节点和转移直接读取服务器执行图。上方卡片用于展示创作阶段。</p><div class="graph-nodes">' + nodes.map(node => '<code>' + h(node.id) + '</code>').join('') + '</div><ul class="graph-edges">' + edges.map(edge => '<li>' + h(edge.source) + ' → ' + h(edge.target) + (edge.conditional ? ' · 条件' : '') + '</li>').join('') + '</ul><p class="tiny muted">配置按一次执行批次固定，直到下一个决策或暂停。修改角色分配不替换执行图代码。</p></details>';
}
export const phaseRoles = [['researcher', 'creator'], ['screenwriter', 'independent_critic', 'storyboard_director'], ['visual_designer'], [], ['visual_critic'], ['edit_director']];
