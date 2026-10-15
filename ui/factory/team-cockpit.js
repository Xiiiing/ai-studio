import { h, list, safeUrl, dateText } from './format.js';

const NAMES = { researcher: '选题研究', creator: '故事策划', story_editor: '故事统筹', screenwriter: '编剧', storyboard_director: '分镜导演', visual_designer: '美术', independent_critic: '独立审稿', image_generator: '图像制作', video_generator: '视频制作', visual_critic: '画面质检', editor: '后期剪辑', producer: '出品人', executive_producer: '总制片代理' };
const STATES = { pending: ['待执行', 'neutral'], running: ['执行中', 'attention'], succeeded: ['执行已返回', 'success'], blocked: ['需处理', 'danger'], failed: ['执行失败', 'danger'], waiting: ['等待外部任务', 'attention'], paused: ['已暂停', 'neutral'], interrupted: ['中断未完成', 'attention'], superseded: ['已失效', 'neutral'], recorded: ['已记录', 'neutral'], not_run: ['未执行', 'neutral'], human_required: ['需人工核验', 'attention'], passed: ['已通过本项检查', 'success'] };
const FIELDS = { brief: '创作需求', submitted_sources: '研究资料', research: '选题研究', sources: '来源记录', selected_story: '已选故事', producer_feedback: '出品人意见', series_bible: '系列设定', proposals: '候选故事', creation_review: '独立提案审核', script: '剧本', script_review: '独立剧本审核', styles: '视觉方案', style_previews: '视觉预览', selected_style: '已选画风', cloud_authorization: '云端费用授权', shots: '镜头素材', audio: '声音素材', delivery: '成片文件', technical_checks: '技术检查', revision_request: '返修要求', approved_assets: '已通过素材', revised_script: '修订剧本', edit_plan: '重剪方案' };
const label = role => Object.hasOwn(NAMES, role) ? NAMES[role] : role || '尚未分配';
const fieldLabels = values => list(values).map(value => Object.hasOwn(FIELDS, value) ? FIELDS[value] : value);
function badge(status) { const [text, tone] = Object.hasOwn(STATES, status) ? STATES[status] : ['状态未记录', 'neutral']; return '<span class="badge ' + tone + '">' + text + '</span>'; }
function textList(values) { return list(values).slice(0, 12).map(value => h(String(value))).join(' · ') || '尚未记录'; }

export function supervisorSupport(inventory) {
  const role = list(inventory?.roles).find(row => row?.role === 'executive_producer');
  const supported = !!role;
  const configured = !!(role && role.configured === true && role.enabled !== false && typeof role.profile === 'string' && role.profile && inventory?.role_assignments?.executive_producer === role.profile);
  return { supported, configured };
}
export function applyTeamMode(payload, values, inventory) {
  const mode = values.team_mode || 'guided';
  if (!['guided', 'delegated'].includes(mode)) throw new Error('团队模式无效，请重新选择。');
  if (mode === 'delegated' && !supervisorSupport(inventory).configured) throw new Error('请先在模型与连接中保存启用的总制片代理绑定，再选择委托模式。');
  payload.team_mode = mode;
  return payload;
}
export function teamModeFields(inventory) {
  const support = supervisorSupport(inventory);
  return '<section class="field team-mode-field"><label for="project-team-mode">团队协作方式</label><select id="project-team-mode" name="team_mode"><option value="guided" selected>人工把关：我选择故事、视觉并审核终片</option><option value="delegated" ' + (!support.configured ? 'disabled' : '') + '>委托总制片：自动选择合格创意，终片由我审核</option></select><small>' + (!support.supported ? '当前后端尚未启用总制片功能，保留人工关卡。' : !support.configured ? '先在“模型与连接”为总制片代理显式绑定已启用的连接。' : '委托仅包含故事和视觉选择；付费视频授权、预算调整及终片发布仍由你确认。总制片文本请求可能按所选模型计费。') + '</small></section>';
}
function tasks(flow) {
  const board = flow.task_board;
  if (board?.schema !== 'ai-studio.team-tasks.v1') return '<section class="cockpit-block"><h3>团队任务</h3><p class="muted">当前后端尚无任务工单记录，下方仍可查看已有成员回执。</p></section>';
  const rows = list(board.tasks).filter(row => row && typeof row === 'object').slice(0, 12);
  return '<section class="cockpit-block"><div class="panel-title"><h3>团队任务</h3><span class="muted tiny">节点返回 ≠ 审核通过</span></div><div class="team-task-grid">' + rows.map(row => {
    const record = row.current === true && row.record && typeof row.record === 'object' ? row.record : null;
    const status = record ? row.status : 'pending';
    const dependencies = list(row.depends_on).map(id => rows.find(peer => peer.id === id)?.label || id);
    return '<article class="team-task" data-team-task="' + h(row.id) + '"><div><h4>' + h(row.label || row.id) + '</h4>' + badge(status) + '</div><p>负责：<strong>' + h(label(row.owner)) + '</strong> · 审核职责：' + h(label(row.reviewer)) + '</p><dl><dt>接收自</dt><dd>' + (dependencies.length ? textList(dependencies) : '出品人的需求') + '</dd><dt>输入</dt><dd>' + textList(fieldLabels(row.inputs)) + '</dd><dt>交付</dt><dd>' + textList(fieldLabels(row.outputs)) + '</dd></dl>' + (record ? '<small>第 ' + h(Number.isSafeInteger(record.attempt) ? record.attempt : '—') + ' 次执行' + (record.started_at ? ' · ' + h(dateText(record.started_at, true)) : '') + '</small>' : '<small>' + (row.previous_run ? '旧版本记录已保留，当前版本尚未执行。' : '尚无当前任务执行记录。') + '</small>') + '</article>';
  }).join('') + '</div></section>';
}
function supervisor(flow) {
  const info = flow.supervision;
  const delegated = info?.mode === 'delegated';
  const rows = list(info?.records).filter(row => row && typeof row === 'object').slice(0, 3);
  return '<section class="cockpit-block supervisor-panel"><div class="panel-title"><h3>总制片与独立审核</h3><span class="badge neutral">' + (delegated ? '已委托创意选择' : '人工把关模式') + '</span></div><p class="muted tiny">独立审稿不能被总制片覆盖。这里的建议依据文字和已有记录，不代表已观看成片。</p>' + (rows.length ? rows.map(row => {
    const state = { selected: '已作授权选择', advisory: '终审建议已记录', manual: '已交回人工', requested: '请求已记录，完成情况未确认', superseded: '决定已失效' }[row.status] || '状态未记录';
    const proposal = row.proposal && typeof row.proposal === 'object' ? row.proposal : {};
    return '<article class="supervisor-record"><div><strong>' + h({ story: '故事方向', style: '视觉方向', final: '终片建议' }[row.kind] || row.kind) + '</strong><span>' + h(state) + (row.current_gate_version !== true ? ' · 历史版本' : '') + '</span></div>' + (proposal.choice ? '<p>选择：' + h(proposal.choice) + '</p>' : '') + (proposal.rationale ? '<p>' + h(proposal.rationale) + '</p>' : '<p class="muted">尚无可用的总制片结论，继续保留人工处理入口。</p>') + (list(proposal.risks).length ? '<p class="supervisor-risk">待查：' + textList(proposal.risks) + '</p>' : '') + '<small>请求模型：' + h(row.requested_model || '未记录') + (row.reason_code ? ' · ' + h(row.reason_code) : '') + '</small></article>';
  }).join('') : '<p class="muted">尚无总制片决策记录；专业成员继续按原流程执行。</p>') + '<button class="button small" data-action="tab" data-tab="overview">查看需要我处理的决策</button></section>';
}
function delivery(flow, stale) {
  const info = flow.delivery_readiness;
  const matching = info?.schema === 'ai-studio.delivery-readiness.v1' && info.project_id === flow.project_id && info.project_revision === flow.revision;
  if (!matching) return '<section class="cockpit-block"><h3>交付与发布检查</h3><p class="muted">尚无与当前版本匹配的交付检查，请在成片页查看已有素材。</p></section>';
  const rows = list(info.checks).filter(row => row && typeof row === 'object').slice(0, 12);
  const files = list(info.artifacts).filter(row => row && typeof row === 'object').slice(0, 32);
  const prefix = /^[a-f0-9]{12}$/.test(flow.project_id) ? '/media/production/factory-' + flow.project_id + '/release/' : null;
  return '<section class="cockpit-block delivery-readiness"><div class="panel-title"><h3>交付与发布检查</h3><span class="badge attention">平台审核未核验</span></div><ul class="handoff-checks">' + rows.map(row => '<li><span>' + h(row.label || row.id) + '</span>' + badge(row.status) + (list(row.missing).length ? '<small>缺少：' + textList(row.missing) + '</small>' : '') + '</li>').join('') + '</ul><p class="muted tiny">在线记录不读取媒体；材料齐全后仍须重新验收文件和音画。工程验收不等于红果审核通过。</p>' + (files.length ? '<details data-flow-detail="delivery-materials"><summary>交付材料 · ' + files.length + ' 项</summary><div class="handoff-files">' + files.map(file => {
    const raw = typeof file.url === 'string' ? file.url : '';
    const relative = prefix && raw.startsWith(prefix) ? raw.slice(prefix.length) : '';
    const allowed = /^v[1-9][0-9]*-[a-f0-9]{12}(?:-retry-[0-9]+)?\/[A-Za-z0-9][A-Za-z0-9_.-]{0,100}$/.test(relative);
    const url = !stale && allowed ? safeUrl(raw) : '';
    return '<div>' + (url ? '<a href="' + h(url) + '" target="_blank" rel="noopener noreferrer">' + h(file.label || file.name) + ' ↗</a>' : '<span>' + h(file.label || file.name) + '</span>') + '<small>已记录回执，未重新校验</small></div>';
  }).join('') + '</div></details>' : '') + '<details data-flow-detail="platform-checklist"><summary>出品方发布前待办</summary><ul class="handoff-checks">' + list(info.platform_checklist).filter(row => row && typeof row === 'object').slice(0, 8).map(row => '<li><span>' + h(row.label || row.id) + '</span>' + badge('human_required') + '</li>').join('') + '</ul></details></section>';
}
export function teamCockpit(flow, { stale = false } = {}) {
  if (!flow || typeof flow !== 'object') return '';
  return '<section class="team-cockpit" aria-label="团队指挥台">' + (stale ? '<p class="notice" role="status">以下是上一次读取的团队记录，当前版本正在同步；旧交付链接已停用。</p>' : '') + tasks(flow) + '<div class="cockpit-review-grid">' + supervisor(flow) + delivery(flow, stale) + '</div></section>';
}
