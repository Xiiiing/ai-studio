"""Evidence-bound episode observations and immutable whole-drama delivery.

Sampled frames do not certify unobserved action, speech comprehension or human
approval. All adopted media remain checksum-bound and retain their originals.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from studio import core
from . import store
from .drama_intent import enabled
from .schemas import object_schema as obj, text_schema as txt, array_schema as arr
from .scheduler import ProductionNeedsDecision, ProductionPaused

REVIEW = obj({'status': {'enum': ['pass', 'fail', 'uncertain']}, 'summary': txt(1, 2000),
    'checks': arr(obj({'beat_id': txt(1, 64), 'status': {'enum': ['pass', 'fail', 'uncertain']},
                      'shot_ids': arr(txt(1, 80), 1, 24), 'evidence': txt(1, 1500)}), 2, 64),
    'observed_end': txt(1, 2000), 'issues': arr(txt(1, 1500), 0, 20)})


def _file(project, name):
    receipt = next((r for r in project['delivery'].get('artifacts', []) if Path(r['path']).name == name), None)
    if not receipt:
        raise ProductionNeedsDecision('交付缺少文件：' + name, 'delivery_incomplete')
    path = core.within(core.ROOT, receipt['path'])
    owned = core.ROOT / 'production' / project['slug']
    if not path.is_relative_to(owned.resolve()) or core.sha256(path) != receipt['sha256'] or path.stat().st_size != receipt['bytes']:
        raise ProductionNeedsDecision('交付文件与当前回执不符', 'delivery_corrupt')
    return path, receipt


def review_episode(project, config):
    if not enabled(project):
        return
    from .drama_resources import ensure
    from .config import llm_identity
    from .llm import _request, _image_url
    from .drama_continuity import frame
    path, media = _file(project, 'episode_clean.mp4')
    shots = project.get('shots') or (project.get('script') or {}).get('shots') or []
    units = []
    if project.get('unit_ids'):
        units = [store.get(uid, False) for uid in project.get('edit_unit_order', project['unit_ids'])]
        shots = [{**s, 'id': unit['global_prefix'] + '-' + s['id'], 'unit_id': unit['id'], 'local_shot_id': s['id']} for unit in units for s in unit.get('shots', [])]
    beats = project['outline'].get('beats') or [{'id': 'unit_endpoint', 'description': '只描述该单元实际末帧可见人物、持物与位置；不据此证明未观察的事件。'}]
    identity = {'media_sha256': media['sha256'], 'outline': project['outline'],
                'shots': core.fingerprint(shots), 'production_revision': project['production_revision'],
                'dependencies': core.fingerprint(project.get('dependency_context', [])), 'model': llm_identity(config, 'visual_critic'), 'policy': 'episode_beats_and_endpoints.v1'}
    signature = core.fingerprint(identity)
    saved = project.get('episode_review') or {}
    if saved.get('signature') == signature and saved.get('status') == 'pass' and not saved.get('issues') and all(c['status'] == 'pass' for c in saved.get('checks', [])):
        return saved
    # Samples are located on the exported timeline; last frame is always included.
    samples, cursor = [], 0.0
    for index, shot in enumerate(shots):
        visible = set(shot.get('beat_ids', []))
        if beats[0]['id'] == 'unit_endpoint' and index == len(shots) - 1:
            visible.add('unit_endpoint')
        if visible or index == len(shots) - 1:
            for offset in sorted({0.0, shot['seconds']/2, max(0, shot['seconds'] - 1/24)}):
                samples.append({'shot_id': shot['id'], 'beat_ids': sorted(visible),
                                'timestamp': round(cursor + offset, 6),
                                'unit_id': shot.get('unit_id'), 'local_shot_id': shot.get('local_shot_id', shot['id'])})
        cursor += shot['seconds']
    if not samples or abs(cursor - project['duration_seconds']) > 1/24:
        raise ProductionNeedsDecision('单集检查缺少与实际成片对应的镜头时间线', 'episode_review_evidence_missing')
    ensure(project, config, role='visual_critic')
    evidence = {'media_sha256': media['sha256'], 'samples': samples,
                'coverage': 'sampled_shot_endpoints_not_full_film_or_audio_observation'}
    payload = {'narrative_scope': project['narrative_scope'], 'beats': beats,
               'planned_ending': project['outline']['ending_state'], 'evidence': evidence,
               'script': project.get('script'), 'audio_timing': [s.get('audio_timing') for s in shots]}
    folder = core.ROOT / 'production' / project['slug'] / 'quality' / 'episode' / signature
    result_path = folder / 'review.json'
    if result_path.exists():
        result = _read_review(result_path, signature)
    else:
        results = []
        # One request per event: bounded context, every mapped shot retained.
        for beat in beats:
            relevant = [s for s in samples if beat['id'] in s['beat_ids']]
            if not relevant or len(relevant) > 48:
                raise ProductionNeedsDecision('必要事件缺少可用的有界媒体证据', 'episode_review_evidence_missing')
            # Include the actual final frame for observed_end in every review.
            final = samples[-1]
            if final not in relevant:
                relevant.append(final)
            event_payload = {**payload, 'beats': [beat], 'evidence': {**evidence, 'samples': relevant}}
            content = [{'type': 'text', 'text': json.dumps(event_payload, ensure_ascii=False)}]
            for sample in relevant:
                content.extend([{'type': 'text', 'text': json.dumps(sample, ensure_ascii=False)},
                                {'type': 'image_url', 'image_url': {'url': _image_url(frame(path, sample['timestamp']))}}])
            event_schema = copy.deepcopy(REVIEW)
            event_schema['properties']['checks'].update(minItems=1, maxItems=1)
            event_folder = core.within(folder, beat['id'])
            event_path = event_folder / 'review.json'
            intent = event_folder / 'intent.json'
            if event_path.exists():
                answer = _read_review(event_path, signature)
            else:
                parsed = list(event_folder.glob('calls/*/parsed.json'))
                if parsed:
                    answer = core.read_json(parsed[-1])
                elif intent.exists():
                    raise ProductionNeedsDecision('上次成片检查提交结果未知，保留原视频等待对账', 'episode_review_unknown')
                else:
                    core.write_json(intent, {'signature': signature, 'beat_id': beat['id']})
                    answer = _request(config, 'visual_critic',
                        '按实际带时间戳的成片抽样帧检查本次必要事件及末态，只返回本次beat的一个check。'
                        '不得用剧本或标签证明画面发生。明确矛盾fail；动作或结果无法由样本判断时uncertain。'
                        '指出实际镜号和可见证据；observed_end只描述最后实际画面可见的人物持物状态，不复制计划。'
                        '遵循narrative_scope：中间集不得被要求结束全剧，末集检查结局兑现。'
                        '不声称看过未采样画面或听过音轨。',
                        event_payload, event_schema, event_folder, max_tokens=2400, temperature=.2, content=content)
                from jsonschema import validate
                validate(answer, event_schema)
                core.write_json(event_path, {'signature': signature, 'value': answer, 'sha256': core.fingerprint(answer)})
            results.append(answer)
        checks = [c for r in results for c in r['checks']]
        statuses = {r['status'] for r in results} | {c['status'] for c in checks}
        result = {'status': 'uncertain' if 'uncertain' in statuses else 'fail' if 'fail' in statuses else 'pass',
                  'summary': '；'.join(r['summary'] for r in results), 'checks': checks,
                  'observed_end': results[-1]['observed_end'], 'issues': [i for r in results for i in r['issues']]}
        core.write_json(result_path, {'signature': signature, 'value': result, 'sha256': core.fingerprint(result)})
    required = {b['id'] for b in beats}
    checks = result.get('checks', [])
    allowed_shots = {b['id']: {s['shot_id'] for s in samples if b['id'] in s['beat_ids']} for b in beats}
    if ({c['beat_id'] for c in checks} != required or len(checks) != len(required)
            or any(not set(c['shot_ids']) <= allowed_shots.get(c['beat_id'], set()) for c in checks)):
        raise ProductionNeedsDecision('必要事件审核缺少当前媒体的对应证据', 'episode_review_evidence_missing')
    result = {**result, 'signature': signature, 'evidence': evidence, 'outline_sha256': core.fingerprint(project['outline']),
              'production_revision': project['production_revision'], 'delivery_signature': project['delivery']['source_signature']}
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        _guard(db, project)
        current = store._read(db, project['id'])
        current['episode_review'] = result
        store._write(db, current)
    if result['status'] != 'pass' or result['issues'] or any(c['status'] != 'pass' for c in checks):
        if result['status'] == 'uncertain' or any(c['status'] == 'uncertain' for c in checks):
            raise ProductionNeedsDecision('成片事件证据不足，已保留视频等待核验', 'episode_review_uncertain', result)
        return_to_script(project, result)
    return result


def return_to_script(project, review):
    """Bounded local revision, through the original graph and cumulative budgets."""
    from .remote_tasks import ProductionDeferred
    import time
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        _guard(db, project)
        current = store._read(db, project['id'])
        _apply_return(db, current, review)
        store._event(db, project['id'], '整体成片检查已退回编剧与分镜定向返修，累计费用及原片保留。', 'episode_repair')
    raise ProductionDeferred(time.time() + 1, 'local_render', {'reason': 'episode_repair'})



def _apply_return(db, current, review):
    history = current.get('episode_repair_history', [])
    if len(history) >= current['budget']['max_attempts'] - 1:
        raise ProductionNeedsDecision('单集整体返修额度用尽，保留原片和全部检查', 'episode_review_exhausted', review)
    if current.get('pause_requested') or current['status'] == 'cancelled':
        raise ProductionPaused()
    shot_ids = sorted({sid for c in review['checks'] if c['status'] == 'fail' for sid in c['shot_ids']})
    if not shot_ids:
        raise ProductionNeedsDecision('检查未确定可安全返修的镜头范围', 'episode_review_failed', review)
    if current.get('unit_ids'):
        current.pop('dependency_context', None)
        current.pop('accepted_by', None)
        current.pop('accepted_at', None)
        samples = review['evidence']['samples']
        targets = {}
        for sample in samples:
            if sample['shot_id'] in shot_ids:
                targets.setdefault(sample['unit_id'], set()).add(sample['local_shot_id'])
        for uid, local_ids in targets.items():
            unit = store._read(db, uid)
            _return_patch(unit, review, sorted(local_ids))
            store._write(db, unit)
        current['episode_repair_history'] = [*history, copy.deepcopy(review)]
        current['delivery_history'] = [*current.get('delivery_history', []), current['delivery']]
        current.update(delivery=None, status='producing', production_revision=current['production_revision']+1)
        store._write(db, current)
    else:
        _return_patch(current, review, shot_ids)
        store._write(db, current)

def _return_patch(current, review, shot_ids):
    current.pop('dependency_context', None)
    current.pop('accepted_by', None)
    current.pop('accepted_at', None)
    revisions = current.setdefault('episode_media_revisions', {})
    shots = {s['id']: s for s in current.get('shots', [])}
    for sid in shot_ids:
        previous = shots.get(sid, {}).get('render_revisions', {})
        old = revisions.get(sid, {})
        revisions[sid] = {kind: max(previous.get(kind, 0), old.get(kind, 0)) + 1 for kind in ('image', 'video')}
        current.get('shot_selections', {}).pop(sid, None)
    history = current.get('episode_repair_history', [])
    current['delivery_history'] = [*current.get('delivery_history', []), current['delivery']]
    current.update(episode_repair_history=[*history, copy.deepcopy(review)],
        script_feedback={'shot_ids': shot_ids, 'notes': '仅修复成片检查指出的必要事件，保留其余已批准内容：' + json.dumps(review, ensure_ascii=False)},
        script_generation=current['script_generation'] + 1, production_revision=current['production_revision'] + 1,
        workflow_restart_from='screenplay', workflow_restart_revision=current['revision'] + 1,
        delivery=None, status='producing', error=None, needs_decision=None)


def review_current(project):
    review = project.get('episode_review') or {}
    return bool(review.get('status') == 'pass' and not review.get('issues') and review.get('checks')
        and all(c.get('status') == 'pass' for c in review['checks'])
        and review.get('production_revision') == project.get('production_revision')
        and review.get('outline_sha256') == core.fingerprint(project.get('outline'))
        and review.get('delivery_signature') == (project.get('delivery') or {}).get('source_signature'))

def adopted_end(project):
    review = project.get('episode_review') or {}
    media = next((r for r in (project.get('delivery') or {}).get('artifacts', []) if Path(r['path']).name == 'episode_clean.mp4'), {})
    if (not review_current(project) or review.get('evidence', {}).get('media_sha256') != media.get('sha256')
            or review.get('production_revision') != project.get('production_revision')
            or review.get('outline_sha256') != core.fingerprint(project.get('outline'))
            or review.get('delivery_signature') != project.get('delivery', {}).get('source_signature')):
        raise ProductionNeedsDecision('前集缺少绑定当前成片的实际末态证据', 'dependency_evidence_missing')
    _file(project, 'episode_clean.mp4')
    return {'state': review['observed_end'], 'media_sha256': media['sha256'],
            'samples': review['evidence']['samples'][-1:], 'scope': 'observed_final_frame_not_unseen_action'}


def maybe_enqueue(db, drama):
    if not enabled(drama) or drama.get('pause_requested') or drama['status'] in {'cancelled', 'blocked', 'failed'}:
        return
    episodes = [store._read(db, eid) for eid in drama['episodes']]
    if episodes and all(e['status'] in {'awaiting_final', 'approved'} and review_current(e) for e in episodes):
        signature = core.fingerprint([e['episode_review']['signature'] for e in episodes])
        if (drama.get('delivery_bundle') or {}).get('source_signature') != signature:
            store._enqueue(db, drama['id'], 'drama_delivery')


def assemble(pid):
    from .config import config_snapshot
    from .schemas import cached_json_call
    from .drama_exports import archive
    drama = store.get(pid, False)
    episodes = [store.get(eid, False) for eid in drama['episodes']]
    if drama.get('pause_requested') or drama['status'] == 'cancelled':
        raise ProductionPaused()
    if not episodes or any(e['status'] not in {'awaiting_final', 'approved'} for e in episodes):
        raise ProductionNeedsDecision('全剧尚未完成，部分成果仍可下载', 'series_incomplete')
    from .drama_runtime import stale_dependencies
    stale = [e for e in episodes if stale_dependencies(e, episodes)]
    if stale:
        _return_series(drama, episodes, {'status': 'fail', 'summary': '上游实际成片版本已变化，重新采用实际末态。',
            'issues': ['后续剧集仍引用旧的上游成片版本'],
            'repairs': [{'episode_number': e['episode_number'],
                         'shot_ids': sorted({s['shot_id'] for s in e['episode_review']['evidence']['samples']}),
                         'reason': '上游成片已返修，请依据当前实际末态重新检查并修正承接，保留无关事件。'} for e in stale]})
        return {'status': 'repairing'}
    evidence = [{'number': e['episode_number'], 'outline': e['outline'], 'review': e['episode_review'],
                 'observed_end': adopted_end(e)} for e in episodes]
    signature = core.fingerprint([e['episode_review']['signature'] for e in episodes])
    with config_snapshot(drama['execution_config']):
        review = cached_json_call(drama, drama['execution_config'], 'story_critic', 'series-delivery-' + signature[:12],
            '独立检查整剧必要事件、因果承接、相邻集实际可见状态与末集结局兑现。'
            '只依据提供的实际抽样审核和固定版本；计划或清单不等于真实效果。'
            '明确矛盾列issues，repairs只列有证据、可定向修复的集号和该集检查中的实际镜号；不能凭偏好修改无关集。'
            '证据不足status=uncertain，不得伪称看过全片或听过音轨。',
            {'brief': drama['brief'], 'plan': drama['plans'][-1]['document'], 'episodes': evidence},
            obj({'status': {'enum': ['pass', 'fail', 'uncertain']}, 'summary': txt(1, 2000), 'issues': arr(txt(1, 1500), 0, 20),
                 'repairs': arr(obj({'episode_number': {'type': 'integer', 'enum': [e['episode_number'] for e in episodes]},
                                     'shot_ids': arr(txt(1, 80), 1, 24), 'reason': txt(1, 1500)}), 0, len(episodes))}), max_tokens=4500)
    if review['status'] == 'fail' and review.get('repairs'):
        _return_series(drama, episodes, review)
        return {'status': 'repairing'}
    if review['status'] != 'pass' or review['issues'] or review.get('repairs'):
        raise ProductionNeedsDecision('整剧检查尚未通过，逐集成果与证据已保留', 'series_review_' + review['status'], review)
    # Existing exporter verifies every artifact checksum and preserves partial-download behavior.
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        _guard(db, drama)
        current = store._read(db, pid)
        current['series_review'] = {'source_signature': signature, **review}
        store._write(db, current)
    path = archive(pid)
    receipt = core.media_receipt(path)
    bundle = {'source_signature': signature, 'status': 'awaiting_human_review', 'review': review,
              'episode_count': len(episodes), 'archive': receipt,
              'download_url': '/api/factory/dramas/' + pid + '/download?bundle=' + signature,
              'evidence_scope': 'sampled_episode_reviews_and_measured_audio_not_human_approval'}
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        _guard(db, drama)
        current = store._read(db, pid)
        fresh = [store._read(db, eid) for eid in current['episodes']]
        if (current.get('pause_requested') or current['status'] == 'cancelled'
                or any(not review_current(e) for e in fresh)
                or core.fingerprint([e.get('episode_review', {}).get('signature') for e in fresh]) != signature):
            raise ProductionPaused('整剧版本或授权已变化，旧交付包保留但未采用')
        current['delivery_bundle'] = bundle
        store._write(db, current)
        store._event(db, pid, '全剧固定版本交付包已生成，等待出品人观看验收。', 'series_delivery')
    return bundle


def download_bundle(pid, signature):
    project = store.get(pid, False)
    bundle = project.get('delivery_bundle') or {}
    if project.get('record_kind') != 'drama' or bundle.get('source_signature') != signature:
        raise ValueError('交付包版本不存在')
    receipt = bundle['archive']
    path = core.within(core.ROOT, receipt['path'])
    folder = (core.ROOT / 'production' / project['slug'] / 'exports').resolve()
    if not path.is_relative_to(folder) or core.sha256(path) != receipt['sha256'] or path.stat().st_size != receipt['bytes']:
        raise ValueError('固定交付包与回执不符')
    return path


def _guard(db, project):
    from .drama_planning import _authority
    from .execution_context import current_job
    job = current_job.get()
    owner = project['id']
    if job and project.get('record_kind') == 'scene_unit' and project.get('episode_id') == job['project_id']:
        owner = project['episode_id']
    if job and job.get('kind') == 'drama_delivery' and project.get('drama_id') == job['project_id']:
        drama = store._read(db, job['project_id'])
        if project['id'] not in drama['episodes']:
            raise ProductionPaused('返修对象不在当前整剧授权范围')
        owner = job['project_id']
    _authority(db, owner)
    current = store._read(db, project['id'])
    root = store._read(db, current['drama_id']) if current.get('drama_id') else current
    if any(p.get('pause_requested') or p['status'] in {'paused', 'cancelled'} or p.get('automation', {}).get('suspended') for p in [current, root]):
        raise ProductionPaused('已在成片证据提交边界暂停，原回执保留')
    if (current.get('production_revision') != project.get('production_revision') or current.get('outline') != project.get('outline')
            or current.get('plan_version') != project.get('plan_version')):
        raise ProductionPaused('制作版本已变化，迟到检查仅保留历史证据')


def _read_review(path, signature):
    saved = core.read_json(path)
    if saved.get('signature') != signature or saved.get('sha256') != core.fingerprint(saved.get('value')):
        raise ProductionNeedsDecision('成片检查缓存与当前媒体或回执不符，未新增请求', 'episode_review_corrupt')
    return saved['value']


def _return_series(drama, episodes, review):
    history = drama.get('series_repair_history', [])
    if len(history) >= drama['automation']['budget']['max_attempts'] - 1:
        raise ProductionNeedsDecision('整剧定向返修额度用尽，原成果和证据已保留', 'series_review_exhausted', review)
    by_number = {e['episode_number']: e for e in episodes}
    if len({r['episode_number'] for r in review['repairs']}) != len(review['repairs']):
        raise ProductionNeedsDecision('整剧返修重复引用同一集，未扩大生成范围', 'series_review_invalid_scope')
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        _guard(db, drama)
        current = store._read(db, drama['id'])
        for request in review['repairs']:
            episode = by_number[request['episode_number']]
            _guard(db, episode)
            episode = store._read(db, episode['id'])
            allowed = {s['shot_id'] for s in episode['episode_review']['evidence']['samples']}
            if not set(request['shot_ids']) <= allowed:
                raise ProductionNeedsDecision('整剧返修引用未知镜头，未扩大生成范围', 'series_review_invalid_scope')
            returned = {'status': 'fail', 'summary': review['summary'], 'issues': [request['reason']],
                        'evidence': episode['episode_review']['evidence'],
                        'checks': [{'beat_id': episode['outline']['beats'][0]['id'], 'status': 'fail',
                                    'shot_ids': request['shot_ids'], 'evidence': request['reason']}]}
            _apply_return(db, episode, returned)
            store._enqueue(db, episode['id'], 'drama_episode')
        current.update(series_repair_history=[*history, copy.deepcopy(review)], status='producing', stage='episodes', error=None)
        store._write(db, current)
        store._event(db, drama['id'], '整剧检查已按证据退回指定剧集镜头，未受影响成果保留。', 'series_repair')
