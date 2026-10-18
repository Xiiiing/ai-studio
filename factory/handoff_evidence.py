"""Associate recorded product outputs with equal values in recorded receiver inputs.

This is a read-only content check, not a fabricated transport event. Ambiguous
origins and outputs recorded after a request are deliberately left unassociated.
Raw prompts stay in the caller and never enter the returned projection.
"""
from datetime import datetime

from .creative_evidence_io import digest


def _time(value):
    try:
        if type(value) in (int, float):
            return float(value)
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (TypeError, ValueError, AttributeError, OverflowError):
        return None


def attach_received_content(outputs, inputs, summarize):
    """outputs/inputs contain private raw objects; only summarized matches escape."""
    index = {}
    for task, value in outputs:
        parts = [('完整结果', value)]
        if isinstance(value, dict) and value.get('issues'):
            parts.append(('审核修改意见', value['issues']))
        for label, part in parts:
            if not isinstance(part, (dict, list)) or not part:
                continue
            index.setdefault(digest(part), []).append((task, label, part))

    fields = {'world': '全剧设定', 'episodes': '分集大纲', 'outline': '单集大纲',
        'previous': '前序大纲', 'review_feedback': '审核修改意见',
        'scenes': '场景方案', 'scene_plan': '场景方案'}
    remaining = 128
    for receiver, payload in inputs:
        if remaining <= 0:
            break
        received, seen = [], set()
        started = _time(receiver.get('started_at'))
        for key, label in fields.items():
            value = payload.get(key)
            parts = [value, *value] if isinstance(value, list) else [value]
            for part in parts:
                if not isinstance(part, (dict, list)) or not part:
                    continue
                candidates = []
                for sender, kind, original in index.get(digest(part), []):
                    if sender['role'] == receiver['role']:
                        continue
                    ended = _time(sender.get('finished_at') or sender.get('generated_at'))
                    if started is not None and ended is not None and ended > started:
                        continue
                    candidates.append((sender, kind, original, ended))
                if len(candidates) > 1:
                    timed = [c for c in candidates if c[3] is not None]
                    if started is None or len(timed) != len(candidates):
                        continue
                    newest = max(c[3] for c in timed)
                    candidates = [c for c in timed if c[3] == newest]
                if len(candidates) != 1:
                    continue
                sender, kind, original, _ = candidates[0]
                ident = (sender['id'], key, digest(original))
                if ident in seen:
                    continue
                seen.add(ident)
                content = summarize(original)
                if not content:
                    continue
                received.append({'source_task_id': sender['id'], 'source_role': sender['role'],
                    'label': label if kind == '完整结果' else kind, 'value': content,
                    'input_field': key, 'basis': 'recorded_input_content_match'})
                remaining -= 1
                if len(received) >= 12 or remaining <= 0:
                    break
            if len(received) >= 12 or remaining <= 0:
                break
        if received:
            receiver['received'] = received
