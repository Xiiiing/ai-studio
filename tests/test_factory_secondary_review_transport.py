"""Real media review -> typed classification -> durable worker deferral.

The CPU clip and main verdict are fixtures, never a claim about model quality.
"""
import copy
import shutil
import urllib.error

import pytest

from factory import (config, llm, quality, store, worker, workflows)
from factory.production_execution import DEFAULT_POLICY
from factory.scheduler import ProductionNeedsDecision
from studio import core
from tests.test_factory_execution_settlement import production
from tests.test_factory_dramas import isolated
from tests.test_factory_temporal_review import video, verdict, GAP, gap_scope
from tests.test_factory_role_routing import routing_config


@pytest.mark.parametrize('stage', ['closed_lips', 'continuous_frames'])
@pytest.mark.parametrize('exception', ['timeout', 'http503'])
@pytest.mark.parametrize('provider', ['llama_cpp', 'openai'])
def test_secondary_review_transport_reaches_actual_worker_retry_boundary(production, video, tmp_path, monkeypatch,
                                                                        stage, exception, provider):
    root = store.get(production, False)
    episode_id = root['episodes'][0]
    episode = store.get(episode_id, False)
    models = routing_config()
    models['llm_profiles']['qwen']['provider'] = provider
    models['llm']['provider'] = provider
    models['quality'] = {'semantic_enabled': True, 'temporal_samples': 8}
    models['production'] = {'production_recovery': copy.deepcopy(DEFAULT_POLICY)}
    store.update(episode_id, {'execution_config': models})
    requests, classified = [], []
    def request(configuration, role, system, payload, schema, output, **kwargs):
        requests.append(str(output))
        if len(requests) == 1:
            initial = verdict([] if stage == 'closed_lips' else [GAP], 95 if stage == 'closed_lips' else 45)
            if stage == 'continuous_frames':
                initial['temporal_concerns'] = [gap_scope()]
            return initial
        if exception == 'timeout':
            raise TimeoutError('Synthetic known transient secondary inspection timeout')
        raise urllib.error.HTTPError('http://fixture-review/v1', 503, 'Synthetic unavailable', {}, None)
    monkeypatch.setattr(llm, '_request', request)
    monkeypatch.setattr('factory.providers.context_budget', lambda *args, **kwargs: {'fits': True})
    def execute(uid):
        unit = store.get(uid, False)
        owned = core.ROOT / 'production' / unit['slug'] / 'renders' / 'S02' / 'take_003' / 'video.mp4'
        owned.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(video, owned)
        shot = {'id': 'S02', 'action': 'Raise the hand in one continuous movement.'}
        if stage == 'closed_lips':
            shot['prompt'] = 'Visible mouths stay closed throughout the clip.'
        media_project = {'id': uid, 'slug': unit['slug'], 'format': 'animation', 'creative_contract': {
            'schema_version': 1, 'visual_style': 'anime_2d', 'story_mode': 'standalone',
            'pacing': 'short_drama', 'motion': 'generated_video'}}
        result = quality.semantic_review(lambda p, s, path, refs:
            llm.review_media(p, s, path, refs, models, output_dir=owned.parent / 'qa'),
            media_project, shot, owned, [])
        classified.append(result)
        assert result['status'] == 'unavailable' and result['retryable_transport'] is True
        raise ProductionNeedsDecision('Saved clip pending secondary review transport recovery', 'semantic_unavailable', {
            'resume_action': 'review_existing_assets_only', 'pending': [
                {'asset': 'S02', 'stage': 'video', 'path': str(owned.relative_to(core.ROOT)),
                 'take': 'take_003', 'retryable_transport': result['retryable_transport']}]})
    monkeypatch.setattr(workflows, 'advance', execute)
    job = store.claim_job()
    worker.handle(job)
    assert len(requests) == 2 and len(classified) == 1
    expected_phase = 'explicit-constraints' if stage == 'closed_lips' else 'continuous'
    assert expected_phase in requests[-1]
    episode = store.get(episode_id, False)
    unit = store.get(episode['active_unit_id'], False)
    if provider == 'llama_cpp':
        assert episode['status'] == unit['status'] == 'waiting_remote'
        recovery = episode['production']['review_transport_recovery']
        assert recovery['status'] == 'waiting' and recovery['count'] == 1
        assert unit['production']['review_transport_recovery']['pending'] == recovery['pending']
        with store.connection() as db:
            saved_job = db.execute('SELECT status,phase FROM jobs WHERE id=?', (job['id'],)).fetchone()
            assert tuple(saved_job) == ('queued', 'quality_review')
    else:
        assert episode['status'] == unit['status'] == 'blocked'
        assert not episode['production'].get('review_transport_recovery')
    assert list((core.ROOT / 'production' / unit['slug'] / 'renders').rglob('video.mp4'))
    failures = list((core.ROOT / 'production' / unit['slug']).rglob('evidence.json' if stage == 'closed_lips' else 'transport_failure.json'))
    assert failures and core.read_json(failures[0])['status'] == 'unavailable'
