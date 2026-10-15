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
from .video_transport import _transfer
from .scheduler import ProductionNeedsDecision


class ExternalComfy:
    def __init__(self, profile):
        self.profile = profile
        self.base = validate_endpoint(profile)

    def request(self, route, payload=None, headers=None, destination=None, timeout=None):
        auth = {}
        env = self.profile.get('api_key_env')
        if env:
            secret = os.environ.get(env)
            if not secret or any(c in secret for c in '\r\n'): raise ProviderError('missing_auth', 'ComfyUI 密钥环境变量缺失或无效')
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

    def execute(self, graph, dest, kind, output=None, check=None, timeout=1800):
        import fcntl
        dest = Path(dest); dest.mkdir(parents=True, exist_ok=True)
        with (dest / 'submission.lock').open('a+') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            return self._execute(graph, dest, kind, output, check, timeout)

    def _execute(self, graph, dest, kind, output, check, timeout):
        journal = dest / 'submission.json'; result = dest / 'result.json'
        signature = fingerprint({'graph': graph, 'output': output, 'endpoint': self.base})
        if result.exists():
            saved = read_json(result); path = Path(saved['path'])
            from studio.core import ROOT, sha256
            path = ROOT / path
            if saved.get('graph_sha256') != signature or not path.is_file() or sha256(path) != saved['sha256']:
                raise ProductionNeedsDecision('ComfyUI 缓存与当前工作流不匹配', 'cache_integrity')
            return path
        info = self.check()
        from .workflow_templates import check_nodes
        check_nodes({"graph": graph}, info)
        if journal.exists():
            state = read_json(journal)
            if state.get('graph_sha256') != signature: raise ValueError('已有提交的工作流发生变化')
            if not state.get('prompt_id'): raise ProductionNeedsDecision('上次 ComfyUI 提交结果不确定，请核对后处理', 'comfy_submission_unresolved')
            if state.get('state') == 'failed': raise ProductionNeedsDecision('上次 ComfyUI 任务已失败', 'comfy_task_failed')
        else:
            if check: check()
            state = {'state': 'dispatching', 'graph_sha256': signature, 'created': time.time()}
            write_json(journal, state)
            answer = self.request('/prompt', {'prompt': graph, 'client_id': uuid.uuid4().hex})
            ident = answer.get('prompt_id')
            if not isinstance(ident, str) or not ident or len(ident) > 128: raise ValueError('ComfyUI 未返回任务编号')
            state.update(state='accepted', prompt_id=ident); write_json(journal, state)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if check: check()
            history = self.request('/history/' + quote(state['prompt_id'], safe='')).get(state['prompt_id'])
            if history:
                if history.get('status', {}).get('status_str') == 'error':
                    state['state'] = 'failed'; write_json(journal, state)
                    raise ProductionNeedsDecision('ComfyUI 任务失败，回执已保留', 'comfy_task_failed')
                if history.get('status', {}).get('completed') or history.get('status', {}).get('status_str') == 'success': break
            time.sleep(.25)
        else: raise ProductionNeedsDecision('ComfyUI 任务仍在执行，可恢复查询', 'comfy_task_pending')
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
