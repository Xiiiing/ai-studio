"""Persist shot execution observations with the production checkpoint.

An attempt receipt is execution evidence; a path is only an output reference.
This projection does not schedule work, authorize fees or infer media approval.
"""

from __future__ import annotations

import copy

from . import store


def _source_version(record):
    value = record.get("production_revision")
    return value if type(value) is int and value > 0 else None


def _subtasks(project, patch):
    tasks = {}
    current = project.get("production_revision", 1)
    for shot in patch.get("shots", []):
        if not isinstance(shot, dict):
            continue
        for index, record in enumerate(shot.get("attempts", [])):
            if not isinstance(record, dict) or record.get("stage") not in (
                "image",
                "video",
            ):
                continue
            stage = record["stage"]
            attempt = record.get("take") or str(index + 1)
            version = _source_version(record)
            ident = f"{shot['id']}:{stage}:v{version if version is not None else 'unknown'}:{attempt}"
            state = record.get("state", record.get("status", "unknown"))
            status = {
                "passed": "succeeded",
                "failed": "failed",
                "rejected": "blocked",
                "reviewing": "running",
                "rendering": "running",
                "starting": "running",
                "generated": "waiting",
                "semantic_pending": "waiting",
            }.get(state, state)
            execution_status = status
            status = (
                "unknown"
                if version is None
                else "reused"
                if version != current and status == "succeeded"
                else status
            )
            tasks[ident] = {
                "id": ident,
                "shot_id": shot["id"],
                "production_revision": version,
                "referenced_by_revision": current,
                "execution_status": execution_status,
                "stage": stage,
                "owner": "image_generator" if stage == "image" else "video_generator",
                "reviewer": "visual_critic",
                "attempt": attempt,
                "status": status,
                "source_signature": record.get("source_signature"),
                "started_at": record.get("started_at", record.get("started")),
                "finished_at": record.get("finished_at", record.get("finished")),
                "artifact": record.get("path"),
                "provider_receipt": record.get("provider_receipt"),
                "completion_semantics": "provider_attempt_receipt_not_path_existence",
            }
    return tasks


def _receipt_subtasks(project, patch):
    tasks = {}
    current = project.get("production_revision", 1)
    for shot in patch.get("shots", []):
        if not isinstance(shot, dict) or not shot.get("id"):
            continue
        sid = shot["id"]
        for index, record in enumerate(shot.get("dialogue_repairs", [])):
            if not isinstance(record, dict):
                continue
            attempt = record.get("attempt", index + 1)
            version = _source_version(record)
            ident = f"{sid}:dialogue:v{version if version is not None else 'unknown'}:{attempt}"
            status = {
                "rewriting": "running",
                "applied": "succeeded",
                "failed": "failed",
                "paused": "paused",
            }.get(record.get("state"), "unknown")
            execution_status = status
            status = (
                "unknown"
                if version is None
                else "reused"
                if version != current and status == "succeeded"
                else status
            )
            tasks[ident] = {
                "id": ident,
                "shot_id": sid,
                "production_revision": version,
                "stage": "dialogue",
                "referenced_by_revision": current,
                "execution_status": execution_status,
                "owner": "dialogue_editor",
                "reviewer": "speech",
                "attempt": attempt,
                "status": status,
                "started_at": record.get("started_at"),
                "finished_at": record.get("finished_at"),
                "completion_semantics": "dialogue_rewrite_receipt_not_story_approval",
            }
        for index, record in enumerate(shot.get("attempts", [])):
            if not isinstance(record, dict) or record.get("stage") not in {
                "image",
                "video",
            }:
                continue
            stage = record["stage"]
            version = _source_version(record)
            take = record.get("take") or str(index + 1)
            for number, review in enumerate(record.get("semantic_attempts", []), 1):
                assessment = review.get("assessment", {})
                ident = f"{sid}:{stage}_review:v{version if version is not None else 'unknown'}:{take}:{number}"
                finished = review.get("at")
                elapsed = review.get("elapsed_seconds")
                started = (
                    finished - elapsed
                    if isinstance(finished, (int, float))
                    and isinstance(elapsed, (int, float))
                    else None
                )
                tasks[ident] = {
                    "id": ident,
                    "shot_id": sid,
                    "production_revision": version,
                    "stage": stage + "_review",
                    "owner": "visual_critic",
                    "reviewer": "producer",
                    "attempt": number,
                    "media_attempt": take,
                    "referenced_by_revision": current,
                    "status": "unknown"
                    if version is None
                    else (
                        "reused"
                        if version != current and assessment.get("status") == "pass"
                        else {"pass": "succeeded", "fail": "blocked"}.get(
                            assessment.get("status"), "waiting"
                        )
                    ),
                    "started_at": started,
                    "finished_at": finished,
                    "assessment": copy.deepcopy(assessment),
                    "completion_semantics": "explicit_semantic_assessment_receipt",
                }
    for line in patch.get("assets", {}).get("audio", {}).get("lines", []):
        if (
            not isinstance(line, dict)
            or not line.get("id")
            or not line.get("source_signature")
            or not line.get("metadata_path")
        ):
            continue
        version = _source_version(line)
        ident = f"{line['id']}:speech:v{version if version is not None else 'unknown'}:{line['source_signature'][:16]}"
        tasks[ident] = {
            "id": ident,
            "shot_id": line["id"].split("_", 1)[0],
            "line_id": line["id"],
            "production_revision": version,
            "stage": "speech",
            "owner": "speech",
            "reviewer": "dialogue_editor",
            "attempt": 1,
            "status": "succeeded" if version == current else "reused",
            "referenced_by_revision": current,
            "artifact": line.get("path"),
            "provider_receipt": line["metadata_path"],
            "source_signature": line["source_signature"],
            "completion_semantics": "validated_per_line_speech_source_receipt",
        }
    return tasks


PHASE_TASKS = {
    "voice": ("speech", "speech", "dialogue_editor"),
    "voice_rendering": ("speech", "speech", "dialogue_editor"),
    "voice_review": ("speech", "speech", "dialogue_editor"),
    "voice_complete": ("speech", "speech", "dialogue_editor"),
    "dialogue_timing_review": ("dialogue", "dialogue_editor", "speech"),
    "dialogue_rewriting": ("dialogue", "dialogue_editor", "speech"),
    "dialogue_repaired": ("dialogue", "dialogue_editor", "speech"),
    "dialogue_rewrite_failed": ("dialogue", "dialogue_editor", "speech"),
    "quality_review": ("media_review", "visual_critic", "producer"),
    "semantic_review_pending": ("media_review", "visual_critic", "producer"),
    "camera_motion": ("camera_motion", "editor", "visual_critic"),
    "sound_edit": ("sound_edit", "editor", "producer"),
    "lip_sync": ("lip_sync", "editor", "visual_critic"),
    "lip_sync_review": ("lip_sync_review", "visual_critic", "producer"),
    "edit": ("edit", "editor", "producer"),
    "final_technical_review": ("technical_review", "editor", "producer"),
    "delivery_ready": ("technical_review", "editor", "producer"),
}


def _phase_task(project, patch, previous):
    if "production" not in patch:
        return {}, None
    production = project.get("production", {})
    phase = production.get("phase")
    if phase not in PHASE_TASKS:
        return {}, None
    stage, owner, reviewer = PHASE_TASKS[phase]
    version = project.get("production_revision", 1)
    source_id = f"production:{stage}:v{version}"
    old_id = project.get("production_observed_task")
    old = previous.get(old_id, {})
    # A repeated checkpoint within a phase updates its existing attempt; an
    # actual later re-entry starts a distinct observational attempt.
    if (
        old_id
        and old_id.startswith(source_id + ":")
        and (
            old.get("phase") == phase
            or old.get("status") in {"running", "waiting", "blocked"}
        )
    ):
        ident, attempt = old_id, old["attempt"]
    else:
        attempts = [
            row.get("attempt", 0)
            for row in previous.values()
            if row.get("task_id") == source_id
        ]
        attempt = max(attempts, default=0) + 1
        ident = source_id + ":" + str(attempt)
        old = {}
    status = (
        "succeeded"
        if phase in {"voice_complete", "dialogue_repaired", "delivery_ready"}
        else (
            "waiting"
            if phase == "semantic_review_pending"
            else "blocked"
            if phase == "dialogue_rewrite_failed"
            else "running"
        )
    )
    row = {
        "id": ident,
        "task_id": source_id,
        "shot_id": None,
        "production_revision": version,
        "stage": stage,
        "phase": phase,
        "owner": owner,
        "reviewer": reviewer,
        "attempt": attempt,
        "status": status,
        "started_at": old.get("started_at") or store.now(),
        "finished_at": (old.get("finished_at") or store.now())
        if status in {"succeeded", "blocked"}
        else None,
        "completion_semantics": "explicit_runtime_phase_complete_not_quality_approval",
        "voice_attempts": list(production.get("voice_attempts", [])),
        "artifact": (patch.get("delivery") or {}).get("manifest_url")
        if phase == "delivery_ready"
        else None,
    }
    result = {ident: row}
    if (
        old_id
        and old_id != ident
        and previous.get(old_id, {}).get("status") == "running"
    ):
        # A phase transition alone is evidence of a handoff, not a QA pass.
        result[old_id] = {
            **previous[old_id],
            "status": "finished",
            "finished_at": store.now(),
            "completion_semantics": "observed_runtime_handoff_not_quality_approval",
        }
    return result, ident


def checkpoint(pid, patch):
    if "shots" not in patch and "production" not in patch:
        return store.update(pid, patch)
    store.update(pid, patch)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        project = store._read(db, pid)
        previous = project.get("production_tasks", {})
        tasks = _subtasks(project, patch)
        tasks.update(_receipt_subtasks(project, patch))
        phase_tasks, active = _phase_task(project, patch, previous)
        tasks.update(phase_tasks)
        for ident, row in tasks.items():
            old = previous.get(ident, {})
            if (
                old.get("status") != row.get("status")
                or old.get("phase") != row.get("phase")
                or not old
            ):
                store._event(
                    db,
                    pid,
                    f"{row['shot_id'] or '制作'} · {row['stage']} · {row['status']}",
                    "production_task",
                    row,
                )
        project["production_tasks"] = {**previous, **tasks}
        if active:
            project["production_observed_task"] = active
        store._write(db, project)
        return copy.deepcopy(project)
