"""Evidence consistency checks added to the existing seven-dimension evaluator."""
from __future__ import annotations

from pathlib import Path

from studio.core import fingerprint, sha256
from .acceptance_inputs import source_path
from .evaluation_io import check_receipt, finite, read_json, row
from .production_profiles import resolve


def receipt_evidence(receipt):
    return {key: receipt.get(key) for key in ("path", "sha256", "bytes")}


def contracts(project, root, slug, *, production_revision=None, media_sha=None):
    checks = []
    def record(name, passed, evidence, *, category="delivery", shot_id=None):
        checks.append(row(category, name, "pass" if passed else "fail", shot_id=shot_id, evidence=evidence))

    revision = project.get("production_revision")
    record("recorded_production_revision", type(revision) is int and revision > 0, [{"recorded": revision}])
    if production_revision is not None:
        record("production_revision_selector", revision == production_revision,
               [{"requested": production_revision, "recorded": revision}])
    owner = source_path(root, f"production/{slug}/factory_owner.json", slug=slug)
    if owner.exists():
        try:
            record("project_directory_owner", read_json(owner).get("project_id") == project["id"],
                   [{"path": owner.relative_to(root).as_posix(), "sha256": sha256(owner)}])
        except (ValueError, OSError, AttributeError):
            record("project_directory_owner", False, ["Unreadable ownership receipt"])
    shots = project.get("shots", [])
    script = project.get("episode") or project.get("script") or {}
    if project.get("script") and project.get("episode"):
        locked = ("id", "seconds", "characters", "dialogue", "action", "dramatic_beat", "continuity")
        before = [{key: shot.get(key) for key in locked} for shot in project["script"].get("shots", [])]
        after = [{key: shot.get(key) for key in locked} for shot in project["episode"].get("shots", [])]
        record("screenplay_episode_locked_fields", before == after,
               [{"screenplay_signature": fingerprint(before), "episode_signature": fingerprint(after)}], category="dialogue")
    try:
        profile = resolve(project)
        planned = {"S%02d" % (i + 1): value for i, value in enumerate(profile["shot_seconds"])}
        actual = {s["id"]: s["seconds"] for s in shots}
        trims = (project.get("edit_plan") or {}).get("trim_seconds", {})
        matches = (len(shots) == len(planned) and set(actual) == set(planned) and
                   all(abs(actual[sid] - float(trims.get(sid, seconds))) <= 1 / 48 and
                       .5 <= actual[sid] <= seconds for sid, seconds in planned.items()))
        record("production_profile_contract", matches,
               [{"profile": profile["preset"], "signature": fingerprint(profile), "planned_shots": len(planned), "actual_shots": len(shots)}])
    except (ValueError, KeyError, TypeError):
        profile = None
        record("production_profile_contract", False, ["Invalid or incompatible production profile"])
    script_shots = {s["id"]: s for s in script.get("shots", [])}
    for shot in shots:
        approved = script_shots.get(shot["id"])
        fields = ("id", "role", "text", "start")
        lines = lambda value: [{key: line.get(key) for key in fields} for line in value.get("dialogue", [])]
        evidence = [{"shot_id": shot["id"],
                     "script_dialogue_signature": fingerprint(lines(approved)) if approved else None,
                     "recorded_dialogue_signature": fingerprint(lines(shot))}]
        structure = lambda value: [(line.get("id"), line.get("role")) for line in value.get("dialogue", [])]
        record("screenplay_storyboard_dialogue", approved is not None and structure(shot) == structure(approved),
               evidence, category="dialogue", shot_id=shot["id"])
        if approved is not None and lines(shot) != lines(approved):
            # Existing voice-fit adaptation changes dialogue without rewriting the
            # reviewed screenplay. Surface it for a media-bound human review;
            # neither silently certify equivalence nor mutate historical scripts.
            checks.append(row("dialogue", "screenplay_voice_adaptation", "review_needed",
                              shot_id=shot["id"], evidence=evidence, source="recorded_adaptation_not_semantic_equivalence"))
    delivery = project.get("delivery") or {}
    record("delivery_project_revision", delivery.get("project_id") == project["id"] and type(delivery.get("revision")) is int and delivery.get("revision") == revision,
           [{"project_id": delivery.get("project_id"), "revision": delivery.get("revision")}])
    record("delivery_schema_fps", delivery.get("schema") == "autonomous_factory.delivery.v1" and delivery.get("fps") == 24,
           [{"schema": delivery.get("schema"), "fps": delivery.get("fps")}])
    duration = sum(s["seconds"] for s in shots)
    record("delivery_duration_receipt", finite(delivery.get("duration_seconds")) is not None and
           abs(delivery["duration_seconds"] - duration) <= 1 / 48, [{"planned_seconds": duration, "recorded_seconds": delivery.get("duration_seconds")}])
    artifacts = delivery.get("artifacts", [])
    names = [Path(r.get("path", "")).name for r in artifacts]
    record("unique_delivery_artifacts", len(names) == len(set(names)), [{"artifact_count": len(names)}])
    primary = next((r for r in artifacts if Path(r.get("path", "")).name == "episode.mp4"), {})
    record("primary_delivery_receipt", bool(primary) and delivery.get("path") == primary.get("path"), [receipt_evidence(primary)])
    if media_sha is not None:
        try:
            _, actual = check_receipt(root, primary)
        except (ValueError, OSError, KeyError):
            actual = None
        record("media_sha_selector", actual == media_sha, [{"requested": media_sha, "actual": actual, "path": primary.get("path")}])
    if primary.get("path"):
        manifest = source_path(root, str(Path(primary["path"]).parent / "manifest.json"), slug=slug)
        try:
            saved = read_json(manifest)
            record("delivery_manifest_matches_snapshot", saved == delivery,
                   [{"path": manifest.relative_to(root).as_posix(), "sha256": sha256(manifest),
                     "snapshot_signature": fingerprint(delivery), "manifest_signature": fingerprint(saved)}])
        except (ValueError, OSError):
            record("delivery_manifest_matches_snapshot", False, [{"path": manifest.relative_to(root).as_posix(), "error": "Manifest missing or unreadable"}])
    else:
        record("delivery_manifest_matches_snapshot", False, ["No primary delivery manifest"])
    return checks, profile
