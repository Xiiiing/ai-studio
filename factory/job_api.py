"""Version-fenced views/controls over the existing SQLite queue, not a new scheduler."""
from __future__ import annotations

import re

from studio.http_security import HTTPError
from . import store

KNOWN_STATES = frozenset({"queued", "running", "completed", "failed", "cancelled"})
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})


def validate_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise HTTPError(400, "invalid_job_id", "Job IDs must be 32 lowercase hexadecimal characters")
    return value


def _read(db, jid):
    job = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    if job is None:
        raise HTTPError(404, "job_not_found", "Job not found; no control was dispatched")
    return dict(job), store._read(db, job["project_id"])


def _view(db, job, project):
    state = job["status"] if job["status"] in KNOWN_STATES else "unknown"
    if state == "running" and project.get("pause_requested"):
        state = "pause_requested"
    elif state == "cancelled" and project["status"] == "paused":
        state = "paused"
    # Provider status and IDs are observations from the authority ledger, never
    # inferred from the local queue. Reading this endpoint cannot poll or submit.
    remote = [dict(row) for row in db.execute(
        """SELECT intent_id,shot_id,attempt,task_id,file_id,status,phase,next_poll_at
           FROM cloud_tasks WHERE project_id=? ORDER BY created DESC,intent_id LIMIT 101""",
        (job["project_id"],))]
    return {"id": job["id"], "project_id": job["project_id"], "state": state,
            "queue_state": job["status"], "project_state": project["status"],
            "project_revision": project["revision"], "lease": job["lease"],
            "phase": job["phase"], "next_poll_at": job["next_poll_at"],
            "created": job["created"], "started": job["started"], "finished": job["finished"],
            "pause_requested": bool(project.get("pause_requested")),
            "can_pause": job["status"] in {"queued", "running"} and project["status"] not in store.TERMINAL,
            "can_cancel": False, "external_cancellation": "not_supported",
            "remote_tasks": remote[:100], "remote_tasks_truncated": len(remote) > 100}


def snapshot(jid):
    validate_id(jid)
    with store.connection() as db:
        db.execute("BEGIN")
        return _view(db, *_read(db, jid))


def control(jid, operation, payload):
    validate_id(jid)
    if operation not in {"pause", "cancel"} or not isinstance(payload, dict):
        raise HTTPError(400, "invalid_control", "Unsupported job control")
    if (set(payload) != {"expected_revision", "expected_lease"} or
            any(type(v) is not int or v < 0 for v in payload.values())):
        raise HTTPError(400, "invalid_control", "Job control requires integer expected_revision and expected_lease")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        job, project = _read(db, jid)
        result = {"job": _view(db, job, project), "requested": operation,
                  "dispatched": False, "external_cancelled": False}
        if job["status"] in TERMINAL_STATES:
            return result, 200
        if operation == "cancel":
            # Neither killing a process nor deleting a claim is proof that a paid
            # provider task stopped. Do not imitate ComfyUI's local interrupt API.
            return {**result, "error": "Cancellation is not supported; use cooperative pause and reconcile external tasks",
                    "code": "cancel_not_supported"}, 409
        if job["status"] not in {"queued", "running"} or project["status"] in store.TERMINAL:
            raise HTTPError(409, "job_not_controllable", "Job state cannot safely accept pause")
        if payload["expected_lease"] != job["lease"] or payload["expected_revision"] != project["revision"]:
            raise HTTPError(409, "stale_job", "Job or project changed; reload before controlling it")
        if project.get("pause_requested"):
            return result, 200
        store._pause(db, project)
        store._write(db, project)
        return {**result, "dispatched": True, "job": _view(db, *_read(db, jid))}, 202
