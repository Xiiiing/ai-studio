"""Impact preview follows real edit scope while preserving project state."""
import copy
from factory import store, shot_workbench
from test_factory_shot_workbench import project


def test_reference_dependency_preview_matches_actual_edit(project):
    from factory.shot_edits import mutate
    before = store.get(project['id'], False)
    impact = shot_workbench.snapshot(project['id'])['shots'][0]['operation_impact']
    assert store.get(project['id'], False)['revision'] == before['revision']
    changed = mutate(project['id'], 'S01', {'action': 'edit', 'expected_revision': before['revision'],
        'changes': {'prompt': 'An adult returns a book beside a blue platform bench'}})
    actual = changed['production_edits'][-1]['shot_ids']
    assert [s['shot_id'] for s in impact['edit_image']['affected']] == actual == ['S01', 'S03']
    assert impact['edit_image']['preserved_shot_ids'] == ['S02']
    assert impact['select_image']['affected'][0]['stages'] == ['video']
    assert impact['select_image']['affected'][1]['stages'] == ['image', 'video']
    assert impact['edit_video']['affected'] == [{'shot_id': 'S01', 'stages': ['video']}]


def test_unresolved_tasks_are_shown_only_for_affected_shots(project):
    result = shot_workbench.operation_impact(project, 'S01', [
        {'shot_id': 'S02', 'status': 'submitted'}, {'shot_id': 'S03', 'status': 'unknown'}])
    assert result['edit_image']['unresolved_tasks'] == [{'shot_id': 'S03', 'status': 'unknown'}]
    assert result['edit_video']['unresolved_tasks'] == []
    assert result['edit_image']['generation_submitted'] is False


def test_running_edit_is_read_only_even_if_snapshot_is_paused(project):
    with store.connection() as db:
        store._enqueue(db, project['id'])
        db.execute("UPDATE jobs SET status='running' WHERE project_id=?", (project['id'],))
    result = shot_workbench.snapshot(project['id'])
    assert result['execution_busy'] and not result['editable']
    assert result['shots'][0]['operation_impact']['edit_image']['source_revision'] == result['revision']


def test_candidate_proof_changes_with_current_or_previous_continuity_state(project):
    from test_factory_shot_workbench import fixture_candidate
    fixture_candidate(project, 'take_001')
    before = shot_workbench.snapshot(project['id'])['shots'][0]['candidates'][0]
    assert before['selectable']
    episode = copy.deepcopy(project['episode'])
    episode['shots'][0]['continuity_state']['opening_state'] = 'The adult is holding the approved red book.'
    store.update(project['id'], {'episode': episode, 'script': copy.deepcopy(episode)})
    after = shot_workbench.snapshot(project['id'])['shots'][0]['candidates'][0]
    assert not after['selectable'] and not after['plan_current']
    second = episode['shots'][1]
    second['continuity_state']['mode'] = 'continuous'
    p = {**project, 'episode': episode, 'script': copy.deepcopy(episode)}
    signature = shot_workbench.plan_signature(p, second, 'image')
    episode['shots'][0]['continuity_state']['ending_state'] = 'The book is now placed on the bench.'
    assert shot_workbench.plan_signature(p, second, 'image') != signature


def test_incomplete_new_state_cannot_select_a_candidate_or_break_read_projection(project):
    from test_factory_shot_workbench import fixture_candidate
    from factory.shot_edits import mutate
    import pytest
    fixture_candidate(project, 'take_001')
    episode = copy.deepcopy(project['episode'])
    episode['shots'][0].pop('continuity_state')
    p = store.update(project['id'], {'episode': episode, 'script': copy.deepcopy(episode)})
    board = shot_workbench.snapshot(p['id'])
    candidate = board['shots'][0]['candidates'][0]
    assert not candidate['continuity_valid'] and not candidate['selectable']
    with pytest.raises(ValueError, match='候选未通过验证'):
        mutate(p['id'], 'S01', {'action':'select', 'expected_revision':p['revision'],
            'stage':'image', 'take':'take_001', 'confirmed':True})
