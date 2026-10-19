"""Production fences reconcile accepted GPU jobs before legacy Comfy checks."""
from __future__ import annotations

import copy

import pytest

from factory import gpu_client, production, scheduler
from factory.scheduler import Budget, ProductionNeedsDecision, ProductionPaused
from studio import core


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for module in (production, core, gpu_client, scheduler):
        monkeypatch.setattr(module, 'ROOT', tmp_path)
    project = {'id': 'reconcile-reference', 'slug': 'factory-reconcile-reference', 'format': 'motion_comic',
        'styles': [{'id': 'approved', 'prompt': 'Approved watercolor'}], 'selected_style': 'approved',
        'episode': {'cast': {'hero': 'Adult wearing a blue coat'}, 'shots': []}}
    config = {'gpu_service': {'base_url': 'http://127.0.0.1:19999/inference', 'poll_seconds': .01},
        'max_gpu_seconds': 1000, 'max_wall_seconds': 1000, 'max_attempts': 3,
        'image_width': 288, 'image_height': 512, 'output_width': 288, 'output_height': 512}
    run = production._Run(project, config, None, None)
    old = run._target('reference', run.folder / 'references/hero', 'Approved original reference')
    record = run._new_attempt(old)
    folder = old['folder'] / record['take']
    ident = 'a' * 64
    submission = {'gpu_service_job_id': ident, 'state': 'running', 'request_id': ident}
    core.write_json(folder / 'submission.json', submission)
    changed = run._target('reference', old['folder'], 'Approved changed reference')
    state = {'gets': [], 'answer': {'id': ident, 'status': 'completed',
        'result': {'receipt': {'gpu_seconds': 12.75, 'execution_host': 'fixture-wkp'}}}}
    def get_only(client, route, payload=None, **kwargs):
        assert payload is None, 'Reconciliation may never submit a replacement GPU job'
        assert route == '/jobs/' + ident
        state['gets'].append(route)
        return copy.deepcopy(state['answer'])
    monkeypatch.setattr(gpu_client.GPUClient, 'request', get_only)
    return run, old, changed, folder, submission, state


@pytest.mark.parametrize('terminal', ['completed', 'failed'])
def test_changed_source_reconciles_terminal_gpu_before_legacy_fence_and_accounts_once(runtime, terminal):
    run, old, changed, folder, submission, state = runtime
    state['answer']['status'] = terminal
    attempt_before = (folder / 'attempt.json').read_bytes()
    run._guard_remote_submissions(changed)
    assert core.read_json(folder / 'submission.json')['state'] == terminal
    assert core.read_json(folder / 'remote_result.json') == state['answer']
    assert run.budget.snapshot()['gpu_seconds'] == 12.75
    assert run.budget.snapshot()['remote_gpu_receipts'] == {submission['gpu_service_job_id']: 12.75}
    assert (folder / 'attempt.json').read_bytes() == attempt_before
    assert not (folder / 'result.json').exists(), 'Reconciliation must not invent a downloaded artifact'
    assert (folder / 'submission_error.json').exists() == (terminal == 'failed')
    # Cached terminal receipt can restore an unchanged checkpoint repeatedly.
    run._guard_remote_submissions(changed)
    run.budget = Budget(run.config, run.budget.snapshot())
    run._guard_remote_submissions(changed)
    assert len(state['gets']) == 1
    assert run.budget.snapshot()['gpu_seconds'] == 12.75
    assert [p.name for p in old['folder'].glob('take_*')] == ['take_001']


@pytest.mark.parametrize('status', ['queued', 'running', 'unresolved', None])
def test_changed_source_pending_or_missing_gpu_job_blocks_without_submission(runtime, status):
    run, old, changed, folder, submission, state = runtime
    state['answer'] = None if status is None else {'id': submission['gpu_service_job_id'], 'status': status}
    attempt_before = (folder / 'attempt.json').read_bytes()
    with pytest.raises(ProductionNeedsDecision) as failure:
        run._guard_remote_submissions(changed)
    assert failure.value.reason == 'remote_gpu_unresolved'
    assert len(state['gets']) == 1
    assert core.read_json(folder / 'submission.json') == submission
    assert (folder / 'attempt.json').read_bytes() == attempt_before
    assert not (folder / 'remote_result.json').exists()
    assert run.budget.snapshot()['gpu_seconds'] == 0
    assert len(list(old['folder'].glob('take_*'))) == 1


@pytest.mark.parametrize('status', ['completed', 'failed'])
def test_changed_source_terminal_without_accounting_receipt_stays_blocked(runtime, status):
    run, old, changed, folder, submission, state = runtime
    state['answer'] = {'id': submission['gpu_service_job_id'], 'status': status, 'result': {}}
    with pytest.raises(ProductionNeedsDecision) as failure:
        run._guard_remote_submissions(changed)
    assert failure.value.reason == 'remote_gpu_receipt_invalid'
    assert len(state['gets']) == 1
    assert core.read_json(folder / 'submission.json') == submission
    assert not (folder / 'remote_result.json').exists()
    assert len(list(old['folder'].glob('take_*'))) == 1


@pytest.mark.parametrize('problem', ['identity', 'usage'])
def test_changed_source_invalid_remote_receipt_does_not_release_fence(runtime, problem):
    run, old, changed, folder, submission, state = runtime
    if problem == 'identity':
        state['answer']['id'] = 'b' * 64
    else:
        state['answer']['result']['receipt']['gpu_seconds'] = -1
    with pytest.raises(ProductionNeedsDecision) as failure:
        run._guard_remote_submissions(changed)
    assert failure.value.reason == 'remote_gpu_receipt_invalid'
    assert core.read_json(folder / 'submission.json') == submission
    assert not (folder / 'remote_result.json').exists()
    assert len(state['gets']) == 1


@pytest.mark.parametrize('gpu_configured', [True, False])
@pytest.mark.parametrize('state_name', ['running', 'submitted'])
def test_external_comfy_without_gpu_identity_keeps_unknown_submission_fence(runtime, gpu_configured, state_name):
    run, old, changed, folder, submission, state = runtime
    if not gpu_configured:
        run.config.pop('gpu_service')
    journal = {'state': state_name, 'prompt_id': 'external-comfy-prompt'}
    core.write_json(folder / 'submission.json', journal)
    with pytest.raises(ProductionNeedsDecision) as failure:
        run._guard_remote_submissions(changed)
    assert failure.value.reason == 'unresolved_submission'
    assert state['gets'] == []
    assert core.read_json(folder / 'submission.json') == journal
    assert len(list(old['folder'].glob('take_*'))) == 1


@pytest.mark.parametrize('gpu_configured', [True, False])
def test_gpu_identity_cannot_bypass_unknown_fence_without_its_configured_service(runtime, gpu_configured):
    run, old, changed, folder, submission, state = runtime
    if gpu_configured:
        state['answer']['status'] = 'running'
    else:
        run.config.pop('gpu_service')
    with pytest.raises(ProductionNeedsDecision) as failure:
        run._guard_remote_submissions(changed)
    assert failure.value.reason == ('remote_gpu_unresolved' if gpu_configured else 'unresolved_submission')
    assert len(state['gets']) == int(gpu_configured)
    assert core.read_json(folder / 'submission.json') == submission


def test_same_source_stays_with_original_take_and_normal_resume(runtime, monkeypatch):
    run, old, changed, folder, submission, state = runtime
    before = (folder / 'attempt.json').read_bytes()
    run._guard_remote_submissions(old)
    assert state['gets'] == []
    calls = []
    def resume(config, destination, budget, control, **kwargs):
        calls.append(destination)
        assert core.read_json(destination / 'submission.json') == submission
        raise ProductionPaused('same accepted job is still running')
    monkeypatch.setattr(gpu_client, 'resume_remote_comfy', resume)
    with pytest.raises(ProductionPaused):
        run._unfinished_candidate(old)
    assert calls == [folder]
    assert (folder / 'attempt.json').read_bytes() == before
    assert core.read_json(folder / 'submission.json') == submission
    assert len(list(old['folder'].glob('take_*'))) == 1
    assert state['gets'] == []
