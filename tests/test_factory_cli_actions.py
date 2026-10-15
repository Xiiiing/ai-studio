"""CLI producer actions reach the same transaction boundary as the workbench."""

import json

import pytest

from factory import cli, store


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(store, "DATABASE", tmp_path / "studio.sqlite3")
    store.initialize()
    project = store.create({"brief": "原创成年邻居合作归还钥匙，完成温暖结局。"})
    return store.update(
        project["id"],
        {
            "status": "blocked",
            "creation_mode": "adaptation",
            "needs_decision": {
                "kind": "source_rights",
                "type": "review",
                "message": "缺少授权原文",
            },
        },
    )


def test_action_inputs_file_recovers_source_blocker(project, tmp_path, capsys):
    inputs = tmp_path / "source.json"
    inputs.write_text(
        json.dumps({"text": "这是我原创且允许改编的邻里故事原文。", "rights": "owned"})
    )
    assert (
        cli.main(
            [
                "action",
                project["id"],
                "provide_authorized_source",
                "--inputs-file",
                str(inputs),
                "--expected-revision",
                str(project["revision"]),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "queued"
    assert result["submitted_sources"][-1]["rights"] == "owned"
    assert result["workflow_restart_from"] == "research"


def test_action_inputs_cannot_override_command_or_revision(project, tmp_path, capsys):
    inputs = tmp_path / "override.json"
    inputs.write_text(
        json.dumps({"action": "resume", "expected_revision": project["revision"]})
    )
    before = store.get(project["id"], False)
    assert (
        cli.main(
            [
                "action",
                project["id"],
                "switch_to_original",
                "--inputs-file",
                str(inputs),
            ]
        )
        == 2
    )
    assert store.get(project["id"], False) == before
    assert "不能覆盖" in capsys.readouterr().err


def test_status_lists_executable_source_recovery(project, capsys):
    assert cli.main(["status", project["id"]]) == 0
    output = capsys.readouterr().out
    assert "provide_authorized_source" in output
    assert "switch_to_original" in output
