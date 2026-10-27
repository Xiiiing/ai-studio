"""Cooperative remote phases on the existing SQLite queue, not a second worker.

A job lease is a monotonically increasing claim generation. Recovery is called
only by the singleton worker after it owns worker.lock. Cloud generation intent
and provider IDs remain in the existing ledger and durable media receipts.
"""
from __future__ import annotations

import math
import time

from . import store

PHASES = frozenset({"remote_submit", "remote_poll", "remote_download", "local_render", "quality_review"})
MAX_REMOTE_INFLIGHT = 2
MAX_POLL_FAILURES = 6


class ProductionDeferred(RuntimeError):
    """A normal durable wait, not an approval, failure, or new generation intent."""
    def __init__(self, next_poll_at, phase="remote_poll", details=None):
        super().__init__("远程任务状态已保存，等待期间执行器可推进其他项目")
        if phase not in PHASES:
            raise ValueError("Invalid deferred production phase")
        if not isinstance(next_poll_at, (int, float)) or not math.isfinite(next_poll_at):
            raise ValueError("Invalid next_poll_at")
        self.next_poll_at = float(next_poll_at)
        self.phase = phase
        self.details = details or {}


def migrate(db):
    """Additive, transactional and repeatable; never rewrite project snapshots."""
    db.execute("BEGIN IMMEDIATE")
    additions = {
        "jobs": {"next_poll_at": "REAL NOT NULL DEFAULT 0", "phase": "TEXT NOT NULL DEFAULT 'local_render'",
                 "lease": "INTEGER NOT NULL DEFAULT 0", "kind": "TEXT NOT NULL DEFAULT 'workflow'"},
        "cloud_tasks": {"next_poll_at": "REAL", "phase": "TEXT NOT NULL DEFAULT 'remote_submit'",
                        "poll_failures": "INTEGER NOT NULL DEFAULT 0"},
    }
    for table, columns in additions.items():
        present = {row["name"] for row in db.execute("PRAGMA table_info(" + table + ")")}
        for name, declaration in columns.items():
            if name not in present:
                db.execute("ALTER TABLE " + table + " ADD COLUMN " + name + " " + declaration)
    db.execute("CREATE INDEX IF NOT EXISTS jobs_due ON jobs(status,next_poll_at,created)")
    db.execute("CREATE TABLE IF NOT EXISTS factory_migrations(name TEXT PRIMARY KEY,applied REAL NOT NULL)")
    db.execute("INSERT OR IGNORE INTO factory_migrations VALUES('remote_phases_v1',?)", (time.time(),))


def phase_for(task):
    if task.get("status") == "downloaded":
        return "quality_review"
    if task.get("file_id"):
        return "remote_download"
    if task.get("task_id"):
        return "remote_poll"
    return "remote_submit"


def settle_wait(project, timestamp=None):
    """Count cloud waiting against cumulative wall time, but never manual pause."""
    timestamp = time.time() if timestamp is None else timestamp
    production = project.setdefault("production", {})
    started = production.pop("remote_wait_started_at", None)
    if isinstance(started, (int, float)) and math.isfinite(started):
        elapsed = max(0.0, timestamp - started)
        if (production.get("remote_wait") or {}).get("wait_kind") == "resources":
            production["resource_wait_seconds"] = round(float(production.get("resource_wait_seconds", 0)) + elapsed, 3)
        else:
            production["elapsed_seconds"] = round(float(production.get("elapsed_seconds", 0)) + elapsed, 3)
            production["remote_wait_seconds"] = round(float(production.get("remote_wait_seconds", 0)) + elapsed, 3)
    production.pop("next_poll_at", None)


def owns_job(job):
    with store.connection() as db:
        row = db.execute("SELECT status,lease FROM jobs WHERE id=?", (job["id"],)).fetchone()
        return bool(row and row["status"] == "running" and row["lease"] == job.get("lease"))


def recover_jobs():
    """Require the process-level worker.lock; no arbitrary lease-timeout repost."""
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("SELECT id,project_id FROM jobs WHERE status='running'").fetchall()
        for row in rows:
            db.execute("UPDATE jobs SET status='queued',started=NULL,lease=lease+1 WHERE id=?", (row["id"],))
            store._event(db, row["project_id"], "执行器恢复：按持久任务编号继续，未知提交不得重投。", "recovery")
        return len(rows)


def claim_job(project_id=None):
    timestamp = time.time()
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("""SELECT j.* FROM jobs j WHERE j.status='queued' AND j.next_poll_at<=?
                             AND NOT EXISTS(SELECT 1 FROM jobs r WHERE r.project_id=j.project_id AND r.status='running')
                             ORDER BY j.next_poll_at,j.created,j.id""", (timestamp,)).fetchall()
        for row in rows:
            if project_id is not None and row["project_id"] != project_id:
                continue
            project = store._read(db, row["project_id"])
            from .review_advice import eligible as advice_eligible
            advice = row["kind"] == "review_advice" and advice_eligible(project)
            delivery_job = row["kind"] == "drama_delivery" and project.get("production_mode") == "one_sentence" and project["status"] not in {"cancelled", "blocked", "failed", "paused"}
            team_command = row["kind"] == "team_instruction" and project.get("team_control_version") == 1 and project["status"] != "cancelled"
            edit_task = (project.get('timeline_edits') or {}).get('task') or {}
            timeline_job = (row['kind'] == 'timeline_edit' and edit_task.get('job_id') == row['id']
                and edit_task.get('status') in {'queued','running'} and not edit_task.get('cancel_requested')
                and project['status'] in {'paused','blocked','awaiting_final','approved'})
            review_task = (project.get('quality_recheck') or {}).get('task') or {}
            review_job = (row['kind'] == 'creative_quality_review' and review_task.get('job_id') == row['id']
                and review_task.get('status') in {'queued','running'} and not review_task.get('cancel_requested')
                and project['status'] in {'paused','blocked','awaiting_final','approved'})
            if row['kind'] == 'creative_quality_review' and not review_job:
                db.execute("UPDATE jobs SET status='cancelled',finished=? WHERE id=?", (timestamp, row['id']))
                continue
            if row['kind'] == 'timeline_edit' and not timeline_job:
                db.execute("UPDATE jobs SET status='cancelled',finished=? WHERE id=?", (timestamp, row['id']))
                continue
            if not (timeline_job or review_job) and not team_command and ((row["kind"] == "review_advice" and not advice) or project.get("pause_requested") or (project["status"] in store.TERMINAL | {"paused", "blocked", "failed"} and not (advice or delivery_job))
                    or project["status"].startswith("awaiting_") and not delivery_job):
                db.execute("UPDATE jobs SET status='cancelled',finished=? WHERE id=?", (timestamp, row["id"]))
                continue
            if not (timeline_job or review_job) and project.get("production", {}).get("remote_wait_started_at") is not None:
                settle_wait(project, timestamp)
                store._write(db, project)
            lease = row["lease"] + 1
            db.execute("UPDATE jobs SET status='running',started=?,lease=? WHERE id=? AND status='queued' AND lease=?",
                       (timestamp, lease, row["id"], row["lease"]))
            return {**dict(row), "status": "running", "started": timestamp, "lease": lease}
    return None


def finish_job(jid, error=None, lease=None):
    with store.connection() as db:
        where = "id=?" + (" AND status='running' AND lease=?" if lease is not None else "")
        values = ["failed" if error else "completed", time.time(), error, jid]
        if lease is not None:
            values.append(lease)
        result = db.execute("UPDATE jobs SET status=?,finished=?,error=? WHERE " + where, values)
        return result.rowcount == 1


def defer_job(job, deferred):
    timestamp = time.time()
    # Keep malformed/clock-skewed provider metadata from spinning or sleeping forever.
    due = min(timestamp + 300, max(timestamp, deferred.next_poll_at))
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        if not current or current["status"] != "running" or current["lease"] != job.get("lease"):
            return False
        project = store._read(db, job["project_id"])
        delivery_job = job.get("kind") == "drama_delivery" and project.get("production_mode") == "one_sentence"
        closed = project["status"] in store.TERMINAL | {"paused", "blocked", "failed"}
        if delivery_job and project["status"] == "approved":
            closed = False  # Individual approval does not cancel an unfinished whole-drama check.
        if project.get("pause_requested") or closed or project["status"].startswith("awaiting_") and not delivery_job:
            db.execute("UPDATE jobs SET status='cancelled',finished=? WHERE id=?", (timestamp, job["id"]))
            if project.get("pause_requested"):
                project["status"] = "paused"
                store._write(db, project)
            return True
        db.execute("UPDATE jobs SET status='queued',started=NULL,next_poll_at=?,phase=?,error=NULL WHERE id=?",
                   (due, deferred.phase, job["id"]))
        production = project.setdefault("production", {})
        production.update(phase=deferred.phase, remote_wait_started_at=timestamp, next_poll_at=due,
                          remote_wait=deferred.details, executor_released=True)
        project.update(status="waiting_remote", error=None)
        store._write(db, project)
        store._event(db, project["id"], "远程等待已持久化，执行器已让出。", "remote_wait",
                     {"phase": deferred.phase, "next_poll_at": due, **deferred.details})
        return True
