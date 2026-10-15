import test from 'node:test';
import assert from 'node:assert/strict';
import {parseHTML} from 'linkedom';
import {decisionCenter,reviewIssues,suggestedFeedback} from '../ui/factory/decision-center.js';
import {pendingDecisions} from '../ui/factory/format.js';
import {dashboardView} from '../ui/factory/console-dashboard.js';
import {flowCanvas} from '../ui/factory/flow-canvas.js';
const id='012345abcdef';
globalThis.location={origin:'http://127.0.0.1:8787'};
function p(){return {id,revision:7,title:'成人修表师的谜题',team_mode:'guided',status:'awaiting_story',proposal_generation:1,decisions:[{id:id+':story:1',type:'story',status:'pending',options:[{id:'A'}]}],proposals:[{id:'A',title:'方向A',eligible:true},{id:'B',title:'不应出现',eligible:false},{id:'C',title:'也不应该出现',eligible:true}]};}
const document=html=>parseHTML('<html><body>'+html+'</body></html>').document;
test('canonical null attention hides an in-flight automatic gate',()=>{
  const project={...p(),team_mode:'delegated',operator:{attention:null,job_running:true}};
  assert.deepEqual(pendingDecisions(project),[]);
  assert.equal(decisionCenter(project),'');
});
test('empty needs_decision and completed stale gates are never shown',()=>{
  assert.deepEqual(pendingDecisions({status:'producing',needs_decision:{}}),[]);
  assert.deepEqual(pendingDecisions({...p(),status:'approved'}),[]);
  assert.deepEqual(pendingDecisions({...p(),proposal_generation:2}),[]);
});
test('story action list is the intersection of eligible proposals and actual gate options',()=>{
  const dom=document(decisionCenter(p()));
  assert.equal(dom.querySelectorAll('[data-action="decide"]').length,1);
  assert.equal(dom.querySelector('[data-action="decide"]').dataset.choice,'A');
  assert.doesNotMatch(dom.body.textContent,/不应出现/);
});
test('object preview receipts become real media links',()=>{
  const project={...p(),status:'awaiting_style',decisions:[{id:'style-gate',type:'style',status:'pending',options:[{id:'ink'}]}],styles:[{id:'ink',name:'手绘',preview:{path:'production/factory-'+id+'/style_previews/ink/approved.png'}}]};
  assert.match(decisionCenter(project),/src="http:\/\/127.0.0.1:8787\/media\/production/);
});
test('missing choices cannot render approve or continue controls',()=>{
  const project={...p(),proposals:[]};
  assert.equal(document(decisionCenter(project)).querySelectorAll('[data-action="decide"]').length,0);
  const final={...project,status:'awaiting_final',decisions:[{id:'final',type:'final',status:'pending'}],delivery:null};
  assert.equal(document(decisionCenter(final)).querySelectorAll('[value="approve"]').length,0);
});
test('nested blockers are readable and can be converted into user-reviewed feedback',()=>{
  const project={...p(),status:'blocked',decisions:[],needs_decision:{type:'review',kind:'script_review',details:{issues:[{severity:'blocking',description:'缺少动机',suggestion:'补充原因',shot_ids:['S03'],evidence:'原稿缺失'}]}}};
  const dom=document(decisionCenter(project));
  assert.match(dom.body.textContent,/缺少动机/);assert.match(dom.body.textContent,/补充原因/);
  assert(dom.querySelector('[data-action="use-review-feedback"]'));
  assert.match(suggestedFeedback(project),/S03：补充原因/);
});
test('untrusted review and candidate strings never produce executable HTML',()=>{
  const project=p();project.proposals[0].title='<img src=x onerror=alert(1)>';
  const dom=document(decisionCenter(project));assert.equal(dom.querySelectorAll('img,script,[onerror]').length,0);
  assert.match(dom.body.textContent,/<img src=x/);
});
test('review traversal is bounded and only reads last historic round',()=>{
  const project={needs_decision:{rounds:[{issues:[{description:'old'}]},{issues:Array.from({length:200},(_,i)=>({description:'new '+i}))}]}};
  const issues=reviewIssues(project);assert(issues.length<=24);assert(!issues.some(i=>i.description==='old'));
});
test('dashboard uses measured metrics and real attention rather than marketing or fictional trends',()=>{
  const html=dashboardView({overview:{projects:[],metrics:{projects:0,running:0,attention:0,delivered:0,gpu_minutes:0},queue:{}},models:{profiles:[]}});
  const dom=document(html);assert.equal(dom.querySelectorAll('.metric-card').length,4);
  assert.match(dom.body.textContent,/制片总览/);assert.match(dom.body.textContent,/当前没有需要你处理/);
  assert.doesNotMatch(dom.body.textContent,/昨日|增长|一个方向，一支制作团队/);
});
test('graph renders selectable nodes and return paths without giving them execution controls',()=>{
  const dom=document(flowCanvas({project:p(),models:{roles:[]}}));
  assert.equal(dom.querySelectorAll('[data-flow-node]').length,10);
  assert(dom.querySelector('.return-edge'));
  assert(dom.querySelector('[data-action="flow-fit"]'));
  assert.equal(dom.querySelectorAll('[data-action="project-action"]').length,0);
});
