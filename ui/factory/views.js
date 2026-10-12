import { h, list, numeric, clamp, words, safeUrl, dateText, formatName, stageNames, statusNames, badge, pendingDecisions, isRunning } from './format.js';
import { decisionPanel, overviewPanel, scriptPanel, qualityPanel, deliveryPanel } from './panels.js';
import { workflowBoard, modelChips, phaseRoles } from './workflow.js';
export function capabilities(caps = {}) {
  const labels = { worker: '制片执行器', optimizer: '反馈优化', llm: '创作与推理', research: '选题研究', image: '图像生成', images: '图像生成', video: '视频制作', audio: '配音与声音', tts: '配音与声音', editor: '剪辑与导出', editing: '剪辑与导出', vlm: '视觉质量评审', quality: '质量评审', comfyui: 'ComfyUI 服务', lipsync: '口型同步', scheduler: 'GPU 调度' };
  const source = caps.services || caps.models || caps;
  const entries = Array.isArray(source) ? source.map((row, i) => [row.id || row.name || i, row]) : Object.entries(source).filter(([key]) => labels[key]);
  return entries.map(([key, raw]) => {
    const value = typeof raw === 'boolean' ? { available: raw } : typeof raw === 'string' ? { message: raw } : raw || {};
    const available = typeof value.available === 'boolean' ? value.available : typeof value.ready === 'boolean' ? value.ready : typeof value.healthy === 'boolean' ? value.healthy : ['ready', 'available', 'healthy', 'ok'].includes(value.status) ? true : ['unavailable', 'missing', 'failed', 'offline', 'blocked'].includes(value.status) ? false : null;
    return { key, name: value.name || labels[key] || key, available, model: value.model || value.model_name || '', message: value.message || value.reason || value.detail || (available === true ? '服务检测通过' : available === false ? '当前不可用' : '状态尚未检测') };
  });
}
const count = value => Array.isArray(value) ? value.length : numeric(value);
const waitingCount = project => pendingDecisions(project).length || numeric(project.pending_decisions ?? project.decision_count);
export function renderSidebar(state) {
  const projects = list(state.overview?.projects);
  document.getElementById('project-count').textContent = projects.length;
  document.getElementById('decision-count').textContent = projects.reduce((total, project) => total + waitingCount(project), 0);
  document.getElementById('project-list').innerHTML = projects.length ? projects.map(project => '<button class="project-item ' + (state.projectId === project.id ? 'active' : '') + '" data-action="select-project" data-id="' + h(project.id) + '"><span class="project-symbol" aria-hidden="true">◫</span><span><strong>' + h(project.title || '未命名制作') + '</strong><small>' + h(statusNames[project.status] || stageNames[project.stage] || project.status || '待开始') + '</small></span></button>').join('') : '<p class="sidebar-empty">第一部作品<br>从一个创作方向开始。</p>';
  const rows = capabilities(state.overview?.capabilities);
  const available = rows.filter(row => row.available === true).length;
  const dot = document.getElementById('engine-dot');
  dot.className = 'dot ' + (rows.length && available === rows.length ? 'ready' : available ? 'partial' : '');
  document.getElementById('engine-label').textContent = rows.length ? available + ' / ' + rows.length + ' 项能力可用' : state.overview?.capabilities?.refreshing ? '资源检测中' : '查看实测能力';
  document.getElementById('breadcrumb').textContent = state.view === 'models' ? '模型与连接' : state.project?.title || '工作流与项目';
  document.querySelector('[data-action="dashboard"]').classList.toggle('active', !state.projectId && state.view !== 'models');
  document.querySelector('[data-action="models"]').classList.toggle('active', state.view === 'models');
  document.querySelector('[data-action="dashboard"]').setAttribute('aria-current', !state.projectId && state.view !== 'models' ? 'page' : 'false');
  document.querySelector('[data-action="models"]').setAttribute('aria-current', state.view === 'models' ? 'page' : 'false');
}
export function dashboard(state) {
  const projects = list(state.overview?.projects);
  const waiting = projects.filter(project => waitingCount(project));
  const running = projects.filter(isRunning).length;
  const completed = projects.filter(project => ['completed', 'approved'].includes(project.status) && !project.is_validation).length;
  const rows = capabilities(state.overview?.capabilities);
  const activeQueue = count(state.overview?.queue?.active ?? state.overview?.queue?.running);
  return '<section class="page-heading"><div><div class="eyebrow">AI STUDIO / WORKFLOW</div><h1>一个方向，一支制作团队。</h1><p>分配模型，推进工作流，在关键创作节点做决定。</p></div><button class="button primary" data-action="new-project">＋ 发起新制作</button></section><section class="stats-row" aria-label="制作总览"><div class="stat"><span class="stat-label">等待你决定</span><div class="stat-value">' + waiting.length + '<small>个项目</small></div><span class="stat-foot">故事 · 视觉 · 成片</span></div><div class="stat"><span class="stat-label">正在制作</span><div class="stat-value">' + running + '<small>个项目</small></div><span class="stat-foot">当前执行任务 ' + activeQueue + '</span></div><div class="stat"><span class="stat-label">已交付作品</span><div class="stat-value">' + completed + '<small>部</small></div><span class="stat-foot">通过本制片台完成验收</span></div><div class="stat"><span class="stat-label">制作能力</span><div class="stat-value">' + (rows.length ? rows.filter(row => row.available === true).length + '<small>/ ' + rows.length + ' 项</small>' : '<span style="font-size:20px">检测中</span>') + '</div><button class="button quiet small" data-action="capabilities" style="padding:0;min-height:0;font-size:9px">查看资源详情 ↗</button></div></section>' + workflowBoard(state.models, state.overview?.capabilities, state.workflow) + (!projects.length ? '<section class="empty-hero"><div><div class="eyebrow">YOUR NEXT STORY STARTS HERE</div><h2>给一个方向，<br>让下一部故事开始发生。</h2><p>系统研究选题、创作提案，并在你选定故事与视觉后，继续完成分镜、生产和交付。</p><button class="button primary" data-action="new-project">写下创作方向 <span aria-hidden="true">↗</span></button></div><div class="decision-path"><div><b>01</b><span><strong>选一个故事</strong><p>从真实创作的候选提案中选择</p></span></div><div><b>02</b><span><strong>确定视觉</strong><p>批准人物、场景与画面风格</p></span></div><div><b>03</b><span><strong>审看成片</strong><p>通过、局部修改或重新剪辑</p></span></div></div></section><p class="empty-project-note">原项目已有素材与成片，可从左侧「原有作品与工具」继续查看。</p>' : (waiting.length ? '<div class="section-heading"><h2>待决策项目</h2><span>你的选择将推进下一阶段</span></div><div class="project-grid">' + waiting.map(projectCard).join('') + '</div>' : '') + '<div class="section-heading"><h2>全部制作</h2><span>' + projects.length + ' 个项目</span></div><div class="project-grid">' + projects.map(projectCard).join('') + '</div>');
}
function projectCard(project) {
  const progress = clamp(project.progress);
  return '<article class="project-card">' + badge(project.status, waitingCount(project) ? '等待你的决定' : undefined) + (project.is_validation ? ' <span class="badge neutral">验收项目</span>' : '') + '<h3>' + h(project.title || '未命名制作') + '</h3><p>' + h(String(project.brief || project.logline || '项目已建立，正在准备创作任务。').slice(0, 100)) + '</p><div class="progress-track"><span style="width:' + progress + '%"></span></div><div class="card-foot"><span>' + h(formatName(project.format)) + ' · ' + h(dateText(project.created_at)) + '</span><button class="button small" data-action="select-project" data-id="' + h(project.id) + '">进入制作 ↗</button></div></article>';
}
function phaseIndex(project) {
  const stage = String(project.stage || '');
  const pending = pendingDecisions(project)[0]?.type;
  if (['completed', 'approved'].includes(project.status)) return 5;
  if (pending === 'story' || project.status === 'awaiting_story') return 0;
  if (pending === 'style' || project.status === 'awaiting_style') return 2;
  if (pending === 'final' || project.status === 'awaiting_final') return 4;
  if (/^(research|proposal|story)(_|$|s)/.test(stage) && stage !== 'storyboard') return 0;
  if (/^(screenplay|script|screenwriting|writing)/.test(stage)) return 1;
  if (/visual|style/.test(stage)) return 2;
  if (/review|quality|final|edit|deliver/.test(stage)) return 4;
  if (/production|render|image|video|audio|storyboard|character|shot|revision/.test(stage)) return 3;
  if (project.delivery?.video_url) return 4;
  if (project.selected_style) return 3;
  if (list(project.styles).length) return 2;
  if (project.selected_story) return 1;
  return 0;
}
function steps(project, registry, caps = {}) {
  const current = phaseIndex(project);
  const names = ['选题与提案', '剧本开发', '视觉决策', '镜头制作', '质检与审片', '成片交付'];
  return '<div class="production-steps" aria-label="制作阶段">' + names.map((name, index) => '<div class="step ' + (index === current ? 'current' : index < current ? 'complete' : '') + '"><span class="step-no">' + (index < current ? '✓' : String(index + 1).padStart(2, '0')) + '</span><div><strong>' + name + '</strong><div class="step-models">' + (phaseRoles[index].length ? modelChips(registry, phaseRoles[index]) : '<span>' + h(caps.image?.model || 'GPU 制作引擎') + '</span>') + '</div></div></div>').join('') + '</div>';
}
export function projectView(state) {
  const project = state.project;
  if (!project) return '<section class="loading-panel"><div class="loader"></div><h1>正在读取制作项目</h1></section>';
  let actions = '';
  if (isRunning(project)) actions = '<button class="button small" data-action="project-action" data-command="pause">暂停制作</button>';
  if (project.status === 'paused') actions = '<button class="button small primary" data-action="project-action" data-command="resume">恢复制作</button>';
  if (pendingDecisions(project)[0]?.type === 'budget') actions = '<button class="button small primary" data-action="budget">调整预算并继续</button>';
  else if (['failed', 'blocked'].includes(project.status) && !pendingDecisions(project).length) actions = '<button class="button small primary" data-action="project-action" data-command="retry">重试当前阶段</button>';
  const tabs = [['overview', '项目概览'], ['script', '剧本与分镜'], ['quality', '制作与质检'], ['delivery', '交付文件']];
  const panes = { overview: overviewPanel, script: scriptPanel, quality: qualityPanel, delivery: deliveryPanel };
  const decision = pendingDecisions(project)[0];
  return '<section class="page-heading"><div><div class="eyebrow">PRODUCTION PROJECT / ' + h(String(project.id).slice(-8).toUpperCase()) + '</div><h1>' + h(project.title || '未命名制作') + '</h1>' + (project.is_validation ? '<span class="badge neutral">验收项目</span>' : '') + '<div class="project-meta">' + badge(project.status) + '<span>' + h(formatName(project.format)) + '</span><span>' + h(project.duration_seconds ?? '—') + ' 秒</span><span>' + h(stageNames[project.stage] || project.stage || '项目建立') + '</span></div></div><div class="heading-actions">' + actions + '<button class="button small" data-action="dashboard">全部项目</button></div></section>' + steps(project, state.models, state.overview?.capabilities) + (project.error ? '<div class="notice error"><div><strong>制作状态提示</strong>' + h(words(project.error)) + '</div></div>' : '') + (state.tab === 'overview' ? decisionPanel(project, state.busy) : decision ? '<div class="notice"><div><strong>有一个关键决策等待你处理</strong>完成选择后，系统会继续执行下一阶段。</div><button class="button small" style="margin-left:auto;flex:none" data-action="tab" data-tab="overview">查看决策</button></div>' : '') + '<nav class="tabbar" aria-label="项目内容">' + tabs.map(([id, name]) => '<button class="tab-button ' + (state.tab === id ? 'active' : '') + '" data-action="tab" data-tab="' + id + '" aria-current="' + (state.tab === id ? 'page' : 'false') + '">' + name + (id === 'script' && list(project.shots).length ? '<span>' + list(project.shots).length + '</span>' : '') + '</button>').join('') + '</nav>' + (panes[state.tab] || overviewPanel)(project);
}
export function createForm() {
  return '<div class="dialog-head"><div><div class="eyebrow">NEW PRODUCTION</div><h2 id="create-title">发起一部新制作</h2><p>给出题材与创作要求，系统开始自主研究和提案。</p></div><button type="button" class="icon-button" data-action="close-dialog" data-dialog="project-dialog" aria-label="关闭">×</button></div><form id="create-form" data-form="create" class="dialog-body"><div class="field"><label for="project-title">项目名称 <span class="muted">/ 可选</span></label><input id="project-title" name="title" maxlength="80" placeholder="可以先留空，待故事选定后命名"></div><div class="field"><label for="project-brief">创作方向</label><textarea id="project-brief" name="brief" rows="5" maxlength="6000" required minlength="5" placeholder="写下题材、受众、核心情绪，以及你希望避免的内容。也可以给出完整的原创故事想法。"></textarea><small>描述越具体，候选提案越贴近你的创作意图。</small></div><div class="field"><label>制作形式</label><div class="format-options"><label class="format-option"><input type="radio" name="format" value="motion_comic" checked>动态漫剧</label><label class="format-option"><input type="radio" name="format" value="live_action">真人影视</label><label class="format-option"><input type="radio" name="format" value="animation">二维动画</label></div><small>实际制作路径由当前模型能力决定；不可用能力会明确显示。</small></div><div class="form-row three"><div class="field"><label for="duration">目标时长</label><select id="duration" name="duration_seconds"><option value="60" selected>60 秒</option><option value="75">75 秒</option><option value="90">90 秒</option></select></div><div class="field"><label for="gpu-budget">GPU 分钟上限</label><input id="gpu-budget" name="gpu_minutes" type="number" value="120" min="5" max="1440" step="1" required></div><div class="field"><label for="max-attempts">每素材版本最多尝试</label><input id="max-attempts" name="max_attempts" type="number" value="3" min="1" max="5" step="1" required></div></div><div class="field"><label for="wall-budget">累计制作时间上限 / 分钟</label><input id="wall-budget" name="wall_minutes" type="number" value="180" min="10" max="1440" step="1" required><small>与 GPU 用量独立计量；包含已保存的各次制作耗时。</small></div><details class="source-options"><summary>添加研究资料或已授权素材</summary><div class="field"><label for="sources">来源链接 <span class="muted">/ 每行一条</span></label><textarea id="sources" name="sources" rows="3" maxlength="8000" placeholder="https://…"></textarea></div><div class="field"><label for="rights">这些资料的使用范围</label><select id="rights" name="rights"><option value="analysis_only">仅研究分析，不直接改编</option><option value="owned">我的自有原创作品</option><option value="licensed">已获得相应改编授权</option><option value="public_domain">确认属于可使用的公版素材</option></select></div></details><p class="form-note">你将在故事、视觉与成片三个节点做决定。项目在所设 GPU 用量、制作时间与尝试次数内自动制作和返修；遇到异常时暂停并说明原因。</p><div id="create-error" class="form-error" role="alert" hidden></div><div class="dialog-actions"><button type="button" class="button" data-action="close-dialog" data-dialog="project-dialog">取消</button><button type="submit" class="button primary">开始自主策划 <span aria-hidden="true">↗</span></button></div></form>';
}
export function capabilitiesDialog(state) {
  const caps = state.overview?.capabilities || {};
  const rows = capabilities(caps);
  const gpus = list(caps.gpus || caps.gpu?.devices || state.overview?.gpus);
  return '<div class="dialog-head"><div><div class="eyebrow">PRODUCTION CAPABILITIES</div><h2 id="capabilities-title">制作能力与资源</h2><p>根据服务实际返回展示；尚未检测的能力不会标为就绪。</p></div><button class="icon-button" data-action="close-dialog" data-dialog="capabilities-dialog" aria-label="关闭">×</button></div><div class="dialog-body">' + (rows.length ? '<div class="capability-grid">' + rows.map(row => '<article class="capability-card"><h3>' + h(row.name) + badge(row.available === true ? 'ready' : row.available === false ? 'failed' : 'pending', row.available === true ? '可用' : row.available === false ? '不可用' : '未确认') + '</h3><p>' + h(words(row.message)) + '</p>' + (row.model ? '<small>' + h(words(row.model)) + '</small>' : '') + '</article>').join('') + '</div>' : '<div class="empty-state"><h3>' + (caps.refreshing ? '正在检测制作资源' : '尚未返回能力检测结果') + '</h3><p>资源状态会自动更新，项目列表可正常使用。</p></div>') + '<div class="section-heading"><h2>GPU 资源</h2><span>' + gpus.length + ' 张卡已返回状态</span></div>' + (gpus.length ? '<div class="gpu-grid">' + gpus.map(gpu => '<div class="gpu-card"><strong>GPU ' + h(gpu.index ?? gpu.id ?? '—') + '</strong><p>' + h(gpu.name || gpu.model || '型号未返回') + '</p><p>' + (gpu.free_mb != null || gpu.memory_free_mb != null ? '空闲 ' + (numeric(gpu.free_mb ?? gpu.memory_free_mb) / 1024).toFixed(1) + ' GiB' : gpu.memory_used_mb != null ? '已用 ' + (numeric(gpu.memory_used_mb) / 1024).toFixed(1) + ' GiB' : '显存用量未返回') + '</p></div>').join('') + '</div>' : '<p class="muted tiny">当前响应没有 GPU 实测信息。</p>') + '</div>';
}
export function budgetForm(project) {
  const budget = project?.budget || {};
  const decision = pendingDecisions(project).find(item => item.type === 'budget') || {};
  const limits = { gpu: numeric(budget.gpu_minutes), wall: numeric(budget.wall_minutes, 180) };
  const used = { gpu: numeric(budget.spent_gpu_minutes ?? numeric(project?.production?.gpu_seconds) / 60), wall: numeric(project?.production?.elapsed_seconds) / 60 };
  const exhausted = new Set(list(decision.exhausted_limits || decision.details?.exhausted_limits).filter(key => ['gpu', 'wall'].includes(key)));
  if (!exhausted.size) for (const key of ['gpu', 'wall']) if (used[key] >= limits[key]) exhausted.add(key);
  const capped = [...exhausted].some(key => limits[key] >= 1440);
  const field = key => {
    const isExhausted = exhausted.has(key), current = limits[key];
    const minimum = isExhausted ? Math.floor(current) + 1 : current;
    const suggested = isExhausted ? Math.min(1440, Math.ceil(Math.max(current * 1.5, current + 30))) : current;
    const name = key === 'gpu' ? 'gpu_minutes' : 'wall_minutes';
    const title = key === 'gpu' ? 'GPU 分钟总上限' : '累计制作分钟总上限';
    return '<div class="field"><label for="budget-' + key + '">' + title + (isExhausted ? ' <span style="color:var(--accent)">/ 已耗尽</span>' : '') + '</label><input id="budget-' + key + '" name="' + name + '" type="number" min="' + minimum + '" max="1440" step="1" value="' + suggested + '" required><small>已用 ' + used[key].toFixed(1) + ' / 当前上限 ' + current + ' 分钟' + (isExhausted ? '；需要提高这个分量。' : '；可以保留当前上限。') + '</small></div>';
  };
  return '<div class="dialog-head"><div><div class="eyebrow">PROJECT BUDGET</div><h2 id="create-title">调整生产预算</h2><p>提高已耗尽分量的总上限，已消耗用量仍计入项目。</p></div><button type="button" class="icon-button" data-action="close-dialog" data-dialog="project-dialog" aria-label="关闭">×</button></div><form data-form="budget" class="dialog-body">' + field('gpu') + field('wall') + (capped ? '<p class="form-error">已耗尽的预算已达到 1440 分钟上限，当前项目不能在此继续增额。</p>' : '') + '<div class="dialog-actions"><button type="button" class="button" data-action="close-dialog" data-dialog="project-dialog">取消</button><button type="submit" class="button primary" ' + (capped ? 'disabled' : '') + '>更新预算并恢复</button></div></form>';
}
export const connectionError = message => '<section class="connection-panel"><div class="eyebrow">CONNECTION STATUS</div><h1>暂时无法连接制片服务</h1><p>' + h(message) + '</p><button class="button" data-action="refresh">重新连接</button></section>';
