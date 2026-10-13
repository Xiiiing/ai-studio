import { h, list, badge, safeUrl } from './format.js';
import { money } from './cloud-budget.js';

const text = value => h(typeof value === 'object' ? JSON.stringify(value, null, 2) : value ?? '尚未记录');
const media = candidate => {
  const url = safeUrl(candidate.media_url);
  if (!url) return '<div class="candidate-empty">尚无可预览素材</div>';
  const identity = h(candidate.stage + ':' + candidate.take + ':' + url);
  return candidate.stage === 'video' ? '<video data-dom-key="' + identity + '" controls playsinline preload="metadata" src="' + h(url) + '"></video>' : '<img loading="lazy" src="' + h(url) + '" alt="候选 ' + h(candidate.take) + '">';
};
function candidateCard(project, shot, candidate, revision, editable, draft) {
  revision = draft?.revision ?? revision;
  const cost = candidate.cost;
  const price = cost ? '估算 ' + money(cost.estimated_cost_micros, cost.currency) + ' / 已确认 ' + money(cost.confirmed_cost_micros, cost.currency) : '无已登记云费用';
  return '<article class="candidate-card" data-dom-key="candidate:' + h(candidate.id) + '"><header><strong>' + h(candidate.take) + ' · ' + (candidate.stage === 'image' ? '画面' : '视频') + '</strong>' + badge(candidate.status) + '</header>' + media(candidate) + '<p>' + (candidate.producer_selected ? '<strong>出品人已选定</strong>' : candidate.plan_current ? '当前参数候选' : '历史参数候选') + '</p><dl class="data-list"><div><dt>模型</dt><dd>' + text(candidate.model || candidate.receipt?.model) + '</dd></div><div><dt>费用</dt><dd>' + h(price) + '</dd></div></dl><details><summary>评分、失败原因与生成参数</summary><pre>' + text(candidate.qa) + '</pre>' + (candidate.error ? '<p class="form-error">' + text(candidate.error) + '</p>' : '') + '<pre>' + text(candidate.parameters) + '</pre><p>' + text(candidate.prompt) + '</p></details><form data-shot-operation="select" data-project="' + h(project.id) + '" data-shot="' + h(shot.id) + '" data-stage="' + h(candidate.stage) + '" data-take="' + h(candidate.take) + '" data-revision="' + h(revision) + '"><label><input type="checkbox" name="confirmed" ' + (draft?.values.confirmed === 'on' ? 'checked' : '') + ' required ' + (!editable || !candidate.selectable ? 'disabled' : '') + '>确认采用此候选，原素材保留</label><button class="button small" ' + (!editable || !candidate.selectable ? 'disabled' : '') + '>设为选定素材</button></form></article>';
}
export function shotWorkbenchView(state) {
  const project = state.project, board = project?.workbench;
  if (!board) return '<section class="panel"><h2>镜头制作台</h2><p>正在读取镜头与候选回执。</p></section>';
  const shots = list(board.shots), shot = shots.find(row => row.id === state.shotId) || shots[0];
  if (!shot) return '<section class="panel"><h2>镜头制作台</h2><p>剧本生成后显示逐镜计划。</p></section>';
  const id = project.id + ':' + shot.id;
  const draft = state.shotDrafts?.[id], revision = draft?.revision ?? board.revision;
  const values = draft?.values || shot;
  const disabled = !board.editable || state.busy;
  const stale = draft && draft.revision !== board.revision;
  const field = (name, label, max) => '<label>' + label + '<textarea name="' + name + '" rows="4" maxlength="' + max + '" data-preserve="shot:' + h(id + ':' + revision + ':' + name) + '" required>' + h(values[name] || shot[name] || (name === 'motion_prompt' ? shot.prompt : '') || '') + '</textarea></label>';
  const camera = values.camera_motion || shot.camera_motion || 'hold';
  const listHtml = '<aside class="shot-list" aria-label="镜头列表" data-dom-key="shot-list">' + shots.map(row => '<button class="shot-list-item ' + (row.id === shot.id ? 'active' : '') + '" data-shot-open="' + h(row.id) + '" aria-current="' + (row.id === shot.id ? 'true' : 'false') + '"><strong>' + h(row.id) + ' · ' + h(row.seconds) + ' 秒</strong><span>' + h(row.dramatic_beat || row.action || row.prompt) + '</span>' + badge(row.status) + '</button>').join('') + '</aside>';
  const errors = list(board.errors).length ? '<p class="notice error" role="alert">部分候选回执不可用：' + text(board.errors) + '</p>' : '';
  return '<section class="shot-workbench" data-dom-key="shot-workbench">' + listHtml + '<div class="shot-detail" data-dom-key="detail:' + h(shot.id) + '"><header><h2>' + h(shot.id) + ' · ' + h(shot.dramatic_beat || shot.action || '镜头详情') + '</h2><p>镜头版本 ' + h(shot.shot_revision) + ' / 制作版本 ' + h(board.production_revision) + ' · ' + h(shot.seconds) + ' 秒</p></header><dl class="data-list"><div><dt>人物</dt><dd>' + text(shot.cast || shot.characters || shot.reference_group) + '</dd></div><div><dt>参考资产</dt><dd>' + text(shot.asset_references) + '</dd></div></dl><details data-flow-detail="shot-editor:' + h(id) + '" open><summary>单镜精修</summary>' + (!board.editable ? '<p class="notice">先暂停制作并等待当前步骤结束，再修改或选片。</p>' : '') + '<form data-shot-operation="edit" data-project="' + h(project.id) + '" data-shot="' + h(shot.id) + '" data-revision="' + h(revision) + '">' + field('prompt', '首帧提示词', 1600) + field('motion_prompt', '视频提示词', 800) + '<label>程序运镜<select name="camera_motion">' + Object.entries({ hold: '静止', push_in: '推进', pull_out: '拉远', pan_left: '左移', pan_right: '右移' }).map(([key, label]) => '<option value="' + key + '" ' + (camera === key ? 'selected' : '') + '>' + label + '</option>').join('') + '</select></label><label>重制范围<select name="stage"><option value="image" ' + (values.stage !== 'video' ? 'selected' : '') + '>首帧及依赖视频</option><option value="video" ' + (values.stage === 'video' ? 'selected' : '') + '>仅视频</option></select></label><label><input type="checkbox" name="regenerate" ' + (values.regenerate === 'on' ? 'checked' : '') + '>即使参数相同，也明确创建新的生成版本</label>' + (stale ? '<p class="form-error">项目已更新，草稿仍绑定旧版本。请核对后重新编辑。</p><button type="button" class="button" data-shot-discard="' + h(id) + '">放弃草稿并读取新版本</button>' : '') + '<button class="button primary" ' + (disabled || stale ? 'disabled' : '') + '>保存局部制作版本</button><small>不直接调用收费 API；保存后恢复制作，新的云请求仍须单独授权。镜长和模型参数在本阶段只读。</small></form></details>' + errors + '<h3>候选比较 · ' + list(shot.candidates).length + '</h3>' + (shot.truncated ? '<p class="notice">仅展示每类最新 40 个候选，历史文件仍保留。</p>' : '') + '<div class="candidate-grid">' + list(shot.candidates).map(value => candidateCard(project, shot, value, board.revision, !disabled, state.shotDrafts?.[id + ':' + value.id])).join('') + '</div></div></section>';
}
