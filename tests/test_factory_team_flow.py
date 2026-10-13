"""Fixture-only contracts for role flow, provenance and bounded project reads."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from factory import team_flow, workflow_spec


PID = "012345abcdef"


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setattr(team_flow, "ROOT", tmp_path)
    project = {
        "id": PID, "slug": "factory-" + PID, "revision": 4,
        "brief": "两个虚构成年人在街角发生误会并共同解决问题", "status": "queued",
        "stage": "queued", "format": "motion_comic", "proposal_generation": 1,
        "script_generation": 1, "production_revision": 1, "shots": [],
    }
    config = {"llm": {"model": "current-writer", "provider": "openai_compatible"}}
    return tmp_path, project, config


def role(flow, name):
    return next(row for row in flow["nodes"] if row["role"] == name)


def put(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def call(root, name, value, location="planning/llm/calls"):
    return put(root, Path("production") / ("factory-" + PID) / location / name / "receipt.json", value)


def test_configured_roles_are_not_execution_evidence_and_read_is_pure(context):
    root, project, config = context
    previous = copy.deepcopy((project, config))
    flow = team_flow.build_flow(project, config)
    assert (project, config) == previous
    assert not (root / "production").exists()
    assert flow["assignment_mode"] == "orchestrator"
    assert flow["orchestrator"] == "LangGraph"
    assert all(row["executions"] == [] for row in flow["nodes"])
    assert role(flow, "screenwriter")["status"] == "pending"
    assignment = next(row for row in flow["teams"] if row["role"] == "screenwriter")
    assert assignment["model"] == "current-writer"
    assert {edge["source"] for edge in flow["edges"]} <= {row["id"] for row in flow["nodes"]}
    assert {edge["target"] for edge in flow["edges"]} <= {row["id"] for row in flow["nodes"]}


def test_receipts_keep_original_model_profile_and_real_timestamps(context):
    root, project, config = context
    call(root, "20261013T010000-screenwriter-00000001", {
        "role": "screenwriter", "profile": "old-local", "provider": "ollama",
        "configured_model": "qwen:8b", "model": "qwen:8b", "response_model": "qwen:8b",
        "status": "succeeded", "started_at": 1791853200, "elapsed_seconds": 3.2,
        "attempts": [{"attempt": 1}, {"attempt": 2}], "api_key": "never-expose-this",
        "parameters": {"Authorization": "Bearer never-expose-this"},
    })
    call(root, "20261013T010001-independent_critic-00000002", {
        "role": "independent_critic", "status": "failed", "model": "old-reviewer",
        "attempts": [], "error": "private-stack-and-credentials",
    }, location="planning/calls")
    flow = team_flow.build_flow(project, config)
    execution = role(flow, "screenwriter")["executions"][0]
    assert execution["profile_id"] == "old-local"
    assert execution["model"] == "qwen:8b"
    assert execution["provider"] == "ollama"
    assert execution["model_source"] == "response"
    assert execution["attempt"] == 2
    assert execution["started_at"].endswith("+00:00")
    assert execution["finished_at"] is None
    assert execution["scope"] == "project_history"
    assert execution["receipt"].startswith("/media/production/factory-" + PID + "/")
    assert role(flow, "screenwriter")["status"] == "pending"
    assert role(flow, "independent_critic")["executions"][0]["status"] == "failed"
    rendered = json.dumps(flow)
    assert "never-expose-this" not in rendered
    assert "private-stack-and-credentials" not in rendered
    assert "Authorization" not in rendered


def test_unrecorded_model_is_not_filled_from_configuration(context):
    root, project, config = context
    call(root, "20261013T010000-screenwriter-00000003", {
        "role": "screenwriter", "status": "starting", "started_at": "not-a-date"})
    execution = role(team_flow.build_flow(project, config), "screenwriter")["executions"][0]
    assert execution["model"] is None
    assert execution["profile_id"] is None
    assert execution["model_source"] == "unrecorded"
    assert execution["started_at"] is None
    assert execution["finished_at"] is None


def test_generation_fences_and_gate_status(context):
    root, project, config = context
    project.update(status="awaiting_story", stage="story_decision",
                   research_complete=True, research={"status": "degraded", "source_count": 0},
                   proposals=[{"id": "A", "title": "候选故事"}],
                   proposals_complete_generation=1,
                   script={"title": "旧剧本", "shots": []},
                   script_complete_generation=1, script_generation=2)
    flow = team_flow.build_flow(project, config)
    assert role(flow, "researcher")["status"] == "succeeded"
    assert role(flow, "creator")["status"] == "succeeded"
    assert role(flow, "screenwriter")["status"] == "pending"
    assert role(flow, "producer")["status"] == "waiting"
    assert role(flow, "orchestrator")["status"] == "waiting"
    project.update(status="scripting", stage="script_creation")
    flow = team_flow.build_flow(project, config)
    assert role(flow, "screenwriter")["status"] == "running"
    project["status"] = "paused"
    assert role(team_flow.build_flow(project, config), "screenwriter")["status"] == "paused"
    project["status"] = "cancelled"
    assert role(team_flow.build_flow(project, config), "screenwriter")["status"] == "cancelled"


def test_symlinks_and_cross_project_paths_never_become_evidence(context):
    root, project, config = context
    foreign = put(root, "outside/receipt.json", {"role": "screenwriter", "model": "private-foreign-model"})
    directory = root / "production" / ("factory-" + PID) / "planning/llm/calls"
    directory.mkdir(parents=True)
    (directory / "linked").symlink_to(foreign.parent, target_is_directory=True)
    folder = directory / "linked-file"
    folder.mkdir()
    (folder / "receipt.json").symlink_to(foreign)
    secret_artifact = put(root, "production/factory-fedcba543210/planning/script.md", "other-project")
    project["planning_artifacts"] = {
        "script": str(secret_artifact.relative_to(root)),
        "sources": "production/factory-" + PID + "/../../outside/receipt.json",
        "research": "https://example.invalid/secret",
    }
    flow = team_flow.build_flow(project, config)
    assert not role(flow, "screenwriter")["executions"]
    assert "private-foreign-model" not in json.dumps(flow)
    assert not any(item.get("artifact_url") for node in flow["nodes"] for item in node["outputs"])
    project["slug"] = "../outside"
    with pytest.raises(ValueError):
        team_flow.build_flow(project, config)


def test_receipt_limits_malformed_data_and_unknown_roles(context, monkeypatch):
    root, project, config = context
    call(root, "20261013T010004-unknown", {"role": {"malformed": True}, "status": "succeeded"})
    bad = call(root, "20261013T010003-bad", {})
    bad.write_text("{broken", encoding="utf-8")
    large = call(root, "20261013T010002-large", {})
    large.write_bytes(b" " * (team_flow.MAX_RECEIPT_BYTES + 1))
    for index in range(2):
        call(root, "20261013T01000%d-screenwriter" % index,
             {"role": "screenwriter", "model": "writer", "status": "succeeded"})
    monkeypatch.setattr(team_flow, "MAX_RECEIPTS", 4)
    flow = team_flow.build_flow(project, config)
    assert flow["evidence"]["receipts_read"] == 4
    assert flow["evidence"]["truncated"] is True
    assert flow["evidence"]["unreadable_receipts"] == 2
    assert len(role(flow, "screenwriter")["executions"]) == 1


def test_media_attempts_preserve_remote_task_and_motion_comic_origin(context):
    root, project, config = context
    prefix = "production/factory-" + PID + "/shots/S01/take_01/"
    put(root, prefix + "frame.png", {})
    put(root, prefix + "clip.mp4", {})
    put(root, prefix + "provider_receipt.json", {"task_id": "task-1"})
    project["shots"] = [{
        "id": "S01", "image": {"path": prefix + "frame.png"},
        "video": {"path": prefix + "clip.mp4", "origin": "procedural_camera_motion"},
        "attempts": [{"stage": "image", "take": "take_01", "attempt": 1, "state": "passed",
                      "model": "recorded-image", "started_at": 100, "finished_at": 105}],
    }]
    flow = team_flow.build_flow(project, config)
    assert role(flow, "image_generator")["status"] == "succeeded"
    assert role(flow, "video_generator")["status"] == "pending"
    assert role(flow, "video_generator")["executions"] == []
    assert any(item.get("artifact_url") for item in role(flow, "editor")["outputs"])
    project["format"] = "live_action"
    project["shots"][0]["video"].pop("origin")
    project["shots"][0]["attempts"].append({
        "stage": "video", "attempt": 1, "state": "rendering", "model": "hailuo",
        "profile": "cloud-video", "provider": "minimax",
        "remote_task": {"task_id": "task-1", "status": "Processing"},
        "output": {"provider_receipt": prefix + "provider_receipt.json"},
    })
    project.update(status="producing", stage="production", production={"phase": "diffusion_video"})
    flow = team_flow.build_flow(project, config)
    execution = role(flow, "video_generator")["executions"][0]
    assert execution["task_id"] == "task-1"
    assert execution["profile_id"] == "cloud-video"
    assert execution["provider"] == "minimax"
    assert execution["receipt"].endswith("provider_receipt.json")
    assert role(flow, "video_generator")["status"] == "running"


def test_voice_reads_only_explicit_project_batch_receipts(context):
    root, project, config = context
    reference = "production/factory-" + PID + "/audio/tts/batches/batch-1/attempt_01.json"
    put(root, reference, {"state": "completed", "attempt": 1, "started_at": 100, "finished_at": 105,
                          "argv": ["private-argument"], "error_tail": "private-log"})
    project["production"] = {"voice_attempts": [reference, "production/factory-fedcba543210/audio/tts/batches/batch-1/attempt_01.json"]}
    flow = team_flow.build_flow(project, config)
    executions = role(flow, "speech")["executions"]
    assert len(executions) == 1
    assert executions[0]["status"] == "succeeded"
    assert executions[0]["model"] is None
    assert "private-argument" not in json.dumps(flow)
    assert "private-log" not in json.dumps(flow)


def test_spec_keeps_executable_nodes_and_edges(context, monkeypatch):
    _, _, config = context
    from factory import workflows
    topology = {"nodes": [{"id": "research"}],
                "edges": [{"source": "__start__", "target": "research"}]}
    class Graph:
        def get_graph(self):
            return self
        def to_json(self):
            return copy.deepcopy(topology)
    monkeypatch.setattr(workflows, "build_graph", lambda checkpointer: Graph())
    spec = workflow_spec.export_spec(config)
    assert spec["nodes"] == topology["nodes"]
    assert spec["edges"] == topology["edges"]
    assert spec["orchestrator"] == "LangGraph"
    assert spec["assignment_mode"] == "orchestrator"
    assert any(row["role"] == "screenwriter" for row in spec["teams"])
    assert any(row["target"] == "screenwriter" for row in spec["handoffs"])


def test_rejected_or_missing_media_is_not_marked_as_finished(context):
    root, project, config = context
    reference = "production/factory-" + PID + "/shots/S01/rejected/frame.png"
    put(root, reference, {})
    project["shots"] = [{"id": "S01", "image_path": reference, "status": "retrying"}]
    flow = team_flow.build_flow(project, config)
    assert role(flow, "image_generator")["status"] == "pending"
    assert role(flow, "image_generator")["outputs"][0]["value"] == "候选素材"
    project["shots"][0]["image"] = {"path": reference + ".missing"}
    project.update(delivery={"path": "production/factory-" + PID + "/delivery/missing.mp4"},
                   delivery_complete_revision=1)
    flow = team_flow.build_flow(project, config)
    assert role(flow, "image_generator")["status"] == "pending"
    assert role(flow, "editor")["status"] == "pending"
