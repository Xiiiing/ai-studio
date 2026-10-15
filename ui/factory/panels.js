import { h, list, words, numeric, clamp, safeUrl, dateText, badge, pendingDecisions, canReviseProduction, readable, formatName, fieldNames } from './format.js';
const empty = (title, message) => '<div class="empty-state"><h3>' + h(title) + '</h3><p>' + h(message) + '</p></div>';
const attemptCount = shot => Array.isArray(shot.attempts) ? shot.attempts.length : numeric(shot.attempts);
const shotQuality = shot => shot.quality ?? shot.qa;
const hasRetry = shot => Array.isArray(shot.attempts) ? shot.attempts.some(attempt => numeric(attempt.attempt) > 1) : numeric(shot.attempts) > 1;
export { decisionCenter as decisionPanel } from './decision-center.js';
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
