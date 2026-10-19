"""Owned HTTP jobs on a user-managed ComfyUI; never controls its process."""
from __future__ import annotations
import json
import os
from pathlib import Path
import time
from urllib.parse import quote, urlencode
import uuid
from studio.core import fingerprint, media_receipt, read_json, write_json
from .providers import ProviderError, validate_endpoint
from .connection_secrets import secret_value
from .video_transport import _transfer
from .scheduler import ProductionBudgetExceeded, ProductionNeedsDecision


OWNED_CANCEL_PROTOCOL = 'comfyui.jobs.cancel.v1'
VERIFIED_CANCEL_VERSIONS = {'0.30.0'}


def _execution_usage(history):
    events = (history or {}).get('status', {}).get('messages', [])
    starts = [row[1].get('timestamp') for row in events if len(row) == 2 and row[0] == 'execution_start']
    ends = [row[1].get('timestamp') for row in events if len(row) == 2 and row[0] in
            {'execution_success', 'execution_error', 'execution_interrupted'}]
    if starts and ends and isinstance(starts[0], (int, float)) and isinstance(ends[-1], (int, float)) and ends[-1] >= starts[0]:
        return {'execution_seconds': (ends[-1] - starts[0]) / 1000,
                'start_timestamp_ms': starts[0], 'end_timestamp_ms': ends[-1]}


def owned_usage(state, gpu_count=2):
    """Only terminal Comfy execution timestamps settle a running job's bill."""
    meter = state.get('owned_usage') or {}
    seconds = meter.get('execution_seconds')
    final = state.get('state') in {'cancelled', 'completed', 'failed'} and isinstance(seconds, (int, float))
    observed = max(0.0, state.get('last_active_at', 0) - state.get('first_active_at', state.get('last_active_at', 0)))
    return {'gpu_seconds': round(seconds * gpu_count, 3) if final else 0,
            'usage_final': final, 'provisional_gpu_seconds': round(observed * gpu_count, 3),
            'accounting': 'comfy_execution_timestamps_times_gpu_count' if final else 'unsettled_owned_comfy_execution',
            'prompt_id': state.get('prompt_id'), 'gpu_count': gpu_count}


class ExternalComfy:
    def __init__(self, profile):
        self.profile = profile
        self.base = validate_endpoint(profile)

    def request(self, route, payload=None, headers=None, destination=None, timeout=None):
        auth = {}
        env = self.profile.get('api_key_env')
        if env:
            secret = secret_value(env)
            if not secret or any(c in secret for c in '\r\n'): raise ProviderError('missing_auth', 'ComfyUI API Key 缺失或无效')
            auth['Authorization'] = 'Bearer ' + secret
        if isinstance(payload, dict):
            payload = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
            auth['Content-Type'] = 'application/json'
        return _transfer(self.base + route, self.profile, {**auth, **(headers or {})}, payload=payload,
                         destination=destination, timeout=timeout or self.profile.get('request_timeout_seconds', 120),
                         max_bytes=512*1024*1024 if destination else 8*1024*1024)

    def check(self):
        info = self.request('/object_info')
        if self.profile.get('workflow_template'):
            from .workflow_templates import load_template, check_nodes
            check_nodes(load_template(self.profile['workflow_template']), info)
        return info

    def upload(self, path):
        path = Path(path)
        if not path.is_file() or path.stat().st_size > 20*1024*1024: raise ValueError('参考图须小于20 MiB')
        # Content-derived names avoid collisions across jobs; no paths from the user.
        from studio.core import sha256
        name = sha256(path) + path.suffix.lower()
        boundary = 'studio-' + uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
                + path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode())
        answer = self.request('/upload/image', body, {'Content-Type': 'multipart/form-data; boundary=' + boundary})
        filename = answer.get('name'); sub = answer.get('subfolder', '')
        if not isinstance(filename, str) or not filename or any(c in filename for c in '/\\') or not isinstance(sub, str) or Path(sub).is_absolute() or '..' in Path(sub).parts:
            raise ValueError('ComfyUI 上传结果无效')
        return (sub + '/' if sub else '') + filename

    def execute(self, graph, dest, kind, output=None, check=None, timeout=1800,
                *, stop_on_timeout=False, cancellation_timeout=10):
        import fcntl
        dest = Path(dest); dest.mkdir(parents=True, exist_ok=True)
        with (dest / 'submission.lock').open('a+') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            return self._execute(graph, dest, kind, output, check, timeout, stop_on_timeout, cancellation_timeout)

    def _owned_capability(self):
        protocol = self.profile.get('owned_cancel_protocol')
        version = self.profile.get('verified_comfyui_version')
        if protocol != OWNED_CANCEL_PROTOCOL or version not in VERIFIED_CANCEL_VERSIONS:
            raise ProductionNeedsDecision('驻留服务尚未核验按任务原子取消协议，未提交新生成', 'comfy_owned_cancel_unverified')
        try:
            stats = self.request('/system_stats', timeout=2)
            jobs = self.request('/api/jobs?limit=1', timeout=2)
        except ProviderError:
            raise ProductionNeedsDecision('驻留服务未提供已核验取消协议，未提交新生成', 'comfy_owned_cancel_unverified') from None
        if stats.get('system', {}).get('comfyui_version') != version:
            raise ProductionNeedsDecision('驻留服务版本与已核验取消协议不符', 'comfy_owned_cancel_unverified')
        if not isinstance(jobs.get('jobs'), list) or not isinstance(jobs.get('pagination'), dict):
            raise ProductionNeedsDecision('驻留服务未提供已核验的任务协议', 'comfy_owned_cancel_unverified')
        return {'protocol': protocol, 'comfyui_version': version}

    def _observe_owned(self, state):
        ident = quote(state['prompt_id'], safe='')
        history = self.request('/history/' + ident, timeout=2).get(state['prompt_id'])
        observed_at = time.time()
        queue = self.request('/queue', timeout=2)
        if not all(isinstance(queue.get(key), list) for key in ('queue_running', 'queue_pending')):
            raise ValueError('Comfy queue response is not a supported queue snapshot')
        running = any(len(item) > 1 and item[1] == state['prompt_id'] for item in queue['queue_running'])
        pending = any(len(item) > 1 and item[1] == state['prompt_id'] for item in queue['queue_pending'])
        if running:
            state.setdefault('first_active_at', time.time())
            state['last_active_at'] = max(state.get('last_active_at', 0), observed_at)
        state['owned_queue_state'] = 'running' if running else 'pending' if pending else 'absent'
        if history:
            state['terminal_history'] = history
        return history, running or pending

    def reconcile_owned_stop(self, dest):
        """Only observes the saved ID; never submits work or repeats a cancel."""
        dest = Path(dest)
        state = read_json(dest / 'submission.json')
        if state.get('state') not in {'cancel_pending', 'cancelled', 'completed', 'failed'}:
            return state
        if state.get('owned_usage') is not None:
            return state
        history, active = self._observe_owned(state)
        if not active:
            state.setdefault('stop_observed_at', time.time())
            usage = _execution_usage(history)
            if usage:
                state['owned_usage'] = usage
            if state.get('owned_usage') is not None:
                state.update(state='cancelled' if state['state'] == 'cancel_pending' else state['state'], stop_confirmed_at=time.time())
        write_json(dest / 'submission.json', state)
        return state

    def _stop_owned(self, state, dest, reason, confirmation_seconds):
        state.setdefault('queue_at_cancel', state.get('owned_queue_state'))
        state.update(state='cancel_pending', stop_reason=reason, stop_requested_at=state.get('stop_requested_at', time.time()))
        write_json(dest / 'submission.json', state)  # Intent precedes any cancellation side effect.
        try:
            proof = self._owned_capability()
            state['cancellation_capability'] = proof
            answer = self.request('/api/jobs/' + quote(state['prompt_id'], safe='') + '/cancel',
                                  {'prompt_id': state['prompt_id']}, timeout=2)
            if type(answer.get('cancelled')) is not bool:
                raise ValueError('Invalid cancellation acknowledgement')
            state['cancel_dispatched'] = answer['cancelled']
        except Exception as exc:
            state['cancel_error'] = type(exc).__name__ + ': ' + str(exc)[:500]
        write_json(dest / 'submission.json', state)
        end = time.monotonic() + min(30, max(.01, confirmation_seconds))
        while time.monotonic() < end:
            try:
                state = self.reconcile_owned_stop(dest)
                if state['state'] == 'cancelled':
                    raise ProductionNeedsDecision('本次驻留任务已按预算停止，原材料与费用回执保留', 'comfy_budget_stopped',
                                                  {'prompt_id': state['prompt_id'], 'stop_reason': reason})
            except ProductionNeedsDecision:
                raise
            except Exception:
                pass
            time.sleep(min(.1, max(0, end - time.monotonic())))
        raise ProductionNeedsDecision('本次驻留任务取消尚未确认；禁止新付费提交，继续核对原任务', 'comfy_cancellation_unresolved',
                                      {'prompt_id': state['prompt_id'], 'stop_reason': reason})

    def _execute(self, graph, dest, kind, output, check, timeout, stop_on_timeout=False, cancellation_timeout=10):
        journal = dest / 'submission.json'; result = dest / 'result.json'
        signature = fingerprint({'graph': graph, 'output': output, 'endpoint': self.base})
        if result.exists():
            saved = read_json(result); path = Path(saved['path'])
            from studio.core import ROOT, sha256
            path = ROOT / path
            if saved.get('graph_sha256') != signature or not path.is_file() or sha256(path) != saved['sha256']:
                raise ProductionNeedsDecision('ComfyUI 缓存与当前工作流不匹配', 'cache_integrity')
            return path
        # A retained owned submission already passed validation. Recovery must
        # reach its saved deadline even when unrelated discovery is unavailable.
        if not (stop_on_timeout and journal.exists()):
            info = self.check()
            from .workflow_templates import check_nodes
            check_nodes({"graph": graph}, info)
        if journal.exists():
            state = read_json(journal)
            if state.get('graph_sha256') != signature: raise ValueError('已有提交的工作流发生变化')
            if state.get('state') == 'failed': raise ProductionNeedsDecision('上次 ComfyUI 任务已失败', 'comfy_task_failed')
            if state.get('state') == 'cancelled': raise ProductionNeedsDecision('原驻留任务已停止，不会重新提交', 'comfy_budget_stopped')
            if state.get('state') == 'cancel_pending':
                raise ProductionNeedsDecision('原驻留任务取消尚待确认，不会重新提交', 'comfy_cancellation_unresolved')
            if not state.get('prompt_id'): raise ProductionNeedsDecision('上次 ComfyUI 提交结果不确定，请核对后处理', 'comfy_submission_unresolved')
        else:
            proof = self._owned_capability() if stop_on_timeout else None
            if check: check()
            state = {'state': 'dispatching', 'graph_sha256': signature, 'created': time.time()}
            if stop_on_timeout:
                state.update(deadline_at=state['created'] + timeout, cancellation_capability=proof, prompt_id=str(uuid.uuid4()))
                write_json(dest / 'owned_workflow.json', graph)
            write_json(journal, state)
            try:
                answer = self.request('/prompt', {'prompt': graph, 'client_id': uuid.uuid4().hex,
                    **({'prompt_id': state['prompt_id']} if stop_on_timeout else {})},
                    timeout=min(2, max(.05, timeout)) if stop_on_timeout else None)
            except ProviderError as exc:
                if exc.code.startswith('http_4'):
                    if stop_on_timeout:
                        state['owned_usage'] = {'execution_seconds': 0, 'scope': 'submission_rejected'}
                    state['state']='failed'; write_json(journal,state)
                    raise ProductionNeedsDecision('ComfyUI 拒绝了工作流，请检查配置', 'comfy_task_failed') from None
                if stop_on_timeout:
                    state['submission_response_unknown'] = True
                    answer = {'prompt_id': state['prompt_id']}
                else:
                    raise
            if answer.get('error') or answer.get('node_errors'):
                if stop_on_timeout:
                    state['submission_response_unknown'] = True
                else:
                    state['state']='failed'; write_json(journal,state)
                    raise ProductionNeedsDecision('ComfyUI 工作流校验未通过', 'comfy_task_failed')
            ident = answer.get('prompt_id')
            if stop_on_timeout and ident != state['prompt_id']:
                state['submission_response_unknown'] = True
                ident = state['prompt_id']  # Never acquire or cancel an unrelated ID.
            if not isinstance(ident, str) or not ident or len(ident) > 128: raise ValueError('ComfyUI 未返回任务编号')
            state.update(state='accepted', prompt_id=ident); write_json(journal, state)
        deadline = time.monotonic() + (max(0, state.get('deadline_at', state['created'] + timeout) - time.time()) if stop_on_timeout else timeout)
        while time.monotonic() < deadline:
            if check:
                try:
                    check()
                except ProductionBudgetExceeded:
                    if stop_on_timeout:
                        self._stop_owned(state, dest, 'budget_exhausted', cancellation_timeout)
                    raise
            try:
                history = self.request('/history/' + quote(state['prompt_id'], safe=''),
                                       timeout=min(2, max(.05, deadline-time.monotonic())) if stop_on_timeout else None).get(state['prompt_id'])
                if stop_on_timeout:
                    _, active = self._observe_owned(state)
                    write_json(journal, state)
            except (ProviderError, ValueError, TypeError):
                if not stop_on_timeout: raise
                time.sleep(min(.1, max(0, deadline-time.monotonic())))
                continue
            if history:
                if history.get('status', {}).get('status_str') == 'error':
                    if stop_on_timeout:
                        state['owned_usage'] = _execution_usage(history)
                    state['state'] = 'failed'; write_json(journal, state)
                    raise ProductionNeedsDecision('ComfyUI 任务失败，回执已保留', 'comfy_task_failed')
                if (history.get('status', {}).get('completed') or history.get('status', {}).get('status_str') == 'success') and (not stop_on_timeout or not active): break
            time.sleep(min(.25, max(0, deadline-time.monotonic())))
        else:
            if stop_on_timeout:
                self._stop_owned(state, dest, 'request_timeout', cancellation_timeout)
            raise ProductionNeedsDecision('ComfyUI 任务仍在执行，可恢复查询', 'comfy_task_pending')
        write_json(dest / "history.json", history)
        state["state"] = "completed"
        if stop_on_timeout:
            state['owned_usage'] = _execution_usage(history)
        write_json(journal, state)
        outputs = history.get('outputs', {})
        if output:
            files = outputs.get(output['node'], {}).get(output['field'], [])
            if len(files) <= output['index']: raise ValueError('指定输出文件不存在')
            item = files[output['index']]
        else:
            files = [item for node in outputs.values() for field in ('images', 'videos', 'gifs') for item in node.get(field, [])
                     if Path(item.get('filename', '')).suffix.lower() in (('.png', '.jpg', '.jpeg', '.webp') if kind == 'image' else ('.mp4', '.webm', '.mov'))]
            if len(files) != 1: raise ValueError('请为多输出工作流指定输出节点与索引')
            item = files[0]
        filename = item.get('filename', ''); sub = item.get('subfolder', '')
        if not filename or any(c in filename for c in '/\\') or Path(sub).is_absolute() or '..' in Path(sub).parts or item.get('type', 'output') != 'output':
            raise ValueError('ComfyUI 输出路径无效')
        target = dest / ('image' if kind == 'image' else 'video')
        target = target.with_suffix(Path(filename).suffix.lower())
        self.request('/view?' + urlencode({'filename': filename, 'subfolder': sub, 'type': 'output'}), destination=target)
        write_json(result, media_receipt(target, graph_sha256=signature, prompt_id=state['prompt_id'], origin='user_comfyui'))
        return target
