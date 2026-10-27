"""Explicit, read-only rechecks of existing text and media under frozen authority.

This queue never writes a screenplay, renders media, or submits generation.
Unknown model intents are reconciled from saved parsed responses, never reposted.
"""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path

from studio import core
from . import store
from . import creative_contracts as editorial

VERSION = 'ai-studio.quality-recheck.v1'
KIND = 'creative_quality_review'
ACTIVE = {'queued', 'running'}
FINAL_OPTIONS = [{'id': 'approve', 'label': '通过终版'}, {'id': 'revise', 'label': '局部修改'}, {'id': 'reedit', 'label': '重新剪辑'}]
APPROVAL_KEYS = ('accepted_at', 'accepted_by', 'approved_at', 'approved_by', 'final_approved_revision', 'producer_approved')


class UnknownReview(RuntimeError):
    pass


def _state(project):
    return project.setdefault('quality_recheck', {'version': VERSION, 'requests': {}, 'history': []})


def require_current(project):
    task = (project.get('quality_recheck') or {}).get('task') or {}
    if task.get('final_active', True) and task.get('final_gate_id') and (project.get('delivery') or {}).get('quality_recheck_id') == task.get('job_id') and not final_current(project):
        raise ValueError('现有复核证明与当前稿件、成片或文件字节不匹配，请重新核对')
    if project.get('creative_quality_review_required'):
        raise ValueError('新创作契约尚未完成当前稿件与已有成片复核，不能沿用旧终审通过')


def _episode(project):
    units = []
    if project.get('unit_ids'):
        units = [store.get(uid, False) for uid in project.get('edit_unit_order', project['unit_ids'])]
        shots, cast = [], {}
        for unit in units:
            episode = unit.get('episode') or unit.get('script') or {}
            cast.update(copy.deepcopy(episode.get('cast', {})))
            shots.extend({**copy.deepcopy(s), 'id': unit['global_prefix'] + '-' + s['id'],
                'unit_id': unit['id'], 'local_shot_id': s['id']} for s in episode.get('shots', []))
        return {'shots': shots, 'cast': cast, 'synopsis': (project.get('outline') or {}).get('synopsis'),
            'ending': (project.get('outline') or {}).get('ending_state'), 'total_seconds': project['duration_seconds']}, units
    return copy.deepcopy(project.get('episode') or project.get('script') or {}), units


def _signature(project):
    episode, units = _episode(project)
    delivery = project.get('delivery') or {}
    return core.fingerprint({'contract': project.get('creative_quality_contract'), 'episode': episode,
        'units': [{'id': u['id'], 'contract': u.get('creative_quality_contract'), 'delivery': u.get('delivery')} for u in units],
        'brief': project.get('brief'), 'outline': project.get('outline'), 'resolved': project.get('resolved_story_brief'),
        'feedback': [project.get('story_feedback'), project.get('script_feedback')],
        'style': project.get('selected_style'), 'production_profile': project.get('production_profile'),
        'delivery': {k: copy.deepcopy(delivery.get(k)) for k in ('source_signature', 'artifacts', 'duration_seconds', 'fps', 'resolution', 'edit_version_id')},
        'source_document': project.get('creative_source_document')})


def _config(project):
    from .config import load_config
    return copy.deepcopy(project.get('execution_config') or load_config())


def _authorize(project, config):
    from .config import resolve_llm
    from .prompt_workbench import _local_trial
    roles = ['independent_critic'] + (['visual_critic'] if project.get('delivery') else [])
    for role in roles:
        if not _local_trial(resolve_llm(config, role, require_vision=role == 'visual_critic')):
            raise ValueError('现有稿件复核仅允许明确授权的内网文字/视觉模型，不提交费用未知的供应商请求')


def _base(project):
    import re
    if not re.fullmatch(r'[a-f0-9]{12}', str(project.get('id', ''))) or project.get('slug') != 'factory-' + project['id']:
        raise ValueError('复核项目目录身份无效')
    base = core.within(core.ROOT / 'production', project['slug'])
    owner = base / 'factory_owner.json'
    if owner.exists() and core.read_json(owner).get('project_id') != project['id']:
        raise ValueError('复核项目目录归属不匹配')
    if base.exists() and not owner.exists() and any(base.iterdir()):
        raise ValueError('已有目录缺少归属标记，请先核对原项目目录，未写入复核文件')
    return core.within(base, 'quality/rechecks')


def _unknown(project):
    base = _base(project)
    for intent in (base.parent / 'shot-transitions').glob('*/intent.json'):
        if not (intent.parent / 'review.json').exists(): return True
    for index, intent in enumerate(base.glob('*/**/intent.json')):
        if index >= 2000: raise ValueError('已有复核意图过多，请先核对历史记录')
        if not (intent.parent / 'result.json').exists() and not list((intent.parent / 'call').glob('calls/*/parsed.json')):
            return True
    return False



def require_idle_scope(db, project):
    """Project adoption/recheck cannot race descendant production or cloud intents."""
    from .production_edits import require_resolved_cloud
    from .review_advice import ensure_no_gpu_intents
    pending, seen = [project['id']], set()
    while pending:
        pid = pending.pop()
        if pid in seen: continue
        if len(seen) >= 10000: raise ValueError('项目层级过大，请先核对制作范围')
        seen.add(pid)
        current = store._read(db, pid)
        if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running')", (pid,)).fetchone():
            raise ValueError('项目或制作单元仍有在途任务，请先暂停并等待收尾')
        ensure_no_gpu_intents(current)
        episode = current.get('episode') or current.get('script') or {}
        require_resolved_cloud(db, pid, [s['id'] for s in episode.get('shots', [])])
        if db.execute("SELECT 1 FROM cloud_tasks WHERE project_id=? AND status NOT IN ('downloaded','failed','rejected')", (pid,)).fetchone():
            raise ValueError('项目或制作单元尚有未核对云任务，不能用新契约绕过原提交')
        children = [*current.get('unit_ids', [])]
        if current.get('record_kind') == 'drama': children += current.get('episodes', [])
        if any(not isinstance(child, str) for child in children): raise ValueError('项目层级记录无效，请先核对归属')
        pending.extend(children)
    # A scene-unit's parent can still own a job changing its plan.
    parent_id = project.get('episode_id')
    if parent_id and parent_id not in seen and db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running')", (parent_id,)).fetchone():
        raise ValueError('父剧集仍在制作，请先暂停父项目后启用新契约')


def configure(pid, payload):
    from .production_edits import require_idle, require_resolved_cloud
    from .review_advice import ensure_no_gpu_intents
    from .dramas import cas
    if not isinstance(payload, dict) or set(payload) != {'expected_revision', 'request_id', 'confirmed', 'authorize_review'} or payload.get('confirmed') is not True or payload.get('authorize_review') is not True:
        raise ValueError('复核现有稿件与成片须确认当前版本及本次内网模型调用')
    rid = payload.get('request_id')
    if not isinstance(rid, str) or not 1 <= len(rid) <= 100: raise ValueError('复核需要有效请求编号')
    request_signature = core.fingerprint({k: v for k, v in payload.items() if k != 'expected_revision'})
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        project = store._read(db, pid)
        state = _state(project)
        previous = state['requests'].get(rid)
        if previous:
            if previous['signature'] != request_signature: raise ValueError('请求编号已用于不同复核操作')
            return editorial.snapshot(project)
        cas(project, payload)
        if not editorial.enabled(project): raise ValueError('请先明确启用新创作契约，历史项目不会自动升级')
        if project.get('record_kind') == 'drama': raise ValueError('请逐集选择现有稿件与成片进行复核')
        require_idle(db, project)
        require_idle_scope(db, project)
        if _unknown(project): raise ValueError('已有复核模型请求结果未知，请先核对原调用，不能重复提交')
        config = _config(project)
        _authorize(project, config)
        signature = _signature(project)
        if state.get('task'): state['history'].append(copy.deepcopy(state['task']))
        jid = store._enqueue(db, pid, KIND)
        state['task'] = {'job_id': jid, 'status': 'queued', 'input_signature': signature,
            'config_signature': core.fingerprint(config), 'source_revision': project['revision'],
            'authorized_by': 'producer', 'authorize_review': True, 'created_at': store.now(),
            'requires_media': bool(project.get('delivery') or project['status'] in {'approved', 'awaiting_final'} or any(a.get('previous_status') in {'approved', 'awaiting_final'} for a in project.get('creative_quality_adoptions', [])))}
        state['requests'][rid] = {'signature': request_signature, 'job_id': jid}
        project['creative_quality_review_required'] = True
        store._write(db, project)
        store._event(db, pid, '已安排现有稿件与成片复核；不生成或覆盖任何素材。', KIND, {'job_id': jid})
        return editorial.snapshot(project)


def _owned(db, job):
    row = db.execute('SELECT project_id,status,lease FROM jobs WHERE id=?', (job['id'],)).fetchone()
    if not row or row['project_id'] != job['project_id'] or row['status'] != 'running' or row['lease'] != job.get('lease'): return False
    project = store._read(db, job['project_id'])
    if project['status'] == 'cancelled': return False
    task = (project.get('quality_recheck') or {}).get('task') or {}
    return task.get('job_id') == job['id'] and task.get('status') in ACTIVE and not task.get('cancel_requested')


def _control(job):
    with store.connection() as db:
        if not _owned(db, job):
            from .scheduler import ProductionPaused
            raise ProductionPaused('复核任务执行权已释放，迟到结果只保留历史文件')
    return False


def _call(project, job, config, phase, role, system, payload, schema, content=None):
    from .llm import _request
    from .config import llm_identity
    from jsonschema import validate
    _control(job)
    identity = {'version': VERSION, 'input_signature': job['input_signature'], 'phase': phase,
        'model': llm_identity(config, role), 'system': core.fingerprint(str(system)), 'payload': core.fingerprint(payload), 'schema': core.fingerprint(schema)}
    signature = core.fingerprint(identity)
    folder = core.within(_base(project) / job['input_signature'], phase + '-' + signature[:16])
    result = folder / 'result.json'
    if result.exists():
        record = core.read_json(result)
        if record.get('signature') != signature: raise ValueError('现有复核回执签名不匹配')
        answer = record['answer']; validate(answer, schema); return answer
    parsed = list((folder / 'call').glob('calls/*/parsed.json'))
    if parsed:
        answer = core.read_json(parsed[-1]); validate(answer, schema)
    elif (folder / 'intent.json').exists():
        raise UnknownReview('上次复核结果未知，未重复请求模型')
    else:
        folder.mkdir(parents=True, exist_ok=True)
        owner = core.within(core.ROOT / 'production', project['slug']) / 'factory_owner.json'
        if not owner.exists(): core.write_json(owner, {'project_id': project['id'], 'schema': 'autonomous_factory.owner.v1'})
        core.write_json(folder / 'intent.json', identity)
        try:
            answer = _request(config, role, system, payload, schema, folder / 'call',
                max_tokens=5800 if role == 'independent_critic' else 2400, temperature=.2, content=content)
            validate(answer, schema)
        except Exception as exc:
            raise UnknownReview('复核没有保存可验证的模型结果，需核对原调用') from exc
    core.write_json(result, {'signature': signature, 'identity': identity, 'answer': answer})
    _control(job)
    return answer


def _structural(project, episode, units):
    from .schemas import technical_issues
    from .story_coverage import structural_issues as coverage_issues
    from .shot_continuity import structural_issues as continuity_issues
    issues = []
    if not episode.get('shots'):
        return [{'code': 'script_missing', 'description': '没有可复核的现有稿件，请在原创作入口补齐稿件后再复核', 'shot_ids': []}]
    if units:
        for unit in units: issues.extend(technical_issues(unit.get('episode') or unit.get('script') or {}, unit))
        issues.extend(coverage_issues(project, episode)); issues.extend(continuity_issues(project, episode))
    else: issues.extend(technical_issues(episode, project))
    return issues


def _review_text(project, episode, job, config):
    from .schemas import SCRIPT_REVIEW_SCHEMA, project_context
    from .prompt_registry import load_prompt
    payload = {'project': project_context(project), 'episode': episode,
        'resolved_story_brief': project.get('resolved_story_brief'),
        'task': '只复核当前已存在的完整稿件。逐项检查批准目标、必要事件、因果、动作、对白和跨镜状态，不改写稿件，不调用生成工具。'}
    system, payload, schema = editorial.review_call(project, 'independent_critic', load_prompt('independent_critic'), payload, SCRIPT_REVIEW_SCHEMA)
    # Long episodes use stable global shot identities rather than local Sxx.
    schema = copy.deepcopy(schema)
    issue_schema = schema['properties']['issues']['items']
    issue_schema['properties']['shot_ids']['items'] = {'type': 'string', 'enum': [s['id'] for s in episode['shots']]}
    raw = _call(project, job, config, 'text', 'independent_critic', system, payload, schema)
    review = editorial.normalize(project, raw, episode)
    from .schemas import review_passes
    review['passed'] = review_passes(review)
    return review


def _review_media(project, episode, units, job, config):
    from .episode_assembly import verified_artifact
    from .quality import inspect_video
    from .drama_continuity import frame
    from .llm import _image_url
    from .story_coverage import required_events
    from .schemas import object_schema, text_schema, array_schema
    from .shot_continuity import verify_delivery
    delivery = project['delivery']
    path = verified_artifact(project, 'episode_clean.mp4')
    seconds = float(delivery.get('duration_seconds', episode.get('total_seconds', project['duration_seconds'])))
    size = delivery.get('resolution') or [episode.get('width'), episode.get('height')]
    technical = inspect_video(path, seconds, tuple(size), require_audio=True)
    if technical['status'] != 'pass': return {'status': 'fail', 'technical': technical, 'issues': ['已有成片未通过当前技术检查']}
    positions, cursor = {}, 0.0
    for shot in episode['shots']:
        positions[shot['id']] = (cursor, float(shot['seconds'])); cursor += float(shot['seconds'])
    if abs(cursor - seconds) > 1/24: return {'status': 'fail', 'technical': technical, 'issues': ['成片时间线与当前稿件长度不符，需明确修复后复核']}
    checks = []
    for event in required_events(project):
        carriers = [s for s in episode['shots'] if event['id'] in s.get('beat_ids', [])]
        if not carriers: return {'status': 'uncertain', 'technical': technical, 'issues': ['必要事件缺少实际成片镜头映射：' + event['id']]}
        for batch in range(0, len(carriers), 12):
            samples = []
            for shot in carriers[batch:batch+12]:
                start, duration = positions[shot['id']]
                for offset in sorted({0., duration / 2, max(0., duration - 1/24)}):
                    samples.append({'shot_id': shot['id'], 'timestamp': round(start + offset, 6)})
            payload = {'event': event, 'media_sha256': core.sha256(path), 'samples': samples,
                'scope': 'only_actual_sampled_frames_not_full_action_or_audio_observation'}
            content = [{'type': 'text', 'text': json.dumps(payload, ensure_ascii=False)}]
            for sample in samples:
                content.extend([{'type': 'text', 'text': json.dumps(sample, ensure_ascii=False)},
                    {'type': 'image_url', 'image_url': {'url': _image_url(frame(path, sample['timestamp']))}}])
            schema = object_schema({'status': {'type': 'string', 'enum': ['pass', 'fail', 'uncertain']},
                'summary': text_schema(1, 1800), 'issues': array_schema(text_schema(1, 1200), 0, 12)})
            answer = _call(project, job, config, 'event-' + str(len(checks)), 'visual_critic',
                '只根据所给真实成片采样帧核验必要事件。明确可见的矛盾fail，不能确定动作或声音时uncertain；pass时issues为空。不得把事件标签、剧本文字当作画面已经发生。只说明采样范围，不声称看过未采样动作或听过音轨。',
                payload, schema, content)
            checks.append({'event_id': event['id'], 'samples': samples, 'media_sha256': payload['media_sha256'], **answer})
            if answer['status'] != 'pass' or answer['issues']:
                return {'status': answer['status'] if answer['status'] != 'pass' else 'fail', 'technical': technical, 'checks': checks, 'issues': answer['issues']}
    from .scheduler import ProductionNeedsDecision
    try:
        transitions = verify_delivery({**project, 'script': episode, 'episode': episode}, delivery, config, lambda: _control(job))
    except ProductionNeedsDecision as exc:
        detail = exc.details or {}
        if exc.reason == 'shot_continuity_unknown': raise UnknownReview('上次跨镜复核结果未知，未重复请求') from exc
        return {'status': 'fail', 'technical': technical, 'checks': checks, 'continuity': detail,
            'repair_shot_ids': detail.get('shot_ids', []), 'unit_id': detail.get('unit_id'),
            'issues': (detail.get('review') or {}).get('issues') or [str(exc)]}
    return {'status': 'pass', 'technical': technical, 'checks': checks, 'continuity': transitions,
        'media_sha256': core.sha256(path), 'scope': 'sampled_events_and_adjacent_endpoint_frames_not_human_acceptance'}


def run(job):
    pid = job['project_id']; error = None; outcome = None
    try:
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if not _owned(db, job): return
            project = store._read(db, pid); task = _state(project)['task']
            if task['input_signature'] != _signature(project): raise ValueError('现有稿件或成片版本已变化，未采用旧复核任务')
            config = _config(project)
            if task['config_signature'] != core.fingerprint(config): raise ValueError('复核模型配置已变化，需重新确认当前调用')
            _authorize(project, config)
            task['status'] = 'running'; store._write(db, project)
            bound = {**job, 'input_signature': task['input_signature']}
        episode, units = _episode(project)
        structural = _structural(project, episode, units)
        if task.get('requires_media') and not project.get('delivery'):
            structural.append({'code': 'existing_delivery_missing', 'description': '原终审项目缺少可校验的现有成片，请先找回原交付文件或明确安排修复', 'shot_ids': []})
        if structural:
            outcome = {'status': 'needs_metadata', 'issues': structural,
                'message': '现有稿件缺少新契约的有效映射、状态或规格；请在原修改入口明确补齐后再复核。本任务不会重写或重制。'}
        else:
            text = _review_text(project, episode, bound, config)
            from .creative import _script_gate_issues
            outcome = {'status': 'pass' if text['passed'] else 'fail', 'text_review': text, 'issues': text['issues'] if text['passed'] else _script_gate_issues(text)}
            if text['passed'] and project.get('delivery'):
                media = _review_media(project, episode, units, bound, config)
                media_issues = [{'severity': 'major', 'code': 'existing_media_recheck', 'description': str(value),
                    'evidence': '当前媒体 ' + str(media.get('media_sha256') or (project.get('delivery') or {}).get('source_signature')) + ' 的实际检查回执',
                    'suggestion': '在原镜头或剪辑入口按证据明确安排修复；本复核任务不会重制。',
                    'shot_ids': media.get('repair_shot_ids', []), 'unit_id': media.get('unit_id'), 'auto_repair': False} for value in media.get('issues', [])]
                outcome.update(status=media['status'], media_review=media, issues=media_issues)
            elif text['passed']:
                outcome['media_review'] = {'status': 'not_created', 'scope': 'text_only_existing_media_absent'}
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if not _owned(db, job): return
            current = store._read(db, pid)
            if _signature(current) != bound['input_signature']: raise ValueError('复核期间输入版本改变，结果只保留历史文件')
            task = _state(current)['task']; task.update(outcome, finished_at=store.now())
            current['creative_quality_review_required'] = outcome['status'] != 'pass'
            if outcome['status'] == 'pass':
                if current.get('script_review'): current.setdefault('creative_quality_previous_reviews', []).append(copy.deepcopy(current['script_review']))
                current['draft_review'] = copy.deepcopy(outcome['text_review'])
                current['script_review'] = {'passed': True, 'review': copy.deepcopy(outcome['text_review']), 'origin': KIND, 'job_id': job['id']}
                current['script_complete_generation'] = current.get('script_generation', 1)
                current['needs_decision'] = None; current['error'] = None
                if current.get('delivery'):
                    current.setdefault('delivery_history', []).append({'delivery': copy.deepcopy(current['delivery']), 'reason': KIND, 'at': store.now()})
                    current['production_revision'] = current.get('production_revision', 1) + 1
                    current['delivery'] = {**copy.deepcopy(current['delivery']), 'revision': current['production_revision'], 'quality_recheck_id': job['id']}
                    current['delivery_complete_revision'] = current['production_revision']
                    current.update(status='awaiting_final', stage='final_decision', pause_requested=False, progress=95)
                    gate_id = f"{pid}:final:{current['production_revision']}"
                    current['decisions'].append({'id': gate_id, 'type': 'final', 'title': '现有成片已按新契约复核，请重新观看并人工终审。',
                        'options': copy.deepcopy(FINAL_OPTIONS), 'status': 'pending', 'created_at': store.now(), 'quality_recheck_id': job['id']})
                    task['final_gate_id'] = gate_id
            else:
                current.update(status='blocked', stage='script_review', pause_requested=False,
                    needs_decision={'type': 'script_review', 'kind': 'script_review', 'message': outcome.get('message', '当前复核尚未通过，请按明确问题在原入口修改。'),
                        'issues': outcome.get('issues', []), 'allowed_actions': ['provide_feedback']})
            store._write(db, current)
    except Exception as exc:
        error = type(exc).__name__
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if _owned(db, job):
                current = store._read(db, pid)
                _state(current)['task'].update(status='unknown' if isinstance(exc, UnknownReview) else 'failed',
                    error='复核没有完成有效验证；原稿与素材已保留。' + error, finished_at=store.now())
                current['creative_quality_review_required'] = True
                store._write(db, current)
    finally:
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status,lease FROM jobs WHERE id=?', (job['id'],)).fetchone()
            if row and row['status'] == 'running' and row['lease'] == job.get('lease'):
                db.execute("UPDATE jobs SET status=?,finished=?,error=? WHERE id=? AND status='running' AND lease=?",
                    ('failed' if error else 'completed', time.time(), error, job['id'], job['lease']))


def snapshot(project):
    state = project.get('quality_recheck') or {}
    task = copy.deepcopy(state.get('task'))
    if task and task.get('status') in ACTIVE and project['status'] == 'cancelled': task['status'] = 'cancelled'
    return {'task': task, 'history': copy.deepcopy(state.get('history', [])),
        'scope': 'existing_text_and_media_only_no_generation', 'requires_review': project.get('creative_quality_review_required', False)}


def _files_current(project):
    try:
        for receipt in (project.get('delivery') or {}).get('artifacts', []):
            path = core.within(core.ROOT / 'production' / project['slug'], core.ROOT / receipt['path'])
            if not path.is_file() or path.stat().st_size != receipt['bytes'] or core.sha256(path) != receipt['sha256']: return False
        return bool((project.get('delivery') or {}).get('artifacts'))
    except (OSError, ValueError, KeyError): return False


def final_current(project):
    task = (project.get('quality_recheck') or {}).get('task') or {}
    return bool(task.get('final_active', True) and task.get('status') == 'pass' and task.get('final_gate_id')
        and (project.get('delivery') or {}).get('quality_recheck_id') == task.get('job_id')
        and not project.get('creative_quality_review_required') and _files_current(project) and _signature(project) == task.get('input_signature'))


def resume_reviewed_graph(graph, snapshot, project, configuration):
    if not final_current(project): return snapshot
    jid = project['quality_recheck']['task']['job_id']
    if snapshot.values.get('quality_review_id') == jid: return snapshot
    graph.update_state(configuration, {'project_id': project['id'], 'route': 'continue',
        'final_generation': project['production_revision'], 'quality_review_id': jid}, as_node='production')
    return graph.get_state(configuration)


def handle_final(project, *, long_episode=False):
    if not final_current(project): return None
    task = project['quality_recheck']['task']; gid = task['final_gate_id']
    answer = (project.get('answers') or {}).get(gid)
    if not answer: return {'route': 'awaiting_final'} if long_episode else None
    if answer['choice'] not in ({'approve', 'reedit', 'revise'} if long_episode else {'approve', 'reedit'}): return None
    if answer.get('actor') not in {'producer', 'validation_agent'}: raise ValueError('复核后的终审需要用户明确确认')
    from .execution_context import current_job
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        current = store._read(db, project['id']); job = current_job.get()
        if job:
            row = db.execute('SELECT status,lease FROM jobs WHERE id=?', (job['id'],)).fetchone()
            if not row or row['status'] != 'running' or row['lease'] != job.get('lease'): return None
        if not final_current(current): raise ValueError('终审引用的复核版本已变化')
        if gid in current.get('decision_effects', {}): return {'route': current['decision_effects'][gid]['route']}
        if answer['choice'] == 'approve':
            current.update(status='approved', stage='delivered', progress=100, accepted_at=store.now(), accepted_by=answer['actor'])
            route = 'done'
        elif answer['choice'] == 'revise':
            current.update(revision_action='revise', revision_feedback=answer.get('feedback', ''), revision_scope={})
            _state(current)['task']['final_active'] = False
            route = 'revision'
        else:
            current.update(status='paused', pause_requested=True, needs_decision={'type': 'timeline_edit', 'message': '请在完整时间线另存并检查剪辑版本。', 'feedback': answer.get('feedback', '')})
            route = 'timeline_edit'
        current.setdefault('decision_effects', {})[gid] = {'route': route, 'applied_at': store.now()}
        store._write(db, current)
    return None if route == 'revision' else {'route': route}
