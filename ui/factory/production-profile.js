import { h } from './format.js';

export function productionProfileFields(catalog) {
  const presets = catalog?.presets || { legacy15: { label: '经典短剧 · 15 镜 / 60–90 秒' } };
  return '<fieldset class="source-options"><legend>创作规格</legend><div class="field"><label for="production-preset">镜头与角色规格</label><select id="production-preset" name="production_preset">' + Object.entries(presets).map(([key, value]) => '<option value="' + h(key) + '" ' + (key === 'legacy15' ? 'selected' : '') + '>' + h(value.label) + '</option>').join('') + '</select><small>新规格使用预设片长；下方目标时长仅用于经典15镜。实际镜头参数仍须符合所选模型。</small></div><details><summary>指定逐镜时长与稳定角色 / 可选</summary><div class="field"><label>逐镜秒数<input name="profile_shot_seconds" placeholder="例如 4,5,6,4,5,6"><small>填写后明确确定镜头数和总时长；新增规格支持3–24镜、总长15–90秒，每镜3–10秒。</small></label></div><div class="field"><label>人物规格 JSON<textarea name="profile_characters" rows="4" maxlength="5000" placeholder="[{&quot;character_id&quot;:&quot;char_lead&quot;,&quot;name&quot;:&quot;主角&quot;,&quot;voice_id&quot;:&quot;female&quot;}]"></textarea></label><small>voice_id 是已登记声音的别名，不代表人物性别；留空使用预设角色。参考图用已批准的 reference_asset 固定版本。</small></div></details></fieldset>';
}

export function applyProductionProfile(payload, values, catalog) {
  const preset = String(values.production_preset || 'legacy15');
  if (preset !== 'legacy15' && !catalog?.presets?.[preset]) throw new Error('创作规格尚未同步，请刷新后选择。');
  const profile = { schema_version: 1, preset };
  const plan = String(values.profile_shot_seconds || '').trim();
  if (plan) {
    const seconds = plan.split(/[,，\s]+/).map(Number);
    if (seconds.some(value => !Number.isFinite(value) || value <= 0)) throw new Error('逐镜时长需要正数，使用逗号分隔。');
    profile.shot_seconds = seconds;
    profile.shot_count = seconds.length;
    profile.duration_seconds = seconds.reduce((sum, value) => sum + value, 0);
  }
  const characters = String(values.profile_characters || '').trim();
  if (characters) {
    try { profile.characters = JSON.parse(characters); }
    catch { throw new Error('人物规格不是有效 JSON。'); }
    if (!Array.isArray(profile.characters) || !profile.characters.length) throw new Error('人物规格必须是非空数组。');
  }
  if (preset !== 'legacy15' || plan) delete payload.duration_seconds;
  payload.production_profile = profile;
  return payload;
}
