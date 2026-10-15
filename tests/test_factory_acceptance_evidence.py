"""Acceptance evidence integrity is not equivalent to merely parsing a JSON file."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory import acceptance
from factory.evaluation_io import read_json


@pytest.mark.parametrize("content", ['{"revision":1,"revision":2}', '{"score":1e999}', '{"text":"\\ud800"}', '{"nested":' + '[' * 70 + '0' + ']' * 70 + '}'])
def test_ambiguous_or_unbounded_evidence_json_is_rejected(tmp_path, content):
    path = tmp_path / "evidence.json"; path.write_text(content)
    with pytest.raises(ValueError):
        read_json(path)


@pytest.fixture
def exported(tmp_path):
    pid = "012345abcdef"
    source = tmp_path / "project.json"
    source.write_text(json.dumps({"id": pid, "revision": 4, "production_revision": 2,
                                  "duration_seconds": 60, "shots": [], "delivery": None}))
    report = acceptance.validate(pid, project_json=source, root=tmp_path, output=tmp_path / "reports")
    return Path(report["report_dir"]), report


def test_verified_baseline_can_be_loaded_without_mutating_it(exported):
    from factory.acceptance_reports import load_report
    directory, report = exported
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    assert load_report(directory / "report.json")["run_id"] == report["run_id"]
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}


@pytest.mark.parametrize("defect", ["incomplete", "tampered_report", "tampered_evidence", "symlink"])
def test_incomplete_or_changed_baseline_cannot_be_compared(exported, defect):
    from factory.acceptance_reports import load_report
    directory, _ = exported
    if defect == "incomplete":
        (directory / "export-manifest.json").unlink()
    elif defect == "tampered_report":
        value = read_json(directory / "report.json"); value["technical_passed"] = True
        (directory / "report.json").write_text(json.dumps(value))
    elif defect == "tampered_evidence":
        (directory / "failures.json").write_text("[]")
    else:
        target = directory / "summary.md"
        target.unlink(); target.symlink_to(directory / "failures.json")
    with pytest.raises(ValueError):
        load_report(directory / "report.json")


def test_historical_report_stays_readable_without_invented_provenance(tmp_path):
    from factory.acceptance_reports import load_report, compare
    path = tmp_path / "acceptance_report.json"
    path.write_text(json.dumps({"schema": "autonomous_factory.acceptance.v1", "project_id": "012345abcdef"}))
    value = load_report(path)
    assert value["schema"] == "autonomous_factory.acceptance.v1"
    assert compare({}, value)["comparable"] is False


@pytest.mark.parametrize("revision", [True, -1, "bad/../../escape", [], {}])
def test_recorded_revision_is_never_a_report_output_path(tmp_path, revision):
    pid = "012345abcdef"
    source = tmp_path / "project.json"
    source.write_text(json.dumps({"id": pid, "revision": 4, "production_revision": revision,
                                  "duration_seconds": 60, "shots": [], "delivery": None}))
    with pytest.raises(ValueError, match="production_revision"):
        acceptance.validate(pid, project_json=source, root=tmp_path, output=tmp_path / "reports")
    assert not (tmp_path / "reports").exists()


def test_baseline_cannot_silently_drop_required_failure_evidence(exported):
    from factory.acceptance_reports import load_report
    directory, _ = exported
    path = directory / "export-manifest.json"
    manifest = read_json(path)
    manifest["files"] = [item for item in manifest["files"] if item["path"] != "failures.json"]
    path.write_text(json.dumps(manifest))
    (directory / "failures.json").unlink()
    with pytest.raises(ValueError, match="required"):
        load_report(directory / "report.json")
