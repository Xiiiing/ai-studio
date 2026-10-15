import { suggestedFeedback } from './decision-center.js';
import { graphData } from './flow-canvas.js';

export function bindConsoleActions(state,ui) {
  document.addEventListener('click',async event=>{
    const button=event.target.closest('[data-action]');if(!button||button.disabled)return;
    const action=button.dataset.action;
    if(action==='view')await ui.selectView(button.dataset.view);
    if(action==='open-project-tab')await ui.selectProject(button.dataset.id,null,button.dataset.tab||'overview');
    if(action==='project-filter'){state.projectFilter=button.dataset.filter;ui.paint(true);}
    if(action==='log-filter'){state.logFilter=button.dataset.filter;ui.paint(true);}
    if(action==='select-node'){state.selectedNode=button.dataset.node;ui.paint(true);}
    if(action==='flow-zoom'){state.flowZoom=Math.min(1.2,Math.max(.32,(state.flowZoom??.66)+Number(button.dataset.delta)));ui.paint(true);}
    if(action==='flow-fit'){
      const viewport=document.querySelector('[data-flow-viewport]');state.flowZoom=Math.min(1,Math.max(.32,((viewport?.clientWidth||680)-24)/950));ui.paint(true);
    }
    if(action==='flow-current'){
      state.selectedNode=graphData(state).active_node||'research';ui.paint(true);
      document.querySelector('[data-flow-node="'+CSS.escape(state.selectedNode)+'"]')?.scrollIntoView({block:'nearest',inline:'nearest',behavior:'smooth'});
    }
    if(action==='inspect-shot'){state.shotId=button.dataset.shot;ui.selectTab('shots');}
    if(action==='confirm-decision-revision'){
      const form=button.closest('form[data-project-revision]');
      if(form&&state.project){form.dataset.projectRevision=String(state.project.revision);form.querySelector('.decision-draft-stale')?.remove();ui.toast('已将意见绑定到当前项目版本。');}
    }
    if(action==='use-review-feedback'){
      const target=button.closest('form')?.querySelector('textarea[name="feedback"]');
      if(!target)return;
      const suggestion=suggestedFeedback(state.project);
      if(!suggestion){ui.toast('当前没有可自动整理的建议，请填写具体修改要求。');return;}
      target.value=target.value.trim()?target.value+'\n\n'+suggestion:suggestion;target.value=target.value.slice(0,3900);
      target.dispatchEvent(new Event('input',{bubbles:true}));target.focus();ui.toast('已填入审核建议，请检查后提交。');
    }
    if(action==='toggle-navigation')document.body.classList.toggle('navigation-open');
    if(['view','dashboard','models','select-project','open-project-tab'].includes(action))document.body.classList.remove('navigation-open');
  });
  let searchTimer;
  document.addEventListener('input',event=>{
    if(!event.target.matches('[data-console-search]'))return;
    state.projectSearch=event.target.value;clearTimeout(searchTimer);searchTimer=setTimeout(()=>ui.paint(true),180);
  });
}
