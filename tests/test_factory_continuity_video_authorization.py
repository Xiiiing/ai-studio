"""Narrow H3 continuity returns exercise real rendering with synthetic CPU media."""
import copy

import pytest

from factory import production, scheduler
from studio import core
from test_factory_h3_prompt_integration import setup


def retained(setup):
    project, cfg, target, *rest = setup
    run = production._Run(project, cfg, None, None)
    original = run._render_targets([target(run)], 'diffusion_video')[0]
    # Simulate loading a completed unit from before the automatic return feature.
    project = copy.deepcopy(run.project)
    project['production'].pop('review_only_video_results', None)
    project.update(record_kind='scene_unit', episode_id='parentepisode', production_revision=2,
        continuity_video_repair={'shot_ids': ['S01'], 'production_revision': 2,
            'source': 'episode_continuity_review', 'episode_id': 'parentepisode', 'cycle': 1, 'round': 1})
    project['episode']['shots'][0]['action'] = 'Leave the cup on the table and walk to the window.'
    return project, cfg, target, original


def test_authorized_changed_shot_generates_new_take_and_preserves_old_media(setup):
    project, cfg, target, original = retained(setup)
    path = core.ROOT / original['path']
    digest = core.sha256(path)
    run = production._Run(project, cfg, None, None)
    current = target(run)
    assert run._review_only_video(current) is False
    result = run._render_targets([current], 'diffusion_video')[0]
    assert result['take'] == 'take_002' and len(setup[5]) == 2
    assert result['source_signature'] != original['source_signature']
    assert path.is_file() and core.sha256(path) == digest
    assert core.read_json(path.parent/'result.json')['source_signature'] == original['source_signature']


@pytest.mark.parametrize('invalid', ['other_shot', 'old_revision', 'regular_project', 'wrong_source', 'wrong_parent', 'missing_authorization'])
def test_authorization_cannot_unlock_unrelated_or_stale_video(setup, invalid):
    project, cfg, target, original = retained(setup)
    if invalid == 'other_shot': project['continuity_video_repair']['shot_ids'] = ['S02']
    elif invalid == 'old_revision': project['continuity_video_repair']['production_revision'] = 1
    elif invalid == 'regular_project': project['record_kind'] = 'project'
    elif invalid == 'wrong_source': project['continuity_video_repair']['source'] = 'manual_text'
    elif invalid == 'wrong_parent': project['continuity_video_repair']['episode_id'] = 'anotherparent'
    else: project.pop('continuity_video_repair')
    run = production._Run(project, cfg, None, None)
    assert run._review_only_video(target(run)) is True
    with pytest.raises(scheduler.ProductionNeedsDecision) as error:
        run._render_targets([target(run)], 'diffusion_video')
    assert error.value.reason == 'existing_video_review_required'
    assert len(setup[5]) == 1
    assert core.sha256(core.ROOT/original['path']) == original['sha256']


def test_unchanged_source_still_uses_existing_video_protection(setup):
    project, cfg, target, original = retained(setup)
    project['episode']['shots'][0]['action'] = setup[0]['episode']['shots'][0]['action']
    run = production._Run(project, cfg, None, None)
    assert target(run)['signature'] == original['source_signature']
    assert run._review_only_video(target(run)) is True
    assert run._render_targets([target(run)], 'diffusion_video')[0]['take'] == 'take_001'
    assert len(setup[5]) == 1


def test_authorization_does_not_bypass_unresolved_older_submission(setup):
    project, cfg, target, original = retained(setup)
    run = production._Run(project, cfg, None, None)
    folder = target(run)['folder']/'take_002'
    core.write_json(folder/'attempt.json', {'state': 'rendering', 'source_signature': 'unresolved-old-input'})
    core.write_json(folder/'submission.json', {'state': 'submitted'})
    with pytest.raises(scheduler.ProductionNeedsDecision) as error:
        run._render_targets([target(run)], 'diffusion_video')
    assert error.value.reason == 'unresolved_submission'
    assert len(setup[5]) == 1 and not (target(run)['folder']/'take_003').exists()
