"""Running jobs: the operations this studio knows, and the in-process worker that
runs the ones needing nothing beyond the studio itself.

Operations that need platform tools (makecatalogs, installer inspection) are run
by a worker that declares those capabilities; until one does, they are refused
at submission rather than queued to wait forever.
"""

import hashlib
import logging
import secrets
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from munki_web_studio.jobs.store import Job, JobStore, LeaseLostError
from munki_web_studio.repository.config import Repository, Settings
from munki_web_studio.repository.index import Index
from munki_web_studio.repository.references import validate
from munki_web_studio.repository.scanner import scan

logger = logging.getLogger(__name__)

# What the in-process worker can do: anything that only reads and indexes files.
PORTABLE = "portable"


class CancelledError(Exception):
    """Cancellation was asked for; the operation should stop."""


class JobFailedError(Exception):
    """An operation failed in a way another attempt could fix, or could not."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def repository_revision(index: Index, repo: Repository) -> str:
    """One hash over every file's path and revision: the state a job ran against."""
    digest = hashlib.sha256()
    for obj in sorted(index.all(repo.id), key=lambda o: o.path):
        digest.update(f"{obj.path}\0{obj.revision}\n".encode())
    return digest.hexdigest()


@dataclass
class JobContext:
    job: Job
    repo: Repository
    index: Index
    _cancel: threading.Event
    _report: Callable[[float | None, str | None], None]

    def progress(self, fraction: float | None = None, message: str | None = None) -> None:
        """Report progress, and stop here if cancellation was asked for."""
        self._report(fraction, message)
        self.check()

    def check(self) -> None:
        if self._cancel.is_set():
            raise CancelledError


@dataclass(frozen=True)
class Operation:
    name: str
    title: str
    run: Callable[[JobContext], Any]
    requirements: tuple[str, ...] = (PORTABLE,)
    max_attempts: int = 3
    timeout_seconds: int = 600


def _validate(ctx: JobContext) -> dict[str, Any]:
    ctx.progress(0.1, "Checking references, includes and conditions")
    issues = validate(ctx.index, ctx.repo)
    errors = sum(1 for i in issues if i.severity == "error")
    ctx.progress(0.9, f"{errors} errors, {len(issues) - errors} warnings")
    return {
        "errors": errors,
        "warnings": len(issues) - errors,
        "input_revision": ctx.job.input_revision,
        "checked_revision": repository_revision(ctx.index, ctx.repo),
    }


def _rescan(ctx: JobContext) -> dict[str, Any]:
    ctx.progress(0.1, "Reading every file again")
    result = scan(ctx.repo, ctx.index, full=True)
    return {
        "files": result.files,
        "read": result.read,
        "removed": result.removed,
        "invalid": result.invalid,
        "revision": repository_revision(ctx.index, ctx.repo),
    }


OPERATIONS: dict[str, Operation] = {
    op.name: op
    for op in (
        Operation("repository.validate", "Validate the repository", _validate),
        Operation("repository.rescan", "Re-read every file", _rescan, max_attempts=2),
    )
}


class Runner:
    """Claims and runs jobs on background threads, so they finish whether or not
    anyone is still watching."""

    def __init__(
        self,
        store: JobStore,
        settings: Settings,
        index: Index,
        *,
        operations: dict[str, Operation] | None = None,
        capabilities: frozenset[str] = frozenset({PORTABLE}),
        threads: int = 2,
        lease_seconds: float = 30,
        poll_seconds: float = 1,
    ) -> None:
        self.store = store
        self.settings = settings
        self.index = index
        self.operations = operations if operations is not None else OPERATIONS
        self.capabilities = capabilities
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self.name = f"{socket.gethostname()}/studio-{secrets.token_hex(3)}"
        self._threads = threads
        self._stop = threading.Event()
        self._workers: list[threading.Thread] = []

    def runnable(self) -> list[str]:
        return [
            name
            for name, op in self.operations.items()
            if set(op.requirements) <= self.capabilities
        ]

    def start(self) -> None:
        for n in range(self._threads):
            thread = threading.Thread(
                target=self._loop, args=(f"{self.name}#{n}",), name=f"job-runner-{n}", daemon=True
            )
            thread.start()
            self._workers.append(thread)

    def stop(self, timeout: float = 5) -> None:
        self._stop.set()
        for thread in self._workers:
            thread.join(timeout)

    def _loop(self, worker: str) -> None:
        while not self._stop.is_set():
            try:
                if not self.run_once(worker):
                    self._stop.wait(self.poll_seconds)
            except Exception:
                logger.exception("Job runner %s hit an error", worker)
                self._stop.wait(self.poll_seconds)

    def run_once(self, worker: str) -> bool:
        """Run one job if one is waiting. Returns whether it found one."""
        job = self.store.claim(worker, self.runnable(), self.lease_seconds)
        if job is None:
            return False
        self._run(job, worker)
        return True

    def _run(self, job: Job, worker: str) -> None:
        operation = self.operations[job.operation]
        try:
            repo = self.settings.repository(job.repo_id)
        except KeyError:
            self.store.fail(job.id, worker, "The repository is no longer configured")
            return
        cancel = threading.Event()
        lost = threading.Event()
        done = threading.Event()
        pending: dict[str, Any] = {}
        guard = threading.Lock()

        def report(fraction: float | None, message: str | None) -> None:
            with guard:
                if fraction is not None:
                    pending["progress"] = fraction
                if message is not None:
                    pending["message"] = message
            beat()

        def beat() -> None:
            with guard:
                update = dict(pending)
                pending.clear()
            try:
                if self.store.heartbeat(job.id, worker, lease_seconds=self.lease_seconds, **update):
                    cancel.set()
            except LeaseLostError:
                lost.set()
                cancel.set()

        def keep_alive() -> None:
            # Often enough that a cancel takes effect within about a second.
            while not done.wait(min(self.lease_seconds / 3, 1.0)):
                beat()

        pulse = threading.Thread(target=keep_alive, daemon=True)
        pulse.start()
        context = JobContext(job, repo, self.index, cancel, report)
        outcome: tuple[str, Any, bool]
        try:
            result = operation.run(context)
            context.check()
            outcome = ("succeeded", result, False)
        except CancelledError:
            outcome = ("cancelled", None, False)
        except JobFailedError as failure:
            outcome = ("failed", str(failure), failure.retryable)
        except Exception as failure:
            logger.exception("Job %s (%s) failed", job.id, job.operation)
            outcome = ("failed", f"{type(failure).__name__}: {failure}", False)
        finally:
            done.set()
            pulse.join()
        status, value, retryable = outcome
        try:
            if lost.is_set():
                raise LeaseLostError(job.id)
            if status == "succeeded":
                self.store.complete(job.id, worker, value)
            elif status == "cancelled":
                self.store.cancelled(job.id, worker)
            else:
                self.store.fail(job.id, worker, str(value), retryable=retryable)
        except LeaseLostError:
            # Another attempt owns the job now, so this attempt's outcome is dropped.
            logger.warning("Dropped the outcome of job %s after its lease was lost", job.id)
