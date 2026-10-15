import { h, list, dateText, safeUrl } from './format.js';
import { icon, roleName, statePill, sectionTitle, viewHeading } from './console-ui.js';

const SPECS = [
  ['research','选题研究','researcher','创作需求与来源','研究结论与材料'], ['proposals','故事提案','creator','研究结论','经过独立审稿的候选'], ['story_decision','故事决策','producer','合格候选','故事方向与意见'],
  ['screenplay','剧本与分镜','screenwriter','已选故事','剧本、分镜与审核'], ['visual_development','视觉预览','visual_designer','剧本与角色','人物和画风预览'], ['style_decision','视觉决策','producer','视觉预览','确认的视觉方向'],
  ['production','镜头与后期','editor','确认的分镜与视觉','镜头、声音和初版'], ['final_decision','终片审阅','producer','成片及质检证据','通过或定向修改'], ['delivery','正式交付','producer','人工终审决定','版本绑定的交付包'],
  ['revision','定向返修','story_editor','出品人的具体意见','修订剧本或剪辑单'],
];
const POS = { research:[24,32],proposals:[342,32],story_decision:[660,32],screenplay:[660,210],visual_development:[342,210],style_decision:[24,210],production:[24,388],final_decision:[342,388],delivery:[660,388],revision:[342,566] };
const LOOP_EDGES = [['story_decision','proposals','重新提案'],['final_decision','revision','修改 / 重剪'],['revision','production','重新制作']];
const GROUPS = [
  {id:'direction',label:'统筹与决策',lead:'executive_producer',roles:['executive_producer','producer'],responsibility:'总制片比较合格方案；出品人掌握费用授权与最终审片。'},
  {id:'story',label:'故事开发',lead:'screenwriter',roles:['researcher','creator','story_editor','screenwriter','storyboard_director'],responsibility:'研究、策划、编剧和分镜交付明确产物，按审核意见修改。'},
  {id:'media',label:'视觉与声音',lead:'visual_designer',roles:['visual_designer','prompt_style_compiler','image_generator','video_generator','speech','dialogue_editor','editor'],responsibility:'围绕已批准分镜制作人物、镜头、对白、字幕与成片。'},
  {id:'review',label:'独立质量审核',lead:'independent_critic',roles:['story_critic','independent_critic','visual_critic'],responsibility:'独立检查叙事、图像与连续性；不以节点完成代替审核通过。'},
];
const evidenceNames = {not_recorded:'尚无执行记录',task_receipt:'实际任务记录',decision_receipt:'已记录决定',producer_approval:'出品人人工批准',current_project_state:'当前项目状态'};

export function graphData(state) {
  const graph = state.project?.production_graph || state.productionGraph;
  if (graph?.schema === 'ai-studio.production-graph.v1') return graph;
  const board = state.projectFlow?.task_board?.tasks || [];
  return { nodes:SPECS.map(([id,label,owner,input,output]) => {
    const row=board.find(x=>x.id===id), record=row?.current ? row.record : null;
    return {id,label,owner,input,output,members:[owner],status:record?row.status:'pending',evidence:record?'task_receipt':'not_recorded',...record};
  }), edges: SPECS.slice(0,8).map((n,i)=>({source:n[0],target:SPECS[i+1][0],label:'continue'})).concat(LOOP_EDGES.map(([source,target,label])=>({source,target,label,loop:true}))), groups:GROUPS, active_node:'', current_activity:state.project?.operator?.current_activity || '' };
}
function modelFor(role, state) {
  const assigned=list(state.models?.roles).find(r=>r.role===role);
  if (assigned) return assigned.model || '模型尚未分配';
  return {producer:'人工决策',image_generator:state.overview?.capabilities?.image?.model || '图像引擎',video_generator:state.overview?.capabilities?.video?.model || '视频引擎',speech:state.overview?.capabilities?.audio?.model || '语音引擎',editor:'剪辑与音画处理'}[role] || '工作流执行器';
}
function edgePath(edge) {
  const [x,y]=POS[edge.source] || [0,0], [xx,yy]=POS[edge.target] || [0,0], w=250, hh=128;
  if(edge.loop) {
    if(edge.source==='revision') return `M${x} ${y+hh/2} C${x-65} ${y+hh/2} ${xx+w/2} ${y+hh/2} ${xx+w/2} ${yy+hh}`;
    if(yy===y) return `M${x+w/2} ${y} C${x+w/2} ${y-26} ${xx+w/2} ${y-26} ${xx+w/2} ${yy}`;
    return `M${x+w/2} ${y+hh} L${xx+w/2} ${yy}`;
  }
  if(yy===y) return xx>x ? `M${x+w} ${y+hh/2} L${xx} ${yy+hh/2}` : `M${x} ${y+hh/2} L${xx+w} ${yy+hh/2}`;
  return `M${x+w/2} ${y+hh} L${xx+w/2} ${yy}`;
}
function inspector(node, state) {
  if(!node) return '';
  const roles=list(node.members).length?node.members:[node.owner];
  const issue=state.project?.operator?.attention;
  const relevant=list(state.projectFlow?.nodes).filter(n=>roles.includes(n.role));
  const executions=relevant.flatMap(n=>list(n.executions).map(e=>({...e,role:n.role}))).slice(0,8);
  const action=issue?.blocking && (node.status==='human'||node.status==='blocked') ? '<button class="button primary full" data-action="tab" data-tab="'+h(issue.target_tab||'decisions')+'">'+h(issue.action_label||'处理当前事项')+icon('arrow',15)+'</button>':'';
  return '<aside class="node-inspector" aria-label="节点详情" data-dom-key="node-inspector"><div class="inspector-eyebrow">节点详情 <code>'+h(node.id)+'</code></div><h3>'+h(node.label)+'</h3>'+statePill(node.status)+'<dl class="inspector-fields"><div><dt>负责人</dt><dd>'+h(roleName(node.owner))+'</dd></div><div><dt>接收</dt><dd>'+h(node.input)+'</dd></div><div><dt>交付</dt><dd>'+h(node.output)+'</dd></div><div><dt>状态依据</dt><dd>'+h(evidenceNames[node.evidence]||node.evidence||'尚未记录')+'</dd></div>'+(node.attempt?'<div><dt>执行次数</dt><dd>'+h(node.attempt)+' 次</dd></div>':'')+(node.finished_at?'<div><dt>结束时间</dt><dd>'+h(dateText(node.finished_at,true))+'</dd></div>':'')+'</dl><h4>参与成员</h4><div class="inspector-roles">'+roles.map(role=>'<div><strong>'+h(roleName(role))+'</strong><small title="'+h(modelFor(role,state))+'">'+h(modelFor(role,state))+'</small></div>').join('')+'</div>'+action+(executions.length?'<details data-flow-detail="node-receipts"><summary>查看实际调用记录 · '+executions.length+'</summary>'+executions.map(e=>'<div class="execution-mini"><strong>'+h(roleName(e.role))+' · '+h(e.recorded_status||e.status)+'</strong><small>'+h(e.model||'实际模型未记录')+'</small>'+(safeUrl(e.receipt)?'<a href="'+h(safeUrl(e.receipt))+'" target="_blank" rel="noopener noreferrer">打开回执</a>':'')+'</div>').join('')+'</details>':'<p class="inspector-note">这里只展示已保存证据，不推测未执行的审核结果。</p>')+'</aside>';
}
export function flowCanvas(state, { standalone=false }={}) {
  const graph=graphData(state), nodes=list(graph.nodes).filter(n=>Object.hasOwn(POS,n.id));
  const selected=state.selectedNode || graph.active_node || 'research';
  const chosen=nodes.find(n=>n.id===selected)||nodes[0];
  const zoom=typeof state.flowZoom==='number'?Math.min(1.2,Math.max(.32,state.flowZoom)):0.66;
  const edges=list(graph.edges).filter(e=>POS[e.source]&&POS[e.target]&&e.source!=='production');
  // Keep the main production-to-final edge; only suppress the duplicate inverse repair loop.
  edges.push(...list(graph.edges).filter(e=>e.source==='production'&&e.target==='final_decision'));
  return '<section class="flow-workspace" aria-label="制作工作流"><div class="flow-toolbar"><div><span class="live-indicator '+(state.project?.operator?.job_running?'working':'')+'"></span><strong>'+h(graph.current_activity||'制作流程')+'</strong><small>'+(standalone?'标准工作流 · 不会直接执行':h(state.project?.revision?'版本 '+state.project.revision:'状态同步中'))+'</small></div><div class="flow-tools"><button data-action="flow-zoom" data-delta="-0.1" aria-label="缩小工作流">−</button><span>'+Math.round(zoom*100)+'%</span><button data-action="flow-zoom" data-delta="0.1" aria-label="放大工作流">＋</button><button data-action="flow-fit" title="适应画布">适应</button><button data-action="flow-current">定位当前</button></div></div><div class="flow-body"><div class="flow-stage" data-flow-viewport><div class="flow-spacer" style="width:'+Math.round(950*zoom)+'px;height:'+Math.round(730*zoom)+'px"><div class="flow-transform" style="transform:scale('+zoom+')"><svg class="flow-edges" width="950" height="730" viewBox="0 0 950 730" aria-hidden="true"><defs><marker id="flow-arrow" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8"/></marker></defs>'+edges.map(e=>'<path class="flow-edge '+(e.loop?'return-edge':'')+'" d="'+edgePath(e)+'" marker-end="url(#flow-arrow)"/>').join('')+'</svg>'+nodes.map((node,index)=>{
    const [x,y]=POS[node.id],active=graph.active_node===node.id;
    return '<button class="flow-node node-'+h(node.status||'pending')+(node.id===chosen?.id?' selected':'')+(active?' current':'')+'" style="left:'+x+'px;top:'+y+'px" data-action="select-node" data-node="'+h(node.id)+'" data-flow-node="'+h(node.id)+'" aria-pressed="'+(node.id===chosen?.id)+'"><span class="node-kicker"><b>'+String(index+1).padStart(2,'0')+'</b><span>'+h(roleName(node.owner))+'</span>'+(node.decision_mode?'<em>'+(node.decision_mode==='delegated'?'代理决策':'人工关卡')+'</em>':'')+'</span><strong>'+h(node.label)+'</strong><div>'+statePill(node.status)+'</div></button>';
  }).join('')+'<span class="flow-loop-label" style="left:474px;top:533px">修改 / 重剪</span><span class="flow-loop-label" style="left:106px;top:614px">重新审核与制作</span></div></div></div>'+inspector(chosen,state)+'</div><div class="flow-legend"><span><i class="legend-running"></i>执行中</span><span><i class="legend-human"></i>人工处理</span><span><i class="legend-done"></i>本步完成</span><span><i class="legend-loop"></i>返修路径</span><small>节点可选择查看；执行顺序由持久化工作流控制</small></div></section>';
}
export function executionLedger(state) {
  const nodes=list(state.projectFlow?.nodes);
  const calls=nodes.flatMap(n=>list(n.executions).map(e=>({...e,role:n.role,label:n.label}))).sort((a,b)=>new Date(b.started_at||0)-new Date(a.started_at||0)).slice(0,30);
  if(!nodes.length)return '';
  return '<section class="console-panel execution-ledger">'+sectionTitle('执行记录与交接','实际调用模型保留历史身份，不会跟随当前配置改名。')+'<div class="table-scroll"><table><thead><tr><th>成员</th><th>实际模型</th><th>状态</th><th>时间</th><th>证据</th></tr></thead><tbody>'+calls.map(e=>'<tr><td>'+h(roleName(e.role))+'</td><td><code>'+h(e.model||'未记录')+'</code><small>'+h(e.model_source==='response'?'来自模型响应':e.model_source==='request'?'来自请求记录':'未记录')+'</small></td><td>'+statePill(e.status)+'</td><td>'+h(dateText(e.finished_at||e.started_at,true))+'</td><td>'+(safeUrl(e.receipt)?'<a class="text-link" href="'+h(safeUrl(e.receipt))+'" target="_blank" rel="noopener noreferrer">回执 ↗</a>':'—')+'</td></tr>').join('')+'</tbody></table></div>'+(calls.length?'':'<p class="muted">尚无实际模型调用记录。</p>')+'<details data-flow-detail="member-handoffs"><summary>查看成员交付与上下游</summary><div class="handoff-ledger">'+nodes.filter(n=>list(n.inputs).length||list(n.outputs).length).map(n=>'<article><h4>'+h(roleName(n.role))+'</h4><p><b>接收：</b>'+list(n.inputs).map(x=>h(x.label)+(x.value?' · '+h(x.value):'')).join('；')+'</p><p><b>交付：</b>'+list(n.outputs).map(x=>h(x.label)+(x.value?' · '+h(x.value):'')).join('；')+'</p></article>').join('')+'</div><p>'+list(state.projectFlow?.edges).map(e=>h(e.label)).join(' · ')+'</p></details></section>';
}
export function organizationView(state) {
  const graph=graphData(state), project=state.project;
  const supervisor=list(project?.supervision ? Object.values(project.supervision) : state.projectFlow?.supervision?.records).filter(x=>x&&typeof x==='object');
  return '<section class="console-panel">'+sectionTitle('制作团队','按职责交接，创作与审核保持独立。','<button class="text-link" data-action="models">调整模型分工 '+icon('arrow',14)+'</button>')+'<div class="department-grid">'+list(graph.groups).map(group=>'<article class="department-card"><div class="department-head">'+icon(group.id==='review'?'check':group.id==='direction'?'flow':group.id==='media'?'film':'users',22)+'<div><h3>'+h(group.label)+'</h3><span>负责统筹：'+h(roleName(group.lead))+'</span></div></div><p>'+h(group.responsibility)+'</p><div class="department-roles">'+list(group.roles).map(role=>'<div><span>'+h(roleName(role))+'</span><small title="'+h(modelFor(role,state))+'">'+h(modelFor(role,state))+'</small></div>').join('')+'</div></article>').join('')+'</div></section>'+(supervisor.length?'<section class="console-panel supervisor-panel">'+sectionTitle('总制片决定','记录选择依据、待查风险与人工终审建议。')+supervisor.map(r=>'<article class="supervisor-record"><div><strong>'+h({story:'故事方向',style:'视觉方向',final:'终审建议'}[r.kind]||r.kind)+'</strong><span>'+h({selected:'已选择',requested:'正在请求模型',manual:'已转交人工',advisory:'建议已记录',superseded:'旧版本结果'}[r.status]||r.status)+'</span></div><p>'+h(r.proposal?.rationale||'尚无已完成的决定。')+'</p>'+(list(r.proposal?.risks).length?'<p class="supervisor-risk">待查：'+list(r.proposal.risks).map(h).join('；')+'</p>':'')+'<small>'+h(r.requested_model||'请求模型未记录')+' · 不代表已观看成片</small></article>').join('')+'</section>':'')+executionLedger(state);
}
export function workflowView(state) { return viewHeading('WORKFLOW & TEAM','工作流与分工','每个阶段都有负责人、交付物和审核边界；进入项目查看实际执行状态。')+flowCanvas({...state,project:null,projectFlow:null,productionGraph:null},{standalone:true})+organizationView({...state,project:null,projectFlow:null,productionGraph:null}); }
