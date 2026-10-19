"""Owned cancellation and accounting over real isolated loopback HTTP only."""
import copy
import base64
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from factory import external_comfy as external, gpu_client, gpu_service
from factory.scheduler import Budget, ProductionBudgetExceeded, ProductionNeedsDecision, ProductionPaused
from studio.core import fingerprint, read_json, write_json
from test_factory_gpu_client import service


OWNED_ID = '11111111-1111-4111-8111-111111111111'


@pytest.fixture
def comfy(monkeypatch):
    monkeypatch.setattr(external.uuid, 'uuid4', lambda: external.uuid.UUID(OWNED_ID))
    state = {'posts': [], 'active': False, 'pending': False, 'history': None, 'mode': 'stop',
             'peer': 'pending', 'version': '0.30.0', 'legacy': False}
    def finish(event='execution_interrupted'):
        state['active'] = state['pending'] = False
        state['end'] = int(time.time()*1000)
        state['history'] = {'status': {'status_str': 'error' if event != 'execution_success' else 'success',
            'completed': event == 'execution_success', 'messages': [
                ['execution_start', {'timestamp': state['start']}], [event, {'timestamp': state['end']}]]}, 'outputs': {}}
    state['finish'] = finish
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def answer(self, data, status=200):
            body = json.dumps(data).encode()
            self.send_response(status); self.send_header('Content-Length', str(len(body))); self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/object_info': return self.answer(state.get('nodes', {'Fixture': {}}))
            if path == '/system_stats': return self.answer({'system': {'comfyui_version': state['version']}})
            if path == '/api/jobs':
                return self.answer({'legacy': True}, 404) if state['legacy'] else self.answer({'jobs': [], 'pagination': {'total': 0}})
            if path.startswith('/history/'):
                return self.answer({OWNED_ID: state['history']} if state['history'] else {})
            if path == '/queue':
                running = [[0, OWNED_ID]] if state['active'] else []
                pending = [[1, OWNED_ID]] if state['pending'] else []
                (running if state['peer'] == 'running' else pending).append([2, 'peer-2'])
                return self.answer({'queue_running': running, 'queue_pending': pending})
            self.answer({}, 404)
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
            state['posts'].append((self.path, data))
            if self.path == '/prompt':
                assert data.get('prompt_id') == OWNED_ID
                if state['mode'] == 'reject': return self.answer({'error': 'Invalid fixture graph'}, 400)
                state['start'] = int(time.time()*1000)
                state['pending'] = state['mode'] == 'pending'
                state['active'] = not state['pending']
                if state['mode'] == 'drop_prompt':
                    self.close_connection = True
                    return
                return self.answer({'prompt_id': OWNED_ID})
            if self.path == f'/api/jobs/{OWNED_ID}/cancel':
                assert data == {'prompt_id': OWNED_ID}
                if state['mode'] == 'race':
                    finish('execution_success'); state['peer'] = 'running'
                    return self.answer({'cancelled': False})
                if state['mode'] == 'pending':
                    state['pending'] = False
                elif state['mode'] != 'hold':
                    finish()
                if state['mode'] == 'drop':
                    self.close_connection = True
                    return
                return self.answer({'cancelled': True})
            # A legacy server could ignore prompt_id here and stop its peer.
            if self.path == '/interrupt':
                state['peer'] = 'interrupted'
                return self.answer({})
            self.answer({}, 404)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    profile = {'base_url': f'http://127.0.0.1:{server.server_port}', 'network_mode': 'loopback',
               'owned_cancel_protocol': external.OWNED_CANCEL_PROTOCOL, 'verified_comfyui_version': '0.30.0'}
    yield profile, state
    server.shutdown(); server.server_close(); thread.join(timeout=2)


GRAPH = {'1': {'class_type': 'Fixture', 'inputs': {}}}


@pytest.mark.parametrize('mode', ['stop', 'race', 'drop', 'drop_prompt'])
def test_timeout_stops_only_owned_prompt_and_never_resubmits(comfy, tmp_path, mode):
    profile, state = comfy; state['mode'] = mode
    client = external.ExternalComfy(profile)
    with pytest.raises(ProductionNeedsDecision) as stopped:
        client.execute(GRAPH, tmp_path, 'video', timeout=.03, stop_on_timeout=True, cancellation_timeout=.1)
    assert stopped.value.reason == 'comfy_budget_stopped'
    saved = read_json(tmp_path / 'submission.json')
    assert saved['state'] == 'cancelled' and saved['prompt_id'] == OWNED_ID
    assert state['peer'] != 'interrupted'
    assert [route for route, _ in state['posts']] == ['/prompt', f'/api/jobs/{OWNED_ID}/cancel']
    usage = external.owned_usage(saved)
    assert usage['usage_final'] is True
    assert usage['gpu_seconds'] == pytest.approx((state['end'] - state['start']) / 1000 * 2)
    assert saved['deadline_at'] == pytest.approx(saved['created'] + .03)
    with pytest.raises(ProductionNeedsDecision):
        client.execute(GRAPH, tmp_path, 'video', timeout=100, stop_on_timeout=True)
    assert len(state['posts']) == 2


def test_unknown_stop_keeps_original_intent_and_reconciles_actual_usage_without_post(comfy, tmp_path):
    profile, state = comfy; state['mode'] = 'hold'
    client = external.ExternalComfy(profile)
    with pytest.raises(ProductionNeedsDecision) as unknown:
        client.execute(GRAPH, tmp_path, 'video', timeout=.03, stop_on_timeout=True, cancellation_timeout=.03)
    assert unknown.value.reason == 'comfy_cancellation_unresolved'
    saved = read_json(tmp_path / 'submission.json')
    assert saved['state'] == 'cancel_pending' and external.owned_usage(saved)['usage_final'] is False
    before = copy.deepcopy(state['posts'])
    with pytest.raises(ProductionNeedsDecision):
        client.execute(GRAPH, tmp_path, 'video', timeout=100, stop_on_timeout=True)
    state['finish']()
    settled = client.reconcile_owned_stop(tmp_path)
    assert settled['state'] == 'cancelled' and external.owned_usage(settled)['usage_final'] is True
    assert state['posts'] == before


def test_pending_to_running_cancel_with_delayed_history_never_settles_zero(comfy, tmp_path):
    profile, state = comfy; state['mode'] = 'pending'; state['peer'] = 'running'
    client = external.ExternalComfy(profile)
    with pytest.raises(ProductionNeedsDecision) as unknown:
        client.execute(GRAPH, tmp_path, 'video', timeout=.03, stop_on_timeout=True, cancellation_timeout=.03)
    assert unknown.value.reason == 'comfy_cancellation_unresolved'
    saved = read_json(tmp_path / 'submission.json')
    assert saved['queue_at_cancel'] == 'pending' and saved['cancel_dispatched'] is True
    assert saved['stop_observed_at'] and external.owned_usage(saved)['usage_final'] is False
    # The real atomic endpoint may have interrupted a just-started job. A delayed
    # execution receipt, rather than the old pending snapshot, settles its cost.
    state['finish']()
    settled = client.reconcile_owned_stop(tmp_path)
    assert settled['state'] == 'cancelled'
    assert external.owned_usage(settled)['gpu_seconds'] == pytest.approx((state['end'] - state['start']) / 1000 * 2)
    assert external.owned_usage(settled)['usage_final'] is True
    assert state['peer'] == 'running'


@pytest.mark.parametrize('unsupported', ['undeclared', 'old_version', 'old_protocol'])
def test_unknown_or_legacy_global_interrupt_service_is_rejected_before_submission(comfy, tmp_path, unsupported):
    profile, state = comfy
    if unsupported == 'undeclared': profile.pop('owned_cancel_protocol')
    if unsupported == 'old_version': state['version'] = '0.2.0'
    if unsupported == 'old_protocol': state['legacy'] = True
    with pytest.raises(Exception):
        external.ExternalComfy(profile).execute(GRAPH, tmp_path, 'video', timeout=.03, stop_on_timeout=True)
    assert state['posts'] == [] and state['peer'] == 'pending'


def test_budget_check_triggers_scoped_stop_without_waiting_for_request_timeout(comfy, tmp_path):
    profile, state = comfy
    checks = []
    def check():
        checks.append(True)
        if len(checks) > 1: raise ProductionBudgetExceeded()
    with pytest.raises(ProductionNeedsDecision) as stopped:
        external.ExternalComfy(profile).execute(GRAPH, tmp_path, 'video', timeout=100,
            check=check, stop_on_timeout=True, cancellation_timeout=.03)
    assert stopped.value.reason == 'comfy_budget_stopped'
    assert read_json(tmp_path / 'submission.json')['stop_reason'] == 'budget_exhausted'
    assert state['peer'] == 'pending'


def test_definitive_submission_rejection_has_a_zero_execution_receipt(comfy, tmp_path):
    profile, state = comfy; state['mode'] = 'reject'
    with pytest.raises(ProductionNeedsDecision) as rejected:
        external.ExternalComfy(profile).execute(GRAPH, tmp_path, 'video', timeout=.03, stop_on_timeout=True)
    assert rejected.value.reason == 'comfy_task_failed'
    usage = external.owned_usage(read_json(tmp_path / 'submission.json'))
    assert usage['usage_final'] is True and usage['gpu_seconds'] == 0
    assert [route for route, _ in state['posts']] == ['/prompt']


@pytest.mark.parametrize('guard', ['budget', 'paused'])
@pytest.mark.parametrize('status', ['completed', 'failed', 'unresolved'])
def test_retained_client_can_settle_receipt_when_spending_is_blocked(service, tmp_path, guard, status):
    _, config, state = service
    client = gpu_client.GPUClient(config['gpu_service'])
    workload = {'kind': 'comfy', 'graph': GRAPH, 'inputs': [], 'output_kind': 'image'}
    dest = tmp_path / 'retained'
    initial = Budget(config)
    client.run(workload, dest, initial)
    assert len(state['posts']) == 1
    ident = next(iter(state['jobs']))
    state['jobs'][ident][0]['status'] = status
    state['jobs'][ident][0]['result']['receipt']['gpu_seconds'] = 11
    budget = Budget(config, {'gpu_seconds': 101}) if guard == 'budget' else Budget(config)
    control = (lambda: {'pause': True}) if guard == 'paused' else None
    before = budget.snapshot()['gpu_seconds']
    for _ in range(2):
        if status == 'completed':
            assert client.run(workload, dest, budget, control=control)['status'] == status
        else:
            with pytest.raises((ProductionNeedsDecision, RuntimeError)):
                client.run(workload, dest, budget, control=control)
    assert budget.snapshot()['gpu_seconds'] == before + 11
    assert len(state['posts']) == 1


def test_blocked_client_never_posts_if_retained_job_is_missing(service, tmp_path):
    _, config, state = service
    client = gpu_client.GPUClient(config['gpu_service'])
    workload = {'kind': 'comfy', 'graph': GRAPH, 'inputs': [], 'output_kind': 'image'}
    dest = tmp_path / 'retained'
    client.run(workload, dest, Budget(config))
    state['jobs'].clear()
    with pytest.raises(ProductionNeedsDecision) as unknown:
        client.run(workload, dest, Budget(config), control=lambda: True)
    assert unknown.value.reason == 'remote_gpu_unresolved' and len(state['posts']) == 1


def test_unknown_cancellation_is_not_reposted_when_remote_record_disappears(service, tmp_path):
    _, config, state = service
    client = gpu_client.GPUClient(config['gpu_service'])
    workload = {'kind': 'comfy', 'graph': GRAPH, 'inputs': [], 'output_kind': 'image'}
    dest = tmp_path / 'retained'
    client.run(workload, dest, Budget(config))
    ident = next(iter(state['jobs']))
    state['jobs'][ident][0]['status'] = 'unresolved'
    with pytest.raises(ProductionNeedsDecision): client.run(workload, dest, Budget(config))
    state['jobs'].clear()
    with pytest.raises(ProductionNeedsDecision) as unknown:
        client.run(workload, dest, Budget(config))
    assert unknown.value.reason == 'remote_gpu_unresolved' and len(state['posts']) == 1


@pytest.mark.parametrize('paused', [False, True])
def test_new_request_remains_forbidden_without_spending_authorization(service, tmp_path, paused):
    _, config, state = service
    budget = Budget(config) if paused else Budget(config, {'gpu_seconds': 101})
    workload = {'kind': 'comfy', 'graph': GRAPH, 'inputs': [], 'output_kind': 'image'}
    with pytest.raises((ProductionPaused, ProductionBudgetExceeded)):
        gpu_client.GPUClient(config['gpu_service']).run(workload, tmp_path / 'new', budget,
            control=(lambda: True) if paused else None)
    assert state['posts'] == [] and not (tmp_path / 'new/submission.json').exists()


def test_inference_store_keeps_unknown_stop_fenced_and_settles_real_bill_on_get(comfy, tmp_path, monkeypatch):
    from studio import core, h3
    from studio.graphs import h3_video
    profile, state = comfy; state['mode'] = 'hold'
    monkeypatch.setattr(gpu_service, 'ROOT', tmp_path)
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    def cold_start_forbidden(*args):
        raise AssertionError('A retained resident must not require cold-start node files')
    monkeypatch.setattr(h3, 'require_installation', cold_start_forbidden)
    original_init, original_execute = external.ExternalComfy.__init__, external.ExternalComfy.execute
    def isolated_init(self, selected):
        original_init(self, {**selected, 'base_url': profile['base_url']})
    def bounded_execute(self, *args, **kwargs):
        return original_execute(self, *args, **kwargs, cancellation_timeout=.02)
    monkeypatch.setattr(external.ExternalComfy, '__init__', isolated_init)
    monkeypatch.setattr(external.ExternalComfy, 'execute', bounded_execute)
    monkeypatch.setattr(external.ExternalComfy, 'upload', lambda self, path: 'uploaded.png')
    resident = {'base_url': 'http://127.0.0.1:8346', 'gpu_cards': [6, 7],
                'owned_cancel_protocol': external.OWNED_CANCEL_PROTOCOL, 'verified_comfyui_version': '0.30.0'}
    engine = gpu_service.InferenceEngine({'gpu_cards': [5], 'resident_h3': resident})
    raw = b'synthetic reference bytes'
    digest = hashlib.sha256(raw).hexdigest(); name = digest + '.png'
    graph = h3_video('Approved silent shot.', 42, name, seconds=1)
    state['nodes'] = {node['class_type']: {} for node in graph.values()}
    payload = {'request_id': 'a'*64, 'kind': 'comfy', 'graph': graph, 'output_kind': 'video',
               'inputs': [{'name': name, 'sha256': digest, 'data': base64.b64encode(raw).decode()}],
               'timeout_seconds': 1, 'max_gpu_seconds': 1}
    store = gpu_service.InferenceStore(tmp_path / 'inference', engine, start_worker=False)
    try:
        store.submit(payload); store.execute_one(payload['request_id'])
        pending = store.get(payload['request_id'])
        assert pending['status'] == 'unresolved' and pending['reason'] == 'comfy_cancellation_unresolved'
        assert pending['result']['receipt']['usage_final'] is False
        assert pending['result']['receipt']['gpu_seconds'] == 0
        assert pending['result']['receipt']['provisional_gpu_seconds'] > 0
        with pytest.raises(gpu_service.Conflict, match='unresolved'):
            store.submit({**payload, 'request_id': 'b'*64})
        assert store.submit(payload)['status'] == 'unresolved'
        assert len([route for route, _ in state['posts'] if route == '/prompt']) == 1
        state['finish']()
        settled = store.get(payload['request_id'])
        assert settled['status'] == 'failed' and settled['reason'] == 'comfy_budget_stopped'
        expected_gpu = round((state['end'] - state['start']) / 1000 * 2, 3)
        assert settled['result']['receipt']['gpu_seconds'] == expected_gpu
        assert settled['result']['receipt']['usage_final'] is True
        assert store.get(payload['request_id'])['result']['receipt']['gpu_seconds'] == expected_gpu
        store.execute_one(payload['request_id'])
        assert store.submit({**payload, 'request_id': 'b'*64})['status'] == 'queued'
        assert len([route for route, _ in state['posts'] if route == '/prompt']) == 1
        assert (tmp_path / 'inference' / ('a'*64) / 'inputs' / name).read_bytes() == raw
    finally:
        store.close()


def test_retained_completed_video_waits_for_actual_usage_then_becomes_downloadable(comfy, tmp_path, monkeypatch):
    profile, state = comfy
    original_init = external.ExternalComfy.__init__
    monkeypatch.setattr(external.ExternalComfy, '__init__',
        lambda self, selected: original_init(self, {**selected, 'base_url': profile['base_url']}))
    engine = gpu_service.InferenceEngine({'resident_h3': {'base_url': 'http://127.0.0.1:8346', 'gpu_cards': [6, 7]}})
    folder = tmp_path / 'inference' / ('a'*64)
    media = folder / 'artifacts/000-video.mp4'; media.parent.mkdir(parents=True)
    media.write_bytes(b'existing fixture video bytes')
    artifact = {'index': 0, 'name': media.name, 'bytes': media.stat().st_size,
                'sha256': hashlib.sha256(media.read_bytes()).hexdigest()}
    write_json(folder / 'output/submission.json', {'state': 'completed', 'prompt_id': OWNED_ID, 'owned_usage': None})
    write_json(folder / 'job.json', {'id': 'a'*64, 'status': 'unresolved', 'reason': 'comfy_usage_unresolved',
        'result': {'receipt': {'gpu_seconds': 0, 'usage_final': False}, 'artifacts': [artifact]}})
    store = gpu_service.InferenceStore(tmp_path / 'inference', engine, start_worker=False)
    try:
        assert store.get('a'*64)['status'] == 'unresolved'
        state['start'] = int(time.time()*1000) - 100
        state['finish']('execution_success')
        settled = store.get('a'*64)
        assert settled['status'] == 'completed' and settled['result']['receipt']['usage_final'] is True
        assert settled['result']['receipt']['gpu_seconds'] == pytest.approx((state['end'] - state['start']) / 1000 * 2)
        assert store.artifact('a'*64, 0).read_bytes() == b'existing fixture video bytes'
        assert state['posts'] == []
    finally:
        store.close()


@pytest.mark.parametrize('has_journal', [True, False])
def test_service_restart_restores_owned_deadline_without_replaying_submission(comfy, tmp_path, monkeypatch, has_journal):
    from studio.graphs import h3_video
    profile, remote = comfy
    original_init = external.ExternalComfy.__init__
    monkeypatch.setattr(external.ExternalComfy, '__init__',
        lambda self, selected: original_init(self, {**selected, 'base_url': profile['base_url']}))
    engine = gpu_service.InferenceEngine({'resident_h3': {
        'base_url': 'http://127.0.0.1:8346', 'gpu_cards': [6, 7],
        'owned_cancel_protocol': external.OWNED_CANCEL_PROTOCOL, 'verified_comfyui_version': '0.30.0'}})
    raw = b'retained synthetic first frame'
    digest = hashlib.sha256(raw).hexdigest(); name = digest + '.png'
    graph = h3_video('An approved silent shot.', 42, name, seconds=1)
    payload = {'request_id': 'a'*64, 'kind': 'comfy', 'graph': graph,
               'inputs': [{'name': name, 'sha256': digest, 'data': base64.b64encode(raw).decode()}], 'output_kind': 'video',
               'timeout_seconds': 100, 'max_gpu_seconds': 200}
    folder = tmp_path / 'inference' / payload['request_id']
    write_json(folder / 'job.json', {'id': payload['request_id'], 'status': 'running', 'request': payload,
        'request_hash': fingerprint({k: v for k, v in payload.items() if k != 'request_id'}), 'started_at': time.time() - 1})
    if has_journal:
        deadline = time.time() + .05
        write_json(folder / 'output/owned_workflow.json', graph)
        write_json(folder / 'output/submission.json', {'state': 'accepted', 'prompt_id': OWNED_ID,
            'created': time.time() - 1, 'deadline_at': deadline,
            'graph_sha256': fingerprint({'graph': graph, 'output': None, 'endpoint': profile['base_url']}),
            'cancellation_capability': {'protocol': external.OWNED_CANCEL_PROTOCOL, 'comfyui_version': '0.30.0'}})
        remote.update(active=True, start=int(time.time()*1000) - 1000)
    # A second restart before the worker consumes recovery must retain ownership.
    first = gpu_service.InferenceStore(tmp_path / 'inference', engine, start_worker=False)
    first.close()
    store = gpu_service.InferenceStore(tmp_path / 'inference', engine, start_worker=False)
    try:
        assert store.get('a'*64)['status'] == 'unresolved'
        with pytest.raises(gpu_service.Conflict, match='unresolved'):
            store.submit({**payload, 'request_id': 'b'*64})
        assert remote['posts'] == []  # GET and the budget barrier cannot cancel.
        if has_journal:
            store.close()
            # The normal service consumer owns the timer; no operator or new
            # monitor is required after a process restart.
            store = gpu_service.InferenceStore(tmp_path / 'inference', engine)
            until = time.monotonic() + 2
            while time.monotonic() < until and read_json(folder / 'job.json')['status'] == 'unresolved':
                time.sleep(.01)
        else:
            store.execute_one('a'*64)
        result = store.get('a'*64)
        if has_journal:
            assert result['status'] == 'failed' and result['reason'] == 'comfy_budget_stopped'
            assert result['result']['receipt']['usage_final'] is True
            assert result['result']['receipt']['gpu_seconds'] >= 2
            assert read_json(folder / 'output/submission.json')['deadline_at'] == deadline
            assert [route for route, _ in remote['posts']] == [f'/api/jobs/{OWNED_ID}/cancel']
            assert remote['peer'] == 'pending'
        else:
            assert result['status'] == 'unresolved' and result['reason'] == 'comfy_submission_unresolved'
            assert remote['posts'] == []  # Unproven legacy ownership fails closed.
    finally:
        store.close()


@pytest.mark.parametrize('defect', [None, 'node', 'required_input', 'model_enum', 'missing_enum', 'protocol', 'url', 'cards'])
def test_resident_health_checks_actual_runtime_contract_separately_from_cold_files(comfy, monkeypatch, defect):
    from studio import h3
    from studio.graphs import h3_video
    profile, remote = comfy
    original_init = external.ExternalComfy.__init__
    monkeypatch.setattr(external.ExternalComfy, '__init__',
        lambda self, selected: original_init(self, {**selected, 'base_url': profile['base_url']}))
    nodes = {}
    for quality in ('preview', 'production'):
        graph = h3_video('Runtime inspection.', 42, 'existing-reference.png', profile={'quality': quality})
        for node in graph.values():
            fields = nodes.setdefault(node['class_type'], {'input': {'required': {}}})['input']['required']
            for name, value in node['inputs'].items():
                fields[name] = (['ANY'] if isinstance(value, list) else [[value]] if name in
                    {'unet_name', 'clip_name', 'vae_name', 'lora_name', 'image', 'placement', 'weight_dtype',
                     'sampler_name', 'scheduler', 'format', 'codec'} else ['STRING'] if isinstance(value, str) else ['FLOAT'])
    remote['nodes'] = nodes
    resident = {'base_url': 'http://127.0.0.1:8346', 'gpu_cards': [6, 7],
                'owned_cancel_protocol': external.OWNED_CANCEL_PROTOCOL, 'verified_comfyui_version': '0.30.0'}
    if defect == 'node': nodes.pop('MiniMaxH3ImageToVideo')
    if defect == 'required_input': nodes['UNETLoader']['input']['required']['unknown_required'] = ['STRING']
    if defect == 'model_enum': nodes['UNETLoader']['input']['required']['unet_name'] = [['unrelated-model.safetensors']]
    if defect == 'missing_enum': nodes['UNETLoader']['input']['required'].pop('unet_name')
    if defect == 'protocol': resident.pop('owned_cancel_protocol')
    if defect == 'url': resident['base_url'] = 'http://127.0.0.1:9999'
    if defect == 'cards': resident['gpu_cards'] = [0, 1]
    cold = h3.installation()
    cold.update(installed=False, missing=['int8_memory_adapter'])
    for row in cold['recipes'].values(): row.update(installed=False, missing=['int8_memory_adapter'])
    monkeypatch.setattr(h3, 'installation', lambda: copy.deepcopy(cold))
    observed = gpu_service.InferenceEngine({'resident_h3': resident}).health()['video_backends']['comfyui_h3']
    assert observed['cold_start_installation'] == cold
    assert observed['validation'] == 'runtime_node_contract_only_not_inference'
    assert observed['installed'] is (defect is None)
    assert all(row['installed'] is (defect is None) for row in observed['recipes'].values())
    if defect is None:
        assert observed['object_info_sha256'] == fingerprint(nodes)
        assert observed['owned_cancel_capability']['protocol'] == external.OWNED_CANCEL_PROTOCOL
        assert h3.backend_ready(observed, [6, 7])
        assert h3.backend_ready(observed, [6, 7], {'quality': 'production'})
    else:
        assert observed['missing'] == ['resident_node_contract_unavailable']
    assert remote['posts'] == []  # Only /object_info, /system_stats and /api/jobs are read.


def test_without_resident_health_preserves_cold_installation(monkeypatch):
    from studio import h3
    cold = h3.installation()
    monkeypatch.setattr(h3, 'installation', lambda: copy.deepcopy(cold))
    assert gpu_service.InferenceEngine({}).health()['video_backends']['comfyui_h3'] == cold
