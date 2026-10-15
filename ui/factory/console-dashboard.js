import { h, list, numeric, clamp, safeUrl, dateText, badge, formatName, isRunning, pendingDecisions } from './format.js';
import { icon, sectionTitle, viewHeading, emptyState, openProject } from './console-ui.js';

const attentionOf = p => p.attention || (pendingDecisions(p).length || numeric(p.pending_decisions) ? { blocking: true, kind: 'pending', title: '有待处理事项', summary: '', action_label: '查看待办', target_tab: 'decisions' } : null);
const createButton = '<button class="button primary" data-action="new-project">' + icon('plus') + '新建制作</button>';
const statuses = { all: '全部项目', active: '制作中', attention: '需要处理', delivered: '已交付' };

export function attentionRows(projects, limit = 20) {
  const items = projects.filter(p => attentionOf(p)?.blocking).slice(0, limit);
  return items.length ? '<div class="attention-rows">' + items.map(p => {
    const a = attentionOf(p);
    return '<article class="attention-row" data-dom-key="attention-' + h(p.id) + '"><span class="attention-icon">' + icon(a.kind === 'final' ? 'play' : 'inbox', 18) + '</span><div><strong>' + h(p.title || '未命名制作') + '</strong><p>' + h(a.title) + '</p>' + (a.summary ? '<small>' + h(a.summary.slice(0, 140)) + '</small>' : '') + '</div>' + openProject(p.id, a.action_label || '处理', a.target_tab || 'decisions') + '</article>';
  }).join('') + '</div>' : '<div class="quiet-empty">' + icon('check', 24) + '<strong>当前没有需要你处理的事项</strong><p>自动执行中的步骤不会误报为人工待办。</p></div>';
}

export function projectTable(projects, { compact = false } = {}) {
  if (!projects.length) return emptyState('还没有制作项目', '从一段需求开始，团队将逐步完成故事、镜头和成片。', createButton);
  return '<div class="console-project-table table-scroll"><table><thead><tr><th>制作项目</th><th>当前状态</th><th>进度与产物</th>' + (!compact ? '<th>规格</th><th>更新</th>' : '') + '<th><span class="sr-only">操作</span></th></tr></thead><tbody>' + projects.map(p => {
    const a = attentionOf(p), image = safeUrl(p.poster);
    return '<tr data-dom-key="project-' + h(p.id) + '"><td><button class="project-table-title" data-action="select-project" data-id="' + h(p.id) + '"><span class="project-thumb">' + (image ? '<img alt="" loading="lazy" src="' + h(image) + '">' : icon('film', 24)) + '</span><span><strong>' + h(p.title || '未命名制作') + '</strong><small>' + h(p.current_activity || (a?.blocking ? a.title : formatName(p.format))) + '</small></span></button></td><td>' + badge(p.status) + (p.team_mode === 'delegated' ? '<small class="table-note">总制片协作</small>' : '') + '</td><td><div class="table-progress"><span>' + clamp(p.progress) + '%</span><div class="progress-track"><span style="width:' + clamp(p.progress) + '%"></span></div></div><small class="table-note">' + (p.shot_total ? (p.shot_images ?? 0) + ' / ' + p.shot_total + ' 关键帧' : '等待分镜') + '</small></td>' + (!compact ? '<td>' + h(formatName(p.format)) + '<small class="table-note">' + h(p.duration_seconds || '—') + ' 秒</small></td><td class="table-date">' + h(dateText(p.updated_at || p.created_at, true)) + '</td>' : '') + '<td>' + openProject(p.id, a?.blocking ? '处理待办' : '打开', a?.blocking ? a.target_tab || 'decisions' : 'overview') + '</td></tr>';
  }).join('') + '</tbody></table></div>';
}

function metrics(overview) {
  const projects = list(overview?.projects), m = overview?.metrics || {};
  const values = [
    ['正在执行', m.running ?? projects.filter(isRunning).length, '执行器运行中的制作', 'play'],
    ['等待你处理', m.attention ?? projects.filter(p => attentionOf(p)?.blocking).length, '决策、审核问题与异常', 'inbox'],
    ['已交付作品', m.delivered ?? projects.filter(p => p.status === 'approved' && !p.is_validation).length, '已由出品人确认终版', 'check'],
    ['累计 GPU 用量', m.gpu_minutes ?? projects.reduce((s,p) => s + numeric(p.budget?.spent_gpu_minutes), 0).toFixed(1), '卡分钟 · 已记录的项目用量', 'cpu'],
  ];
  return '<section class="console-metrics" aria-label="制作总览">' + values.map(([label, value, detail, glyph], i) => '<article class="metric-card"><div><span>' + label + '</span>' + icon(glyph, 19) + '</div><strong class="metric-number" data-metric="' + i + '">' + h(value) + '</strong><small>' + detail + '</small></article>').join('') + '</section>';
}

export function activityView(state, compact = false) {
  const logs = list(state.overview?.activity).filter(x => state.logFilter !== 'problems' || ['error', 'decision_required', 'review_blocked'].includes(x.kind));
  const content = '<div class="activity-list">' + logs.slice(0, compact ? 7 : 36).map(e => '<article class="activity-item" data-dom-key="event-' + h(e.project_id + e.at + e.kind + e.message?.slice(0,20)) + '"><span class="activity-dot ' + (e.kind === 'error' || e.kind === 'review_blocked' ? 'problem' : '') + '"></span><div><p>' + h(e.message) + '</p><button data-action="open-project-tab" data-id="' + h(e.project_id) + '" data-tab="quality" class="text-link">' + h(e.title || e.project_id) + '</button></div><time>' + h(dateText(e.at, true)) + '</time></article>').join('') + (!logs.length ? '<p class="muted quiet-padding">暂无运行记录</p>' : '') + '</div>';
  if (compact) return content;
  return viewHeading('OPERATIONS', '运行记录', '按项目追踪执行、审核和异常；点击项目进入相关证据。') + '<section class="console-panel">' + sectionTitle('最近运行事件', '', '<div class="segmented"><button data-action="log-filter" data-filter="all" class="' + (state.logFilter !== 'problems' ? 'selected' : '') + '">全部事件</button><button data-action="log-filter" data-filter="problems" class="' + (state.logFilter === 'problems' ? 'selected' : '') + '">问题与待办</button></div>') + content + '</section>';
}

function resources(state) {
  const caps = state.overview?.capabilities || {}, devices = list(caps.gpus), connections = list(state.models?.profiles);
  return '<section class="console-panel resource-panel">' + sectionTitle('资源与模型', '当前能力与实测显存，不代表生成质量', '<button class="text-link" data-action="models">管理连接 ' + icon('arrow',14) + '</button>') + '<div class="resource-models">' + connections.map(p => '<div><span class="resource-dot ' + (p.enabled === false ? 'off' : '') + '"></span><div><strong>' + h(p.id) + '</strong><small>' + h(p.model || '模型未配置') + '</small></div><span class="resource-kind">' + (p.vision ? '视觉' : '文本') + '</span></div>').join('') + '</div><div class="resource-gpus">' + devices.map(gpu => {
    const free = numeric(gpu.free_mb ?? gpu.memory_free_mb), total = numeric(gpu.total_mb ?? gpu.memory_total_mb, 24576), used = clamp(100 - free / total * 100);
    return '<div class="resource-gpu"><div><b>GPU ' + h(gpu.index) + '</b><span>' + (free / 1024).toFixed(1) + ' GB 空闲</span></div><div class="progress-track"><span style="width:' + used + '%"></span></div></div>';
  }).join('') + (!devices.length ? '<p class="muted">GPU 实测状态尚未返回</p>' : '') + '</div></section>';
}

export function dashboardView(state) {
  const projects = list(state.overview?.projects), running = projects.filter(p => p.job_running || isRunning(p)), queue = state.overview?.queue || {};
  return viewHeading('PRODUCTION OVERVIEW', '制片总览', '查看正在发生什么、哪里受阻，以及需要你做的下一步。', createButton) + metrics(state.overview) + '<div class="dashboard-grid"><section class="console-panel dashboard-main">' + sectionTitle('正在制作', queue.running ? '执行器运行中 · 排队 ' + numeric(queue.queued) + ' 个任务' : '当前没有执行中的任务', '<button class="text-link" data-action="view" data-view="projects">全部项目 ' + icon('arrow',14) + '</button>') + projectTable(running.slice(0,5), {compact:true}) + '</section><section class="console-panel dashboard-inbox">' + sectionTitle('待办决策', '每一项都对应明确操作', '<button class="text-link" data-action="view" data-view="inbox">查看全部</button>') + attentionRows(projects,3) + '</section><section class="console-panel">' + sectionTitle('最近动态', '', '<button class="text-link" data-action="view" data-view="activity">运行记录 ' + icon('arrow',14) + '</button>') + activityView(state,true) + '</section>' + resources(state) + '</div>';
}

export function projectsView(state) {
  const filter = state.projectFilter || 'all', search = (state.projectSearch || '').trim().toLowerCase();
  const projects = list(state.overview?.projects).filter(p => (!search || [p.title,p.brief,p.id].some(t => String(t || '').toLowerCase().includes(search))) && (filter === 'all' || filter === 'active' && isRunning(p) || filter === 'attention' && attentionOf(p)?.blocking || filter === 'delivered' && p.status === 'approved'));
  return viewHeading('PRODUCTIONS', '全部项目', '从需求、剧本、镜头到交付，所有制作集中在这里。', createButton) + '<section class="console-panel"><div class="project-toolbar"><div class="segmented">' + Object.entries(statuses).map(([id,label]) => '<button data-action="project-filter" data-filter="' + id + '" class="' + (id === filter ? 'selected' : '') + '">' + label + '</button>').join('') + '</div><label class="search-box">' + icon('search') + '<input aria-label="搜索项目" placeholder="搜索名称或需求" data-console-search data-preserve="project-search" value="' + h(state.projectSearch || '') + '"></label></div>' + projectTable(projects) + '</section>';
}
export function inboxView(state) { return viewHeading('DECISION INBOX', '待办决策', '这里只展示真正需要你处理的选择、审核问题与异常。') + '<section class="console-panel">' + attentionRows(list(state.overview?.projects)) + '</section>'; }
