import { h, list, safeUrl, pendingDecisions, canReviseProduction, dateText } from './format.js';
import { icon, emptyState } from './console-ui.js';

function ownedMedia(project, raw) {
  if (typeof raw !== 'string') return '';
  if (raw.startsWith('/media/')) return safeUrl(raw);
  if (raw.startsWith('production/factory-' + project.id + '/') && !/[\\%?#:\u0000]/.test(raw) && !raw.split('/').some(p => p === '..' || p === '.')) return safeUrl('/media/' + raw);
  return '';
}
export function reviewIssues(project) {
  if (Array.isArray(project?.operator?.attention?.issues)) return project.operator.attention.issues;
  const out=[],seen=new Set();let visited=0;
  const walk=(v,depth=0)=>{
    if(depth>6 || ++visited>150 || out.length>=24)return;
    if(Array.isArray(v)){v.slice(0,30).forEach(x=>walk(x,depth+1));return;}
    if(!v || typeof v!=='object')return;
    const description=typeof v.description==='string'?v.description:typeof v.detail==='string'?v.detail:'';
    if(description&&!seen.has(description)){seen.add(description);out.push({description,severity:v.severity||'blocking',suggestion:typeof v.suggestion==='string'?v.suggestion:'',evidence:typeof v.evidence==='string'?v.evidence:'',shot_ids:list(v.shot_ids)});}
    ['issues','review','details','blockers'].forEach(k=>{if(v[k])walk(v[k],depth+1);});
    if(Array.isArray(v.rounds)&&v.rounds.length)walk(v.rounds.at(-1),depth+1);
  };
  walk(project?.needs_decision);
  if(!out.length)walk(project?.selected_story?project?.script_review:project?.creation_review);
  return out;
}
export function suggestedFeedback(project) {
  if(project?.operator?.attention?.suggested_feedback)return project.operator.attention.suggested_feedback;
  const issues=reviewIssues(project),significant=issues.filter(i=>['blocking','major'].includes(i.severity));
  return issues.length?('请按以下审核意见修改，并保留未涉及的角色和剧情。修改后重新独立审核：\n'+(significant.length?significant:issues).map(i=>(list(i.shot_ids).length?list(i.shot_ids).join('、')+'：':'')+(i.suggestion||i.description)).join('\n')).slice(0,3900):'';
}
export function issueCards(project) {
  const issues=reviewIssues(project);
  return issues.length?'<div class="decision-issues" aria-label="审核问题">'+issues.map((issue,i)=>'<article class="decision-issue"><div class="issue-top"><span class="issue-index">'+String(i+1).padStart(2,'0')+'</span><strong>'+h({blocking:'阻断项',major:'主要问题',minor:'建议优化'}[issue.severity]||'待处理问题')+'</strong>'+list(issue.shot_ids).map(id=>'<button type="button" class="shot-tag" data-action="inspect-shot" data-shot="'+h(id)+'">'+h(id)+'</button>').join('')+'</div><p>'+h(issue.description)+'</p>'+(issue.suggestion?'<div class="issue-recommendation"><span>建议修改</span><p>'+h(issue.suggestion)+'</p></div>':'')+(issue.evidence?'<details><summary>查看依据</summary><p>'+h(issue.evidence)+'</p></details>':'')+'</article>').join('')+'</div>':'<p class="muted">没有返回结构化问题，可根据上方原因补充处理意见。</p>';
}
function selectedOptions(project, decision) {
  const kind=decision.type,allowed=new Set(list(decision.options).map(x=>typeof x==='string'?x:x.id));
  return list(kind==='story'?project.proposals:project.styles).filter(v=>(!allowed.size||allowed.has(v.id)) && (kind!=='story'||v.eligible!==false));
}
function choiceCards(project, decision, busy) {
  const options=selectedOptions(project,decision),style=decision.type==='style';
  if(!options.length)return emptyState('当前没有可选择的合格方案','候选方案和当前关卡不一致，或尚未通过审核。请刷新查看状态，不会跳过审核继续制作。','<button class="button" data-action="refresh">重新同步</button>');
  return '<div class="decision-options">'+options.map((item,i)=>{
    const preview=style?ownedMedia(project,item.preview_url||item.image_url||item.preview?.path||item.preview):'';
    return '<article class="decision-option" data-dom-key="choice-'+h(item.id)+'">'+(style?'<div class="choice-image">'+(preview?'<img alt="'+h(item.name||item.id)+'" src="'+h(preview)+'" loading="lazy">':'<span>预览未返回</span>')+'</div>':'<div class="choice-heading"><span>方案 '+String(i+1).padStart(2,'0')+'</span><span class="state-pill state-succeeded">审核通过</span></div>')+'<div class="choice-body"><h3>'+h(item.title||item.name||item.id)+'</h3><p>'+h(item.logline||item.description||item.synopsis||'')+'</p>'+(item.hook?'<div class="choice-hook"><b>开场钩子</b><p>'+h(item.hook)+'</p></div>':'')+'<details data-flow-detail="candidate-'+h(item.id)+'"><summary>查看完整方案</summary><p>'+h(item.synopsis||item.description||item.prompt||'')+'</p>'+(item.ending?'<p><strong>结局：</strong>'+h(item.ending)+'</p>':'')+(item.review?.summary?'<p><strong>审核结论：</strong>'+h(item.review.summary)+'</p>':'')+'</details><button class="button primary full" data-action="decide" data-type="'+h(decision.type)+'" data-choice="'+h(item.id)+'" '+(busy?'disabled':'')+'>'+(style?'采用这套视觉':'选择这个故事')+icon('arrow',15)+'</button></div></article>';
  }).join('')+'</div>';
}
function feedbackForm(project,busy,label,mode='retry') {
  const prefix=project.id+':'+mode;
  return '<form data-form="'+mode+'" data-project-revision="'+h(project.revision)+'" class="decision-feedback"><div class="field"><div class="field-title"><label for="decision-feedback">你的处理意见</label>'+(suggestedFeedback(project)?'<button type="button" class="text-link" data-action="use-review-feedback">填入审核建议</button>':'')+'</div><textarea id="decision-feedback" name="feedback" data-preserve="'+h(prefix)+'" rows="5" maxlength="3900" placeholder="例如：按上方审核意见补齐人物动机，简化指定镜头动作。保留未受影响的剧情与素材。"></textarea><small>建议只会填入文本，点击提交后才会启动修改和重新审核。</small></div><div class="decision-footer"><button type="submit" class="button primary" '+(busy?'disabled':'')+'>'+h(label)+icon('arrow',15)+'</button><button type="button" class="button" data-action="tab" data-tab="script">查看剧本与分镜</button></div></form>';
}
function productionForm(project,busy) {
  return '<form data-form="revise-production" data-project-id="'+h(project.id)+'" data-project-revision="'+h(project.revision)+'" class="decision-feedback"><fieldset><legend>需要修改的镜头</legend><div class="shot-options">'+list(project.shots).map(shot=>'<label class="shot-option"><input type="checkbox" name="shot_ids" value="'+h(shot.id)+'" data-preserve="'+h(project.id+':production:'+shot.id)+'"><span><strong>'+h(shot.id)+'</strong>'+h(String(shot.dramatic_beat||shot.action||'查看分镜详情').slice(0,90))+'</span></label>').join('')+'</div></fieldset><div class="field"><div class="field-title"><label for="production-revision-feedback">具体修改要求</label><button type="button" class="text-link" data-action="use-review-feedback">填入审核建议</button></div><textarea id="production-revision-feedback" name="feedback" data-preserve="'+h(project.id+':production:feedback')+'" rows="5" maxlength="3900" required></textarea><small>未勾选镜头保留原分镜；已生成素材和累计预算继续保留。</small></div><button type="submit" class="button primary" '+(busy?'disabled':'')+'>提交局部返修</button></form>';
}
export function decisionCenter(project,busy=false) {
  const attention=project?.operator?.attention;
  const decision=pendingDecisions(project)[0];
  if(!decision) {
    if(attention?.kind==='paused')return '<section class="console-panel decision-section"><h2>'+h(attention.title)+'</h2><p>'+h(attention.summary)+'</p>'+(attention.can_resume?'<button class="button primary" data-action="project-action" data-command="resume">恢复制作</button>':'')+'</section>';
    return '';
  }
  const kind=decision.type;
  const isCloud=String(decision.code||'').startsWith('cloud_')||attention?.kind==='cloud';
  const title=attention?.title||({story:'选择故事方向',style:'选择人物与画风',final:'审看并确认终片',budget:'预算已达到上限',runtime:'执行服务需要处理'}[kind])||'审核未通过，需要明确修改意见';
  let body='';
  if(kind==='story'||kind==='style') {
    body=choiceCards(project,decision,busy)+(kind==='story'?'<details class="new-direction"><summary>这些方向都不合适？</summary>'+feedbackForm(project,busy,'重新创作故事提案','repropose')+'</details>':'');
  } else if(kind==='final') {
    const video=safeUrl(project.delivery?.video_url);
    const allowed=new Set(list(decision.options).map(x=>typeof x==='string'?x:x.id));
    const can=value=>!allowed.size||allowed.has(value);
    body=video?'<div class="final-review-layout"><video class="delivery-video" data-dom-key="final-video" controls playsinline preload="metadata" src="'+h(video)+'"></video><div class="final-review-controls"><h3>'+h(project.title)+'</h3><p>请实际观看并检查叙事、人物一致性、对白和字幕。自动评分不能代替出品人终审。</p><form data-form="final" data-project-revision="'+h(project.revision)+'"><div class="field"><label for="final-feedback">审片意见</label><textarea id="final-feedback" name="feedback" rows="5" maxlength="4000" data-preserve="'+h(project.id+':'+decision.id+':final-feedback')+'" placeholder="填写需要修改的镜头、对白或剪辑要求。"></textarea></div><div class="final-actions">'+(can('approve')?'<button type="submit" class="button primary" name="choice" value="approve" '+(busy?'disabled':'')+'>'+icon('check',16)+'确认通过并交付</button>':'')+(can('revise')?'<button type="submit" class="button" name="choice" value="revise" '+(busy?'disabled':'')+'>局部修改</button>':'')+(can('reedit')?'<button type="submit" class="button" name="choice" value="reedit" '+(busy?'disabled':'')+'>重新剪辑</button>':'')+'</div><small>修改与重剪必须填写意见；不会默认批准。</small></form></div></div>':emptyState('暂时没有可播放的成片','该关卡没有有效媒体链接，不能批准交付。请检查制作记录。','<button class="button" data-action="tab" data-tab="quality">查看制作记录</button>');
  } else if(kind==='budget') body='<div class="decision-callout">'+icon('cpu',24)+'<p>GPU 用量与累计制作时间分别计量。提高已耗尽的上限后，系统从检查点继续，不会清空用量。</p></div><button class="button primary" data-action="budget">调整预算并继续</button>';
  else if(isCloud) body='<div class="decision-callout">'+icon('alert',24)+'<p>先核对批次估价、授权与不确定的供应商任务；不会自动重复提交可能收费的请求。</p></div><button class="button primary" data-action="tab" data-tab="costs">打开云任务与费用</button>';
  else if(canReviseProduction(project)) body=issueCards(project)+productionForm(project,busy)+'<details class="new-direction"><summary>仅模型服务故障，分镜无需修改？</summary><p>确认服务恢复后重试当前阶段；达到素材尝试上限时请使用上方定向返修。</p><button class="button" data-action="project-action" data-command="retry">重试当前阶段</button></details>';
  else if(kind==='runtime'||attention?.kind==='runtime') body=issueCards(project)+'<div class="decision-footer"><button class="button" data-action="models">检查模型连接</button><button class="button primary" data-action="project-action" data-command="retry" '+(busy?'disabled':'')+'>服务已恢复，重试当前阶段</button></div>';
  else body=issueCards(project)+feedbackForm(project,busy,project.selected_story?'提交修改并重新审核':'修改方向并重新提案');
  return '<section class="decision-section console-panel" data-decision-id="'+h(decision.id||'')+'" data-decision-project="'+h(project.id)+'" data-decision-revision="'+h(project.revision)+'" aria-label="待处理的出品人决策"><div class="decision-heading"><span class="decision-heading-icon">'+icon('inbox',24)+'</span><div><div class="eyebrow">需要你处理</div><h2>'+h(title)+'</h2><p>'+h(attention?.summary||decision.message||decision.title||project.error||'选择后，制作团队将继续执行。')+'</p></div></div>'+body+'<div class="decision-inline-error" role="alert" hidden></div></section>';
}
export function decisionPage(project,busy=false) { return decisionCenter(project,busy)||emptyState('当前没有人工待办','团队正在自动执行，或当前阶段尚未产生决策。你可以返回工作流查看当前进度。','<button class="button" data-action="tab" data-tab="overview">查看工作流</button>'); }
