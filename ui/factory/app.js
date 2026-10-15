import { api } from './api.js';
import { list, isRunning } from './format.js';
import { dashboard, projectView, renderSidebar, connectionError, capabilitiesDialog } from './views.js';
import { bindActions } from './actions.js';
import { bindSeriesActions } from './series-actions.js';
import { bindShotActions } from './shot-actions.js';
import { patchWorkspace } from './dom-patch.js';
import { bindModelActions, ingestInventory, modelsView } from './models.js';
import { ingestProjectFlow } from './team.js';
import { bindConsoleActions } from './console-actions.js';

const state = { overview:null, projectId:'', project:null, view:'dashboard', tab:'overview', busy:false, epoch:0,
  models:null, modelsDraft:null, modelsDirty:false, modelTests:{}, workflow:null, projectFlow:null,
  projectFlowError:'', mutationError:'', selectedNode:'', projectFilter:'all', projectSearch:'' };
const workspace = document.getElementById('workspace');
const validTabs = ['overview','decisions','team','shots','script','series','quality','costs','delivery'];
const validViews = ['dashboard','projects','inbox','workflow','models','activity'];
let timer, toastTimer, generation=0, lastPaint='', metadataAt=0, lastError='';

function toast(message,error=false) {
  const element=document.getElementById('toast');clearTimeout(toastTimer);
  element.textContent=message;element.className='toast'+(error?' error':'');element.hidden=false;
  toastTimer=setTimeout(()=>{element.hidden=true;},error?12000:4500);
}
function paint(force=false) {
  const key=JSON.stringify({view:state.view,id:state.projectId,tab:state.tab,project:state.project,overview:state.projectId||state.view==='models'?null:state.overview,
    models:state.models,draft:state.modelsDraft,dirty:state.modelsDirty,tests:state.modelTests,pending:state.modelTestPending,saving:state.modelsSaving,
    modelError:state.modelsError,saveError:state.modelSaveError,remote:state.modelsRemoteChanged,flow:state.projectFlow,flowError:state.projectFlowError,
    shotId:state.shotId,shotDrafts:state.shotDrafts,selectedNode:state.selectedNode,zoom:state.flowZoom,filter:state.projectFilter,search:state.projectSearch,
    logFilter:state.logFilter,mutationError:state.mutationError});
  if(!force&&key===lastPaint)return;
  const preserved=state.clearDrafts?new Map():new Map([...workspace.querySelectorAll('[data-preserve]')].map(el=>[el.dataset.preserve,{value:el.value,checked:el.type==='checkbox'?el.checked:null}]));
  const draftVersions=state.clearDrafts?[]:[...workspace.querySelectorAll('form[data-project-revision]')].filter(form=>[...form.querySelectorAll('textarea')].some(el=>el.value.trim())).map(form=>({type:form.dataset.form,revision:form.dataset.projectRevision,gate:form.closest('[data-decision-id]')?.dataset.decisionId||''}));
  state.clearDrafts=false;
  const active=workspace.contains(document.activeElement)?document.activeElement:null;
  const focusKey=active?.dataset.preserve,selection=focusKey?[active.selectionStart,active.selectionEnd]:null;
  const details=new Map([...workspace.querySelectorAll('details[data-flow-detail]')].map(el=>[el.dataset.flowDetail,el.open]));
  const viewport=workspace.querySelector('[data-flow-viewport]'),scroll=viewport?{x:viewport.scrollLeft,y:viewport.scrollTop}:null;
  patchWorkspace(workspace,state.view==='models'?modelsView(state):state.projectId?projectView(state):dashboard(state));
  for(const [id,value] of preserved){
    const el=[...workspace.querySelectorAll('[data-preserve]')].find(x=>x.dataset.preserve===id);
    if(el){el.value=value.value;if(value.checked!==null)el.checked=value.checked;
      if(focusKey===id){el.focus({preventScroll:true});if(selection&&Number.isInteger(selection[0])&&typeof el.setSelectionRange==='function')el.setSelectionRange(...selection);}}
  }
  for(const draft of draftVersions){
    const form=[...workspace.querySelectorAll('form[data-project-revision]')].find(f=>f.dataset.form===draft.type&&(f.closest('[data-decision-id]')?.dataset.decisionId||'')===draft.gate);
    if(!form)continue;
    form.dataset.projectRevision=draft.revision;
    if(Number(draft.revision)!==state.project?.revision){
      const note=document.createElement('div');note.className='notice decision-draft-stale';note.setAttribute('role','status');
      note.innerHTML='<div><strong>项目状态已更新，你的意见仍保留。</strong>请先核对当前问题和选项，再确认使用这份意见。</div><button type="button" class="button small" data-action="confirm-decision-revision">已核对当前状态</button>';
      form.prepend(note);
    }
  }
  for(const el of workspace.querySelectorAll('details[data-flow-detail]'))if(details.has(el.dataset.flowDetail))el.open=details.get(el.dataset.flowDetail);
  const next=workspace.querySelector('[data-flow-viewport]');if(next&&scroll){next.scrollLeft=scroll.x;next.scrollTop=scroll.y;}
  if(state.busy)workspace.querySelectorAll('[data-action="decide"],[data-action="project-action"],form button').forEach(b=>b.disabled=true);
  lastPaint=key;
}
function updateHash() {
  const params=new URLSearchParams();
  if(state.projectId){params.set('project',state.projectId);if(state.tab!=='overview')params.set('tab',state.tab);}
  else if(state.view!=='dashboard')params.set('view',state.view);
  history.replaceState(null,'',location.pathname+location.search+(params.size?'#'+params:''));
}
function schedule() {
  clearTimeout(timer);
  const active=state.project?isRunning(state.project):list(state.overview?.projects).some(isRunning);
  timer=setTimeout(()=>{if(document.hidden||state.busy)schedule();else refresh();},document.hidden?25000:active?5000:15000);
}
async function refresh({force=false,notify=false}={}) {
  clearTimeout(timer);
  const request=++generation,epoch=state.epoch,pid=state.projectId,tab=state.tab;
  document.getElementById('sync-state').textContent='正在同步';
  const metadata=force||!state.models||Date.now()-metadataAt>60000;
  const calls={overview:api.overview()};
  if(pid)calls.workspace=api.workspace(pid);
  if(metadata){calls.models=api.models();calls.profiles=api.productionProfiles();}
  if(!state.workflow&&(state.view==='workflow'||metadata))calls.workflow=api.workflow();
  if(pid&&tab==='team')calls.flow=api.flow(pid);
  if(pid&&tab==='shots')calls.shots=api.shots(pid);
  if(pid&&tab==='series')calls.series=api.series(pid);
  if(pid&&tab==='costs')calls.cloud=api.cloudBudget(pid);
  const names=Object.keys(calls),result=await Promise.allSettled(Object.values(calls));
  if(request!==generation||epoch!==state.epoch){schedule();return;}
  const data=Object.fromEntries(names.map((name,i)=>[name,result[i]]));
  let error='';
  if(data.overview.status==='fulfilled')state.overview=data.overview.value;else error=data.overview.reason.message;
  if(data.workspace?.status==='fulfilled'&&pid===state.projectId){
    const incoming=data.workspace.value;
    if(incoming?.project?.id!==pid || !Number.isInteger(incoming.revision))error='项目快照无效，保留上次状态。';
    else if(!state.project||incoming.revision>=state.project.revision){
      const previous=state.project;
      state.project={...incoming.project,operator:incoming.operator,production_graph:incoming.workflow};
      if(previous?.id===pid)for(const key of ['workbench','series_library','cloud_budget'])if(previous[key])state.project[key]=previous[key];
    }
  }else if(data.workspace?.status==='rejected')error=data.workspace.reason.message;
  if(data.models?.status==='fulfilled'){ingestInventory(state,data.models.value);metadataAt=Date.now();}
  else if(data.models?.status==='rejected')state.modelsError=data.models.reason.message;
  if(data.profiles?.status==='fulfilled')state.productionProfiles=data.profiles.value;
  if(data.workflow?.status==='fulfilled')state.workflow=data.workflow.value;
  if(data.flow?.status==='fulfilled')ingestProjectFlow(state,data.flow.value,pid);
  else if(data.flow?.status==='rejected')state.projectFlowError=data.flow.reason.message;
  if(state.project&&pid===state.project.id){
    if(data.shots?.status==='fulfilled'){state.project.workbench=data.shots.value;state.project.workbench_error='';}
    else if(data.shots?.status==='rejected'){state.project.workbench_error=data.shots.reason.message;error ||= data.shots.reason.message;}
    if(data.series?.status==='fulfilled'){state.project.series_library=data.series.value;state.project.series_error='';}
    else if(data.series?.status==='rejected'){state.project.series_error=data.series.reason.message;error ||= data.series.reason.message;}
    if(data.cloud?.status==='fulfilled')state.project.cloud_budget=data.cloud.value;
    else if(data.cloud?.status==='rejected'){state.project.cloud_error=data.cloud.reason.message;error ||= data.cloud.reason.message;}
  }
  if(state.overview||state.view==='models'){renderSidebar(state);paint(force);}
  else if(error)workspace.innerHTML=connectionError(error);
  const caps=document.getElementById('capabilities-dialog');
  if(caps.open&&!caps.contains(document.activeElement))caps.innerHTML=capabilitiesDialog(state);
  if(error){document.getElementById('sync-state').textContent='同步中断';if(error!==lastError||notify)toast(error,true);lastError=error;}
  else {document.getElementById('sync-state').textContent='已同步 '+new Intl.DateTimeFormat('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date());lastError='';if(notify)toast('状态已更新，未提交的输入保持不变。');}
  schedule();
}
async function selectProject(id,project=null,tab='overview') {
  state.epoch+=1;state.view='dashboard';state.projectId=id||'';state.project=project;state.projectFlow=null;state.projectFlowError='';
  state.tab=validTabs.includes(tab)?tab:'overview';state.selectedNode='';state.mutationError='';state.flowZoom=undefined;
  lastPaint='';updateHash();workspace.innerHTML='';
  if(state.overview){renderSidebar(state);paint(true);}
  await refresh();workspace.focus({preventScroll:true});window.scrollTo({top:0,behavior:'instant'});
}
async function selectView(view) {
  if(!validViews.includes(view))return;
  state.epoch+=1;state.view=view;state.projectId='';state.project=null;state.projectFlow=null;state.projectFlowError='';state.tab='overview';state.selectedNode='';state.flowZoom=undefined;
  updateHash();renderSidebar(state);paint(true);await refresh();workspace.focus({preventScroll:true});window.scrollTo({top:0,behavior:'instant'});
}
function selectTab(tab) {
  if(!validTabs.includes(tab))return;
  state.epoch+=1;state.tab=tab;updateHash();paint(true);refresh();
}
const ui={refresh,selectProject,selectView,selectTab,toast,paint};
bindActions(state,ui);bindModelActions(state,ui);bindSeriesActions(state,ui);bindShotActions(state,ui);bindConsoleActions(state,ui);
function readLocation() {
  const params=new URLSearchParams(location.hash.slice(1));state.view=validViews.includes(params.get('view'))?params.get('view'):'dashboard';
  const id=params.get('project')||'';state.projectId=/^[a-f0-9]{12}$/.test(id)?id:'';
  state.project=null;state.projectFlow=null;state.projectFlowError='';state.tab=validTabs.includes(params.get('tab'))?params.get('tab'):'overview';state.selectedNode='';state.mutationError='';
}
window.addEventListener('hashchange',()=>{if(location.hash==='#workspace')return;state.epoch+=1;readLocation();workspace.innerHTML='';refresh();});
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!state.busy)refresh();});
readLocation();refresh();
