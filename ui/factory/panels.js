import { h, list, words, numeric, clamp, safeUrl, dateText, badge, pendingDecisions, canReviseProduction, readable, formatName, fieldNames } from './format.js';
const empty = (title, message) => '<div class="empty-state"><h3>' + h(title) + '</h3><p>' + h(message) + '</p></div>';
const attemptCount = shot => Array.isArray(shot.attempts) ? shot.attempts.length : numeric(shot.attempts);
const shotQuality = shot => shot.quality ?? shot.qa;
const hasRetry = shot => Array.isArray(shot.attempts) ? shot.attempts.some(attempt => numeric(attempt.attempt) > 1) : numeric(shot.attempts) > 1;
const optionButton = (type, id, label, busy, unavailable = false) => '<button class="button primary" data-action="decide" data-type="' + h(type) + '" data-choice="' + h(id) + '" ' + (unavailable || !id ? 'data-locked="true" ' : '') + (busy || !id || unavailable ? 'disabled' : '') + '>' + h(label) + ' <span aria-hidden="true">↗</span></button>';
function proposalCard(proposal, index, busy) {
  const scores = proposal.scores && typeof proposal.scores === 'object' ? Object.entries(proposal.scores).filter(([, value]) => typeof value === 'number') : [];
  return '<article class="proposal-card"><div class="proposal-index"><b>' + String(index + 1).padStart(2, '0') + '</b><span class="genre">' + h(proposal.genre || '原创提案') + '</span></div><h3>' + h(proposal.title || '未命名提案') + '</h3><p>' + h(proposal.logline || proposal.synopsis || '梗概待补充') + '</p><div class="hook"><span>叙事钩子</span>' + h(proposal.hook || proposal.production_notes || '等待创作评审记录') + '</div><details><summary>展开故事与制作评审</summary><p>' + h(proposal.synopsis || '') + '</p>' + (proposal.ending ? '<p><b>结局</b> · ' + h(proposal.ending) + '</p>' : '') + (proposal.production_notes ? '<p><b>制作考虑</b> · ' + h(proposal.production_notes) + '</p>' : '') + (scores.length ? '<div class="score-list">' + scores.map(([key, value]) => '<span>' + h(fieldNames[key] || key) + ' ' + h(value) + '</span>').join('') + '</div>' : '') + (proposal.review ? '<p><b>独立评审</b> · ' + h(proposal.review.summary || '') + '</p>' + readable(proposal.review.issues || []) : '') + '</details>' + optionButton('story', proposal.id, proposal.eligible === false ? '评审未通过' : '选择这个故事', busy, proposal.eligible === false) + '</article>';
}
function styleCard(style, index, busy) {
  const palette = list(style.palette).filter(color => typeof color === 'string' && /^#[0-9a-f]{3,8}$/i.test(color));
  const preview = safeUrl(style.preview_url) || safeUrl(style.image_url) || safeUrl(style.preview);
  return '<article class="proposal-card"><div class="proposal-index"><b>' + String(index + 1).padStart(2, '0') + '</b><span class="genre">视觉方案</span></div><div class="style-preview">' + (preview ? '<img src="' + h(preview) + '" alt="' + h(style.name || '视觉方案') + '" loading="lazy">' : '<span>文字视觉方案 · 尚无预览图</span>') + '</div><h3>' + h(style.name || '视觉方案 ' + (index + 1)) + '</h3><p>' + h(style.description || '') + '</p>' + (palette.length ? '<div class="palette" aria-label="建议色板">' + palette.map(color => '<span class="swatch" style="background:' + h(color) + '" title="' + h(color) + '"></span>').join('') + '</div>' : '') + optionButton('style', style.id, '采用这套视觉', busy) + '</article>';
}
function productionRevisionForm(project, busy) {
  const shots = list(project.shots);
  const prefix = String(project.id) + ':production-revision:';
  return '<form data-form="revise-production" data-project-id="' + h(project.id) + '" data-project-revision="' + h(project.revision) + '" class="production-revision-form"><fieldset><legend>选择需要修改的镜头</legend><div class="shot-options">' + shots.map(shot => '<label class="shot-option"><input type="checkbox" name="shot_ids" value="' + h(shot.id) + '" data-preserve="' + h(prefix + shot.id) + '"><span><strong>' + h(shot.id) + '</strong>' + h(String(shot.dramatic_beat || shot.description || shot.action || '查看「剧本与分镜」了解内容').slice(0, 90)) + '</span></label>').join('') + '</div></fieldset><div class="field"><label for="production-revision-feedback">修改说明</label><textarea id="production-revision-feedback" name="feedback" data-preserve="' + h(prefix + 'feedback') + '" rows="4" maxlength="3000" required placeholder="说明所选镜头需要怎样改，例如简化动作、调整道具位置或人物互动。"></textarea><small>明确勾选需要修改的镜头，未选镜头保留原分镜。已有素材、尝试记录与累计预算保留。</small></div><button class="button primary" ' + (busy ? 'disabled' : '') + '>提交局部返修</button></form>';
}
export function decisionPanel(project, busy = false) {
  const decision = pendingDecisions(project)[0];
  if (!decision) return '';
  const header = (number, title, copy) => '<div class="decision-top"><div><div class="eyebrow">PRODUCER DECISION / 出品人决策</div><h2>' + title + '</h2><p>' + copy + '</p></div><span class="decision-token">' + number + ' / 03</span></div>';
  let content = '';
  if (String(decision.code || '').startsWith('cloud_')) {
    content = '<h2>云视频需要明确授权或核对</h2><p>' + h(decision.message) + '</p><button class="button primary" data-action="tab" data-tab="costs">查看批次估价与累计账本</button>';
  } else if (decision.type === 'story') {
    const proposals = list(project.proposals);
    content = header('01', '下一部故事，由你选择', '选定后，编剧与导演继续完成剧本、人物和分镜。') + (proposals.length ? '<div class="proposal-grid">' + proposals.map((p, i) => proposalCard(p, i, busy)).join('') + '</div>' : empty('故事提案正在准备', '待研究和评审完成后，这里将展示可选方案。')) + '<div class="decision-bottom"><span>后续只在视觉方案与成片验收时等待你的决定。</span><details><summary>想换一个方向？</summary><form data-form="repropose" class="feedback-form"><textarea name="feedback" data-preserve="repropose-feedback" aria-label="新的创意方向" placeholder="说明希望加强的题材、冲突或风格…" maxlength="3000"></textarea><button class="button small" ' + (busy ? 'disabled' : '') + '>重新提案</button></form></details></div>';
  } else if (decision.type === 'style') {
    const styles = list(project.styles);
    content = header('02', '为这部故事，确定视觉语言', '选择后，系统按项目预算调度镜头制作、配音与自动剪辑。') + (styles.length ? '<div class="proposal-grid">' + styles.map((s, i) => styleCard(s, i, busy)).join('') + '</div>' : empty('视觉方案正在准备', '实际方案准备完成后即可选择。'));
  } else if (decision.type === 'final') {
    const video = safeUrl(project.delivery?.video_url);
    content = header('03', '初版成片，等待你的审片意见', '可通过交付、提出局部修改，或要求重新剪辑。') + (video ? '<div class="delivery-layout"><video class="delivery-video" controls playsinline preload="metadata" src="' + h(video) + '"></video><div class="delivery-info"><h2>' + h(project.title) + '</h2><p>请查看节奏、叙事和人物表现。技术质检与返修记录保留在「制作与质检」中。</p><form data-form="final"><div class="field"><label for="final-feedback">审片意见</label><textarea id="final-feedback" name="feedback" data-preserve="final-feedback" rows="4" maxlength="4000" placeholder="例如：第 5 镜人物表情需要更克制；结尾停留增加 1 秒。"></textarea></div><div class="delivery-actions"><button class="button primary" name="choice" value="approve" ' + (busy ? 'disabled' : '') + '>通过并交付</button><button class="button" name="choice" value="revise" ' + (busy ? 'disabled' : '') + '>局部修改</button><button class="button" name="choice" value="reedit" ' + (busy ? 'disabled' : '') + '>重新剪辑</button></div></form></div></div>' : empty('成片文件尚未就绪', '服务已进入审片阶段，但尚未返回可播放的成片。请刷新状态或查看制作记录。'));
  } else if (decision.type === 'budget') {
    content = '<div class="decision-top"><div><div class="eyebrow">PRODUCTION BUDGET</div><h2>本轮预算已用尽</h2><p>' + h(decision.message || decision.reason || '制作已暂停，提高已耗尽的 GPU 用量或累计制作时间上限后可继续。') + '</p></div></div><button class="button primary" data-action="budget">调整预算并继续</button>';
  } else if (canReviseProduction(project)) {
    content = '<div class="decision-top"><div><div class="eyebrow">PRODUCTION REVISION</div><h2>调整指定分镜，继续制作</h2><p>镜头制作已暂停。选择要修改的分镜并说明调整目标，系统将评审修改后继续生产。</p><p class="tiny muted">' + h(decision.message || project.error || '') + '</p></div></div>' + productionRevisionForm(project, busy) + '<details><summary>已处理模型或服务故障？</summary><p class="tiny muted">原分镜不变时，可以重新检查当前素材与服务。相同输入已达到尝试上限时，请提交上方的局部返修。</p><button class="button small" data-action="project-action" data-command="retry" ' + (busy ? 'disabled' : '') + '>重试当前阶段</button></details>';
  } else {
    content = '<div class="decision-top"><div><div class="eyebrow">PRODUCTION EXCEPTION</div><h2>制作需要你的决定</h2><p>' + h(decision.message || decision.reason || project.error || '请查看项目记录，处理当前生产异常。') + '</p></div></div><form data-form="retry" class="feedback-form"><textarea name="feedback" data-preserve="exception-feedback" aria-label="处理意见" placeholder="填写此次调整或处理意见…" maxlength="3000"></textarea><button class="button primary" ' + (busy ? 'disabled' : '') + '>重试当前阶段</button></form>';
  }
  return '<section class="decision-section" data-decision-id="' + h(decision.id || '') + '" aria-label="待处理的出品人决策">' + content + '</section>';
}
export function eventsPanel(project) {
  const events = list(project.events).slice(-15).reverse();
  return '<section class="panel"><div class="panel-title"><h2>制作动态</h2><span class="muted tiny">最近 ' + events.length + ' 条</span></div>' + (events.length ? '<ol class="events">' + events.map(event => '<li><time>' + h(dateText(event.at, true)) + '</time><div class="event-text">' + h(event.message || event.kind || '') + '</div></li>').join('') + '</ol>' : '<p class="muted tiny">任务开始后，阶段进展与修复决定会记录在这里。</p>') + '</section>';
}
export function budgetPanel(project) {
  const budget = project.budget || {};
  const limit = numeric(budget.gpu_minutes);
  const spent = budget.spent_gpu_minutes == null ? null : numeric(budget.spent_gpu_minutes);
  const wall = numeric(budget.wall_minutes);
  const elapsed = project.production?.elapsed_seconds == null ? null : numeric(project.production.elapsed_seconds) / 60;
  return '<section class="panel"><div class="panel-title"><h2>项目生产预算</h2><span class="muted tiny">GPU 分钟</span></div><div class="budget-large">' + (spent === null ? '—' : spent.toFixed(1)) + '<small>/ ' + (limit || '未设') + '</small></div><div class="progress-track"><span style="width:' + (spent === null || !limit ? 0 : clamp(spent / limit * 100)) + '%"></span></div><div class="key-value"><span>累计制作时间</span><strong>' + (elapsed === null ? '—' : elapsed.toFixed(1)) + ' / ' + (wall || '未设') + ' 分钟</strong></div><div class="key-value"><span>每个素材版本最多尝试</span><strong>' + h(budget.max_attempts ?? '未设') + ' 次</strong></div><div class="key-value"><span>制作形式</span><strong>' + h(formatName(project.format)) + '</strong></div><div class="key-value"><span>目标时长</span><strong>' + h(project.duration_seconds ?? '—') + ' 秒</strong></div>' + (spent === null ? '<p class="tiny muted" style="margin-top:12px">尚未返回 GPU 实测用量。</p>' : '') + '</section>';
}
export function overviewPanel(project) {
  const selected = typeof project.selected_story === 'object' ? project.selected_story : list(project.proposals).find(item => item.id === project.selected_story);
  const text = value => typeof value === 'string' ? value.trim() : '';
  const script = project.script_review?.passed === true ? project.script : null;
  const goal = project.resolved_story_brief?.resolution_review?.passed === true ? project.resolved_story_brief : null;
  const scriptSummary = text(script?.logline) || text(script?.summary);
  const currentSummary = scriptSummary || text(goal?.logline);
  const proposalSummary = text(selected?.logline) || text(selected?.synopsis);
  const summarySource = scriptSummary ? '当前已通过剧本' : currentSummary ? '当前已通过创作目标' : '原始选题提案（历史参考）';
  const history = currentSummary && proposalSummary && currentSummary !== proposalSummary ? '<details><summary>原始选题提案（历史参考）</summary><p class="brief-text">' + h(proposalSummary) + '</p></details>' : '';
  const rights = { owned: '自有作品', licensed: '已获改编授权', public_domain: '公版素材', analysis_only: '仅用于研究分析' };
  const sources = list(project.sources || project.research?.sources);
  return '<div class="two-columns"><div class="stack"><section class="panel"><div class="panel-title"><h2>创作任务书</h2><span class="muted tiny">' + h(dateText(project.created_at)) + ' 建立</span></div><p class="brief-text">' + h(project.brief || '尚无创作方向') + '</p>' + (selected ? '<div class="section-heading"><h2>已选故事</h2></div><div class="selected-story"><h3>' + h(selected.title) + '</h3><p class="tiny muted">' + h(summarySource) + '</p><p class="brief-text">' + h(currentSummary || proposalSummary) + '</p>' + history + '</div>' : '') + '</section>' + eventsPanel(project) + '</div><div class="stack">' + budgetPanel(project) + '<section class="panel"><div class="panel-title"><h2>研究与素材来源</h2><span class="muted tiny">' + sources.length + ' 条</span></div>' + (sources.length ? '<div class="sources-list">' + sources.map(source => '<div class="source-item">' + (safeUrl(source.url) ? '<a href="' + h(safeUrl(source.url)) + '" target="_blank" rel="noopener noreferrer">' + h(source.title || source.url) + ' ↗</a>' : '<p>' + h(source.title || '未命名来源') + '</p>') + '<span>' + h(rights[source.rights] || source.rights || '用途尚未标注') + '</span></div>').join('') + '</div>' : '<p class="muted tiny">研究完成后显示实际检索来源；未标注改编授权的资料仅用于分析。</p>') + '</section></div></div>';
}
export function shotCards(project) {
  const shots = list(project.shots);
  if (!shots.length) return empty('分镜尚未生成', '故事与视觉方向确定后，导演会把剧本拆成可制作的逐镜计划。');
  return '<div class="shot-grid">' + shots.map((shot, index) => {
    const image = safeUrl(shot.image_url);
    const video = safeUrl(shot.video_url);
    const preview = image ? '<img loading="lazy" src="' + h(image) + '" alt="' + h(shot.description || shot.id) + '">' + (video ? '<span class="play-icon" aria-hidden="true">▶</span>' : '') : '<span class="shot-empty"><b>' + String(index + 1).padStart(2, '0') + '</b>' + (video ? '播放已生成镜头' : '等待首帧') + '</span>';
    return '<article class="shot-card">' + (image || video ? '<button class="shot-preview" data-action="preview" data-shot="' + h(shot.id) + '" aria-label="预览镜头 ' + h(shot.id) + '">' + preview + '</button>' : '<div class="shot-preview">' + preview + '</div>') + '<div class="shot-body"><div class="shot-top"><strong>' + h(shot.id) + ' · ' + h(shot.seconds ?? shot.duration_seconds ?? '—') + 's</strong>' + badge(shot.status) + '</div><p>' + h(shot.dramatic_beat || shot.description || shot.action || shot.prompt || '分镜描述待补充') + '</p><span class="muted tiny">已尝试 ' + attemptCount(shot) + ' 次</span>' + (shot.prompt ? '<details><summary>查看制作提示词</summary><p class="preserve-lines">' + h(shot.prompt) + '</p></details>' : '') + '</div></article>';
  }).join('') + '</div>';
}
export function scriptPanel(project) {
  const script = project.script;
  return '<div class="stack">' + (script && (typeof script !== 'object' || Object.keys(script).length) ? '<section class="panel script-panel"><h2>剧本与人物设定</h2>' + readable(script) + '</section>' : empty('剧本正在等待创作', '选定故事后，编剧会完成剧本、人物动机与场景对白。')) + '<section><div class="section-heading"><h2>逐镜分镜</h2><span>' + list(project.shots).length + ' 个镜头</span></div>' + shotCards(project) + '</section></div>';
}
export function qualityPanel(project) {
  const shots = list(project.shots);
  const passed = shots.filter(shot => ['passed', 'approved', 'completed', 'done'].includes(shot.status) || (safeUrl(shot.video_url) && (shotQuality(shot)?.passed === true || shotQuality(shot)?.status === 'pass'))).length;
  const retried = shots.filter(hasRetry).length;
  const failed = shots.filter(shot => ['failed', 'rejected', 'error'].includes(shot.status)).length;
  return '<div class="stats-row"><div class="stat"><span class="stat-label">镜头计划</span><div class="stat-value">' + shots.length + '<small>镜</small></div></div><div class="stat"><span class="stat-label">通过质检</span><div class="stat-value">' + passed + '<small>镜</small></div></div><div class="stat"><span class="stat-label">进行过返修</span><div class="stat-value">' + retried + '<small>镜</small></div></div><div class="stat"><span class="stat-label">当前失败</span><div class="stat-value">' + failed + '<small>镜</small></div></div></div><section class="panel"><div class="panel-title"><h2>镜头质量与重试记录</h2><span class="muted tiny">按镜头保留每次生产记录</span></div>' + (shots.length ? '<div class="table-scroll"><table class="quality-table"><thead><tr><th>镜头</th><th>制作状态</th><th>尝试次数</th><th>质量结论与修复</th></tr></thead><tbody>' + shots.map(shot => '<tr><td>' + h(shot.id) + '</td><td>' + badge(shot.status) + '</td><td>' + attemptCount(shot) + ' 条记录<br><small class="muted">每个素材版本最多 ' + h(project.budget?.max_attempts ?? '—') + ' 次</small>' + '</td><td>' + (shotQuality(shot) ? readable(shotQuality(shot)) : '<span class="muted">尚无评估结果</span>') + (Array.isArray(shot.attempts) && shot.attempts.length ? '<details><summary>展开尝试记录</summary>' + readable(shot.attempts) + '</details>' : '') + '</td></tr>').join('') + '</tbody></table></div>' : empty('尚无镜头质量记录', '实际渲染后，系统会记录通过项、失败原因与后续修复策略。')) + '</section><div class="section-heading"><h2>生产实况</h2></div>' + eventsPanel(project);
}
export function deliveryPanel(project) {
  const delivery = project.delivery || {};
  const video = safeUrl(delivery.video_url);
  const files = deliveryFiles(delivery);
  return '<section class="panel">' + (video ? '<div class="delivery-layout"><video class="delivery-video" controls playsinline preload="metadata" src="' + h(video) + '"></video><div class="delivery-info"><div class="eyebrow">FILM DELIVERY</div><h2>' + h(project.title) + '</h2><p>' + (project.is_validation ? '这是自动化验收项目，审片操作用于验证流程。素材保留供检查。' : ['completed', 'approved'].includes(project.status) ? '已完成出品人验收。成片与制作资料可在此下载。' : '当前制作版本。完成审片决定后，将进入正式交付。') + '</p><a class="button primary" href="' + h(video) + '" download>下载完整 MP4 <span aria-hidden="true">↓</span></a><div class="download-list">' + downloadLinks(files) + '</div></div></div>' : '<div class="eyebrow">FILM DELIVERY</div>' + empty('完整成片尚未交付', '镜头、对白、字幕与剪辑完成后，成片会出现在这里。') + (files.length ? '<div class="download-list">' + downloadLinks(files) + '</div>' : '')) + '</section>';
}
export function deliveryFiles(delivery) {
  const raw = [...list(delivery.files), ...list(delivery.artifacts)];
  if (delivery.manifest_url) raw.push({ name: '制作清单 · manifest.json', url: delivery.manifest_url });
  const seen = new Set();
  return raw.map(item => {
    const file = typeof item === 'string' ? { path: item } : item || {};
    const path = typeof file.path === 'string' ? file.path : '';
    let url = safeUrl(file.url) || safeUrl(file.download_url);
    if (!url && /^production\//.test(path) && !path.split('/').some(part => ['.', '..'].includes(part))) url = safeUrl('/media/' + path.split('/').map(encodeURIComponent).join('/'));
    if (!url && /^(https?:\/\/|\/media\/)/i.test(path)) url = safeUrl(path);
    return { ...file, url, name: file.name || file.title || path.split('/').pop() || '制作文件' };
  }).filter(file => { if (!file.url || seen.has(file.url)) return false; seen.add(file.url); return true; });
}
function downloadLinks(files) {
  return files.map(file => '<a class="download-item" href="' + h(safeUrl(file.url || file.download_url || file.path)) + '" target="_blank" rel="noopener noreferrer"><span>' + h(file.name || file.title || '制作文件') + '</span><small>' + (file.bytes ? (numeric(file.bytes) / 1048576).toFixed(1) + ' MB · ' : '') + '下载 ↗</small></a>').join('');
}
