"""A resumable role loop inside the existing graph/model-call boundary.

SQLite stores decisions and tool results, not a second scheduling authority.
The worker lease, business versions, original role schema and independent review
remain authoritative. No tool here can render, approve, unlock or publish.
"""
from __future__ import annotations
import copy
from contextlib import contextmanager
import fcntl
import json
import math
from pathlib import Path
import re
import sqlite3
import time

from jsonschema import Draft202012Validator, ValidationError, validate
from studio.core import ROOT, media_receipt, sha256, write_json
from . import store
from .agent_policy import VERSION, DEFAULTS, POLICY, ACTION_SCHEMA, settings, tools
from .creative_evidence_io import canonical, digest, read_json
from .scheduler import ProductionNeedsDecision, ProductionPaused

HARD_FACTS = ('previous_unit_state','dependency_context','production_spec','narrative_scope','outline','brief','story_feedback','script_feedback','producer_feedback','creative_contract','resolved_story_brief',
              'series_bible','material_package','selected_story','production_profile')
MATERIALS = ('previous_unit_state','dependency_context','production_spec','narrative_scope','outline','brief','story_feedback','script_feedback','producer_feedback','creative_contract','resolved_story_brief',
 'selected_story','proposals','research','sources','submitted_sources','series_bible','material_package','script',
 'episode','screenplay_draft','draft_review','storyboard_receipt','storyboard_issues','styles','selected_style','shots','assets')
FENCE_FIELDS = ('previous_unit_state','dependency_context','production_spec','narrative_scope','outline','execution_config','workflow_version','script_generation','proposal_generation','production_revision','style_generation',
                'plan_version','plan_cycle','asset_version','prompt_snapshot','generation_pipeline','agent_loop')
NEW_DRAMA_FIELDS = {'previous_unit_state', 'dependency_context', 'production_spec', 'narrative_scope', 'outline', 'execution_config'}
CLOSED = {'completed','closed','superseded','blocked','handoff','exhausted'}


class SupplementRequested(ProductionNeedsDecision):
    def __init__(self, reason, scope, request=None):
        super().__init__(reason, 'agent_supplement_requested', {'shot_ids':scope,'request':request})
        self.scope, self.request = scope, request


def enabled(project):
    return (project.get('agent_loop') or {}).get('version') == VERSION


def root(project):
    ident = project.get('id','')
    if not re.fullmatch('[a-f0-9]{12}', ident) or project.get('slug', 'factory-'+ident) != 'factory-'+ident:
        raise ValueError('Agent项目目录归属无效')
    path = Path(ROOT) / 'production' / ('factory-'+ident) / 'planning' / 'agent-runs'
    if any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('Agent目录不能包含符号链接')
    return path


@contextmanager
def journal(project, write=False):
    path = root(project) / 'journal.sqlite3'
    if path.is_symlink(): raise ValueError('Agent记录不能是符号链接')
    if not write and not path.exists():
        yield None; return
    if write:path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    db=sqlite3.connect(str(path) if write else 'file:'+str(path)+'?mode=ro',uri=not write,timeout=15)
    db.row_factory=sqlite3.Row
    try:
        if write:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,updated REAL NOT NULL,document TEXT NOT NULL)')
            db.commit();db.execute('BEGIN IMMEDIATE');path.chmod(0o600)
        yield db
        if write:db.commit()
    except BaseException:
        db.rollback();raise
    finally:db.close()


def _save(project, row):
    raw=canonical(row).decode()
    if len(raw.encode()) > 2*1024*1024:
        raise ProductionNeedsDecision('Agent上下文记录过大，必需材料未裁剪；请拆分任务','agent_context_limit')
    with journal(project,True) as db:
        db.execute('INSERT INTO runs VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,document=excluded.document',
                   (row['id'],time.time(),raw))


def _rows(project):
    with journal(project) as db:
        return [json.loads(r[0]) for r in db.execute('SELECT document FROM runs ORDER BY updated DESC')] if db else []


def _fence(project):
    from .drama_intent import enabled as whole_drama
    return digest({k:project.get(k) for k in (*FENCE_FIELDS,*MATERIALS)
                   if whole_drama(project) or k not in NEW_DRAMA_FIELDS})


def _guard(project, fence):
    from .execution_context import current_job
    from .remote_tasks import owns_job
    job=current_job.get()
    if job and not owns_job(job):
        raise ProductionNeedsDecision('原worker租约已失效，迟到结果仅保留证据','agent_stale_lease')
    live=store.get(project['id'],False)
    if _fence(live)!=fence:
        raise ProductionNeedsDecision('任务材料或配置已变化，旧Agent结果不能发布','agent_stale_input')
    if live.get('pause_requested') or live.get('status') in {'paused','cancelled'}:
        raise ProductionPaused('Agent已停在工具边界，已完成动作保留')
    from .execution_context import current_execution
    ctx=current_execution.get() or {}
    if ctx.get('project_id')==project['id'] and ctx.get('stage'):
        from .team_control import admit
        admit(project['id'],'stage',ctx['stage'])
    return live


def identity(project, config, role, stage):
    from .config import llm_identity
    return {'snapshot':project['agent_loop'],'model':llm_identity(config,role),'role':role,'stage':stage,
        'materials':_fence(project),'policy':digest(POLICY),
        'implementation':{name:sha256(Path(__file__).with_name(name)) for name in ('agent_loop.py','agent_policy.py','agent_protocol.py','agent_media.py')}}


def _uncertain(row, project):
    if row['state']=='unknown':return True
    pending=row.get('pending')
    if row['state'] not in CLOSED and pending and pending.get('phase') in {'model','tool_model'}:
        folder=root(project)/row['id']/(pending['directory'] if pending['phase']=='tool_model' else 'turn-%03d'%pending['turn'])
        return not (folder/'turn.json').exists() and not list(folder.glob('calls/*/response-01.json'))
    return False


def _public(row):
    result={k:copy.deepcopy(row.get(k)) for k in ('id','role','stage','state','created_at','updated_at','turns','spent_output_tokens',
        'reserved_output_tokens','reason','plan','candidate_sha256','checked_sha256','quality_approval','result_sha256','evidence_type','snapshot')}
    result['events']=[{k:copy.deepcopy(e[k]) for k in ('seq','at','event','tool','status','summary','result_sha256','arguments') if k in e}
                      for e in row.get('events',[])][-160:]
    return result


def preflight(project):
    from .config import load_config,resolve_llm,ROLE_LABELS
    from .team_planning import task_config
    from .prompt_workbench import _local_trial
    from .agent_protocol import protocol
    config=load_config()
    selected=(project.get('agent_loop') or {}).get('settings',DEFAULTS)['protocol']
    result=[]
    for role,label in ROLE_LABELS.items():
        if role=='executive_producer':continue
        row={'role':role,'label':label,'ready':False}
        try:
            profile=resolve_llm(task_config(config,role),role)
            row.update(model=profile['model'],protocol=protocol(profile,selected),ready=_local_trial(profile))
            if not row['ready']:row['reason']='暂限已配置内网推理，费用未知请求会停止'
        except ValueError:
            row['reason']='请检查岗位模型连接或动作协议'
        result.append(row)
    return result


def snapshot(project):
    records=[]
    all_rows=_rows(project)
    visible=all_rows[:30]+[r for r in all_rows[30:] if _uncertain(r,project)]
    for row in visible:
        item=_public(row)
        if _uncertain(row,project) and row['state'] not in CLOSED:
            lock=root(project)/row['id']/'.lock'
            if lock.exists():
                with lock.open('r') as fd:
                    try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);item['state']='unknown'
                    except BlockingIOError:item['state']='running'
        records.append(item)
    return {'schema':VERSION,'enabled':enabled(project),'revision':project['revision'],'snapshot':project.get('agent_loop'),
        'defaults':DEFAULTS,'preflight':preflight(project),'runs':records,'record_limit':30,'quality_conclusion':'not_established',
        'semantics':'role_candidate_submitted_to_existing_business_validation_not_quality_approval'}


def detail(project, ident):
    if not isinstance(ident,str) or not re.fullmatch('[a-f0-9]{64}',ident):raise ValueError('Agent记录编号无效')
    row=next((r for r in _rows(project) if r['id']==ident),None)
    if not row:raise KeyError('Agent记录不存在')
    return {**_public(row),'candidate':copy.deepcopy(row.get('candidate')),'materials':copy.deepcopy(row['materials']),
            'output_contract':row['schema'],'handoff':row.get('handoff')}


def _idle(db, p):
    children=[p['id']]+[r[0] for r in db.execute("SELECT id FROM projects WHERE json_extract(snapshot,'$.drama_id')=? OR json_extract(snapshot,'$.episode_id')=?",(p['id'],p['id']))]
    slots=','.join('?' for _ in children)
    if p['status'] in store.ACTIVE or db.execute(f"SELECT 1 FROM jobs WHERE project_id IN ({slots}) AND status IN ('queued','running','waiting_remote')",children).fetchone():
        raise ValueError('请先暂停项目并等待全部在途任务结束')
    if db.execute(f"SELECT 1 FROM cloud_tasks WHERE project_id IN ({slots}) AND status NOT IN ('downloaded','failed','cancelled','rejected')",children).fetchone():
        raise ValueError('请先核实未完成的云端提交')
    if db.execute(f"SELECT 1 FROM material_tasks WHERE json_extract(data,'$.project_id') IN ({slots}) AND status IN ('queued','running','pause_requested')",children).fetchone():
        raise ValueError('请先等待素材任务结束')


def configure(pid,payload):
    if set(payload)!={'request_id','expected_revision','enabled','confirmed','settings'} or payload['confirmed'] is not True or type(payload['enabled']) is not bool:
        raise ValueError('Agent切换需要明确确认及完整配置')
    selected=settings(payload['settings'])
    from .prompt_workbench import _operation,_remember
    from .series_bible import project_cas
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE');op=_operation(db,{**payload,'project_id':pid,'kind':VERSION})
        if op[2] is not None:return op[2]
        p=project_cas(db,pid,payload['expected_revision']);_idle(db,p)
        if any(_uncertain(r,p) for r in _rows(p)):
            raise ValueError('存在结果未知的Agent请求，请先核实并结束，不能通过切换版本绕过恢复')
        p['agent_loop']={'version':VERSION,'selection_id':payload['request_id'],'settings':selected} if payload['enabled'] else None
        store._write(db,p);store._event(db,pid,'已显式切换Agent循环；原成果和子项目保留，未启动模型。','agent_configuration')
        return _remember(db,op,{'revision':p['revision'],'snapshot':p['agent_loop']})


def settle(pid,payload):
    if set(payload)!={'request_id','expected_revision','run_id','confirmed'} or payload['confirmed'] is not True:
        raise ValueError('结束未知Agent请求需要明确确认')
    from .prompt_workbench import _operation,_remember
    from .series_bible import project_cas
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE');op=_operation(db,{**payload,'project_id':pid,'kind':'agent_settle'})
        if op[2] is not None:return op[2]
        p=project_cas(db,pid,payload['expected_revision']);_idle(db,p)
        row=next((r for r in _rows(p) if r['id']==payload['run_id']),None)
        if not row or (not _uncertain(row,p) and row['state']!='closed'):raise ValueError('没有可结束的未知Agent请求')
        row.update(state='closed',reason='已核实并结束，需显式重新启用后继续',updated_at=time.time());_save(p,row)
        store._event(db,pid,'已结束未知Agent请求；回执保留，未重投。','agent_recovery')
        return _remember(db,op,{'run_id':row['id'],'state':'closed'})


class Loop:
    def __init__(self, project, config, role, stage, system, payload, schema, signature, max_tokens, temperature):
        self.project,self.config,self.role,self.stage=copy.deepcopy(project),config,role,stage
        self.system,self.payload,self.schema,self.signature=system,payload,schema,signature
        self.max_tokens,self.temperature=max_tokens,temperature
        self.settings=settings(project['agent_loop']['settings']);self.fence=_fence(project)
        self.materials={'project:'+k:copy.deepcopy(project[k]) for k in MATERIALS if k in project and (k not in NEW_DRAMA_FIELDS or project.get('production_mode') == 'one_sentence')}
        self.materials.update({'task:'+k:copy.deepcopy(v) for k,v in payload.items()})
        self.definitions=tools(schema,list(self.materials));self.specs={t['function']['name']:t['function']['parameters'] for t in self.definitions}
        self.directory=root(project)/signature
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        row=next((r for r in _rows(project) if r['id']==signature),None)
        self.row=row or {'id':signature,'project_id':project['id'],'role':role,'stage':stage,'state':'running',
            'created_at':time.time(),'updated_at':time.time(),'fence':self.fence,'schema':schema,
            'snapshot':copy.deepcopy(project['agent_loop']),'materials':{k:{'sha256':digest(v),'characters':len(json.dumps(v,ensure_ascii=False))} for k,v in self.materials.items()},
            'events':[],'history':[],'turns':0,'spent_output_tokens':0,'reserved_output_tokens':0,'pending':None,
            'candidate':None,'candidate_sha256':None,'checked_sha256':None,'plan':None,'quality_approval':False,
            'evidence_type':'synthetic_fixture' if project.get('synthetic_fixture') else 'actual_model_execution'}

    def event(self,event,**fields):
        self.row['events'].append({'seq':len(self.row['events'])+1,'at':time.time(),'event':event,**fields})
        self.row['updated_at']=time.time();_save(self.project,self.row)

    def guard(self):
        _guard(self.project,self.fence)
        if self.row['state'] in {'running','paused'} and time.time()-self.row['created_at']>self.settings['max_wall_seconds']:
            self.stop('exhausted','Agent时限已用尽，已有成果保留')
        for receipt in self.row.get('observed_media',[]):
            path=Path(ROOT)/receipt['path']
            if sha256(path)!=receipt['sha256']:raise ProductionNeedsDecision('已检查的媒体像素变化，旧观察不可用于提交','agent_stale_media')

    def stop(self,state,reason):
        self.row.update(state=state,reason=reason);self.event('agent_end',status=state,summary=reason)
        raise ProductionNeedsDecision(reason,'agent_'+state,{'run_id':self.signature,'role':self.role})

    def _model(self):
        from . import llm
        from .config import resolve_llm
        from .prompt_registry import derive_prompt
        from .agent_protocol import native_turn,protocol
        cfg=copy.deepcopy(self.config);profile=resolve_llm(cfg,self.role)
        selected=protocol(profile,self.settings['protocol'])
        remaining=self.settings['max_wall_seconds']-(time.time()-self.row['created_at'])
        cap=min(self.max_tokens,self.settings['max_output_tokens'],self.settings['total_output_tokens']-self.row['spent_output_tokens'])
        if cap<128:self.stop('exhausted','Agent总输出额度已用尽，已有成果保留')
        if remaining<=0:self.stop('exhausted','Agent时限已用尽，已有成果保留')
        if self.row['turns']>=self.settings['max_turns']:self.stop('exhausted','Agent轮数已用尽，已有成果保留')
        # Fixed derived limits, never model-supplied provider options.
        ident=profile['profile'];target=cfg.get('llm_profiles',{}).get(ident) if ident!='default' else cfg.get('llm')
        if target is None:target=cfg.setdefault('llm',{})
        target.update(max_schema_attempts=1,request_timeout_seconds=max(1,min(profile.get('request_timeout_seconds',480),math.floor(remaining))))
        index=(self.row.get('pending') or {}).get('turn') or self.row['turns']+1
        folder=self.directory/('turn-%03d'%index);folder.mkdir(exist_ok=True)
        saved=folder/'turn.json'
        if saved.exists():return read_json(saved)
        pending=self.row.get('pending')
        if not pending:
            self.row['pending']={'phase':'model','turn':index,'cap':cap}
            self.row['reserved_output_tokens']+=cap
            self.event('turn_start',summary='请求下一组受控动作')
        else:cap=pending['cap']
        adapted=derive_prompt(self.system,str(self.system)+'\n'+POLICY,'agent_loop')
        constraints={k:copy.deepcopy(self.project[k]) for k in HARD_FACTS if k in self.project and (k not in NEW_DRAMA_FIELDS or self.project.get('production_mode') == 'one_sentence')}
        base_payload={**self.payload,'_agent_constraints':constraints}
        context={**base_payload,'_agent':{'role':self.role,'stage':self.stage,'tools':self.definitions,
            'materials':self.row['materials'],'current_candidate_sha256':self.row.get('candidate_sha256'),
            'remaining_turns':self.settings['max_turns']-self.row['turns'],'history':self.row['history']}}
        if selected=='native':
            from .llm import _messages
            messages=_messages(profile,str(adapted),json.dumps({**base_payload,'_agent_materials':self.row['materials']},ensure_ascii=False))
            messages+=copy.deepcopy(self.row['history'])
            outcome=native_turn(cfg,self.role,adapted,self.payload,messages,self.definitions,
                folder/'calls'/digest({'run':self.signature,'turn':index})[:32],max_tokens=cap,temperature=self.temperature,turn=index,evidence_payload=base_payload)
        else:
            parsed=list(folder.glob('calls/*/parsed.json'))
            if parsed:
                action=read_json(parsed[0])
            elif pending:
                self.stop('unknown','上次模型请求结果未知，未重投；请核实原回执')
            else:
                action=llm._request(cfg,self.role,adapted,self.payload,ACTION_SCHEMA,folder,max_tokens=cap,temperature=self.temperature,
                    content=[{'type':'text','text':json.dumps(context,ensure_ascii=False)}])
            receipts=list(folder.glob('calls/*/receipt.json'))
            usage=(read_json(receipts[0]).get('attempts') or [{}])[-1].get('usage') if receipts else {}
            outcome={'assistant':{'role':'assistant','content':json.dumps(action,ensure_ascii=False)},
                'actions':[{'id':f'json_{index}','tool':action['tool'],'arguments':action['arguments']}],'usage':usage or {}}
        write_json(saved,outcome);saved.chmod(0o600)
        return outcome

    def _read(self,args):
        name=args['name'];raw=json.dumps(self.materials[name],ensure_ascii=False)
        offset=args.get('offset',0);limit=args.get('limit',8000)
        if offset>len(raw):raise ValueError('材料分页超出范围')
        part=raw[offset:offset+limit]
        return {'source':name,'sha256':digest(self.materials[name]),'offset':offset,'characters':len(raw),'content':part,
                'next_offset':offset+len(part) if offset+len(part)<len(raw) else None,'truncated':False,'paging':'lossless_character_ranges'}

    def dispatch(self,action):
        name,args=action['tool'],action['arguments']
        validate(args,self.specs[name])
        if name in {'draft_artifact','check_artifact','submit_artifact'} and not self.row.get('plan'):
            raise ValueError('请先提交任务计划，再处理候选产物')
        if name=='plan':self.row['plan']=args['steps'];return {'state':'planned','steps':args['steps']}
        if name=='read_material':return self._read(args)
        if name=='draft_artifact':
            self.row.update(candidate=copy.deepcopy(args['artifact']),candidate_sha256=digest(args['artifact']),checked_sha256=None)
            return {'state':'candidate_only','sha256':self.row['candidate_sha256'],'quality_approval':False}
        if name=='check_artifact':
            if self.row['candidate'] is None:raise ValueError('尚无候选产物')
            errors=[{'path':'.'.join(map(str,e.absolute_path)),'message':e.message[:700]} for e in Draft202012Validator(self.schema).iter_errors(self.row['candidate'])][:16]
            self.row['checked_sha256']=None if errors else self.row['candidate_sha256']
            return {'state':'invalid' if errors else 'schema_checked_pending_business_validation','errors':errors,'sha256':self.row['candidate_sha256'],'quality_approval':False}
        if name=='submit_artifact':
            if args['sha256']!=self.row.get('checked_sha256') or args['sha256']!=self.row.get('candidate_sha256'):
                raise ValueError('只能提交当前已检查候选的精确哈希')
            self.guard()
            document={'schema':VERSION,'kind':'agent_task_result','project_id':self.project['id'],'role':self.role,
                'signature':self.signature,'sha256':args['sha256'],'artifact':self.row['candidate'],'quality_approval':False}
            write_json(self.directory/'result.json',document);(self.directory/'result.json').chmod(0o600)
            self.row.update(state='completed',result_sha256=args['sha256'])
            return {'state':'submitted_to_existing_business_validation','sha256':args['sha256'],'quality_approval':False}
        if name=='inspect_media':
            from .agent_media import inspect
            return inspect(self,args)
        if name=='request_supplement':
            return self._supplement(args)
        raise ValueError('工具不在本岗位白名单')

    def _supplement(self,args):
        from . import role_handoffs as h
        scope=args['shot_ids']
        episode=self.payload.get('current_episode') or self.project.get('screenplay_draft') or self.project.get('script') or {}
        allowed=self.payload.get('editable_shot_ids') or self.payload.get('only_revise_shot_ids') or [s['id'] for s in episode.get('shots',[])]
        if not set(scope)<=set(allowed):raise ValueError('补料范围超出当前任务授权镜头')
        from .team_control import is_locked
        if any(is_locked(self.project['id'],kind,sid) for sid in scope for kind in ('image','video')):
            raise ValueError('请求涉及锁定成果，请保留并交由出品人决定')
        issue={'code':'agent_material_request','severity':'blocking','description':args['reason'],
               'suggestion':'仅修订请求范围，保留其他成果并独立复验','shot_ids':scope,'evidence':'Agent工具请求；不是已确认质量结论'}
        request=None
        # The incoming controller transfer determines the upstream recipient.
        for ident in reversed(h.current()):
            transfer=h.detail(self.directory.parent.parent,self.project,ident)
            if transfer['target']==self.role and scope and set(scope)<=set(transfer['scope']):
                request=h.return_for_supplement(self.directory.parent.parent,self.project,ident,[issue]);break
        if request is None:
            upstream={'screenwriter':'story_editor','story_editor':'producer','storyboard_director':'screenwriter','visual_designer':'screenwriter'}.get(self.role)
            if upstream and upstream!='producer':
                transfer=h.submit(self.directory.parent.parent,self.project,source=self.role,target=upstream,
                    source_stage=self.stage,target_stage='supplement',contract='review_feedback',value={'issues':[issue]},scope=scope)
                request={'id':transfer['id'],'target':upstream,'scope':scope,'state':'awaiting_upstream'}
        self.row.update(state='handoff',handoff=request or {'target':'producer','scope':scope,'reason':args['reason']})
        return {'state':'handoff','request':self.row['handoff'],'reason':args['reason'],'shot_ids':scope}

    def run(self):
        from .config import resolve_llm
        from .prompt_workbench import _local_trial
        if not _local_trial(resolve_llm(self.config,self.role)):
            self.stop('blocked','Agent模型决策暂限已有内网推理，费用未知的供应商请求未启动')
        self.guard()
        if self.row['state']=='completed':
            result=read_json(self.directory/'result.json')
            if result['signature']!=self.signature or result['sha256']!=digest(result['artifact']):self.stop('blocked','Agent产物校验失败，原记录保留')
            validate(result['artifact'],self.schema)
            return result['artifact']
        if self.row['state'] in CLOSED:
            self.stop(self.row['state'],self.row.get('reason') or '此记录已结束，需显式重新启用后继续')
        for row in _rows(self.project):
            if row['id']!=self.signature and _uncertain(row,self.project):self.stop('blocked','项目存在结果未知的Agent请求，未新增模型请求')
            if row['state']=='closed' and row['snapshot']['selection_id']==self.project['agent_loop']['selection_id']:
                self.stop('blocked','已结束未知请求；请显式重新启用后继续')
        self.row['state']='running';self.event('agent_start',summary='读取冻结任务，恢复已完成动作')
        while self.row['state']=='running':
            self.guard()
            outcome=self.row.get('outcome')
            if not outcome:
                if len(self.row.get('started_actions',[]))>=self.settings['max_actions']:
                    self.stop('exhausted','Agent工具动作额度已用尽，未新增模型请求')
                try:outcome=self._model()
                except ProductionNeedsDecision:raise
                except ValueError as exc:self.stop('blocked',str(exc))
                except Exception:self.stop('unknown','模型请求未记录确定结果，未自动重投')
                self.guard()
                usage=outcome.get('usage',{}).get('completion_tokens')
                charge=usage if type(usage) is int and usage>=0 else self.row['pending']['cap']
                self.row['spent_output_tokens']+=charge;self.row['turns']+=1
                self.row.update(outcome=outcome,pending=None,action_offset=0)
                self.row['history'].append(outcome['assistant']);self.event('turn_response',summary='模型动作已持久化')
            actions=outcome['actions']
            # Multiple independent reads may be requested together; mutating
            # actions always run singly and are fenced before any dispatch.
            batch_valid=len(actions)==1 or all(a['tool']=='read_material' for a in actions)
            for index,action in enumerate(actions):
                if index<self.row.get('action_offset',0):continue
                self.guard();name=action['tool'];arg=action['arguments']
                token=digest({'turn':self.row['turns'],'index':index,'action':action})
                result_path=self.directory/('tool-'+token+'.json')
                started=self.row.setdefault('started_actions',[])
                if token not in started:
                    if len(started)>=self.settings['max_actions']:self.stop('exhausted','Agent工具动作额度已用尽')
                    started.append(token)
                exposed={k:v for k,v in arg.items() if k!='artifact'}
                if 'artifact' in arg:exposed['artifact_sha256']=digest(arg['artifact'])
                self.event('tool_start',tool=name,arguments=exposed,summary='校验并执行受控动作')
                if result_path.exists():
                    saved=read_json(result_path);result=saved['result']
                    if saved['sha256']!=digest(result):self.stop('blocked','工具结果校验失败')
                    # Candidate mutations are deterministic and safe to replay;
                    # paid inspection results are recovered by their own journal.
                    if result.get('state')!='rejected' and name in {'plan','draft_artifact','check_artifact','submit_artifact','request_supplement'}:
                        result=self.dispatch(action)
                else:
                    try:
                        if not batch_valid or name not in self.specs:raise ValueError('只允许并行独立材料读取；其他动作须逐项执行')
                        result=self.dispatch(action)
                    except (ValueError,KeyError,ValidationError) as exc:result={'state':'rejected','error':str(exc)[:1000]}
                    write_json(result_path,{'sha256':digest(result),'result':result});result_path.chmod(0o600)
                self.guard()
                fp=digest({'tool':name,'arguments':arg,'result':result})
                if sum(e.get('result_sha256')==fp for e in self.row['events'] if e['event']=='tool_end')>=2:
                    self.stop('exhausted','Agent重复动作没有取得新进展，已有产物保留')
                self.row['history'].append({'role':'tool','tool_call_id':action['id'],'name':name,'content':json.dumps(result,ensure_ascii=False)})
                self.row['action_offset']=index+1
                self.event('tool_end',tool=name,status=result.get('state'),result_sha256=fp,summary=result.get('error') or result.get('reason') or result.get('state','已取得材料'))
                if self.row['state']=='handoff':
                    self.event('agent_end',status='handoff',summary='请求已持久化，交给上游或出品人')
                    raise SupplementRequested(result['reason'],result['shot_ids'],result['request'])
            self.row.pop('outcome',None);self.row.pop('action_offset',None)
            if self.row['state']=='completed':
                result={'schema':VERSION,'kind':'agent_task_result','project_id':self.project['id'],'role':self.role,
                    'signature':self.signature,'sha256':self.row['candidate_sha256'],'artifact':self.row['candidate'],'quality_approval':False}
                write_json(self.directory/'result.json',result);(self.directory/'result.json').chmod(0o600)
                self.event('agent_end',status='completed',summary='候选已交回原业务校验；未授予质量或发布批准')
                return copy.deepcopy(self.row['candidate'])
            _save(self.project,self.row)


def run(project,config,role,stage,system,payload,schema,signature,*,max_tokens,temperature):
    loop=Loop(project,config,role,stage,system,payload,schema,signature,max_tokens,temperature)
    with (loop.directory/'.lock').open('a') as fd:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ProductionNeedsDecision('同一Agent任务正在执行','agent_in_progress')
        durable=next((r for r in _rows(project) if r['id']==signature),None)
        if durable is not None:loop.row=durable
        try:return loop.run()
        except ProductionPaused:
            loop.row['state']='paused';loop.event('agent_paused',summary='工具边界暂停，前序成果保留');raise
        except ProductionNeedsDecision as exc:
            if loop.row['state']=='running':
                loop.row.update(state='superseded' if exc.reason.startswith('agent_stale') else 'blocked',reason=str(exc))
                loop.event('agent_end',status=loop.row['state'],summary=str(exc))
            raise
        except Exception:
            # A tool may have delegated a model request. Preserve its intent and
            # prevent a worker retry from silently resubmitting an unknown call.
            if (loop.row.get('pending') or {}).get('phase')=='tool_model':
                loop.row.update(state='unknown',reason='工具模型请求结果未确定，请核实原回执')
            else:
                loop.row.update(state='blocked',reason='工具未能完成，已保留动作和候选，请检查本地日志')
            loop.event('agent_end',status=loop.row['state'],summary=loop.row['reason'])
            raise
