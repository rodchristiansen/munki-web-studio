"""Jobs live in the studio's state database, so a closed browser or a restarted
server never loses one.

A worker claims a queued job and holds a lease on it, renewed by heartbeats. A
lease that runs out puts the job back in the queue for another attempt, up to a
fixed number of attempts. Only the worker holding the current lease can finish a
job, and a job finishes once, so a retried attempt can never record a second
result. Submitting the same idempotency key twice returns the first job.
"""

import datetime as dt
import hashlib
import json
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    arguments TEXT NOT NULL,
    input_revision TEXT NOT NULL,
    requirements TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    timeout_seconds INTEGER NOT NULL,
    lease_owner TEXT,
    lease_expires_at REAL,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    progress REAL NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    result TEXT,
    result_hash TEXT,
    error TEXT,
    created_by TEXT NOT NULL,
    created_by_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at REAL,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE (repo_id, operation, idempotency_key)
);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs (status, created_at);
CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    progress REAL
);
CREATE INDEX IF NOT EXISTS job_events_by_job ON job_events (job_id, id);
-- A finished job's outcome never changes.
CREATE TRIGGER IF NOT EXISTS jobs_final BEFORE UPDATE ON jobs
WHEN OLD.status IN ('succeeded', 'failed', 'cancelled')
BEGIN
    SELECT RAISE(ABORT, 'finished jobs cannot change');
END;
"""

TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


class LeaseLostError(Exception):
    """This worker no longer holds the job: it timed out, or another took it."""


@dataclass(frozen=True)
class Job:
    id: str
    repo_id: str
    operation: str
    arguments: dict[str, Any]
    input_revision: str
    requirements: list[str]
    idempotency_key: str
    status: str
    attempts: int
    max_attempts: int
    timeout_seconds: int
    lease_owner: str | None
    lease_expires_at: float | None
    cancel_requested: bool
    progress: float
    message: str
    result: Any
    result_hash: str | None
    error: str | None
    created_by: str
    created_by_name: str
    created_at: str
    updated_at: str
    finished_at: str | None

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL


@dataclass(frozen=True)
class JobEvent:
    id: int
    job_id: str
    at: str
    kind: str
    message: str
    progress: float | None


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def result_hash(result: Any) -> str:
    return hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()


def _job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        repo_id=row["repo_id"],
        operation=row["operation"],
        arguments=json.loads(row["arguments"]),
        input_revision=row["input_revision"],
        requirements=json.loads(row["requirements"]),
        idempotency_key=row["idempotency_key"],
        status=row["status"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        timeout_seconds=row["timeout_seconds"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        cancel_requested=bool(row["cancel_requested"]),
        progress=row["progress"],
        message=row["message"],
        result=json.loads(row["result"]) if row["result"] is not None else None,
        result_hash=row["result_hash"],
        error=row["error"],
        created_by=row["created_by"],
        created_by_name=row["created_by_name"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        finished_at=row["finished_at"],
    )


class JobStore:
    def __init__(self, path: Path, clock: Any = None) -> None:
        import time

        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=30, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._clock = clock or time.time
        self._changed = threading.Condition()
        with self._lock:
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    # Waiting for change, for progress streams.

    def _notify(self) -> None:
        with self._changed:
            self._changed.notify_all()

    def wait(self, timeout: float) -> None:
        """Block until any job changes, or `timeout` seconds pass."""
        with self._changed:
            self._changed.wait(timeout)

    def _event(self, job_id: str, kind: str, message: str, progress: float | None = None) -> None:
        self._db.execute(
            "INSERT INTO job_events (job_id, at, kind, message, progress) VALUES (?, ?, ?, ?, ?)",
            (job_id, _now(), kind, message, progress),
        )

    def _write(self, sql: str, args: tuple[Any, ...]) -> int:
        return self._db.execute(sql, args).rowcount

    # Submitting and reading.

    def submit(
        self,
        *,
        repo_id: str,
        operation: str,
        arguments: dict[str, Any],
        input_revision: str,
        requirements: list[str],
        created_by: str,
        created_by_name: str,
        idempotency_key: str | None = None,
        max_attempts: int = 3,
        timeout_seconds: int = 600,
    ) -> tuple[Job, bool]:
        """Queue a job. Returns the job and whether it is new; the same idempotency
        key for the same operation returns the job it first created."""
        key = idempotency_key or secrets.token_hex(16)
        job_id = secrets.token_hex(8)
        now = _now()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                existing = self._db.execute(
                    "SELECT * FROM jobs "
                    "WHERE repo_id = ? AND operation = ? AND idempotency_key = ?",
                    (repo_id, operation, key),
                ).fetchone()
                if existing is not None:
                    self._db.execute("COMMIT")
                    return _job(existing), False
                self._db.execute(
                    "INSERT INTO jobs (id, repo_id, operation, arguments, input_revision, "
                    "requirements, idempotency_key, status, max_attempts, timeout_seconds, "
                    "created_by, created_by_name, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        repo_id,
                        operation,
                        json.dumps(arguments, sort_keys=True),
                        input_revision,
                        json.dumps(requirements),
                        key,
                        max_attempts,
                        timeout_seconds,
                        created_by,
                        created_by_name,
                        now,
                        now,
                    ),
                )
                self._event(job_id, "queued", f"Queued by {created_by_name}")
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        self._notify()
        job = self._must(job_id)
        return job, True

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(row) if row else None

    def _must(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job is None:
            raise RuntimeError(f"Job {job_id} vanished")
        return job

    def list(self, repo_id: str, *, limit: int = 50, before: str | None = None) -> list[Job]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM jobs WHERE repo_id = ? AND (? IS NULL OR created_at < ?) "
                "ORDER BY created_at DESC LIMIT ?",
                (repo_id, before, before, limit),
            ).fetchall()
        return [_job(r) for r in rows]

    def events(self, job_id: str, after: int = 0) -> list[JobEvent]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM job_events WHERE job_id = ? AND id > ? ORDER BY id",
                (job_id, after),
            ).fetchall()
        return [
            JobEvent(r["id"], r["job_id"], r["at"], r["kind"], r["message"], r["progress"])
            for r in rows
        ]

    # The worker's side.

    def _expire(self) -> None:
        """Requeue running jobs whose lease ran out, or fail them when out of attempts."""
        now = self._clock()
        stale = self._db.execute(
            "SELECT id, attempts, max_attempts, cancel_requested FROM jobs "
            "WHERE status = 'running' AND lease_expires_at < ?",
            (now,),
        ).fetchall()
        for row in stale:
            if row["cancel_requested"]:
                self._db.execute(
                    "UPDATE jobs SET status = 'cancelled', error = 'Cancelled', "
                    "lease_owner = NULL, finished_at = ?, updated_at = ? WHERE id = ?",
                    (_now(), _now(), row["id"]),
                )
                self._event(row["id"], "cancelled", "Cancelled")
            elif row["attempts"] >= row["max_attempts"]:
                self._db.execute(
                    "UPDATE jobs SET status = 'failed', error = ?, lease_owner = NULL, "
                    "finished_at = ?, updated_at = ? WHERE id = ?",
                    (
                        "The worker stopped responding on the last attempt",
                        _now(),
                        _now(),
                        row["id"],
                    ),
                )
                self._event(row["id"], "failed", "The worker stopped responding; no attempts left")
            else:
                self._db.execute(
                    "UPDATE jobs SET status = 'queued', lease_owner = NULL, "
                    "lease_expires_at = NULL, updated_at = ? WHERE id = ?",
                    (_now(), row["id"]),
                )
                self._event(row["id"], "requeued", "The worker stopped responding; trying again")

    def claim(self, worker: str, operations: list[str], lease_seconds: float = 30) -> Job | None:
        """Take the oldest queued job this worker can run, with a lease on it."""
        if not operations:
            return None
        marks = ",".join("?" for _ in operations)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._expire()
                row = self._db.execute(
                    f"SELECT id, timeout_seconds FROM jobs WHERE status = 'queued' "  # noqa: S608
                    f"AND cancel_requested = 0 AND operation IN ({marks}) "
                    f"ORDER BY created_at LIMIT 1",
                    tuple(operations),
                ).fetchone()
                if row is None:
                    self._db.execute("COMMIT")
                    return None
                now = self._clock()
                self._db.execute(
                    "UPDATE jobs SET status = 'running', attempts = attempts + 1, "
                    "lease_owner = ?, lease_expires_at = ?, started_at = ?, updated_at = ? "
                    "WHERE id = ?",
                    (worker, now + lease_seconds, now, _now(), row["id"]),
                )
                self._event(row["id"], "started", f"Started on {worker}")
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        self._notify()
        return self.get(row["id"])

    def _owned(self, job_id: str, worker: str) -> sqlite3.Row:
        row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if (
            row is None
            or row["status"] != "running"
            or row["lease_owner"] != worker
            or row["lease_expires_at"] < self._clock()
        ):
            raise LeaseLostError(f"{worker} no longer holds job {job_id}")
        return row

    def heartbeat(
        self,
        job_id: str,
        worker: str,
        *,
        progress: float | None = None,
        message: str | None = None,
        lease_seconds: float = 30,
    ) -> bool:
        """Renew the lease and report progress. Returns True when cancellation was
        asked for, so the worker can stop."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._owned(job_id, worker)
                now = self._clock()
                if (
                    row["started_at"] is not None
                    and now - row["started_at"] > row["timeout_seconds"]
                ):
                    raise LeaseLostError(f"Job {job_id} ran past its timeout")
                self._db.execute(
                    "UPDATE jobs SET lease_expires_at = ?, progress = COALESCE(?, progress), "
                    "message = COALESCE(?, message), updated_at = ? WHERE id = ?",
                    (now + lease_seconds, progress, message, _now(), job_id),
                )
                if message is not None or progress is not None:
                    self._event(job_id, "progress", message or "", progress)
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        self._notify()
        return bool(row["cancel_requested"])

    def complete(self, job_id: str, worker: str, result: Any) -> Job:
        """Record the result. Only the lease holder can, and only once."""
        return self._finish(job_id, worker, "succeeded", result=result)

    def fail(self, job_id: str, worker: str, error: str, *, retryable: bool = False) -> Job:
        """Record a failure; a retryable one goes back in the queue while attempts remain."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._owned(job_id, worker)
                if (
                    retryable
                    and row["attempts"] < row["max_attempts"]
                    and not row["cancel_requested"]
                ):
                    self._db.execute(
                        "UPDATE jobs SET status = 'queued', lease_owner = NULL, "
                        "lease_expires_at = NULL, error = ?, updated_at = ? WHERE id = ?",
                        (error, _now(), job_id),
                    )
                    self._event(job_id, "requeued", f"{error}; trying again")
                    self._db.execute("COMMIT")
                    requeued = True
                else:
                    self._db.execute("ROLLBACK")
                    requeued = False
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        if requeued:
            self._notify()
            job = self._must(job_id)
            return job
        return self._finish(job_id, worker, "failed", error=error)

    def cancelled(self, job_id: str, worker: str) -> Job:
        """The worker stopped because cancellation was asked for."""
        return self._finish(job_id, worker, "cancelled", error="Cancelled")

    def _finish(
        self, job_id: str, worker: str, status: str, *, result: Any = None, error: str | None = None
    ) -> Job:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._owned(job_id, worker)
                encoded = json.dumps(result, sort_keys=True) if status == "succeeded" else None
                self._db.execute(
                    "UPDATE jobs SET status = ?, result = ?, result_hash = ?, error = ?, "
                    "progress = CASE WHEN ? = 'succeeded' THEN 1 ELSE progress END, "
                    "lease_owner = NULL, finished_at = ?, updated_at = ? WHERE id = ?",
                    (
                        status,
                        encoded,
                        result_hash(result) if encoded is not None else None,
                        error,
                        status,
                        _now(),
                        _now(),
                        job_id,
                    ),
                )
                label = {
                    "succeeded": "Finished",
                    "failed": f"Failed: {error}",
                    "cancelled": "Cancelled",
                }
                self._event(job_id, status, label[status], 1.0 if status == "succeeded" else None)
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        self._notify()
        job = self._must(job_id)
        return job

    # The person's side.

    def cancel(self, job_id: str, by: str) -> Job | None:
        """Cancel a queued job at once, or ask a running one to stop."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
                if row is None:
                    self._db.execute("ROLLBACK")
                    return None
                if row["status"] == "queued":
                    self._db.execute(
                        "UPDATE jobs SET status = 'cancelled', cancel_requested = 1, "
                        "error = 'Cancelled', finished_at = ?, updated_at = ? WHERE id = ?",
                        (_now(), _now(), job_id),
                    )
                    self._event(job_id, "cancelled", f"Cancelled by {by}")
                elif row["status"] == "running":
                    self._db.execute(
                        "UPDATE jobs SET cancel_requested = 1, updated_at = ? WHERE id = ?",
                        (_now(), job_id),
                    )
                    self._event(job_id, "cancel-requested", f"{by} asked to cancel")
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        self._notify()
        return self.get(job_id)
