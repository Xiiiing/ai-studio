"""A creative retry must not short-circuit into the previous approved movie."""
import copy

import pytest

from factory import store, team_control, team_ledger, workflows
from test_factory_shot_workbench import project


@pytest.mark.parametrize("stage", ["screenplay", "storyboard", "script_review", "visual_design", "visual_development", "production"])
def test_stage_retry_archives_delivery_and_requires_a_fresh_production_version(project, monkeypatch, stage):
    pid = project["id"]
    previous_delivery = {"video_url": "/media/production/old.mp4", "manifest_url": "/media/production/old.json"}
    # Seed an already approved historical movie with its original review contract.
    project = store.update(pid, {"creative_quality_contract":None, "status": "approved", "delivery": previous_delivery, "delivery_complete_revision": 1})
    ident = "stage:" + stage
    task = team_ledger.task_detail(pid, ident)["task"]
    result = team_control.submit(pid, {"request_id": "retry-" + stage, "expected_control_revision": 0,
        "target_type": "task", "target_id": ident, "task_ids": [ident], "task_generations": {ident: task["generation"]},
        "instruction": "重新完成本步骤，保留其他仍有效的成果", "operation": "retry", "preserve": [], "after": "auto"})
    assert result["instruction"]["status"] == "queued"
    assert team_control.process_pending(pid)["status"] == "applied"
    saved = store.get(pid, False)
    assert saved["production_revision"] > 1
    assert saved["delivery"] is None and saved.get("delivery_complete_revision") is None
    assert saved["delivery_history"][-1]["delivery"] == previous_delivery
    if stage in {"screenplay", "storyboard", "script_review"}:
        assert saved["script_review"] is None
    # Simulate the new independent script review, then invoke the real production
    # node: the previous movie must not take its early-return branch.
    store.update(pid, {"script_review": {"passed": True}, "selected_style": "style-A"})
    calls = []
    new_delivery = {"video_url": "/media/production/new.mp4", "manifest_url": "/media/production/new.json"}
    monkeypatch.setattr(workflows, "load_config", lambda: {})
    monkeypatch.setattr(workflows, "production_config", lambda *args: {})
    monkeypatch.setattr("factory.production.generate_project", lambda p, *args, **kwargs: calls.append(copy.deepcopy(p)) or new_delivery)
    workflows.production({"project_id": pid})
    assert len(calls) == 1 and calls[0]["production_revision"] == saved["production_revision"]
    assert store.get(pid, False)["delivery"] == new_delivery
