"""Actual create/worker/graph/renderer/FFmpeg path; external inference is synthetic.

No business stage is replaced, no final state is seeded, and no synthetic video
or model verdict below is evidence that a real drama meets artistic criteria.
"""
import contextlib
import copy
import json
import subprocess
import sys
import zipfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from jsonschema import validate

from factory import store, workflows, config, llm, providers, worker, dramas
from factory import production, scheduler, schemas, drama_delivery
from factory.video_providers import H3_PRODUCTION_PROFILE
from studio import core
from tests import test_factory_production as cpu_media
from tests.test_factory_production import FixtureWorker, FixtureSpeech, fake_audio_lease
from tests.test_factory_creative import fixture
from tests.test_factory_dramas import plan


def model_value(schema, payload):
    value = fixture(schema)
    props = schema.get('properties', {})
    if 'passed' in props:
        value['passed'] = True
    if 'score' in props:
        value['score'] = 92
    if 'status' in props and 'pass' in props['status'].get('enum', []):
        value['status'] = 'pass'
    if 'world' in props and 'characters' in props:
        value = {k: v for k, v in plan().items() if k != 'episodes'}
        value['relationships'] = []
        value['visual_rules'] = 'Consistent 2D animated adults with clear contours, blue lighting and coherent geometry.'
    elif 'number' in props and 'synopsis' in props:
        number = schema['properties']['number']['const']
        value = plan()['episodes'][number-1]
        value['beats'] = [{'id': f'E{number}B{i}', 'description': f'角色明确完成第{number}集必要事件{i}'} for i in (1, 2)]
    elif 'cast' in props and 'shots' in props:
        roles = list(value['cast'])
        beats = payload['_agent_constraints']['outline']['beats']
        for index, shot in enumerate(value['shots']):
            shot.update(prompt='An adult stands beside a blue wall with a white cup on the table.',
                        action='The adult gently turns toward the camera.',
                        motion_prompt='A visible gentle head turn toward the camera.',
                        reference_group=roles[0], characters=[roles[0]],
                        dialogue=[{'role': roles[0], 'text': '找到线索', 'start': .2, 'emotion': '平静'}],
                        beat_ids=[beats[index]['id']] if index < len(beats) else [])
    elif 'shots' in props and isinstance(value['shots'], dict):
        for sid, shot in value['shots'].items():
            original = next(s for s in payload['current_episode']['shots'] if s['id'] == sid)
            for field in shot:
                if field in original:
                    shot[field] = original[field]
        value['blocked_shots'] = {}
    elif 'approved_action' in props:
        shot = payload['project']['approved_shot']
        value.update(status='ready', reason='', approved_action=shot['action'], camera_motion=shot.get('camera_motion', 'hold'))
        value['image']['description'] = shot['prompt']
        value['video'].update(initial_state='An adult faces the wall beside a white cup.', action=shot['action'],
                              end_state='The adult faces the camera beside the same cup.', camera='Locked camera.', requires_body_motion=True)
    elif 'alignment' in props and 'observations' in props:
        value['alignment'] = 'match'
        value['observations'] = [{'subject': 'lead', 'visible_state': 'Synthetic adult beside a cup.', 'image_region': 'center', 'certainty': 'visible'}]
    elif 'timeline' in props and 'visual_description' in props:
        value.update(status='ready', blocked_reason='', visual_description='An adult beside a blue wall and a white cup.',
                     initial_state='The adult faces the wall.', end_state='The adult faces camera.', camera='Locked camera.',
                     timeline=[{'start_seconds': 0, 'end_seconds': payload['seconds'], 'action': 'The adult turns gently toward camera.'}])
    elif 'checks' in props and isinstance(value['checks'], list):
        value.update(status='pass', checks=[{'beat_id': payload['beats'][0]['id'], 'status': 'pass',
                    'shot_ids': [payload['evidence']['samples'][0]['shot_id']],
                    'evidence': 'Synthetic inference reads the actual CPU fixture frame; not model quality.'}],
                     observed_end='Synthetic CPU fixture ends with the same test pattern and no real actors.')
    elif 'action' in props and 'choice' in props:
        value.update(action='select', choice=payload['candidates'][0]['id']) if payload['candidates'] else value.update(action='escalate', choice='')
    if 'scores' in props:
        value['scores'] = {k: 8 for k in props['scores']['properties']}
    validate(value, schema)
    return value


def test_three_episode_worker_delivery_uses_real_business_stages(monkeypatch, tmp_path):
    case = cpu_media.FactorySafetyTests(methodName='runTest')
    case.setUp()
    try:
        root = core.ROOT
        # Only isolate storage and replace external inference/hardware adapters.
        for name, module in list(sys.modules.items()):
            if name.startswith(('factory.', 'studio.')) and hasattr(module, 'ROOT'):
                monkeypatch.setattr(module, 'ROOT', root)
        monkeypatch.setattr(store, 'DATA', root / 'runtime')
        monkeypatch.setattr(store, 'DATABASE', root / 'runtime/studio.sqlite3')
        monkeypatch.setattr(workflows, 'DATA', root / 'runtime')
        monkeypatch.setattr(workflows, 'CHECKPOINTS', root / 'runtime/checkpoints.sqlite3')
        model = {'base_url': 'http://127.0.0.1:8090/v1', 'model': 'synthetic-qwen', 'vision': True,
                 'context_tokens': 262144, 'context_validation': 'conservative_estimate', 'response_format': 'llama_json_schema'}
        cfg = {'schema_version': 1, 'llm': model, 'creative': {'separate_storyboard': True},
               'video_profiles': {'local_h3': copy.deepcopy(H3_PRODUCTION_PROFILE)},
               'video_roles': {'video_generator': 'local_h3'},
               'production': {**case.config, 'video_width': 160, 'video_height': 288,
                              'h3_prompting': 'automatic', 'review_only_existing_videos': False},
               'quality': {'semantic_enabled': True}}
        fixture_config = root / 'config/factory.json'
        core.write_json(fixture_config, cfg)
        monkeypatch.setattr(config, 'config_read_path', lambda: fixture_config)
        discovery = []
        def discover(*args, **kwargs):
            discovery.append('GET')
            if len(discovery) == 1:
                raise ConnectionError('synthetic temporary service unavailable')
            return {'data': [{'id': 'synthetic-qwen'}]}
        monkeypatch.setattr(providers, 'check_model', discover)
        monkeypatch.setattr(llm, 'check_model', lambda *a, **k: {'data': [{'id': 'synthetic-qwen'}]})
        monkeypatch.setattr(llm, 'context_budget', lambda *a: {'fits': True, 'prompt_tokens': 10})
        calls = []
        drift_injected = []
        review_returned = []
        series_returned = []

        def transport(endpoint, route, request, **kw):
            schema = request['response_format'].get('schema') or request['response_format']['json_schema']['schema']
            message = request['messages'][-1]['content']
            if isinstance(message, list):
                texts = [part['text'] for part in message if part.get('type') == 'text']
                payload = next((json.loads(t) for t in texts if t.startswith('{')), {})
            else:
                payload = json.loads(message)
            if '_agent' in payload:
                actor = payload['_agent']
                turn = len(actor['history']) // 2
                artifact_schema = next(t['function']['parameters']['properties']['artifact'] for t in actor['tools'] if t['function']['name'] == 'draft_artifact')
                if turn == 0:
                    value = {'tool': 'plan', 'arguments': {'steps': ['Complete the frozen role contract using synthetic inference.']}}
                elif turn == 1:
                    artifact = model_value(artifact_schema, payload)
                    if 'repairs' in artifact_schema.get('properties', {}) and payload.get('episodes') and not series_returned:
                        artifact.update(status='fail', issues=['末集结局缺失'], repairs=[{
                            'episode_number': 3, 'shot_ids': ['S01'], 'reason': '末集必要结局未兑现，请定向修复实际镜头。'}])
                        series_returned.append(copy.deepcopy(artifact))
                    if actor['stage'].startswith('generation-image-') and not drift_injected:
                        artifact['approved_action'] = 'Synthetic compiler tried to change approved facts.'
                        drift_injected.append(actor['stage'])
                    value = {'tool': 'draft_artifact', 'arguments': {'artifact': artifact}}
                elif turn == 2:
                    value = {'tool': 'check_artifact', 'arguments': {}}
                else:
                    value = {'tool': 'submit_artifact', 'arguments': {'sha256': actor['current_candidate_sha256']}}
                calls.append(actor['stage'])

            else:
                value = model_value(schema, payload)
                if ('observed_end' in schema.get('properties', {}) and payload.get('beats', [{}])[0].get('id') == 'E1B1' and not review_returned):
                    value.update(status='fail', issues=['实际持物与必要事件不符'])
                    value['checks'][0]['status'] = 'fail'
                    review_returned.append(copy.deepcopy(value))
                if 'repairs' in schema.get('properties', {}) and payload.get('episodes') and not series_returned:
                    value.update(status='fail', issues=['末集结局缺失'], repairs=[{
                        'episode_number': 3, 'shot_ids': ['S01'], 'reason': '末集必要结局未兑现，请定向修复实际镜头。'}])
                    series_returned.append(copy.deepcopy(value))
                calls.append('direct_external_inference')
            validate(value, schema)
            return {'model': 'synthetic-qwen', 'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value, ensure_ascii=False)}}],
                    'usage': {'completion_tokens': 50}}

        def checked_transport(*args, **kwargs):
            try:
                return transport(*args, **kwargs)
            except Exception as exc:
                print('SYNTHETIC_TRANSPORT_ERROR', type(exc).__name__, str(exc))
                raise
        monkeypatch.setattr(llm, 'request_json', checked_transport)
        original_execute = FixtureWorker.execute

        class MediaWorker(FixtureWorker):
            def execute(self, graph, destination, kind, **kw):
                if kind == 'image':
                    return original_execute(self, graph, destination, kind, **kw)
                from factory.audio_editor import FFMPEG
                destination.mkdir(parents=True, exist_ok=True)
                path = destination / 'video.mp4'
                subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                    '-f', 'lavfi', '-i', 'testsrc=size=160x288:rate=24', '-frames:v', str(graph['6']['inputs']['length']),
                    '-an', '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', str(path)], check=True, capture_output=True)
                core.write_json(destination / 'result.json', core.media_receipt(path, origin='synthetic_cpu_not_h3_inference'))
                return path

        @contextlib.contextmanager
        def media_worker(self, tag, control=None, **kw):
            yield MediaWorker()

        monkeypatch.setattr(scheduler.Scheduler, 'worker', media_worker)
        monkeypatch.setattr(scheduler.Scheduler, 'audio_lease', fake_audio_lease)
        monkeypatch.setattr('factory.speech.subprocess.run', FixtureSpeech.invoke)
        store.initialize()
        from factory.web import Handler
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            request = urllib.request.Request(base + '/api/factory/dramas', json.dumps({
                'production_mode': 'one_sentence', 'brief': '成年人在车站发现旧信，三集逐步查明真相并完成和解。',
                'request_id': 'cpu-e2e'}).encode(), {'Origin': base, 'Content-Type': 'application/json'})
            with urllib.request.urlopen(request) as response:
                p = json.load(response)
        finally:
            server.shutdown(); server.server_close(); thread.join()

        resource_job = None
        for _ in range(100):
            job = store.claim_job()
            if job is None:
                with store.connection() as db:
                    due = db.execute("SELECT count(*) FROM jobs WHERE status='queued' AND next_poll_at>0").fetchone()[0]
                    if due:
                        # Advance only the persistent retry clock; never business state.
                        db.execute("UPDATE jobs SET next_poll_at=0 WHERE status='queued'")
                        continue
                break
            worker.handle(job)
            saved_job = None
            with store.connection() as db:
                saved_job = db.execute('SELECT status,next_poll_at FROM jobs WHERE id=?', (job['id'],)).fetchone()
            if saved_job['status'] == 'queued' and saved_job['next_poll_at']:
                if store.get(job['project_id'], False).get('production', {}).get('remote_wait', {}).get('wait_kind') == 'resources':
                    resource_job = job['id']
                    assert not calls  # Discovery failure never submitted inference or media.

            current = dramas.snapshot(p['id'])
            if current['automation_state']['status'] == 'blocked':
                from factory import agent_loop
                for record in [store.get(p['id'], False), *[store.get(e['id'], False) for e in current['episodes']]]:
                    for row in agent_loop._rows(record):
                        if row.get('state') not in agent_loop.CLOSED:
                            print('SYNTHETIC_PENDING_AGENT', record['id'], row.get('stage'), row.get('state'), row.get('pending'), row.get('reason'))
                for receipt in root.glob('production/*/planning/**/receipt.json'):
                    value = core.read_json(receipt)
                    if value.get('status') == 'failed': print('SYNTHETIC_FAILED_RECEIPT', value.get('role'), value.get('error'))
            assert current['automation_state']['status'] != 'blocked', current.get('error') or current['automation_state']
        current = dramas.snapshot(p['id'])
        assert len(current['episodes']) == 3
        assert all(e['status'] == 'awaiting_final' for e in current['episodes']), current['episodes']
        assert current['delivery_bundle']['current'] is True
        assert current['delivery_bundle']['status'] == 'awaiting_human_review'
        assert all(e['status'] != 'approved' for e in current['episodes'])
        archive = drama_delivery.download_bundle(p['id'], current['delivery_bundle']['source_signature'])
        with zipfile.ZipFile(archive) as bundle:
            assert 'series-review.json' in bundle.namelist()
            assert all(f'E{i:03d}/episode.mp4' in bundle.namelist() for i in (1, 2, 3))
        assert drift_injected and any('-repair-1' in stage for stage in calls)
        assert resource_job and len(discovery) > 1 and review_returned and series_returned
        assert 'supervisor_final' not in calls
        assert len(store.get(p['id'], False)['series_repair_history']) == 1
        assert len(store.get(current['episodes'][2]['id'], False)['episode_repair_history']) == 1
        repaired = store.get(current['episodes'][0]['id'], False)
        assert len(repaired['episode_repair_history']) == 1 and repaired['delivery_history']
        assert repaired['episode_media_revisions'] == {'S01': {'image': 1, 'video': 1}}
        takes = {s['id']: s['video']['take'] for s in repaired['shots']}
        assert takes['S01'] != takes['S02']  # Failed media is regenerated even when the writer keeps the same text.
        with store.connection() as db:
            assert db.execute('SELECT status FROM jobs WHERE id=?', (resource_job,)).fetchone()[0] == 'completed'

        assert 'drama-world-1-1' in calls
        assert sum(stage.startswith('screenplay-') for stage in calls) >= 12
        before = len(calls)
        from factory import drama_automation
        drama_automation.recover()
        assert store.claim_job() is None and len(calls) == before
    finally:
        case.doCleanups()
