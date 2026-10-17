"""Exercise the actual take journal and resume path for the deployed typed API."""
import hashlib

from factory import gpu_client, scheduler
from factory.image_generation import image_request
from studio import core


def test_typed_image_uses_saved_task_and_preserves_reference_order(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_client, 'ROOT', tmp_path)
    image = tmp_path / 'ref.png'
    image.write_bytes(b'image fixture')
    posts = []
    payloads = {}
    blob = b'generated image fixture'
    def request(client, route, payload=None, **kwargs):
        if payload is not None:
            posts.append(payload)
            assert payload['kind'] == 'qwen_image21' and 'graph' not in payload
            assert payload['inputs'][0]['sha256'] == core.sha256(image)
            payloads[payload['request_id']] = {'id': payload['request_id'], 'status': 'completed', 'result': {
                'receipt': {'gpu_seconds': 3, 'provider': 'comfyui_qwen_image21', 'model': 'Qwen-Image-2.1'},
                'artifacts': [{'index': 0, 'name': 'image.png', 'bytes': len(blob), 'sha256': hashlib.sha256(blob).hexdigest()}]}}
            return payloads[payload['request_id']]
        return payloads.get(route.split('/')[-1])
    def download(client, ident, artifact, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        return target
    monkeypatch.setattr(gpu_client.GPUClient, 'request', request)
    monkeypatch.setattr(gpu_client.GPUClient, 'download', download)
    cfg = {'gpu_service': {'base_url': 'http://127.0.0.1:8791'}}
    budget = scheduler.Budget({})
    worker = gpu_client.RemoteComfyWorker(cfg['gpu_service'], budget, 'test')
    name = worker.copy_input(image)
    graph = image_request({'provider': 'comfyui_qwen_image21'}, 'A fictional adult.', 1, 512, 768, [name])
    dest = tmp_path / 'take'
    result = worker.execute(graph, dest, 'image')
    (dest / 'result.json').unlink()
    resumed = gpu_client.resume_remote_comfy(cfg, dest, budget)
    assert resumed == result and resumed.read_bytes() == blob
    assert len(posts) == 1 and budget.snapshot()['gpu_seconds'] == 3
