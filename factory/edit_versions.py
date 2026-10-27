"""Durable immutable edit versions on existing projects and worker jobs.

HTTP routes call snapshot/mutate; the existing worker calls run for timeline_edit.
Editing never advances the production graph or submits generation requests.
"""
from __future__ import annotations

import copy
import time
import uuid
from pathlib import Path

from studio import core
from . import store
from . import edit_timeline as timeline
from .production_edits import require_idle

ACTIONS = {'save','check','preview','export','adopt','cancel'}


def _state(project):
    return project.setdefault('timeline_edits', {'schema':timeline.SCHEMA,'versions':[], 'requests':{}})


def _folder(project, version_id):
    if not isinstance(version_id,str) or len(version_id) != 12 or any(c not in '0123456789abcdef' for c in version_id):
        raise ValueError('剪辑版本编号无效')
    base = core.within(core.ROOT/'production',project['slug'])
    marker = base/'factory_owner.json'
    if not marker.is_file() or core.read_json(marker).get('project_id') != project['id']:
        raise ValueError('剪辑输出目录归属不符')
    return core.within(base,'edits/'+version_id)


def _version(project, version_id):
    item = next((v for v in _state(project)['versions'] if v['id'] == version_id),None)
    if not item:
        raise ValueError('剪辑版本不存在')
    return item


def _public(project):
    state = project.get('timeline_edits') or {}
    try:
        catalog = timeline.catalog(project,checksum=False)
        error = None
    except (ValueError,OSError,KeyError) as exc:
        catalog,error = None,str(exc)
    versions = copy.deepcopy(state.get('versions', []))
    for version in versions:
        version['source_current'] = bool(catalog and version['catalog_signature'] == catalog['signature'])
    return {'project_id':project['id'],'revision':project['revision'],'fps':timeline.FPS,
        'catalog':catalog,'error':error,'versions':versions,'task':copy.deepcopy(state.get('task')),
        'adopted_version_id':state.get('adopted_version_id'), 'editable':project['status'] in {'paused','blocked','awaiting_final','approved'},
        'scope':'episode_only_original_script_and_source_media_retained'}


def snapshot(pid):
    return _public(store.get(pid,False))


def mutate(pid, payload):
    if not isinstance(payload,dict) or payload.get('action') not in ACTIONS:
        raise ValueError('不支持的剪辑操作')
    common = {'action','request_id','expected_revision'}
    extra = {'save':{'document','label','parent_id'},'check':{'version_id','authorize_review'},
        'preview':{'version_id'},'export':{'version_id'},'adopt':{'version_id','confirmed','human_reviewed'},'cancel':{'job_id'}}
    action = payload['action']
    if set(payload)-common-extra[action]:
        raise ValueError('剪辑操作包含未知字段')
    rid = payload.get('request_id')
    if not isinstance(rid,str) or not 1 <= len(rid) <= 100:
        raise ValueError('剪辑操作需要请求编号')
    signature = core.fingerprint({k:v for k,v in payload.items() if k != 'expected_revision'})
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        project = store._read(db,pid)
        if project.get('record_kind') == 'drama':
            raise ValueError('请打开具体剧集进行剪辑')
        state = _state(project)
        replay = state['requests'].get(rid)
        if replay:
            if replay['signature'] != signature:
                raise ValueError('请求编号已用于其他剪辑操作')
            return _public(project)
        if type(payload.get('expected_revision')) is not int or payload['expected_revision'] != project['revision']:
            raise ValueError('项目已更新，草稿保留；请刷新核对后保存')
        task = state.get('task') or {}
        active = task.get('status') in {'queued','running'}
        if action == 'cancel':
            if not active or payload.get('job_id') != task.get('job_id'):
                raise ValueError('剪辑任务已结束或已变更')
            task['cancel_requested'] = True
            if task['status'] == 'queued':
                task['status'] = 'cancelled'
                db.execute("UPDATE jobs SET status='cancelled',finished=? WHERE id=? AND status='queued'",(time.time(),task['job_id']))
        else:
            if active:
                raise ValueError('已有剪辑任务正在执行，请等待或取消')
            require_idle(db,project)
            # A parent edit must not race a queued/running production unit.
            ids = [p['id'] for p in timeline.owners(project)]
            for uid in ids:
                if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running') AND kind!='timeline_edit'",(uid,)).fetchone():
                    raise ValueError('项目仍有制作任务，请先暂停并等待安全边界')
                if db.execute("SELECT 1 FROM cloud_tasks WHERE project_id=? AND status NOT IN ('downloaded','failed','rejected')",(uid,)).fetchone():
                    raise ValueError('项目仍有结果未核对的媒体任务')
            catalog = timeline.catalog(project)
            if action == 'save':
                if payload.get('parent_id'):
                    _version(project,payload['parent_id'])
                document = timeline.normalize(payload.get('document'),catalog['sources'])
                origins = {c['id']:c['source_text'] for c in catalog['default']['subtitles']}
                if payload.get('parent_id'):
                    for cue in _version(project,payload['parent_id'])['document']['subtitles']:
                        origins.setdefault(cue['id'],cue['source_text'])
                if any(c['source_text'] != origins.get(c['id'],'') for c in document['subtitles']):
                    raise ValueError('字幕来源文字不可改写；改字只修改text并明确核对声音关系')
                if document['resolution'] != catalog['default']['resolution']:
                    raise ValueError('剪辑沿用原交付分辨率')
                label = payload.get('label','剪辑版本')
                if not isinstance(label,str) or not 1 <= len(label.strip()) <= 80:
                    raise ValueError('剪辑版本名称须为1–80字')
                item = {'id':uuid.uuid4().hex[:12],'label':label.strip(),'parent_id':payload.get('parent_id'),
                    'created_at':time.time(),'source_revision':project['revision'], 'catalog_signature':catalog['signature'],
                    'document':document,'document_signature':core.fingerprint(document),'sources':catalog['sources'],
                    'structural':timeline.check_document(project,document,catalog['sources']),
                    'semantic':{'status':'not_reviewed'},'status':'draft'}
                state['versions'].append(item)
                if not (project.get('delivery') or {}).get('edit_version_id'):
                    state['base_delivery'] = copy.deepcopy(project.get('delivery'))
            else:
                item = _version(project,payload.get('version_id'))
                if item['catalog_signature'] != catalog['signature']:
                    raise ValueError('源制作版本已变化，请刷新素材并保存新剪辑版本')
                structural = timeline.check_document(project,item['document'],item['sources'])
                item['structural'] = structural
                if action == 'adopt':
                    if payload.get('confirmed') is not True or payload.get('human_reviewed') is not True:
                        raise ValueError('采用剪辑须明确确认已观看新版本')
                    exported = item.get('export')
                    if structural['status'] != 'pass' or item.get('semantic',{}).get('status') != 'pass' or not exported:
                        raise ValueError('剪辑尚未完成当前版本检查及正式导出')
                    for receipt in exported['artifacts']:
                        timeline.verified(receipt,project)
                    if state.get('adopted_version_id') != item['id']:
                        if project.get('delivery'):
                            project.setdefault('delivery_history',[]).append({'delivery':copy.deepcopy(project['delivery']),
                                'production_revision':project.get('production_revision',1),'at':time.time(),'reason':'timeline_edit'})
                        project['production_revision'] = project.get('production_revision',1)+1
                        project['delivery'] = {**copy.deepcopy(exported),'revision':project['production_revision'],
                            'status':'awaiting_producer_final_review','edit_version_id':item['id']}
                        project['status'] = 'awaiting_final'
                        project['needs_decision'] = None
                        project['error'] = None
                        state['adopted_version_id'] = item['id']
                        for decision in project.get('decisions',[]):
                            if decision.get('type') == 'final' and decision.get('status') == 'pending':
                                decision['status'] = 'superseded'
                        project['decisions'] = [*project.get('decisions',[]),{'id':uuid.uuid4().hex[:12],
                            'type':'final','status':'pending','edit_version_id':item['id'],'message':'请按新剪辑版本完成原有出品人终审。'}]
                elif action == 'export' and (structural['status'] != 'pass' or item.get('semantic',{}).get('status') != 'pass'):
                    raise ValueError('剪辑存在未解决问题，先检查并修复后再正式导出')
                else:
                    if action == 'check' and type(payload.get('authorize_review',False)) is not bool:
                        raise ValueError('审核授权须为布尔值')
                    if action == 'check' and payload.get('authorize_review'):
                        base = _folder(project,item['id']).parent
                        for intent in base.glob('*/reviews/*/intent.json'):
                            if not (intent.parent/'review.json').exists():
                                raise ValueError('已有剪辑审核结果未知，请核对原请求；新版本不能重复请求模型')
                    jid = store._enqueue(db,pid,'timeline_edit')
                    state['task'] = {'job_id':jid,'version_id':item['id'],'action':action,'status':'queued',
                        'authorize_review':payload.get('authorize_review',False),'cancel_requested':False,
                        'document_signature':item['document_signature'],'catalog_signature':catalog['signature']}
        state['requests'][rid] = {'signature':signature,'at':time.time()}
        store._event(db,pid,'已保存剪辑操作：'+action,'timeline_edit',{'action':action,'version_id':payload.get('version_id')})
        store._write(db,project)
        return _public(project)


def _owned(db, job):
    row = db.execute('SELECT status,lease FROM jobs WHERE id=? AND project_id=?',(job['id'],job['project_id'])).fetchone()
    if not row or row['status'] != 'running' or row['lease'] != job.get('lease'):
        return False
    task = (_state(store._read(db,job['project_id'])).get('task') or {})
    return task.get('job_id') == job['id'] and task.get('status') in {'queued','running'} and not task.get('cancel_requested')


def _cancelled(job):
    with store.connection() as db:
        return not _owned(db,job)


def _semantic(project, item, preview, folder, control):
    """Explicitly authorized private-model observation, never vendor billing bypass."""
    from .config import resolve_llm, llm_identity
    from .prompt_workbench import _local_trial
    from .llm import _request, _image_url
    from .schemas import object_schema as obj, text_schema as txt, array_schema as arr
    from .drama_continuity import frame
    config = project.get('execution_config') or {}
    model = resolve_llm(config,'visual_critic',require_vision=True)
    if not _local_trial(model):
        raise ValueError('剪辑审核目前只允许明确授权且已配置的内网视觉模型；供应商费用须另行接入')
    document,sources = item['document'],item['sources']
    positions = timeline.positions(document['clips'])
    video_receipt = next(r for r in preview['artifacts'] if Path(r['path']).name == 'episode_clean.mp4')
    media = timeline.verified(video_receipt,project)
    try:
        from .story_coverage import required_events
        events = required_events(project)
    except ImportError:
        events = (project.get('outline') or {}).get('beats') or []
    groups = []
    for event in events:
        matching = [c for c in document['clips'] if event['id'] in sources[c['source_id']].get('beat_ids', [])]
        if not matching:
            raise ValueError('必要事件缺少当前剪辑对应镜头：' + event['id'])
        if len(matching) > 12:
            raise ValueError('必要事件抽样超出单次上下文，保留剪辑等待拆分或补证：' + event['id'])
        groups.append((matching,[event],'event'))
    groups.extend((document['clips'][i:i+8],[],'continuity') for i in range(0,len(document['clips']),8))
    checks = []
    for index,(group,group_events,review_kind) in enumerate(groups):
        if control():
            from .scheduler import ProductionPaused
            raise ProductionPaused('剪辑审核已取消')
        ids = {c['id'] for c in group}
        samples = []
        for span in positions:
            if span['clip_id'] in ids:
                # Include endpoints, midpoint and the prior boundary; the declared
                # scope stays sampled and cannot certify unsampled action/audio.
                for f in sorted({max(0,span['start_frame']-1),span['start_frame'],(span['start_frame']+span['end_frame'])//2,span['end_frame']-1}):
                    samples.append({'clip_id':span['clip_id'],'frame':f,'seconds':f/timeline.FPS})
        signature = core.fingerprint({'edit':item['document_signature'],'media':video_receipt['sha256'],
            'samples':samples,'events':group_events,'review_kind':review_kind,'model':llm_identity(config,'visual_critic'),'policy':'edit_observation.v1'})
        review_folder = folder/'reviews'/signature
        path,intent = review_folder/'review.json',review_folder/'intent.json'
        if path.exists():
            answer = core.read_json(path)
        else:
            if intent.exists():
                raise ValueError('上次剪辑审核结果未知，保留预览等待对账，不重复请求模型')
            payload = {'required_events':group_events,'review_kind':review_kind,'samples':samples,'clips':group,
                'source_facts':{c['source_id']:{k:v for k,v in sources[c['source_id']].items() if k in {'action','continuity','beat_ids','shot_id'}} for c in group},
                'audio_edits':document['audio'],'subtitles':document['subtitles'],
                'scope':'sampled_exported_frames_and_timeline_metadata_not_full_video_or_audio_comprehension'}
            schema = obj({'status':{'enum':['pass','fail','uncertain']},'summary':txt(1,2000),
                'issues':arr(txt(1,1500),0,30)})
            content = [{'type':'text','text':__import__('json').dumps(payload,ensure_ascii=False)}]
            for sample in samples:
                content += [{'type':'text','text':__import__('json').dumps(sample)},
                    {'type':'image_url','image_url':{'url':_image_url(frame(media,sample['seconds']))}}]
            core.write_json(intent,{'signature':signature,'scope':payload['scope']})
            answer = _request(config,'visual_critic',
                '审核当前剪辑真实导出抽样帧与时间线元数据，检查必要剧情覆盖、人物持物状态和相邻镜头承接。'
                '原始动作文字和beat标签不是发生证据；明确矛盾fail，样本不足uncertain。正常换场与省略时间允许状态变化。'
                '仅判断本组可见镜头；跨组边界包含前一帧。不得声称看过未采样时段或听懂音轨；字幕不代表真实发声。'
                '问题须写出当前clip_id、时间和实际证据。只有本组证据充分且无问题时pass。',
                payload,schema,review_folder,max_tokens=2400,temperature=.2,content=content)
            from jsonschema import validate
            validate(answer,schema)
            core.write_json(path,answer)
        checks.append({'signature':signature,'samples':samples,'event_ids':[e['id'] for e in group_events],'review_kind':review_kind,**answer})
    statuses = {c['status'] for c in checks}
    status = 'fail' if 'fail' in statuses else 'uncertain' if 'uncertain' in statuses else 'pass'
    if any(c['issues'] for c in checks) and status == 'pass':
        status = 'fail'
    return {'status':status,'checks':checks,'media_sha256':video_receipt['sha256'],
        'edit_signature':item['document_signature'],'scope':'sampled_current_export_not_unobserved_video_or_audio',
        'human_final_review_required':True}


def run(job):
    """Own error settlement so edit failures cannot block/advance original production."""
    from .scheduler import ProductionPaused
    from .edit_render import render
    pid = job['project_id']
    error = None
    try:
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if not _owned(db,job):
                return
            project = store._read(db,pid)
            task = _state(project)['task']
            item = copy.deepcopy(_version(project,task['version_id']))
            if timeline.catalog(project)['signature'] != task['catalog_signature']:
                raise ValueError('剪辑源版本已变化，任务停止')
            task['status'] = 'running'
            store._write(db,project)
        folder = _folder(project,item['id'])
        core.write_json(folder/'document.json',{'document':item['document'],'sources':item['sources'],
            'source_revision':item['source_revision'],'document_signature':item['document_signature']})
        structural = timeline.check_document(project,item['document'],item['sources'])
        action = task['action']
        preview = item.get('preview')
        if not preview:
            preview = render(item['document'],item['sources'],folder,lambda:_cancelled(job))
        else:
            for receipt in preview['artifacts']:
                timeline.verified(receipt,project)
        semantic = item.get('semantic',{'status':'not_reviewed'})
        if action == 'check' and task.get('authorize_review') and structural['status'] == 'pass':
            semantic = _semantic(project,item,preview,folder,lambda:_cancelled(job))
        if action == 'export' and (structural['status'] != 'pass' or semantic.get('status') != 'pass'):
            raise ValueError('剪辑未通过当前版本的剧情、连续性和技术检查')
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if not _owned(db,job):
                return
            current = store._read(db,pid)
            target = _version(current,item['id'])
            if target['document_signature'] != task['document_signature'] or timeline.catalog(current)['signature'] != task['catalog_signature']:
                raise ValueError('剪辑版本在渲染期间已变化，迟到结果仅保留文件')
            target.update(preview=preview,structural=structural,semantic=semantic,
                          status='checked' if semantic.get('status') == 'pass' else 'draft')
            if action == 'export':
                target['export'] = {**copy.deepcopy(preview),'schema':'ai-studio.edited-delivery.v1',
                    'status':'awaiting_producer_final_review','semantic_status':'pass', 'edit_review':semantic,
                    'edit_version_id':item['id'],'project_id':pid,'title':current.get('title','剪辑成片'),
                    'source_signature':core.fingerprint({'edit':item['document_signature'],'review':semantic,'media':preview['artifacts']})}
                target['status'] = 'exported'
            _state(current)['task']['status'] = 'completed'
            store._write(db,current)
    except Exception as exc:
        error = str(exc)[:2000]
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status,lease FROM jobs WHERE id=?',(job['id'],)).fetchone()
            if row and row['status']=='running' and row['lease']==job.get('lease'):
                current = store._read(db,pid)
                task = _state(current).get('task') or {}
                if task.get('job_id') == job['id']:
                    task.update(status='cancelled' if isinstance(exc,ProductionPaused) or task.get('cancel_requested') else 'failed',error=error)
                    store._write(db,current)
    finally:
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status,lease FROM jobs WHERE id=?',(job['id'],)).fetchone()
            if row and row['status']=='running' and row['lease']==job.get('lease'):
                current = store._read(db,pid)
                task = _state(current).get('task') or {}
                cancelled = task.get('cancel_requested') or task.get('status') == 'cancelled'
                if task.get('job_id') == job['id'] and cancelled:
                    task['status'] = 'cancelled'
                    store._write(db,current)
                db.execute('UPDATE jobs SET status=?,finished=?,error=? WHERE id=? AND status=\'running\' AND lease=?',
                    ('cancelled' if cancelled else 'failed' if error else 'completed',time.time(),error,job['id'],job['lease']))
