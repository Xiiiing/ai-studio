"""Whole-drama metadata on existing projects, revisions and durable jobs."""
from __future__ import annotations

import copy
import json
import re
import time
import uuid

from . import store
from studio.core import fingerprint

MAX_EPISODE_SECONDS = 900


class PlanValidationError(ValueError):
    """A candidate contract problem the author can repair, not a service failure."""
    def __init__(self, message, *, role='screenwriter', episodes=()):
        super().__init__(message)
        self.repair_targets = [{'role': role, 'episode_numbers': list(episodes)}]


def source_excerpt(source, number, count):
    headers=list(re.finditer(r'(?im)^\s*(?:第\s*([零〇一二两三四五六七八九十百0-9]+)\s*集|(?:episode|ep)\s*([0-9]+)\b)',source))
    def ordinal(value):
        if value.isdecimal(): return int(value)
        digits=dict(zip('零〇一二两三四五六七八九',[0,0,1,2,2,3,4,5,6,7,8,9]))
        if '百' in value:
            left,right=value.split('百',1)
            return digits.get(left,1)*100+(ordinal(right) if right else 0)
        if '十' in value:
            left,right=value.split('十',1)
            return digits.get(left,1)*10+(digits.get(right,0) if right else 0)
        return int(''.join(str(digits[c]) for c in value))
    for i,header in enumerate(headers):
        if ordinal(header[1] or header[2])==number:
            return source[header.start():headers[i+1].start() if i+1<len(headers) else len(source)][:8000]
    # Unmarked source remains intact on the drama; this is explicitly an excerpt.
    if not source: return ''
    start=(number-1)*len(source)//count
    return source[start:number*len(source)//count][:8000]


def integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label}须为{low}–{high}的整数")
    return value


def text(value, label, low=1, high=6000):
    if not isinstance(value, str) or not low <= len(value.strip()) <= high:
        raise ValueError(f"{label}须为{low}–{high}字")
    return value.strip()


def record(kind, title, brief, **values):
    pid = uuid.uuid4().hex[:12]
    return {"id": pid, "slug": "factory-" + pid, "record_kind": kind,
        "title": title, "brief": brief, "format": "motion_comic", "duration_seconds": 60,
        "status": "queued", "stage": "queued", "progress": 0, "revision": 1,
        "created_at": store.now(), "updated_at": store.now(), "workflow_version": 3,
        "team_mode": "delegated", "sources": [], "submitted_sources": [], "creation_mode": "original",
        "proposals": [], "selected_story": None, "styles": [], "selected_style": None,
        "script": None, "shots": [], "decisions": [], "answers": {}, "production": {},
        "delivery": None, "error": None, "pause_requested": False, "proposal_generation": 1,
        "script_generation": 1, "style_generation": 1, "production_revision": 1,
        "budget": {"gpu_minutes": 120., "spent_gpu_minutes": 0., "wall_minutes": 180., "max_attempts": 3},
        **values}


def insert(db, project):
    from .prompt_workbench import pin_defaults
    parent_id = project.get("episode_id") or project.get("drama_id")
    parent = store._read(db, parent_id) if parent_id else None
    from .drama_intent import inherit
    if parent:
        inherit(project, parent)
    if parent and "agent_loop" in parent:
        project.setdefault("agent_loop", copy.deepcopy(parent["agent_loop"]))
    if parent and "generation_pipeline" in parent:
        project.setdefault("generation_pipeline", copy.deepcopy(parent["generation_pipeline"]))
    if parent and "resolved_visual_style" in parent:
        project.setdefault("resolved_visual_style", copy.deepcopy(parent["resolved_visual_style"]))
    project.setdefault("prompt_snapshot", copy.deepcopy(parent.get("prompt_snapshot")) if parent else pin_defaults(db))
    db.execute("INSERT INTO projects(id,updated,revision,snapshot) VALUES(?,?,?,?)",
               (project['id'], time.time(), project['revision'], store.encoded(project)))
    store._event(db, project['id'], '已建立' + {'drama': '整剧项目', 'episode': '剧集', 'scene_unit': '制作单元'}.get(project['record_kind'], '记录'), 'created')


def owned_folder(project, root):
    from studio.core import within, read_json, write_json
    folder=within(root/'production',project['slug'])
    owner=folder/'factory_owner.json'
    if owner.exists():
        if read_json(owner).get('project_id')!=project['id']: raise ValueError('制作目录归属不匹配')
    else:
        if folder.exists() and any(folder.iterdir()): raise ValueError('已有未登记制作目录被保留，不能自动接管')
        folder.mkdir(parents=True,exist_ok=True)
        write_json(owner,{'project_id':project['id'],'schema':'autonomous_factory.owner.v1'})
    return folder


def require(db, pid, kind='drama'):
    project = store._read(db, pid)
    if project.get('record_kind') != kind:
        raise ValueError('记录类型与操作不匹配')
    return project


def cas(project, payload):
    if payload.get('expected_revision') != project['revision']:
        raise ValueError('内容已更新，请刷新后提交')


def replay(project, payload):
    key = text(payload.get('request_id'), '请求标识', 1, 100)
    signature = fingerprint({k: v for k, v in payload.items() if k not in {'request_id', 'expected_revision'}})
    saved = project.setdefault('operation_requests', {}).get(key)
    if saved and saved['signature'] != signature:
        raise ValueError('请求标识已用于其他操作')
    return key, signature, saved


def create(payload):
    allowed = {'title', 'brief', 'source_text', 'source_name', 'episode_count', 'duration_seconds', 'format', 'genre', 'visual_style', 'request_id', 'automation', 'production_mode'}
    if set(payload) - allowed: raise ValueError('整剧输入包含未知字段')
    mode = payload.get('production_mode')
    if mode not in (None, 'one_sentence'):
        raise ValueError('未知制作模式')
    payload = copy.deepcopy(payload)
    if mode == 'one_sentence':
        payload.setdefault('episode_count', 3)
        payload.setdefault('duration_seconds', 60)
        payload.setdefault('format', 'animation')
        payload.setdefault('title', str(payload.get('brief', '')).strip()[:24] or '新建剧集')
        payload.setdefault('automation', {'enabled': True, 'episode_numbers': list(range(1, integer(payload['episode_count'], '总集数', 1, 100) + 1))})
    source = payload.get('source_text', '')
    if source:
        source = text(source, '剧本', 5, 60000)
        if '\x00' in source: raise ValueError('请导入UTF-8文本剧本')
    source_name = payload.get('source_name', '')
    if not isinstance(source_name, str) or len(source_name) > 150 or (source_name and not source_name.lower().endswith(('.txt', '.md', '.markdown'))):
        raise ValueError('导入支持TXT或Markdown文件')
    title = text(payload.get('title'), '剧名', 1, 100)
    brief = text(payload.get('brief') or source[:1000], '创作方向', 5, 6000)
    count = integer(payload.get('episode_count'), '总集数', 1, 100)
    duration = integer(payload.get('duration_seconds', 300), '每集秒数', 15, MAX_EPISODE_SECONDS)
    fmt = payload.get('format', 'motion_comic')
    if not isinstance(fmt,str) or fmt not in {'motion_comic', 'animation', 'live_action'}: raise ValueError('请选择动态漫或H3视频路线')
    from .drama_automation import normalize, authorize
    automation = normalize(payload.get('automation'), count)
    if mode and (not automation['enabled'] or automation['episode_numbers'] != list(range(1, count + 1))):
        raise ValueError('一句话整剧模式需要授权全部剧集连续制作')
    key = 'drama:' + text(payload.get('request_id'), '请求标识', 1, 80)
    signature = fingerprint(payload)
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        prior = db.execute('SELECT * FROM idempotency WHERE key=?', (key,)).fetchone()
        if prior:
            if prior['request_hash'] != signature: raise ValueError('请求标识已用于其他输入')
            return snapshot(prior['project_id'], db=db)
        p = record('drama', title, brief, format=fmt, duration_seconds=duration,
            episode_count=count, source_text=source, source_name=source_name,
            genre=text(payload.get('genre') or '剧情', '题材', 1, 100),
            visual_style=text(payload.get('visual_style') or '统一人物与场景画风', '画风', 1, 2000),
            plan_version=0, plans=[], episodes=[], batches=[], series_key='')
        p['automation'] = authorize(automation, payload['request_id'])
        if mode:
            from .drama_intent import initialize
            from .config import load_config
            initialize(p, load_config(), payload['request_id'])
        p['series_key'] = 'drama_' + p['id']
        insert(db, p)
        db.execute('INSERT INTO idempotency VALUES(?,?,?)', (key, p['id'], signature))
        store._enqueue(db, p['id'], 'drama_plan')
        return snapshot(p['id'], db=db)


def list_dramas():
    with store.connection() as db:
        return {'dramas': [snapshot(row['id'], db=db, details=False) for row in db.execute(
            "SELECT id FROM projects WHERE json_extract(snapshot,'$.record_kind')='drama' ORDER BY updated DESC")]}


def snapshot(pid, *, db=None, details=True):
    if db is None:
        with store.connection() as connection:
            return snapshot(pid, db=connection, details=details)
    p = require(db, pid)
    episodes = [store._read(db, eid) for eid in p['episodes']]
    # Snapshot reads never dispatch, retry, poll a provider or mutate aggregation.
    for episode in episodes:
        episode.pop('operation_requests', None)
        episode.pop('execution_config', None)
        for field in ('plan','planning_cache','screenplay_draft','script','episode','shots','team_tasks','team_state'):
            episode.pop(field,None)
        if episode.get('delivery'):
            episode['delivery']={k:v for k,v in episode['delivery'].items() if k not in {'sources','artifacts','scenes'}}
    result = {k: copy.deepcopy(v) for k, v in p.items() if k not in {'operation_requests', 'source_text', 'planning_cache', 'plan_repair_basis', 'plan_history', 'execution_config'}}
    from .drama_automation import state
    result['automation'] = copy.deepcopy(p.get('automation', {'enabled': False}))
    result['automation_state'] = state(p, episodes, db=db)
    from .drama_intent import enabled, budget_snapshot
    if enabled(p):
        result['whole_drama_budget'] = budget_snapshot(db, p)
        signature = fingerprint([(e.get('episode_review') or {}).get('signature') for e in episodes])
        if result.get('delivery_bundle'):
            result['delivery_bundle']['current'] = result['delivery_bundle'].get('source_signature') == signature and all(e['status'] in {'approved', 'awaiting_final'} for e in episodes)
            from .drama_delivery import review_current
            result['delivery_bundle']['current'] = result['delivery_bundle']['current'] and all(review_current(e) for e in episodes)
    result['episodes'] = episodes
    result['batches'] = [{**b, 'members': [{'id': e['id'], 'number': e['episode_number'], 'status': e['status'],
        'progress': e['progress'], 'error': e.get('error')} for e in episodes if e['id'] in b['episode_ids']]}
        for b in p['batches']]
    for batch in result['batches']:
        members=[e for e in episodes if e['id'] in batch['episode_ids']]
        states={e['status'] for e in members}
        batch['status']=('approved' if states=={'approved'} else 'cancelled' if states=={'cancelled'}
            else 'awaiting_final' if states <= {'approved','awaiting_final'} else 'blocked' if states & {'blocked','failed'}
            else 'paused' if 'paused' in states else 'producing' if states & {'queued','producing','scripting','waiting_remote','creating','visual_development'}
            else 'blocked' if 'waiting_dependency' in states else 'queued')
        batch['progress']=round(sum(e['progress'] for e in members)/max(1,len(members)))
        batch['spent_gpu_minutes']=round(sum(e['budget'].get('spent_gpu_minutes',0) for e in members),3)
        batch['authorized_gpu_minutes']=sum(e.get('total_authorized_gpu_minutes',e['budget']['gpu_minutes']) for e in members)
        batch['budget_scope']='planned_units' if all(e.get('unit_ids') or e['duration_seconds']<=90 for e in members) else 'per_unit_pending_scene_plan'
    result['affected_episodes'] = [{'id': e['id'], 'number': e['episode_number'], 'pinned_version': e['plan_version']}
        for e in episodes if e['plan_version'] != p['plan_version']]
    indexed={e['id']:e for e in episodes}
    result['dependency_impacts']=[{'episode_id':e['id'],'number':e['episode_number'],'dependency_number':dep['number']}
        for e in episodes for dep in e.get('dependency_context',[]) if
        (indexed.get(dep['episode_id'],{}).get('delivery') or {}).get('source_signature')!=dep['delivery_signature']]
    if details:
        from .drama_workflow import build
        result['source_text'] = p['source_text']
        result['review_history'] = [{k: copy.deepcopy(entry.get(k)) for k in ('cycle','attempt','at','review','returned_to','phase','reused')}
            for entry in p.get('plan_history', [])]
        job = db.execute("SELECT status,started FROM jobs WHERE project_id=? AND kind='drama_plan' AND status IN ('queued','running') ORDER BY started DESC LIMIT 1", (pid,)).fetchone()
        result['workflow'] = build(p, episodes=episodes, batches=result['batches'],
            active_job=job is not None, current_job=dict(job) if job else None)
    return result


def validate_plan(plan, project):
    from jsonschema import validate, ValidationError
    from .drama_planning import plan_schema
    try: validate(plan, plan_schema(project['episode_count']))
    except ValidationError as exc:
        path = list(exc.absolute_path)
        raise PlanValidationError('全剧规划字段无效：' + '.'.join(map(str, path)),
            role='screenwriter' if path and path[0] == 'episodes' else 'story_editor') from exc
    ids = [c['character_id'] for c in plan['characters']]
    if len(set(ids)) != len(ids): raise PlanValidationError('全剧角色ID须唯一，请修正设定及对应引用', role='story_editor')
    names = {}
    for character in plan['characters']:
        name = character['name'].strip().casefold()
        if not name:
            raise PlanValidationError('全剧角色须有可识别的名称', role='story_editor')
        if name in names:
            raise PlanValidationError(f"角色名称重复：{names[name]} 与 {character['character_id']} 均名为“{character['name']}”。"
                '请区分不同角色；同一角色应统一ID和身份，不可让后续大纲猜测。', role='story_editor')
        names[name] = character['character_id']
    numbers = [e['number'] for e in plan['episodes']]
    if sorted(numbers) != list(range(1, project['episode_count'] + 1)): raise PlanValidationError('分集大纲须覆盖全部集数且不重复')
    seen = {}
    for e in plan['episodes']:
        target = {'episodes': [e['number']]}
        if not set(e['characters']) <= set(ids) or not e['characters']: raise PlanValidationError('剧集必须引用全剧已定义角色', **target)
        if len(e['characters'])!=len(set(e['characters'])): raise PlanValidationError('剧集角色不能重复', **target)
        if project['duration_seconds']<=90 and len(e['characters'])>4: raise PlanValidationError('短片规格每集最多4位角色', **target)
        if any(dep >= e['number'] or dep < 1 for dep in e['depends_on']):
            raise PlanValidationError(f"第{e['number']}集：剧集依赖只能引用前面的集数；仅剧情承接不需要制作依赖。", **target)
        if e['synopsis'].strip() == e['opening_state'].strip() == e['ending_state'].strip():
            raise PlanValidationError(f"第{e['number']}集仅重复起止状态，没有本集新事件。"
                '请从前集结尾继续发展本集剧情，不要把收尾状态作为整集大纲。', **target)
        from .drama_intent import enabled
        if enabled(project):
            beats = e.get('beats', [])
            if not 2 <= len(beats) <= 8 or len({b['id'] for b in beats}) != len(beats):
                raise PlanValidationError('一句话模式每集需要2至8个唯一必要事件', **target)
        content = fingerprint({k: e[k] for k in ('title','synopsis','opening_state','ending_state','characters')})
        if content in seen:
            raise PlanValidationError(f"第{e['number']}集与第{seen[content]}集的大纲内容完全重复。"
                '请保留前集已发生的事件，在本集推进新的冲突或结果，不要复制前集高潮。', **target)
        seen[content] = e['number']
    return copy.deepcopy(plan)


def _confirm_reviewed(db, p, plan, *, actor='producer', authorization=None):
    pid = p['id']
    p['plan_version'] += 1
    p['plans'].append({'version': p['plan_version'], 'document': plan, 'sha256': fingerprint(plan), 'confirmed_at': store.now(), 'actor': actor, **({'authorization': copy.deepcopy(authorization)} if authorization else {})})
    bible={k:copy.deepcopy(plan[k]) for k in ('world','visual_rules','scenes','props')}
    bible.update(schema_version=1,characters=[{k:v for k,v in c.items() if k!='voice_id'} for c in plan['characters']])
    from .series_bible import validate_document
    validate_document(bible)
    db.execute('INSERT INTO series_bibles VALUES(?,?,?,?,?)',
        (p['series_key'],p['plan_version'],store.encoded(bible),fingerprint(bible),time.time()))
    p.update(status='ready', stage='episodes', progress=100)
    if not p['episodes']:
        for outline in sorted(plan['episodes'], key=lambda e: e['number']):
            e = record('episode', f"第{outline['number']}集 · {outline['title']}", outline['synopsis'],
                drama_id=pid, parent_id=pid, series_key=p['series_key'], episode_number=outline['number'],
                duration_seconds=p['duration_seconds'], format=p['format'], plan_version=p['plan_version'],
                outline=outline, plan=plan, status='draft', unit_ids=[], batch_id=None,
                source_excerpt=source_excerpt(p['source_text'],outline['number'],p['episode_count']))
            insert(db, e)
            p['episodes'].append(e['id'])


def confirm(pid, payload):
    if set(payload) - {'expected_revision', 'request_id', 'plan'}: raise ValueError('规划确认包含未知字段')
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        p = require(db, pid)
        key, signature, saved = replay(p, payload)
        if saved: return snapshot(pid, db=db)
        cas(p, payload)
        if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='running'",(pid,)).fetchone():
            raise ValueError('规划正在执行，请等待本轮完成')
        plan = validate_plan(payload.get('plan', p.get('draft_plan')), p)
        edited = fingerprint(plan) != fingerprint(p.get('draft_plan'))
        automated_edit = (edited and p.get('automation', {}).get('enabled')
            and (bool(p['episodes']) or p.get('pause_requested') or p['automation'].get('suspended')))
        if automated_edit:
            if not p['automation'].get('suspended'):
                raise ValueError('请先暂停连续制作，再修改全剧规划')
            members = [store._read(db, eid) for eid in p['episodes']]
            ids = [pid, *p['episodes'], *(uid for e in members for uid in e.get('unit_ids', []))]
            if any(db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='running'", (ident,)).fetchone() for ident in ids):
                raise ValueError('请等待当前制作任务到达安全边界，再修改全剧规划')
            # Review is allowed while episode production remains suspended.
            p['pause_requested'] = False
        elif p.get('pause_requested'):
            raise ValueError('请先继续规划，再确认全剧设定')
        if not (p.get('plan_review') or {}).get('passed') or fingerprint(plan) != fingerprint(p.get('draft_plan')):
            # Edited plans go back through independent review before confirmation.
            from .drama_planning import start_cycle
            start_cycle(p, edited=True)
            p.update(draft_plan=plan, plan_review=None, plan_review_sha256=None, status='queued', stage='plan_review')
            store._enqueue(db, pid, 'drama_plan')
        else:
            if p['plans'] and fingerprint(p['plans'][-1]['document'])==fingerprint(plan):
                p['operation_requests'][key]={'signature':signature}
                store._write(db,p)
                return snapshot(pid,db=db)
            _confirm_reviewed(db, p, plan)
        p['operation_requests'][key] = {'signature': signature}
        store._write(db, p)
        store._event(db, pid, '全剧规划已确认' if p['status'] == 'ready' else '修改后的规划等待独立审核', 'drama_plan')
        from .drama_automation import reconcile
        reconcile(db, p)
        return snapshot(pid, db=db)


def _generate_batch(db, p, numbers, budget, config):
    config=copy.deepcopy(p.get('execution_config') or config)
    policy = p.get('automation', {}).get('production_recovery')
    if policy:
        from .production_execution import apply_configuration
        config = apply_configuration(config, policy)
    config.setdefault('creative',{})['max_review_rounds']=budget['max_attempts']
    chosen = [store._read(db, eid) for eid in p['episodes'] if store._read(db, eid)['episode_number'] in numbers]
    if any(e['status'] != 'draft' for e in chosen): raise ValueError('已开始的剧集请使用继续、返修或失败重试')
    batch = {'id': uuid.uuid4().hex[:12], 'episode_ids': [e['id'] for e in chosen], 'episode_numbers': sorted(numbers),
        'plan_version': p['plan_version'], 'budget': copy.deepcopy(budget), 'created_at': store.now(), 'status': 'queued',
        'config_signature': fingerprint(config)}
    # Private configuration remains a backend-only snapshot; never returned by the API.
    for e in chosen:
        outline = next(o for o in p['plans'][-1]['document']['episodes'] if o['number'] == e['episode_number'])
        e.update(outline=outline, plan=p['plans'][-1]['document'], plan_version=p['plan_version'],
            status='queued', stage='queued', budget=copy.deepcopy(budget), batch_id=batch['id'],
            execution_config=copy.deepcopy(config), pause_requested=False)
        store._write(db, e)
    p['batches'].append(batch)
    return batch


def generate(pid, payload):
    if set(payload) - {'expected_revision', 'request_id', 'episode_numbers', 'budget'}: raise ValueError('生成批次包含未知字段')
    from .config import load_config
    config = load_config()
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        p = require(db, pid)
        key, signature, saved = replay(p, payload)
        if saved: return snapshot(pid, db=db)
        cas(p, payload)
        if not p['plan_version']: raise ValueError('请先确认全剧规划')
        from .drama_automation import validate_numbers, validate_budget
        numbers = validate_numbers(payload.get('episode_numbers'), p['episode_count'])
        budget = validate_budget(payload.get('budget', {'gpu_minutes': 120, 'wall_minutes': 180, 'max_attempts': 3}))
        batch = _generate_batch(db, p, numbers, budget, config)
        p['operation_requests'][key] = {'signature': signature, 'batch_id': batch['id']}
        p.update(status='producing', pause_requested=False, progress=0)
        store._write(db, p)
        dispatch(db, p)
        return snapshot(pid, db=db)


def dispatch(db, drama):
    """Publish one eligible episode to the existing queue, in episode order."""
    if drama.get('pause_requested') or drama['status'] == 'cancelled': return
    if drama.get('automation', {}).get('enabled'):
        from .drama_automation import _plan_changed
        if drama['automation'].get('suspended') or _plan_changed(drama): return
    episodes = [store._read(db, eid) for eid in drama['episodes']]
    if any(db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running')", (e['id'],)).fetchone() for e in episodes): return
    by_number = {e['episode_number']: e for e in episodes}
    for e in sorted(episodes, key=lambda e: e['episode_number']):
        if e['status'] not in {'queued', 'waiting_dependency'} or e.get('pause_requested'): continue
        deps = e['outline']['depends_on']
        if any(by_number[n]['status'] not in {'awaiting_final', 'approved'} for n in deps):
            if e['status'] != 'waiting_dependency':
                e.update(status='waiting_dependency', stage='dependency')
                store._write(db, e)
            continue
        e['status'] = 'queued'
        store._write(db, e)
        store._enqueue(db, e['id'], 'drama_episode')
        return


def after_episode(pid, job=None):
    e = store.get(pid, False)
    if not e.get('drama_id'): return
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        if job:
            owner = db.execute('SELECT lease FROM jobs WHERE id=?', (job['id'],)).fetchone()
            if not owner or owner['lease'] != job.get('lease'):
                return
        p = require(db, e['drama_id'])
        aggregate(db,p)
        store._write(db, p)
        dispatch(db, p)
        from .drama_delivery import maybe_enqueue
        maybe_enqueue(db, p)


def aggregate(db,drama):
    members=[store._read(db,eid) for eid in drama['episodes']]
    for episode in members:
        uid = episode.get('active_unit_id')
        if (uid not in episode.get('unit_ids', []) or episode['status'] not in {'blocked','failed','paused','cancelled','waiting_remote'}
                or db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='running'", (episode['id'],)).fetchone()):
            continue
        unit = store._read(db, uid)
        if unit['status'] in {'unit_ready','cancelled'}:
            continue
        desired = {'status': 'blocked' if episode['status'] == 'failed' else episode['status']}
        if desired['status'] == 'blocked':
            desired.update(error=episode.get('error'), needs_decision=copy.deepcopy(episode.get('needs_decision')))
        if desired['status'] in {'paused','cancelled'}:
            desired['pause_requested'] = True
        if any(unit.get(k) != v for k, v in desired.items()):
            unit.update(desired)
            store._write(db, unit)
    states={e['status'] for e in members}
    if not drama.get('pause_requested') and drama['status']!='cancelled':
        drama['status']=('approved' if states=={'approved'} else 'blocked' if states & {'blocked','failed'} else 'paused' if 'paused' in states
            else 'producing' if states & {'queued','producing','scripting','waiting_remote','creating','visual_development'}
            else 'awaiting_final' if 'awaiting_final' in states
            else 'blocked' if 'waiting_dependency' in states else 'ready')
        failure = next((e for e in members if e['status'] in {'blocked','failed'}), None)
        if failure:
            drama['error'] = failure.get('error') or f'第{failure["episode_number"]}集制作已停止'
            drama['needs_decision'] = {**copy.deepcopy(failure.get('needs_decision') or {}),
                'episode_id': failure['id'], 'episode_number': failure['episode_number']}
        elif drama['status'] in {'producing','awaiting_final','approved','ready'}:
            drama.update(error=None, needs_decision=None)
    drama['budget']['spent_gpu_minutes']=round(sum(e['budget'].get('spent_gpu_minutes',0) for e in members),3)
    drama['progress']=round(sum(e['progress'] for e in members)/max(1,len(members)))


def settle_execution(pid, job):
    """Publish child outcomes only at an owned worker boundary, never on GET."""
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        owner = db.execute('SELECT lease FROM jobs WHERE id=?', (job['id'],)).fetchone()
        if not owner or owner['lease'] != job.get('lease'):
            return
        current = store._read(db, pid)
        episode = current if current.get('record_kind') == 'episode' else (
            store._read(db, current['episode_id']) if current.get('record_kind') == 'scene_unit' else None)
        if not episode or not episode.get('drama_id'):
            return
        root = require(db, episode['drama_id'])
        review_wait = (current.get('production') or {}).get('review_transport_recovery')
        if review_wait and review_wait.get('status') == 'waiting':
            if current['status'] in {'blocked','failed'}:
                review_wait.update(status='stopped', message='审核服务自动重试已停止，保留原素材和错误证据。')
                store._write(db, current)
            elif current['status'] in {'unit_ready','awaiting_final','approved'}:
                review_wait.update(status='resolved', message='已保存素材复核完成，系统继续制作。')
                store._write(db, current)
        if current.get('record_kind') == 'episode':
            uid = episode.get('active_unit_id')
            if uid and uid in episode.get('unit_ids', []):
                unit = store._read(db, uid)
                if unit['status'] != 'unit_ready' and unit['status'] != 'cancelled':
                    if episode['status'] in {'blocked','failed'}:
                        unit.update(status='blocked', error=episode.get('error'),
                            needs_decision=copy.deepcopy(episode.get('needs_decision')),
                            message=episode.get('message'))
                        waiting = (unit.get('production') or {}).get('review_transport_recovery')
                        if waiting and waiting.get('status') == 'waiting':
                            waiting.update(status='stopped', message='审核服务自动重试已停止，保留原素材和错误证据。')
                        store._write(db, unit)
                    elif episode['status'] in {'paused','cancelled','waiting_remote'}:
                        unit.update(status=episode['status'], error=None)
                        if episode['status'] in {'paused','cancelled'}:
                            unit['pause_requested'] = True
                        store._write(db, unit)
        elif current['id'] in episode.get('unit_ids', []) and episode.get('active_unit_id') == current['id']:
            if episode['status'] != 'cancelled':
                if current['status'] in {'blocked','failed'} and not episode.get('pause_requested'):
                    episode.update(status='blocked', error=current.get('error'),
                        needs_decision={**copy.deepcopy(current.get('needs_decision') or {}), 'unit_id': current['id']})
                elif current['status'] in {'paused','waiting_remote'}:
                    episode.update(status=current['status'], error=None)
                elif (current['status'] == 'unit_ready' and episode['status'] in {'blocked','waiting_remote','awaiting_final','approved'}
                        and not episode.get('pause_requested') and not root.get('pause_requested')
                        and not root.get('automation', {}).get('suspended')
                        and not db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running','waiting_remote')",
                                           (episode['id'],)).fetchone()):
                    episode.update(status='queued', pause_requested=False, error=None, needs_decision=None,
                        production_revision=episode['production_revision']+1)
                    store._enqueue(db, episode['id'], 'drama_episode')
                store._write(db, episode)
        aggregate(db, root)
        store._write(db, root)
        dispatch(db, root)
        from .drama_delivery import maybe_enqueue
        maybe_enqueue(db, root)


def _wait_for_resources(db, drama, request_id):
    """Explicitly queue the authorized unfinished work behind read-only checks."""
    automation = drama.get('automation') or {}
    if not automation.get('enabled') or drama['status'] == 'cancelled':
        raise ValueError('资源等待需要已有连续制作授权且项目未取消')
    episodes = [store._read(db, eid) for eid in drama.get('episodes', [])]
    selected = [e for e in episodes if e['episode_number'] in automation['episode_numbers']
                and e['status'] not in {'approved', 'awaiting_final', 'cancelled', 'draft'}]
    units = [store._read(db, uid) for e in selected for uid in e.get('unit_ids', [])]
    if not selected:
        raise ValueError('没有可等待制作资源的已授权剧集')
    prior = automation.get('resource_wait') or {}
    if (prior.get('enabled') and not drama.get('pause_requested') and not automation.get('suspended')
            and any(db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running','waiting_remote')",
                               (e['id'],)).fetchone() for e in selected)):
        return
    for record in [drama, *selected, *units]:
        if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running','waiting_remote')",
                      (record['id'],)).fetchone():
            raise ValueError('请等待当前任务到达安全边界，资源等待不会中断或重投模型')
        if (record.get('production') or {}).get('active_gpu_leases', 0):
            raise ValueError('请先对账现有 GPU 执行，资源等待不会中断或重投模型')
        from .production_execution import _guard_remote_evidence
        _guard_remote_evidence(db, record)
    marker = {'enabled': True, 'status': 'waiting', 'reason': 'model_resources_unavailable',
        'message': '等待 Wkp 制作资源，恢复后系统自动继续。', 'poll_seconds': 300,
        'authorized_by': 'producer', 'authorization_id': request_id, 'authorized_at': store.now()}
    for episode in selected:
        episode.setdefault('production', {})['resource_wait'] = copy.deepcopy(marker)
        episode.update(status='queued', pause_requested=False, error=None, needs_decision=None)
        store._write(db, episode)
    for unit in units:
        if unit['status'] in {'unit_ready', 'cancelled'}:
            continue
        unit.setdefault('production', {})['resource_wait'] = copy.deepcopy(marker)
        unit.update(status='queued', pause_requested=False, error=None, needs_decision=None)
        store._write(db, unit)
    automation.update(resource_wait=copy.deepcopy(marker), suspended=False)
    drama.update(status='producing', pause_requested=False, error=None, needs_decision=None)
    store._event(db, drama['id'], '已授权等待制作资源；系统只读预检并自动恢复，保留原预算、用量和素材，不停用其他模型任务。',
                 'production_resource_wait_authorized', marker)


def control(pid, payload):
    allowed = {'expected_revision', 'expected_episode_revision', 'request_id', 'action', 'episode_id', 'batch_id', 'feedback', 'unit_id', 'shot_ids', 'automation'}
    if set(payload) - allowed: raise ValueError('控制请求包含未知字段')
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        p = require(db, pid)
        key, signature, saved = replay(p, payload)
        if saved: return snapshot(pid, db=db)
        cas(p, payload)
        action = payload.get('action')
        if action == 'wait_for_resources':
            if set(payload) - {'action', 'request_id', 'expected_revision'}:
                raise ValueError('资源等待请求包含未知字段')
            _wait_for_resources(db, p, key)
            p['operation_requests'][key] = {'signature': signature}
            store._write(db, p)
            dispatch(db, p)
            return snapshot(pid, db=db)
        if action == 'enable_autonomous_repair':
            if set(payload) - {'action', 'request_id', 'expected_revision'}:
                raise ValueError('执行版本应用请求包含未知字段')
            from .production_execution import activate
            activate(db, p, key)
            p['operation_requests'][key] = {'signature': signature}
            store._write(db, p)
            return snapshot(pid, db=db)
        if action == 'enable_automation':
            from .drama_automation import enable, reconcile
            enable(db, p, payload.get('automation'), key)
            p['operation_requests'][key] = {'signature': signature}
            store._write(db, p)
            reconcile(db, p)
            return snapshot(pid, db=db)
        if 'automation' in payload: raise ValueError('仅启用连续制作时可传入自动制作配置')
        if not isinstance(action,str) or action not in {'pause', 'resume', 'cancel', 'retry', 'approve', 'revise', 'reedit'}: raise ValueError('不支持的剧集操作')
        if p.get('automation', {}).get('enabled') and not payload.get('episode_id') and not payload.get('batch_id'):
            if action in {'pause', 'cancel'}: p['automation']['suspended'] = True
            elif action in {'resume', 'retry'}: p['automation']['suspended'] = False
        targets = [store._read(db, eid) for eid in p['episodes'] if (not payload.get('episode_id') or eid == payload['episode_id'])]
        if payload.get('batch_id'): targets = [e for e in targets if e.get('batch_id') == payload['batch_id']]
        if not targets:
            if p['episodes'] or payload.get('episode_id') or payload.get('batch_id') or action not in {'pause','cancel','resume','retry'}:
                raise ValueError('未找到属于本剧的目标剧集')
            if p['status']=='cancelled': raise ValueError('规划已取消，请建立新项目')
            if action in {'resume', 'retry'}:
                if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='running'", (pid,)).fetchone():
                    raise ValueError('请等待当前任务到达安全边界')
                feedback = payload.get('feedback', '')
                if not isinstance(feedback, str) or len(feedback) > 4000:
                    raise ValueError('规划补充要求须为不超过4000字的文本')
                feedback = feedback.strip()
                changed_feedback = bool(feedback and feedback != (p.get('planning_feedback') or p.get('plan_feedback', '')))
                if changed_feedback:
                    p['planning_feedback'] = feedback
                    p['plan_feedback'] = feedback
                    p.setdefault('plan_feedback_history', []).append({'feedback': feedback,
                        'actor': 'producer', 'at': store.now(), 'request_id': key})
                invalid_candidate = False
                if p.get('plan_phase') == 'reviewing' and p['status'] in {'blocked', 'failed'} and p.get('draft_plan'):
                    try:
                        validate_plan(p['draft_plan'], p)
                    except PlanValidationError as exc:
                        invalid_candidate = True
                        if p.get('plan_review'):
                            p.setdefault('plan_review_archive', []).append({'cycle': p.get('plan_cycle'),
                                'attempt': p.get('plan_attempt'), 'review': copy.deepcopy(p['plan_review']),
                                'reason': 'preserved_before_contract_recovery'})
                        p['plan_review'] = {'passed': False, 'issues': [str(exc)],
                            'source': 'contract', 'candidate_sha256': fingerprint(p['draft_plan']),
                            'repair_targets': exc.repair_targets}
                if changed_feedback or invalid_candidate or p.get('plan_phase') == 'exhausted' or (not p.get('plan_phase') and p['status'] in {'blocked', 'failed'}):
                    from .drama_planning import start_cycle
                    start_cycle(p, full_repair=changed_feedback)
                    if changed_feedback:
                        p.update(plan_review=None, plan_review_sha256=None, stage='plan_repair')
                p.update(error=None, needs_decision=None)
            p.update(pause_requested=action in {'pause','cancel'},status='cancelled' if action=='cancel' else 'paused' if action=='pause' else 'queued')
            db.execute("UPDATE jobs SET status='cancelled',finished=? WHERE project_id=? AND status='queued'",(time.time(),pid))
            p['operation_requests'][key]={'signature':signature}
            store._write(db,p)
            if action in {'resume','retry'}:
                if (p.get('plan_review') or {}).get('passed') and p.get('plan_phase') == 'approved':
                    p.update(status='awaiting_plan',stage='plan_confirmation')
                    store._write(db,p)
                else: store._enqueue(db,pid,'drama_plan')
                from .drama_automation import reconcile
                reconcile(db,p)
            return snapshot(pid,db=db)
        if action in {'approve', 'revise', 'reedit'} and len(targets) != 1: raise ValueError('请明确选择一集验收或返修')
        for e in targets:
            if action == 'pause':
                if e['status'] in {'approved', 'cancelled', 'awaiting_final', 'draft'}: continue
                e['pause_requested'] = True
                running = db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='running'", (e['id'],)).fetchone()
                if not running: e['status'] = 'paused'
            elif action == 'cancel':
                if e['status'] == 'approved': continue
                e.update(pause_requested=True, status='cancelled')
            elif action in {'resume', 'retry'}:
                if e['status'] not in {'paused', 'blocked', 'failed', 'waiting_dependency'}:
                    if payload.get('episode_id'): raise ValueError('该集当前不支持继续或重试')
                    continue
                if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='running'", (e['id'],)).fetchone(): raise ValueError('请等待当前任务到达安全边界')
                if e['duration_seconds'] > 90 and not e.get('unit_ids') and (e.get('needs_decision') or {}).get('type') == 'episode_scenes':
                    e.update(scene_generation=e.get('scene_generation',1)+1,
                        scene_feedback='\n'.join((e.get('needs_decision') or {}).get('issues') or ['根据已保存的场景审核意见定向返修']))
                if (e.get('continuity_repair') or {}).get('status') == 'exhausted':
                    e.update(continuity_repair_cycle=e.get('continuity_repair_cycle',1)+1,continuity_repair=None)
                e.update(status='queued', pause_requested=False, error=None, needs_decision=None)
            else:
                if payload.get('expected_episode_revision')!=e['revision']: raise ValueError('剧集已更新，请重新读取成片版本后验收或修改')
                repair_blocked = action == 'revise' and e['duration_seconds'] > 90 and e['status'] in {'blocked','failed'}
                if not repair_blocked and (e['status'] != 'awaiting_final' or not e.get('delivery')):
                    raise ValueError('请先等待该集成片进入待验收，或对阻塞的长集定向修改')
                if action == 'approve':
                    pending = next((g for g in reversed(e['decisions']) if g.get('type') == 'final' and g.get('status') == 'pending'), None)
                    if pending:
                        answer = {'gate_id': pending['id'], 'type': 'final', 'choice': 'approve', 'feedback': '', 'actor': 'producer', 'at': store.now()}
                        pending.update(status='answered', answer=answer)
                        e['answers'][pending['id']] = answer
                        e['status'] = 'queued'
                        store._enqueue(db, e['id'], 'drama_episode')
                    else:
                        e.update(status='approved', accepted_at=store.now(), accepted_by='producer', progress=100)
                else:
                    e['revision_feedback'] = text(payload.get('feedback'), '修改意见', 2, 4000)
                    e['revision_scope'] = {k: payload[k] for k in ('unit_id', 'shot_ids') if k in payload}
                    if e['duration_seconds']>90 and not e.get('unit_ids'):
                        e.update(scene_feedback=e['revision_feedback'],scene_generation=e.get('scene_generation',1)+1,revision_action=None)
                    else:
                        e['revision_action']=action
                        if repair_blocked and not e['revision_scope'].get('unit_id'):
                            failed=(e.get('needs_decision') or {}).get('unit_id') or e.get('active_unit_id')
                            if failed: e['revision_scope']['unit_id']=failed
                    e.update(status='queued', pause_requested=False, error=None, needs_decision=None)
            if action in {'pause', 'cancel'}:
                db.execute("UPDATE jobs SET status='cancelled',finished=? WHERE project_id=? AND status='queued'", (time.time(), e['id']))
            if action in {'pause', 'cancel', 'resume', 'retry'}:
                for uid in e.get('unit_ids',[]):
                    unit=store._read(db,uid)
                    if unit['status']=='unit_ready': continue
                    unit['pause_requested']=action in {'pause','cancel'}
                    if action=='cancel': unit['status']='cancelled'
                    elif action in {'resume','retry'} and unit['status'] in {'paused','blocked','failed'}:
                        unit['status']='queued'
                        if not db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status='running'", (uid,)).fetchone():
                            unit.update(error=None, needs_decision=None)
                    store._write(db,unit)
            store._write(db, e)
        if not payload.get('episode_id') and not payload.get('batch_id'):
            p.update(pause_requested=action in {'pause','cancel'}, status='cancelled' if action == 'cancel' else 'paused' if action == 'pause' else 'producing')
        p['operation_requests'][key] = {'signature': signature}
        aggregate(db,p)
        store._write(db, p)
        from .drama_automation import reconcile
        reconcile(db, p)
        dispatch(db, p)
        return snapshot(pid, db=db)


def episode_snapshot(pid, eid):
    with store.connection() as db:
        db.execute('BEGIN')
        p=require(db,pid)
        if eid not in p['episodes']: raise KeyError('剧集不属于本剧')
        e=store._read(db,eid)
        units=[store._read(db,uid) for uid in e.get('unit_ids',[])]
        for value in [e,*units]:
            value.pop('execution_config',None)
            value.pop('planning_cache',None)
            value.pop('operation_requests',None)
            value.pop('plan',None)
        from .drama_workflow import build
        job = db.execute("SELECT status,started FROM jobs WHERE project_id=? AND kind IN ('workflow','drama_episode') AND status IN ('queued','running') ORDER BY started DESC LIMIT 1", (eid,)).fetchone() if e['duration_seconds'] > 90 else None
        return {**e,'units':units,'drama_revision':p['revision'],
            'workflow':build(e, units=units, active_job=job is not None,
                current_job=dict(job) if job else None) if e['duration_seconds'] > 90 else None}
