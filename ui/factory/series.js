import { h, list, safeUrl, readable } from './format.js';

export const assetStates = { planned: '计划中', generated: '已登记图片', reviewed: '已技术验证', approved: '已人工批准' };
const kinds = { character: '角色', scene: '场景', prop: '道具' };
export function seriesFormKey(project, operation, asset = {}) {
  return [project.id, operation, asset.asset_id || '', asset.version || ''].join(':');
}
export function seriesPanel(project, drafts = {}) {
  const library = project.series_library;
  if (!project.series_key) return '<section class="panel"><h2>系列资产库</h2><p>此项目使用原有单集素材格式。创建新剧集时填写相同的系列标识，可建立并复用正式系列资产。</p></section>';
  if (!library) return '<section class="panel"><h2>系列资产库</h2><p>' + h(project.series_error || '正在读取系列设定与已批准素材…') + '</p></section>';
  const form = (operation, asset, fields, label) => {
    const key = seriesFormKey(project, operation, asset), draft = drafts[key];
    const attributes = ' data-series-operation="' + h(operation) + '" data-series-key="' + h(key) + '" data-revision="' + h(draft?.expected_revision ?? project.revision) + '" data-asset="' + h(asset.asset_id || '') + '" data-version="' + h(asset.version || 0) + '" data-asset-revision="' + h(draft?.expected_asset_revision ?? asset.revision ?? 0) + '" data-expected-version="' + h(draft?.expected_version ?? (operation === 'save_bible' ? library.bible?.version || 0 : asset.version || 0)) + '"';
    return '<form class="series-form"' + attributes + '>' + fields(draft?.values || {}) + '<button class="button small primary" type="submit">' + h(label) + '</button>' + (draft ? '<button class="button small" type="button" data-series-discard="' + h(key) + '">放弃草稿并读取新版本</button>' : '') + '</form>';
  };
  const emptyBible = { schema_version: 1, world: '', visual_rules: '', characters: [{ character_id: 'character_01', name: '', identity_constraints: '', default_wardrobe: '' }], scenes: [], props: [] };
  const bible = library.bible;
  const biblePanel = '<section class="panel"><h2>Series Bible · ' + h(project.series_key) + '</h2><p>系列最新版本 ' + h(bible?.version || '未建立') + '；本剧集固定版本 ' + h(library.pinned_bible?.version || '未绑定') + '。发布新版本不会自动改写旧剧集。</p>' + (bible ? '<details open><summary>世界观与视觉规范</summary><p>' + h(bible.document.world) + '</p><p>' + h(bible.document.visual_rules) + '</p>' + readable({ characters: bible.document.characters, scenes: bible.document.scenes, props: bible.document.props }) + '</details>' : '') + '<details><summary>编辑并追加设定版本</summary><p>身份外形约束与默认服饰分开填写。逐镜服饰、手持物、位置及剧情状态不改写角色身份。</p>' + form('save_bible', {}, values => '<label>正式设定 JSON<textarea name="document" rows="14" maxlength="60000" required spellcheck="false">' + h(values.document ?? JSON.stringify(bible?.document || emptyBible, null, 2)) + '</textarea></label>', '保存新的 Bible 版本') + '</details></section>';
  const entities = bible ? Object.entries(kinds).flatMap(([kind, label]) => list(bible.document[{ character: 'characters', scene: 'scenes', prop: 'props' }[kind]]).map(entity => ({ kind, id: entity.character_id || entity.entity_id, label: label + ' · ' + entity.name }))) : [];
  const planPanel = bible ? '<section class="panel"><h2>计划新参考资产</h2><p>登记当前项目素材 → 技术验证 → 明确批准。不会自动调用任何生成服务。</p>' + form('plan', {}, values => '<label>系列实体<select name="entity" required>' + entities.map(entity => '<option value="' + h(entity.kind + ':' + entity.id) + '" ' + (values.entity === entity.kind + ':' + entity.id ? 'selected' : '') + '>' + h(entity.label) + '</option>').join('') + '</select></label>', '建立计划素材') + '</section>' : '';
  const sources = list(library.sources), shots = list(project.shots), cast = Object.keys((project.episode || project.script || {}).cast || {});
  const cards = list(library.assets).map(asset => {
    const receipt = asset.data?.receipt, path = typeof receipt?.path === 'string' ? safeUrl('/media/' + receipt.path) : '';
    let controls = '';
    if (asset.status === 'planned') controls = sources.length ? form('register', asset, values => '<label>当前项目已登记图片<select name="source_key" required>' + sources.map(source => '<option value="' + h(source.key) + '" ' + (values.source_key === source.key ? 'selected' : '') + '>' + h(source.key) + '</option>').join('') + '</select></label>', '复制为独立参考版本') : '<p>当前项目还没有可登记的图片回执。</p>';
    if (asset.status === 'generated' || asset.status === 'reviewed') controls += form('review', asset, () => '<p>检查文件、校验值、可解码性及基础图像质量。</p>', '执行技术验证');
    if (asset.status === 'reviewed' && asset.data?.review?.technical?.status === 'pass') controls += form('approve', asset, values => '<label><input type="checkbox" name="confirmed" required ' + (values.confirmed === 'on' ? 'checked' : '') + '>我已审看并确认此版本可作为正式复用参考；技术检查不能保证人物或艺术质量。</label>', '明确批准此版本');
    if (asset.status === 'approved' && shots.length) controls += '<details><summary>引用到指定镜头</summary>' + form('bind', asset, values => '<fieldset><legend>选择实际使用此资产的镜头</legend>' + shots.map(shot => '<label><input type="checkbox" name="shot_ids" value="' + h(shot.id) + '" ' + (list(values.shot_ids).includes(shot.id) ? 'checked' : '') + '>' + h(shot.id) + '</label>').join('') + '</fieldset>' + (asset.kind === 'character' ? '<label>对应本剧角色<select name="role">' + cast.map(role => '<option value="' + h(role) + '" ' + ((values.role || asset.entity_id) === role ? 'selected' : '') + '>' + h(role) + '</option>').join('') + '</select></label>' : '<input type="hidden" name="role" value="' + h(asset.entity_id) + '">') + '<p>仅暂停或决策中的项目可修改。引用改变后会保留旧素材，恢复制作时重新核验云费用授权。</p>', '固定引用此版本') + '</details>';
    if (asset.status === 'approved') controls += form('plan', asset, () => '<input type="hidden" name="entity" value="' + h(asset.kind + ':' + asset.entity_id) + '"><p>换参考图必须创建新版本；此批准版本不可覆盖。</p>', '计划下一个版本');
    return '<article class="panel series-asset"><header><h3>' + h(asset.data?.entity?.name || asset.entity_id) + ' · v' + h(asset.version) + '</h3><span>' + h(kinds[asset.kind]) + ' / ' + h(assetStates[asset.status] || asset.status) + '</span></header>' + (path ? '<img src="' + h(path) + '" alt="' + h(asset.data?.entity?.name || '系列参考图') + '" loading="lazy">' : '') + '<small>' + h(asset.asset_id) + '<br>' + h(receipt?.sha256 || '尚无媒体校验值') + '</small>' + (asset.data?.review ? '<details><summary>验证记录</summary>' + readable(asset.data.review) + '</details>' : '') + controls + (list(asset.affected_shots).length ? '<details><summary>采用此版本会影响的旧引用</summary>' + readable(asset.affected_shots) + '</details>' : '') + '</article>';
  }).join('');
  return '<div class="stack series-workbench">' + biblePanel + planPanel + '<div class="series-grid">' + cards + '</div>' + (library.truncated ? '<p>当前显示最近256个资产版本，未显示版本仍保留在数据库和固定引用中。</p>' : '') + '<section class="panel"><h2>本剧固定引用</h2>' + readable(library.bindings || {}) + '</section></div>';
}
