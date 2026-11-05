"""Durable jobs: leases, retries, cancellation, idempotency, and results that are
recorded exactly once."""

import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.fixtures.build_repo_fixture import REPO

from munki_web_studio.app import create_app
from munki_web_studio.jobs.runner import OPERATIONS, JobContext, Operation, Runner
from munki_web_studio.jobs.store import JobStore, LeaseLostError
from munki_web_studio.repository.config import Repository, Settings


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path: Path, clock: Clock) -> Iterator[JobStore]:
    s = JobStore(tmp_path / "jobs.sqlite", clock=clock)
    yield s
    s.close()


def submit(store: JobStore, key: str | None = None, **options: Any) -> Any:
    job, _ = store.submit(
        repo_id="r",
        operation="op",
        arguments={},
        input_revision="rev",
        requirements=["portable"],
        created_by="u",
        created_by_name="User",
        idempotency_key=key,
        **options,
    )
    return job


def test_same_idempotency_key_returns_the_first_job(store: JobStore) -> None:
    first, created = store.submit(
        repo_id="r",
        operation="op",
        arguments={},
        input_revision="rev",
        requirements=[],
        created_by="u",
        created_by_name="U",
        idempotency_key="k",
    )
    again, created_again = store.submit(
        repo_id="r",
        operation="op",
        arguments={},
        input_revision="other",
        requirements=[],
        created_by="u",
        created_by_name="U",
        idempotency_key="k",
    )
    assert created and not created_again
    assert again.id == first.id and again.input_revision == "rev"
    assert submit(store).id != submit(store).id


def test_a_result_is_recorded_once(store: JobStore) -> None:
    job = submit(store)
    claimed = store.claim("w1", ["op"])
    assert claimed is not None and claimed.id == job.id and claimed.attempts == 1
    done = store.complete(job.id, "w1", {"n": 1})
    assert done.status == "succeeded" and done.result == {"n": 1} and done.result_hash
    with pytest.raises(LeaseLostError):
        store.complete(job.id, "w1", {"n": 2})
    with pytest.raises(LeaseLostError):
        store.fail(job.id, "w1", "late")
    final = store.get(job.id)
    assert final is not None and final.result == {"n": 1} and final.status == "succeeded"


def test_finished_jobs_cannot_be_changed_even_directly(store: JobStore, tmp_path: Path) -> None:
    job = submit(store)
    store.claim("w", ["op"])
    store.complete(job.id, "w", 1)
    db = sqlite3.connect(tmp_path / "jobs.sqlite")
    with pytest.raises(sqlite3.IntegrityError, match="finished jobs cannot change"):
        db.execute("UPDATE jobs SET result = '2' WHERE id = ?", (job.id,))
    db.close()


def test_retry_after_a_lost_lease_cannot_duplicate_the_result(
    store: JobStore, clock: Clock
) -> None:
    job = submit(store)
    store.claim("slow", ["op"], lease_seconds=30)
    clock.now += 31
    retry = store.claim("fresh", ["op"], lease_seconds=30)
    assert retry is not None and retry.id == job.id and retry.attempts == 2
    with pytest.raises(LeaseLostError):
        store.heartbeat(job.id, "slow")
    with pytest.raises(LeaseLostError):
        store.complete(job.id, "slow", "from the stale attempt")
    done = store.complete(job.id, "fresh", "from the retry")
    assert done.result == "from the retry"
    kinds = [e.kind for e in store.events(job.id)]
    assert kinds == ["queued", "started", "requeued", "started", "succeeded"]


def test_attempts_are_bounded(store: JobStore, clock: Clock) -> None:
    job = submit(store, max_attempts=2)
    for _ in range(2):
        assert store.claim("w", ["op"], lease_seconds=5) is not None
        clock.now += 6
    assert store.claim("w", ["op"]) is None
    failed = store.get(job.id)
    assert failed is not None and failed.status == "failed" and failed.attempts == 2


def test_retryable_failures_requeue_and_others_do_not(store: JobStore) -> None:
    job = submit(store, max_attempts=2)
    store.claim("w", ["op"])
    assert store.fail(job.id, "w", "network", retryable=True).status == "queued"
    store.claim("w", ["op"])
    assert store.fail(job.id, "w", "network", retryable=True).status == "failed"
    other = submit(store)
    store.claim("w", ["op"])
    assert store.fail(other.id, "w", "bad input").status == "failed"


def test_heartbeats_keep_a_lease_and_enforce_the_timeout(store: JobStore, clock: Clock) -> None:
    job = submit(store, timeout_seconds=100)
    store.claim("w", ["op"], lease_seconds=30)
    for _ in range(3):
        clock.now += 25
        store.heartbeat(job.id, "w", progress=0.5, message="halfway", lease_seconds=30)
    current = store.get(job.id)
    assert current is not None and current.status == "running" and current.message == "halfway"
    clock.now += 26
    with pytest.raises(LeaseLostError, match="timeout"):
        store.heartbeat(job.id, "w")


def test_cancel(store: JobStore, clock: Clock) -> None:
    queued = submit(store)
    cancelled = store.cancel(queued.id, "User")
    assert cancelled is not None and cancelled.status == "cancelled"
    assert store.claim("w", ["op"]) is None
    running = submit(store)
    store.claim("w", ["op"])
    assert store.cancel(running.id, "User").status == "running"  # type: ignore[union-attr]
    assert store.heartbeat(running.id, "w") is True
    assert store.cancelled(running.id, "w").status == "cancelled"
    # A worker that dies after cancellation was asked for does not get the job back.
    third = submit(store)
    store.claim("w", ["op"], lease_seconds=5)
    store.cancel(third.id, "User")
    clock.now += 6
    assert store.claim("w", ["op"]) is None
    gone = store.get(third.id)
    assert gone is not None and gone.status == "cancelled"


# The studio: jobs run in the background whoever is watching.

GATE = threading.Event()


def _gated(ctx: JobContext) -> dict[str, Any]:
    ctx.progress(0.2, "Waiting for the gate")
    while not GATE.wait(0.05):
        ctx.check()
    ctx.progress(0.9, "Through")
    return {"input": ctx.job.input_revision}


def _settings(tmp_path: Path) -> Settings:
    repo = Repository(id="fixture", name="Fixture", root=REPO.resolve())
    return Settings(repositories=(repo,), state_dir=tmp_path / "state", rescan_seconds=0)


@pytest.fixture
def studio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    GATE.clear()
    operations = OPERATIONS | {
        "test.gated": Operation("test.gated", "Gated test job", _gated),
        "test.mac": Operation("test.mac", "Needs a Mac", _gated, requirements=("macos",)),
    }
    monkeypatch.setattr("munki_web_studio.jobs.runner.OPERATIONS", operations)
    monkeypatch.setattr("munki_web_studio.api.jobs.KEEPALIVE_SECONDS", 0.2)
    app = create_app(_settings(tmp_path), static_dir=tmp_path / "none")
    with TestClient(app, base_url="http://localhost") as client:
        app.state.runner.poll_seconds = 0.05
        client.headers["X-CSRF-Token"] = client.get("/api/session").json()["csrf_token"]
        yield client
    GATE.set()


def wait_for(client: TestClient, job_id: str, status: str, seconds: float = 10) -> dict[str, Any]:
    deadline = time.time() + seconds
    job: dict[str, Any] = {}
    while time.time() < deadline:
        job = client.get(f"/api/repositories/fixture/jobs/{job_id}").json()
        if job["status"] == status:
            return job
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} never reached {status}: {job}")


def start(client: TestClient, operation: str, **body: Any) -> Any:
    return client.post("/api/repositories/fixture/jobs", json={"operation": operation, **body})


def test_validate_job_runs_and_records_its_input_revision(studio: TestClient) -> None:
    created = start(studio, "repository.validate")
    assert created.status_code == 201
    job = wait_for(studio, created.json()["id"], "succeeded")
    assert job["result"]["errors"] > 0
    assert job["result"]["input_revision"] == job["input_revision"]
    listed = studio.get("/api/repositories/fixture/jobs").json()
    assert listed[0]["id"] == job["id"] and listed[0]["title"] == "Validate the repository"


def test_browser_disconnect_does_not_lose_a_job(studio: TestClient) -> None:
    job_id = start(studio, "test.gated").json()["id"]
    wait_for(studio, job_id, "running")
    with studio.stream("GET", f"/api/repositories/fixture/jobs/{job_id}/events") as stream:
        lines = stream.iter_lines()
        first = [next(lines) for _ in range(3)]
    assert first[1] == "event: queued"
    GATE.set()
    job = wait_for(studio, job_id, "succeeded")
    assert job["progress"] == 1 and job["result"]["input"] == job["input_revision"]


def test_events_stream_to_the_end_and_resume_from_an_id(studio: TestClient) -> None:
    GATE.set()
    job_id = start(studio, "test.gated").json()["id"]
    wait_for(studio, job_id, "succeeded")
    body = studio.get(f"/api/repositories/fixture/jobs/{job_id}/events").text
    kinds = [line.split(": ", 1)[1] for line in body.splitlines() if line.startswith("event:")]
    assert kinds[0] == "queued" and kinds[-2:] == ["succeeded", "end"]
    later = studio.get(
        f"/api/repositories/fixture/jobs/{job_id}/events", headers={"Last-Event-ID": "2"}
    ).text
    assert "event: queued" not in later and "event: end" in later
    polled = studio.get(f"/api/repositories/fixture/jobs/{job_id}", params={"after": 2}).json()
    assert polled["events"] and all(e["id"] > 2 for e in polled["events"])


def test_same_key_twice_starts_one_job(studio: TestClient) -> None:
    GATE.set()
    first = start(studio, "test.gated", idempotency_key="click-1")
    second = start(studio, "test.gated", idempotency_key="click-1")
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]


def test_cancel_a_running_job(studio: TestClient) -> None:
    job_id = start(studio, "test.gated").json()["id"]
    wait_for(studio, job_id, "running")
    assert studio.post(f"/api/repositories/fixture/jobs/{job_id}/cancel").status_code == 200
    job = wait_for(studio, job_id, "cancelled")
    assert job["result"] is None
    assert studio.post(f"/api/repositories/fixture/jobs/{job_id}/cancel").status_code == 409


def test_jobs_without_a_worker_are_refused_not_queued(studio: TestClient) -> None:
    refused = start(studio, "test.mac")
    assert refused.status_code == 409 and "macos" in refused.json()["detail"]
    assert start(studio, "no.such").status_code == 400
    assert start(studio, "repository.validate", arguments={"x": 1}).status_code == 400
    ops = {o["name"]: o["available"] for o in studio.get("/api/operations").json()}
    assert ops["repository.validate"] and not ops["test.mac"]


def test_a_job_queued_before_a_restart_runs_after_it(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = JobStore(settings.state_dir / "jobs.sqlite")
    job, _ = store.submit(
        repo_id="fixture",
        operation="repository.rescan",
        arguments={},
        input_revision="r",
        requirements=["portable"],
        created_by="u",
        created_by_name="U",
    )
    store.close()
    app: FastAPI = create_app(settings, static_dir=tmp_path / "none")
    with TestClient(app, base_url="http://localhost") as client:
        app.state.runner.poll_seconds = 0.05
        done = wait_for(client, job.id, "succeeded")
    assert done["result"]["files"] > 0


def test_runner_drops_the_outcome_of_a_lost_attempt(tmp_path: Path) -> None:
    clock = Clock()
    store = JobStore(tmp_path / "jobs.sqlite", clock=clock)
    settings = _settings(tmp_path)

    def slow(ctx: JobContext) -> str:
        clock.now += 100  # The lease runs out while this attempt works.
        return "stale"

    from munki_web_studio.repository.index import Index

    index = Index(tmp_path / "index.sqlite")
    runner = Runner(
        store,
        settings,
        index,
        operations={"op": Operation("op", "Op", slow)},
        lease_seconds=30,
    )
    job, _ = store.submit(
        repo_id="fixture",
        operation="op",
        arguments={},
        input_revision="rev",
        requirements=["portable"],
        created_by="u",
        created_by_name="U",
    )
    assert runner.run_once("w1")
    current = store.get(job.id)
    assert current is not None and current.status == "running" and current.result is None
    retry = store.claim("w2", ["op"])
    assert retry is not None and retry.attempts == 2
    assert store.complete(job.id, "w2", "fresh").result == "fresh"
    store.close()
    index.close()


def test_unexpected_errors_do_not_reach_the_job_record(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite")
    settings = _settings(tmp_path)

    def broken(ctx: JobContext) -> None:
        raise OSError("/srv/secret/path is unreadable")

    from munki_web_studio.repository.index import Index

    index = Index(tmp_path / "index.sqlite")
    runner = Runner(store, settings, index, operations={"op": Operation("op", "Op", broken)})
    job, _ = store.submit(
        repo_id="fixture",
        operation="op",
        arguments={},
        input_revision="rev",
        requirements=["portable"],
        created_by="u",
        created_by_name="U",
    )
    assert runner.run_once("w")
    failed = store.get(job.id)
    assert failed is not None and failed.status == "failed"
    assert failed.error is not None and "/srv/secret" not in failed.error
    assert job.id in failed.error
    assert all("/srv/secret" not in e.message for e in store.events(job.id))
    store.close()
    index.close()
