"""Recorded readiness is never a platform approval; no model/service requests."""
import copy
import json
from pathlib import Path

import pytest

from studio.core import fingerprint
from factory import delivery_readiness as dr, submission

PID = "012345abcdef"


def ready_project():
    gid = PID + ":final:2"
    parent = "production/factory-" + PID + "/release/v2-" + "a" * 12
    return {"id": PID, "slug": "factory-" + PID, "revision": 10, "production_revision": 2,
        "delivery_complete_revision": 2, "status": "approved", "accepted_by": "producer",
        "answers": {gid: {"gate_id": gid, "type": "final", "choice": "approve", "actor": "producer"}},
        "decision_effects": {gid: {"route": "done"}},
        "delivery": {"project_id": PID, "slug": "factory-" + PID, "revision": 2,
            "technical_qa": {"clean": {"status": "pass"}, "subtitled": {"status": "pass"}},
            "artifacts": [{"path": parent + "/" + name, "sha256": "b" * 64, "bytes": 123} for name in dr.REQUIRED]}}


def acceptance_report(project):
    return {"schema": "ai-studio.film-evaluation.v1", "project_id": PID, "production_revision": 2,
            "verdict": "human_approved", "technical_passed": True, "human_approved": True,
            "acceptance": {"schema": "ai-studio.production-acceptance.v2", "input_snapshot_sha256": fingerprint(project)}}


def test_metadata_is_pure_and_never_certifies_files_or_platform():
    p = ready_project()
    before = copy.deepcopy(p)
    result = dr.snapshot(p)
    assert p == before
    assert result["status"] == "ready_for_fresh_acceptance"
    assert not result["engineering_ready"] and not result["platform_ready"] and not result["files_read"]
    assert result["checks"][-1]["status"] == "not_run"
    assert all(v["status"] == "human_required" for v in result["platform_checklist"])


@pytest.mark.parametrize("patch", [{"production_revision": 3}, {"delivery_complete_revision": 1},
    {"accepted_by": "validation_agent"}, {"accepted_by": "executive_producer"}, {"decision_effects": {}},
    {"answers": {}}, {"status": "awaiting_final"}, {"delivery_complete_revision": True}])
def test_old_or_nonhuman_approvals_are_not_ready(patch):
    p = ready_project()
    p.update(patch)
    assert dr.snapshot(p)["status"] == "blocked"


@pytest.mark.parametrize("alter", ["traversal", "foreign", "duplicate", "mixed", "hash", "bytes", "url"])
def test_invalid_artifact_receipts_block_handoff(alter):
    p = ready_project()
    rows = p["delivery"]["artifacts"]
    if alter == "traversal": rows[0]["path"] = "production/../private/key"
    if alter == "foreign": rows[0]["path"] = rows[0]["path"].replace(PID, "abcdef012345")
    if alter == "duplicate": rows.append(copy.deepcopy(rows[0]))
    if alter == "mixed": rows[0]["path"] = rows[0]["path"].replace("a" * 12, "c" * 12)
    if alter == "hash": rows[0]["sha256"] = "not-a-digest"
    if alter == "bytes": rows[0]["bytes"] = True
    if alter == "url": rows[0]["path"] = "https://external.invalid/file.mp4"
    result = dr.snapshot(p)
    assert result["status"] == "blocked"
    assert all(row["url"].startswith("/media/production/factory-" + PID + "/") for row in result["artifacts"])


def test_receipts_have_bounded_size_and_no_raw_extra_fields():
    p = ready_project()
    p["delivery"]["artifacts"][0]["secret"] = "DO_NOT_RETURN"
    assert "DO_NOT_RETURN" not in str(dr.snapshot(p))
    p["delivery"]["artifacts"] *= 100
    assert dr.snapshot(p)["artifacts"] == []


def test_fresh_acceptance_can_only_establish_engineering_readiness():
    p = ready_project()
    result = dr.bind_acceptance(p, acceptance_report(p))
    assert result["engineering_ready"]
    assert result["status"] == "engineering_ready_platform_pending"
    assert result["platform_ready"] is False and result["platform_approval"] == "not_verified"


@pytest.mark.parametrize("patch", [{"project_id": "abcdef012345"}, {"production_revision": 1},
    {"technical_passed": False}, {"human_approved": False}, {"verdict": "needs_repair"}, {"acceptance": {}}])
def test_report_rebinding_or_failure_cannot_pass(patch):
    p = ready_project()
    report = {**acceptance_report(p), **patch}
    assert not dr.bind_acceptance(p, report)["engineering_ready"]


def test_project_mutation_invalidates_even_a_successful_report():
    p = ready_project()
    report = acceptance_report(p)
    p["revision"] += 1
    assert not dr.bind_acceptance(p, report)["engineering_ready"]


def test_real_acceptance_with_missing_film_creates_separate_blocked_manifest(tmp_path):
    p = {"id": PID, "slug": "factory-" + PID, "revision": 4, "production_revision": 2,
         "duration_seconds": 60, "shots": [], "episode": {"shots": [], "cast": {}}, "delivery": None}
    source = tmp_path / "source.json"
    source.write_text(json.dumps(p), encoding="utf-8")
    before = source.read_bytes()
    result = submission.prepare(PID, project_json=source, root=tmp_path, output=tmp_path / "reports")
    assert source.read_bytes() == before and not (tmp_path / "production").exists()
    assert not result["engineering_ready"] and not result["platform_ready"]
    assert Path(result["manifest_path"]).is_file()
    assert Path(result["manifest_path"]).parent != Path(result["acceptance_report"]["path"]).parent
    assert result["operations"] == {"model_requests": 0, "generation_submissions": 0, "platform_uploads": 0, "database_writes": False}


def test_submission_rejects_ambiguous_source_and_production_output(tmp_path):
    with pytest.raises(ValueError):
        submission.prepare(PID, root=tmp_path)
    with pytest.raises(ValueError):
        submission.prepare(PID, database=tmp_path / "db", project_json=tmp_path / "p", root=tmp_path)
    with pytest.raises(ValueError, match="production"):
        submission.prepare(PID, project_json=tmp_path / "p", root=tmp_path, output=tmp_path / "production/reports")
