import { teamModeFields } from './team-cockpit.js';
import { cloudBudgetPanel } from './cloud-budget.js';
import { shotWorkbenchView } from './shot-workbench.js';
import { productionProfileFields } from './production-profile.js';
import { seriesPanel } from './series.js';
import { h, list, numeric, clamp, words, safeUrl, dateText, formatName, stageNames, statusNames, badge, pendingDecisions, isRunning } from './format.js';
import { overviewPanel, scriptPanel, qualityPanel, deliveryPanel, eventsPanel } from './panels.js';
import { dashboardView, projectsView, inboxView, activityView } from './console-dashboard.js';
import { flowCanvas, organizationView, workflowView } from './flow-canvas.js';
import { decisionPage } from './decision-center.js';
import { icon, sectionTitle } from './console-ui.js';
export function capabilities(caps = {}) {
  const labels = { worker: '制片执行器', optimizer: '反馈优化', llm: '创作与推理', research: '选题研究', image: '图像生成', images: '图像生成', video: '视频制作', audio: '配音与声音', tts: '配音与声音', editor: '剪辑与导出', editing: '剪辑与导出', vlm: '视觉质量评审', quality: '质量评审', comfyui: 'ComfyUI 服务', lipsync: '口型同步', scheduler: 'GPU 调度' };
  const source = caps.services || caps.models || caps;
  const entries = Array.isArray(source) ? source.map((row, i) => [row.id || row.name || i, row]) : Object.entries(source).filter(([key]) => labels[key]);
  return entries.map(([key, raw]) => {
    const value = typeof raw === 'boolean' ? { available: raw } : typeof raw === 'string' ? { message: raw } : raw || {};
    const available = typeof value.available === 'boolean' ? value.available : typeof value.ready === 'boolean' ? value.ready : typeof value.healthy === 'boolean' ? value.healthy : ['ready', 'available', 'healthy', 'ok'].includes(value.status) ? true : ['unavailable', 'missing', 'failed', 'offline', 'blocked'].includes(value.status) ? false : null;
    return { key, name: value.name || labels[key] || key, available, status: value.status || '', model: value.model || value.model_name || '', message: value.message || value.reason || value.detail || (available === true ? '服务检测通过' : available === false ? '当前不可用' : '状态尚未检测') };
  });
}
const count = value => Array.isArray(value) ? value.length : numeric(value);
const waitingCount = project => pendingDecisions(project).length || numeric(project.pending_decisions ?? project.decision_count);
const VIEW_NAMES = { dashboard:'制片总览', projects:'全部项目', inbox:'待办决策', workflow:'工作流与分工', models:'模型与连接', activity:'运行记录' };
export function renderSidebar(state) {
  const projects=list(state.overview?.projects);
  document.getElementById('project-count').textContent=projects.length;
  document.getElementById('decision-count').textContent=state.overview?.metrics?.attention ?? projects.reduce((s,p)=>s+waitingCount(p),0);
  document.getElementById('project-list').innerHTML=projects.length?projects.slice(0,12).map(p=>'<button class="project-item '+(state.projectId===p.id?'active':'')+'" data-action="select-project" data-id="'+h(p.id)+'"><span class="project-symbol">'+icon('film',16)+'</span><span><strong>'+h(p.title||'未命名制作')+'</strong><small>'+h(p.current_activity||statusNames[p.status]||'待执行')+'</small></span><span class="sidebar-status status-'+h(p.status)+'"></span></button>').join(''):'<p class="sidebar-empty">新建制作后，项目会显示在这里。</p>';
  const rows=capabilities(state.overview?.capabilities),available=rows.filter(r=>r.available===true).length;
  document.getElementById('engine-dot').className='dot '+(available===rows.length&&available?'ready':available?'partial':'');
  document.getElementById('engine-label').textContent=rows.length?available+' / '+rows.length+' 项可用':'等待实测状态';
  document.getElementById('breadcrumb').textContent=state.project?.title||VIEW_NAMES[state.view]||'制片总览';
  for(const item of document.querySelectorAll('.main-nav [data-action]')){
    const view=item.dataset.view||(item.dataset.action==='models'?'models':'dashboard');
    const active=!state.projectId&&view===(state.view||'dashboard');
    item.classList.toggle('active',active);item.setAttribute('aria-current',active?'page':'false');
  }
}
export function dashboard(state) {
  return ({projects:projectsView,inbox:inboxView,workflow:workflowView,activity:activityView}[state.view]||dashboardView)(state);
}
export function projectView(state) {
  const project=state.project;
  if(!project)return '<section class="loading-panel"><div class="loader"></div><h1>正在读取制作项目</h1></section>';
  const issue=project.operator?.attention;
  const pending=pendingDecisions(project)[0];
  let controls='';
  if(isRunning(project)&&!project.pause_requested)controls='<button class="button small" data-action="project-action" data-command="pause">'+icon('pause',15)+'暂停制作</button>';
  if(project.status==='paused')controls='<button class="button small primary" data-action="project-action" data-command="resume">恢复制作</button>';
  const tabs=[['overview','制作工作流'],['decisions','待办决策'],['team','团队与回执'],['shots','镜头制作台'],['script','剧本与分镜'],['quality','制作与质检'],['delivery','成片交付'],['series','系列资产'],['costs','云任务与费用']];
  const top='<header class="console-heading project-heading"><div><button class="breadcrumb-link" data-action="view" data-view="projects">全部项目 /</button><h1>'+h(project.title||'未命名制作')+'</h1><div class="project-meta">'+badge(project.status)+'<span>'+h(formatName(project.format))+' · '+h(project.duration_seconds)+' 秒</span><span>'+(project.team_mode==='delegated'?'总制片协作':'人工把关')+'</span><code>'+h(project.id)+'</code></div></div><div class="heading-actions">'+controls+'<button class="button small" data-action="tab" data-tab="delivery">'+icon('film',15)+'查看成片</button></div></header>';
  const notice=state.mutationError?'<div class="notice error" role="alert"><div><strong>操作未完成，已保留你的输入</strong>'+h(state.mutationError)+'</div></div>':'';
  const banner=pending&&state.tab!=='decisions'&&state.tab!=='costs'?'<div class="project-attention-banner">'+icon('inbox',21)+'<div><strong>'+h(issue?.title||'有待处理的出品人决策')+'</strong><span>'+h(issue?.summary||pending.message||'查看可选方案或审核问题，完成处理后继续制作。')+'</span></div><button class="button primary small" data-action="tab" data-tab="'+h(issue?.target_tab||'decisions')+'">'+h(issue?.action_label||'处理待办')+icon('arrow',14)+'</button></div>':'';
  const navigation='<nav class="tabbar" aria-label="项目内容">'+tabs.map(([id,name])=>'<button class="tab-button '+(state.tab===id?'active':'')+'" data-action="tab" data-tab="'+id+'" aria-current="'+(state.tab===id?'page':'false')+'">'+name+(id==='decisions'&&pending?'<span class="tab-alert">1</span>':'')+'</button>').join('')+'</nav>';
  let content;
  if(state.tab==='overview')content=flowCanvas(state)+'<div class="project-bottom-grid"><section class="console-panel">'+sectionTitle('创作任务','你提供的需求是整个团队的共同目标')+'<p class="brief-text">'+h(project.brief||'尚未提供需求')+'</p><div class="project-output-counts"><span><b>'+list(project.shots).length+'</b>计划镜头</span><span><b>'+list(project.shots).filter(s=>s.image?.path||s.image_url).length+'</b>已有关键帧</span><span><b>'+list(project.shots).filter(s=>s.video?.path||s.video_url).length+'</b>已有视频</span></div></section>'+eventsPanel(project)+'</div>';
  else if(state.tab==='decisions')content=decisionPage(project,state.busy);
  else if(state.tab==='team')content=organizationView(state)+(state.projectFlowError?'<div class="notice error">历史回执读取失败：'+h(state.projectFlowError)+'</div>':'');
  else if(state.tab==='shots')content=shotWorkbenchView(state);
  else if(state.tab==='script')content=scriptPanel(project);
  else if(state.tab==='quality')content=qualityPanel(project);
  else if(state.tab==='series')content=seriesPanel(project,state.seriesDrafts);
  else if(state.tab==='costs')content=cloudBudgetPanel(project);
  else content=deliveryPanel(project);
  return top+notice+banner+navigation+'<div class="project-pane" data-dom-key="pane-'+h(project.id+':'+state.tab)+'">'+content+'</div>';
}
export function createForm(profileCatalog, inventory = null) {
  return '<div class="dialog-head"><div><div class="eyebrow">NEW PRODUCTION</div><h2 id="create-title">发起一部新制作</h2><p>给出题材与创作要求，系统开始自主研究和提案。</p></div><button type="button" class="icon-button" data-action="close-dialog" data-dialog="project-dialog" aria-label="关闭">×</button></div><form id="create-form" data-form="create" class="dialog-body"><div class="field"><label for="project-title">项目名称 <span class="muted">/ 可选</span></label><input id="project-title" name="title" maxlength="80" placeholder="可以先留空，待故事选定后命名"></div><div class="field"><label for="project-series">系列标识 / 可选</label><input id="project-series" name="series_key" maxlength="100" placeholder="同一系列的剧集使用相同标识"><small>留空保持原有单集格式；填写后可固定并复用已批准的系列资产。</small></div><div class="field"><label for="project-brief">创作方向</label><textarea id="project-brief" name="brief" rows="5" maxlength="6000" required minlength="5" placeholder="写下题材、受众、核心情绪，以及你希望避免的内容。也可以给出完整的原创故事想法。"></textarea><small>描述越具体，候选提案越贴近你的创作意图。</small></div><div class="field"><label>制作形式</label><div class="format-options"><label class="format-option"><input type="radio" name="format" value="motion_comic" checked>动态漫剧</label><label class="format-option"><input type="radio" name="format" value="live_action">真人影视</label><label class="format-option"><input type="radio" name="format" value="animation">二维动画</label></div><small>实际制作路径由当前模型能力决定；不可用能力会明确显示。</small></div>' + teamModeFields(inventory) + productionProfileFields(profileCatalog) + '<div class="form-row three"><div class="field"><label for="duration">经典规格目标时长</label><select id="duration" name="duration_seconds"><option value="60" selected>60 秒</option><option value="75">75 秒</option><option value="90">90 秒</option></select></div><div class="field"><label for="gpu-budget">GPU 分钟上限</label><input id="gpu-budget" name="gpu_minutes" type="number" value="120" min="5" max="1440" step="1" required></div><div class="field"><label for="max-attempts">每素材版本最多尝试</label><input id="max-attempts" name="max_attempts" type="number" value="3" min="1" max="5" step="1" required></div></div><div class="field"><label for="wall-budget">累计制作时间上限 / 分钟</label><input id="wall-budget" name="wall_minutes" type="number" value="180" min="10" max="1440" step="1" required><small>与 GPU 用量独立计量；包含已保存的各次制作耗时。</small></div><details class="source-options"><summary>添加研究资料或已授权素材</summary><div class="field"><label for="sources">来源链接 <span class="muted">/ 每行一条</span></label><textarea id="sources" name="sources" rows="3" maxlength="8000" placeholder="https://…"></textarea></div><div class="field"><label for="rights">这些资料的使用范围</label><select id="rights" name="rights"><option value="analysis_only">仅研究分析，不直接改编</option><option value="owned">我的自有原创作品</option><option value="licensed">已获得相应改编授权</option><option value="public_domain">确认属于可使用的公版素材</option></select></div></details><p class="form-note">人工模式保留故事、视觉和终片关卡；委托模式仅代选合格创意，终片仍由你审核。项目在所设 GPU 用量、制作时间与尝试次数内自动制作和返修；遇到异常时暂停并说明原因。</p><div id="create-error" class="form-error" role="alert" hidden></div><div class="dialog-actions"><button type="button" class="button" data-action="close-dialog" data-dialog="project-dialog">取消</button><button type="submit" class="button primary">开始自主策划 <span aria-hidden="true">↗</span></button></div></form>';
}
export function capabilityStatus(row) {
  if (row.available === true) return '可用';
  if (row.available === false) return '不可用';
  return { configured: '已配置', unverified: '未验证', error: '检测异常' }[row.status] || '未确认';
}
export function capabilitiesDialog(state) {
  const caps = state.overview?.capabilities || {};
  const rows = capabilities(caps);
  const gpus = list(caps.gpus || caps.gpu?.devices || state.overview?.gpus);
  return '<div class="dialog-head"><div><div class="eyebrow">PRODUCTION CAPABILITIES</div><h2 id="capabilities-title">制作能力与资源</h2><p>根据服务实际返回展示；尚未检测的能力不会标为就绪。</p></div><button class="icon-button" data-action="close-dialog" data-dialog="capabilities-dialog" aria-label="关闭">×</button></div><div class="dialog-body">' + (rows.length ? '<div class="capability-grid">' + rows.map(row => '<article class="capability-card"><h3>' + h(row.name) + badge(row.available === true ? 'ready' : row.available === false ? 'failed' : row.status || 'pending', capabilityStatus(row)) + '</h3><p>' + h(words(row.message)) + '</p>' + (row.model ? '<small>' + h(words(row.model)) + '</small>' : '') + '</article>').join('') + '</div>' : '<div class="empty-state"><h3>' + (caps.refreshing ? '正在检测制作资源' : '尚未返回能力检测结果') + '</h3><p>资源状态会自动更新，项目列表可正常使用。</p></div>') + '<div class="section-heading"><h2>GPU 资源</h2><span>' + gpus.length + ' 张卡已返回状态</span></div>' + (gpus.length ? '<div class="gpu-grid">' + gpus.map(gpu => '<div class="gpu-card"><strong>GPU ' + h(gpu.index ?? gpu.id ?? '—') + '</strong><p>' + h(gpu.name || gpu.model || '型号未返回') + '</p><p>' + (gpu.free_mb != null || gpu.memory_free_mb != null ? '空闲 ' + (numeric(gpu.free_mb ?? gpu.memory_free_mb) / 1024).toFixed(1) + ' GiB' : gpu.memory_used_mb != null ? '已用 ' + (numeric(gpu.memory_used_mb) / 1024).toFixed(1) + ' GiB' : '显存用量未返回') + '</p></div>').join('') + '</div>' : '<p class="muted tiny">当前响应没有 GPU 实测信息。</p>') + '</div>';
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
