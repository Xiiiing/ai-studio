"""Durable media-role handoffs executed by the existing production engine.

These records describe real calls and artifacts. They never approve media,
reset generation counters, rewrite a shot or grant additional expenditure.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
from pathlib import Path
import re
import time

from studio.core import sha256, write_json
from .creative_evidence_io import digest, read_json
from .production_recovery import _folder
from .scheduler import ProductionNeedsDecision

VERSION = 'ai-studio.media-handoff.v1'
_STATES = {'diagnosing', 'planning', 'handing_off', 'repairing', 'reviewing', 'resolved', 'stopped'}


def load(engine, ident):
    if not isinstance(ident, str) or not re.fullmatch('[a-f0-9]{64}', ident):
        raise ProductionNeedsDecision('媒体交接标识无效', 'production_handoff_corrupt')
    path = _folder(engine.project, engine.folder.parents[1]) / 'handoffs' / (ident + '.json')
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ProductionNeedsDecision('媒体交接路径无效', 'production_handoff_corrupt')
    saved = read_json(path)
    if saved.get('sha256') != digest(saved.get('document')) or saved['document']['identity']['project_id'] != engine.ident:
        raise ProductionNeedsDecision('媒体交接来源无效', 'production_handoff_corrupt')
    return {'id': ident, 'path': path, 'document': saved['document']}


def start(engine, target, directed):
    identity = {'schema': VERSION, 'project_id': engine.ident, 'asset': target['key'],
                'stage': target['kind'], 'source_signature': target['signature'],
                'recovery_record': directed['id'], 'source_sha256': directed['source_sha256']}
    key = digest(identity)
    path = _folder(engine.project, engine.folder.parents[1]) / 'handoffs' / (key + '.json')
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ProductionNeedsDecision('媒体交接路径无效', 'production_handoff_corrupt')
    if path.exists():
        saved = read_json(path)
        if saved.get('sha256') != digest(saved.get('document')) or saved.get('document', {}).get('identity') != identity:
            raise ProductionNeedsDecision('媒体岗位交接证据损坏，未新增生成', 'production_handoff_corrupt')
        return {'id': key, 'path': path, 'document': saved['document']}
    case = {'id': key, 'path': path, 'document': {'identity': identity, 'events': [], 'status': 'diagnosing'}}
    advance(engine, case, 'diagnosing', 'visual_critic', reason=directed['reason'])
    return case


def advance(engine, case, status, owner, *, reason=None, artifact=None):
    if status not in _STATES:
        raise ValueError('Invalid media handoff state')
    document = copy.deepcopy(case['document'])
    event = {'status': status, 'owner': owner, 'at': time.time()}
    if reason is not None:
        event['reason'] = str(reason)[:1200]
    if artifact is not None:
        event['artifact'] = copy.deepcopy(artifact)
    # Re-entering a completed boundary is reuse, not a second role execution.
    prior = document['events'][-1] if document['events'] else None
    if prior and {k:v for k,v in prior.items() if k != 'at'} == {k:v for k,v in event.items() if k != 'at'}:
        return
    if len(document['events']) >= 48:
        raise ProductionNeedsDecision('媒体岗位交接次数已达上限', 'production_handoff_limit')
    document.update(status=status, owner=owner, reason=reason)
    document['events'].append(event)
    write_json(case['path'], {'sha256': digest(document), 'document': document})
    case['path'].chmod(0o600)
    case['document'] = document
    identity = document['identity']
    engine.production['recovery'] = {'status': status, 'owner': owner, 'asset': identity['asset'],
        'stage': identity['stage'], 'source_signature': identity['source_signature'],
        'case_id': case['id'], 'case_path': str(case['path'].relative_to(engine.folder.parents[1])),
        'record_id': identity['recovery_record'], 'reason': reason,
        'message': '系统正在执行已登记的制作岗位交接。'}
    engine.emit('production_repair')


@contextmanager
def role(engine, case, status, owner):
    from . import team_ledger
    engine.boundary()
    advance(engine, case, status, owner)
    scope = re.sub(r'[^A-Za-z0-9_.-]', '_', str(case['document']['identity']['asset'])) + '_recovery_' + case['id'][:12]
    token = (team_ledger.begin_unit(engine.ident, 'management', scope, owner=owner,
                input_version=case['document']['identity']['source_sha256'],
                inputs={'case_id': case['id'], 'role_operation': status})
             if team_ledger.enabled(engine.project) else None)
    try:
        yield
    except BaseException as exc:
        if token:
            team_ledger.finish_unit(token, status='paused' if type(exc).__name__ == 'ProductionPaused' else 'blocked',
                                    details={'exception_type': type(exc).__name__})
        raise
    else:
        if token:
            team_ledger.finish_unit(token, details={'case_id': case['id'], 'operation': status})


def upstream_receipt(engine, case, target, operation, previous_signature):
    frame = target['references'][0]
    assessment = copy.deepcopy(target.get('first_frame_assessment') or {})
    if assessment.get('status') != 'pass' or assessment.get('eligible_for_selection') is False:
        raise ProductionNeedsDecision('上游尚未交付审核通过的首帧，原视频和费用保留', 'production_upstream_unreviewed')
    value = {'status': 'resolved', 'operation': operation, 'previous_source_signature': previous_signature,
             'frame_sha256': sha256(frame), 'first_frame_assessment': assessment, 'case_id': case['id']}
    if target.get('first_frame_compatibility'):
        value['first_frame_compatibility'] = copy.deepcopy(target['first_frame_compatibility'])
    path = case['path'].parent / ('upstream-' + digest(value) + '.json')
    if path.exists():
        if read_json(path) != value:
            raise ProductionNeedsDecision('上游交付回执不一致', 'production_handoff_corrupt')
    else:
        write_json(path, value)
        path.chmod(0o600)
    return {**value, 'receipt_path': str(path), 'receipt_sha256': sha256(path)}


def _approved_facts(shot):
    from .production_recovery import _STRATEGY_SHOT_FIELDS
    facts = {key: copy.deepcopy(shot[key]) for key in _STRATEGY_SHOT_FIELDS if key in shot}
    # Renderer normalizes the approved timeline to a 24-fps float. Integer 3
    # and float 3.0 are the same approval, not an edit to the shot contract.
    if isinstance(facts.get('seconds'), (int, float)) and not isinstance(facts.get('seconds'), bool):
        facts['seconds'] = round(float(facts['seconds']) * 24) / 24
    return facts


def _review_passed(qa):
    return (isinstance(qa, dict) and qa.get('status') == 'pass'
            and qa.get('eligible_for_selection') is not False
            and (qa.get('technical') or {}).get('status') == 'pass'
            and (qa.get('semantic') or {}).get('status') == 'pass')


def _owned_receipt_path(engine, receipt):
    raw = Path(receipt['path'])
    if not raw.is_absolute():
        raw = engine.folder.parents[1] / raw
    if '..' in raw.parts or any(p.is_symlink() for p in (raw, *raw.parents)):
        raise ValueError('Runtime media contains a symlink or traversal')
    path = engine.receipt_path(receipt)
    if not path.is_relative_to(engine.folder):
        raise ValueError('Runtime media is outside this project')
    return path


def _verified_runtime_media(engine, receipt):
    path = _owned_receipt_path(engine, receipt)
    if sha256(path) != receipt['sha256']:
        raise ValueError('Runtime media checksum changed')
    review = receipt.get('upstream_review')
    if review:
        review_path = Path(review['path'])
        if not review_path.is_absolute():
            review_path = engine.folder.parents[1] / review_path
        if ('..' in review_path.parts or any(p.is_symlink() for p in (review_path, *review_path.parents))
                or not review_path.is_relative_to(engine.folder / 'recovery' / 'handoffs')
                or sha256(review_path) != review['sha256']):
            raise ValueError('Upstream review receipt changed')
        envelope = read_json(review_path)
        document = envelope['document']
        if (envelope['sha256'] != digest(document) or document['media_sha256'] != receipt['sha256']
                or Path(document['path']).resolve() != path.resolve()):
            raise ValueError('Upstream review media mismatch')
        qa = document['qa']
    else:
        saved = read_json(path.parent / 'result.json')
        if (saved.get('sha256') != receipt['sha256']
                or engine.receipt_path(saved).resolve() != path.resolve()):
            raise ValueError('Runtime result receipt mismatch')
        qa = saved.get('qa')
    if not _review_passed(qa) or not _review_passed(receipt.get('qa')) or digest(qa) != digest(receipt['qa']):
        raise ValueError('Runtime media lacks matching independent approval')
    return path


def restore_media(engine, raw, current):
    """Restore verified upstream results without overriding approved shot facts."""
    if not getattr(engine, 'strategy_recovery', False) or not isinstance(current, dict):
        return {}
    sid = raw.get('id') or current.get('id')
    grant = (engine.production.get('upstream_contracts') or {}).get(sid)
    if (not grant or grant != digest(_approved_facts(raw))
            or grant != digest(_approved_facts(current))):
        return {}
    cases = []
    folder = engine.folder / 'recovery' / 'handoffs'
    try:
        for path in folder.glob('*.json'):
            if not re.fullmatch('[a-f0-9]{64}', path.stem):
                continue
            case = load(engine, path.stem)
            identity = case['document']['identity']
            if digest(identity) != case['id']:
                raise ValueError('Handoff identity changed')
            if (identity.get('asset') == sid and identity.get('stage') == 'video'
                    and any(e.get('status') == 'handing_off'
                            and e.get('owner') in {'visual_critic', 'image_generator'}
                            for e in case['document'].get('events', []))):
                cases.append(case)
        if not cases:
            return {}
        restored = {}
        for kind in ('image', 'video'):
            receipt = current.get(kind)
            if not isinstance(receipt, dict) or receipt == raw.get(kind):
                continue
            # Failed/pending video remains a candidate, never a selected result.
            if kind == 'video' and not _review_passed(receipt.get('qa')):
                continue
            path = _verified_runtime_media(engine, receipt)
            if kind == 'video' and not any(
                    event.get('status') == 'resolved'
                    and (event.get('artifact') or {}).get('sha256') == receipt['sha256']
                    for case in cases for event in case['document'].get('events', [])):
                # The motion case follows a separate handoff record. It still
                # must prove the reviewed video belongs to this same shot.
                matching = False
                for other in folder.glob('*.json'):
                    if not re.fullmatch('[a-f0-9]{64}', other.stem):
                        continue
                    doc = load(engine, other.stem)['document']
                    if doc['identity'].get('asset') == sid and any(
                            e.get('status') == 'resolved' and (e.get('artifact') or {}).get('sha256') == receipt['sha256']
                            for e in doc.get('events', [])):
                        matching = True
                        break
                if not matching:
                    raise ValueError('Video result has no independent resolved handoff')
            restored[kind] = copy.deepcopy(receipt)
            restored[kind + '_path'] = receipt['path']
            restored[kind + '_url'] = engine.media_url(path)
        if restored:
            newest = restored.get('video') or restored.get('image')
            restored['qa'] = copy.deepcopy(newest['qa'])
            restored['status'] = 'passed' if 'video' in restored else 'pending'
        return restored
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ProductionNeedsDecision) as exc:
        raise ProductionNeedsDecision('上游已交付素材或审核回执损坏，已保留记录且未回退重生成',
                                      'production_handoff_corrupt') from exc


def review_first_frame(engine, case, target):
    """Independently review current frame pixels; never allocate a media worker."""
    from contextlib import nullcontext
    from . import team_ledger
    from .quality import inspect_image, semantic_review, combine
    from .review_contracts import starting_frame
    shot = target['shot']
    image = shot['image']
    engine.boundary()
    try:
        path = _owned_receipt_path(engine, image)
    except (OSError, ValueError, TypeError, KeyError, ProductionNeedsDecision) as exc:
        raise ProductionNeedsDecision('原首帧归属或校验失败，未替换或重新生成', 'production_handoff_corrupt') from exc
    original_sha = sha256(path)
    if original_sha != image['sha256'] or not path.is_relative_to(engine.folder):
        raise ProductionNeedsDecision('原首帧校验失败，未替换或重新生成', 'production_handoff_corrupt')
    identity = {'case_id': case['id'], 'path': str(path), 'media_sha256': original_sha,
                'expected_sha256': digest(starting_frame(_approved_facts(shot))), 'review_policy': engine.review_policy}
    review_path = case['path'].parent / ('frame-review-' + digest(identity) + '.json')
    if review_path.exists():
        saved = read_json(review_path)
        if saved.get('sha256') != digest(saved.get('document')) or saved['document'].get('identity') != identity:
            raise ProductionNeedsDecision('原首帧补审回执损坏，未重复调用模型', 'production_handoff_corrupt')
        document = saved['document']
        if sha256(path) != document['media_sha256']:
            raise ProductionNeedsDecision('原首帧补审后像素变化，未套用旧结论', 'production_handoff_corrupt')
        if document['qa']['status'] == 'pass':
            image['qa'] = copy.deepcopy(document['qa'])
            image['upstream_review'] = {'path': str(review_path.relative_to(engine.folder.parents[1])),
                                        'sha256': sha256(review_path)}
            engine.emit('production_repair')
            return 'pass'
        if document['qa']['status'] == 'fail':
            image['qa'] = copy.deepcopy(document['qa'])
            image['upstream_review'] = {'path': str(review_path.relative_to(engine.folder.parents[1])), 'sha256': sha256(review_path)}
            engine.emit('production_repair')
            return 'fail'
    prior = copy.deepcopy(image.get('qa'))
    technical = inspect_image(path, engine.image_size)
    attempts = []
    semantic = {'status': 'not_run', 'issues': []}
    context = starting_frame(_approved_facts(shot))
    ledger = (team_ledger.unit(engine.ident, 'image_review', shot['id'], input_version=digest(identity),
              inputs={'existing_media': copy.deepcopy(image), 'case_id': case['id']})
              if team_ledger.enabled(engine.project) else nullcontext())
    with ledger:
        if technical['status'] == 'pass':
            for index in range(engine.semantic_max_attempts):
                engine.boundary()
                semantic = semantic_review(engine.evaluator, engine.project, context, path, [])
                attempts.append(copy.deepcopy(semantic))
                if semantic.get('status') in {'pass', 'fail'}:
                    break
        if sha256(path) != original_sha:
            raise ProductionNeedsDecision('补审期间原首帧像素发生变化，未套用旧结论', 'production_handoff_corrupt')
        qa = combine(technical, semantic, True)
        document = {'identity': identity, 'path': str(path), 'media_sha256': original_sha,
                    'original_qa': prior, 'qa': qa, 'semantic_attempts': attempts}
        write_json(review_path, {'sha256': digest(document), 'document': document})
        review_path.chmod(0o600)
        advance(engine, case, 'handing_off', 'visual_critic',
                reason='已独立补审原首帧像素；保留原审核文字供追溯。',
                artifact={'review_receipt': str(review_path.relative_to(engine.folder.parents[1])),
                          'review_receipt_sha256': sha256(review_path), 'media_sha256': original_sha,
                          'original_qa': prior, 'qa': qa})
        if qa['status'] == 'pass':
            image['qa'] = copy.deepcopy(qa)
            image['upstream_review'] = {'path': str(review_path.relative_to(engine.folder.parents[1])),
                                        'sha256': sha256(review_path)}
            engine.emit('production_repair')
            return 'pass'
        if qa['status'] == 'fail':
            image['qa'] = copy.deepcopy(qa)
            image['upstream_review'] = {'path': str(review_path.relative_to(engine.folder.parents[1])), 'sha256': sha256(review_path)}
            engine.emit('production_repair')
            return 'fail'
        pending = {'asset': shot['id'], 'stage': 'image', 'path': image['path'],
                   'upstream_case_id': case['id'], 'review_kind': 'first_frame',
                   'take': image.get('take', path.parent.name),
                   'retryable_transport': semantic.get('retryable_transport') is True,
                   'detail': semantic.get('detail')}
        raise ProductionNeedsDecision('原首帧审核暂不可用，保留原图待补审且未重新生成',
            'semantic_unavailable', {'pending': [pending], 'resume_action': 'review_existing_assets_only',
                                     'retryable_transport': pending['retryable_transport']})
