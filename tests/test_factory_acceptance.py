"""Read-only acceptance contracts: disposable snapshots, no services or models."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from factory import acceptance, evaluation
from factory.evaluation_io import database_project, row


PID = "012345abcdef"


def empty_project(pid=PID):
    return {"id": pid, "slug": "factory-" + pid, "revision": 4,
            "production_revision": 2, "duration_seconds": 60,
            "shots": [], "episode": {"shots": [], "cast": {}}, "delivery": None}


def test_no_film_generates_independent_report_without_service_or_store_calls(tmp_path, monkeypatch):
    from factory import store
    def forbidden(*args, **kwargs):
        raise AssertionError("Acceptance must not use the writable store or query systemd")
    monkeypatch.setattr(store, "get", forbidden)
    monkeypatch.setattr(store, "connection", forbidden)
    source = tmp_path / "project.json"
    source.write_text(json.dumps(empty_project()))
    report = acceptance.validate(PID, project_json=source, root=tmp_path, output=tmp_path / "reports")
    assert report["technical_passed"] is False
    assert report["verdict"] == "needs_repair"
    directory = Path(report["report_dir"])
    assert (directory / "report.json").is_file()
    assert (directory / "summary.md").is_file()
    assert (directory / "failures.json").is_file()
    assert not (tmp_path / "production").exists()
    assert report["acceptance"]["schema"] == "ai-studio.production-acceptance.v2"
    assert report["acceptance"]["model_execution"] == "not_run"


def test_two_projects_export_to_distinct_immutable_directories(tmp_path):
    def run(pid):
        source = tmp_path / (pid + ".json")
        source.write_text(json.dumps(empty_project(pid)))
        return acceptance.validate(pid, project_json=source, root=tmp_path, output=tmp_path / "reports")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(run, [PID, "abcdef012345"]))
    assert first["report_dir"] != second["report_dir"]
    before = Path(first["report_dir"], "report.json").read_bytes()
    third = run(PID)
    assert third["report_dir"] not in {first["report_dir"], second["report_dir"]}
    assert Path(first["report_dir"], "report.json").read_bytes() == before


def test_version_selector_cannot_accept_another_revision(tmp_path):
    source = tmp_path / "project.json"
    source.write_text(json.dumps(empty_project()))
    report = acceptance.validate(PID, project_json=source, root=tmp_path,
                                 production_revision=3, output=tmp_path / "reports")
    check = next(c for c in report["checks"] if c["check"] == "production_revision_selector")
    assert check["status"] == "fail"
    assert check["evidence"] == [{"requested": 3, "recorded": 2}]


def test_acceptance_rejects_output_inside_production(tmp_path):
    source = tmp_path / "project.json"
    source.write_text(json.dumps(empty_project()))
    with pytest.raises(ValueError, match="production"):
        acceptance.validate(PID, project_json=source, root=tmp_path,
                            output=tmp_path / "production/reports")
    assert not (tmp_path / "production").exists()


def test_database_input_never_initializes_or_changes_rows(tmp_path):
    path = tmp_path / "studio.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE projects(id TEXT,snapshot TEXT,revision INTEGER)")
        db.execute("INSERT INTO projects VALUES(?,?,?)", (PID, json.dumps(empty_project()), 7))
    before = path.read_bytes()
    report = acceptance.validate(PID, database=path, root=tmp_path, output=tmp_path / "reports")
    assert path.read_bytes() == before
    assert report["project_revision"] == 7
    assert database_project(path, PID)["shots"] == []


def test_old_historical_report_is_readable_but_not_comparable(tmp_path):
    from factory.acceptance_reports import compare
    legacy = {"schema": "autonomous_factory.acceptance.v1", "project_id": PID,
              "result": "passed_automated_checks_pending_producer"}
    result = compare({"schema": "ai-studio.film-evaluation.v1"}, legacy)
    assert result["comparable"] is False
    assert result["reason"]


def test_regression_never_compares_unversioned_or_unrelated_projects():
    baseline = {"golden_signature": "g", "profile_signature": "p", "project_id": PID,
                "schema": "ai-studio.film-evaluation.v1", "protocol_version": 1,
                "evaluation_code": {"test.py": "1"}, "checks": []}
    for patch in ({"project_id": "abcdef012345"}, {"protocol_version": 2}, {"evaluation_code": {}},
                  {"golden_signature": None}):
        assert evaluation.compare_reports({**baseline, **patch}, baseline)["comparable"] is False


def test_regression_reports_removed_checks_not_only_current_checks():
    baseline = {"golden_signature": "g", "profile_signature": "p", "project_id": PID,
                "schema": "ai-studio.film-evaluation.v1", "protocol_version": 1,
                "evaluation_code": {"test.py": "1"},
                "checks": [row("delivery", "required_exports", "fail")]}
    result = evaluation.compare_reports({**baseline, "checks": []}, baseline)
    assert result["comparable"] is True
    assert result["changes"][0]["after"] == "not_present"


def test_readonly_wal_snapshot_keeps_project_and_cloud_tasks_together(tmp_path, monkeypatch):
    import factory.evaluation_io as io
    path = tmp_path / "snapshot.sqlite3"
    connect = sqlite3.connect
    writer = connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.executescript("CREATE TABLE projects(id TEXT,snapshot TEXT,revision INTEGER); CREATE TABLE cloud_tasks(project_id TEXT,status TEXT)")
    writer.execute("INSERT INTO projects VALUES(?,?,?)", (PID, json.dumps(empty_project()), 4))
    writer.execute("INSERT INTO cloud_tasks VALUES(?,?)", (PID, "unknown")); writer.commit()
    class SnapshotCursor(sqlite3.Cursor):
        advance = False
        def fetchone(self):
            value = super().fetchone()
            if self.advance:
                self.advance = False
                writer.execute("UPDATE projects SET revision=5")
                writer.execute("UPDATE cloud_tasks SET status='completed'"); writer.commit()
            return value
    class SnapshotConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            cursor = self.cursor(factory=SnapshotCursor)
            cursor.execute(sql, parameters)
            cursor.advance = sql.startswith("SELECT snapshot,revision")
            return cursor
    monkeypatch.setattr(io.sqlite3, "connect", lambda *args, **kwargs: connect(*args, **kwargs, factory=SnapshotConnection))
    try:
        result = database_project(path, PID)
        assert result["revision"] == 4
        assert result["evaluation_cloud_tasks"][0]["status"] == "unknown"
        assert writer.execute("SELECT revision FROM projects").fetchone()[0] == 5
    finally:
        writer.close()


def test_interrupted_export_has_no_completion_manifest(tmp_path, monkeypatch):
    import factory.acceptance_reports as reports
    original = reports.write_json
    def interrupt(path, value):
        if Path(path).name == "failures.json":
            raise OSError("Synthetic interruption")
        return original(path, value)
    monkeypatch.setattr(reports, "write_json", interrupt)
    source = tmp_path / "project.json"; source.write_text(json.dumps(empty_project()))
    with pytest.raises(OSError, match="interruption"):
        acceptance.validate(PID, project_json=source, root=tmp_path, output=tmp_path / "reports")
    folders = list((tmp_path / "reports").iterdir())
    assert len(folders) == 1 and (folders[0] / "report.json").exists()
    assert not (folders[0] / "export-manifest.json").exists()


def test_changed_snapshot_cannot_be_certified(tmp_path, monkeypatch):
    source = tmp_path / "project.json"; source.write_text(json.dumps(empty_project()))
    original = evaluation.evaluate
    def advance(*args, **kwargs):
        result = original(*args, **kwargs)
        source.write_text(json.dumps({**empty_project(), "revision": 5}))
        return result
    monkeypatch.setattr(evaluation, "evaluate", advance)
    result = acceptance.validate(PID, project_json=source, root=tmp_path, output=tmp_path / "reports")
    assert next(v for v in result["checks"] if v["check"] == "input_snapshot_unchanged")["status"] == "fail"


def test_no_film_cli_returns_report_path_and_failure_exit(tmp_path):
    import subprocess
    import sys
    source = tmp_path / "project.json"; source.write_text(json.dumps(empty_project()))
    result = subprocess.run([sys.executable, "-m", "factory.acceptance", "--project-id", PID,
                             "--project-json", str(source), "--root", str(tmp_path), "--output", str(tmp_path / "reports")],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 2, result.stderr
    summary = json.loads(result.stdout)
    assert summary["verdict"] == "needs_repair" and Path(summary["report_dir"], "report.json").exists()
