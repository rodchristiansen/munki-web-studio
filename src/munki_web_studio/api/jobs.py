"""Jobs: start one, watch its progress, cancel it, and read the Activity list.

Progress streams as server-sent events from `/events`; a client that cannot
hold a stream polls the job with `?after=<last event id>` instead. Neither is the
job's record: the job runs and finishes whether or not anyone is watching.
"""

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from munki_web_studio.api.read import _repo  # pyright: ignore[reportPrivateUsage]
from munki_web_studio.api.uploads import owned as owned_upload
from munki_web_studio.auth.model import Role
from munki_web_studio.auth.routes import require
from munki_web_studio.auth.sessions import Session
from munki_web_studio.jobs.runner import Runner, explain_missing, repository_revision
from munki_web_studio.jobs.store import Job, JobEvent, JobStore
from munki_web_studio.repository.paths import PathEscapeError, normalize

router = APIRouter(tags=["jobs"])
Viewer = Annotated[Session, Depends(require(Role.VIEWER))]
Editor = Annotated[Session, Depends(require(Role.EDITOR))]
KEEPALIVE_SECONDS = 15


class OperationInfo(BaseModel):
    name: str
    title: str
    requirements: list[str]
    available: bool = Field(description="Whether a worker that can run it is connected")
    needs: str | None = Field(description="What is missing when it is not available")


class NewJob(BaseModel):
    operation: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(
        default=None,
        max_length=200,
        description="Sending the same key again returns the job it first created",
    )


class JobEventInfo(BaseModel):
    id: int
    at: str
    kind: str
    message: str
    progress: float | None


class JobInfo(BaseModel):
    id: str
    repo_id: str
    operation: str
    title: str
    arguments: dict[str, Any]
    input_revision: str
    status: str
    attempts: int
    max_attempts: int
    cancel_requested: bool
    progress: float
    message: str
    result: Any
    result_hash: str | None
    error: str | None
    created_by_name: str
    created_at: str
    updated_at: str
    finished_at: str | None
    events: list[JobEventInfo] = Field(default_factory=lambda: list[JobEventInfo]())


def _store(request: Request) -> JobStore:
    return request.app.state.jobs


def _runner(request: Request) -> Runner:
    return request.app.state.runner


def _event(e: JobEvent) -> JobEventInfo:
    return JobEventInfo(id=e.id, at=e.at, kind=e.kind, message=e.message, progress=e.progress)


def _info(request: Request, job: Job, events: list[JobEvent] | None = None) -> JobInfo:
    op = _runner(request).operations.get(job.operation)
    return JobInfo(
        id=job.id,
        repo_id=job.repo_id,
        operation=job.operation,
        title=op.title if op else job.operation,
        arguments=job.arguments,
        input_revision=job.input_revision,
        status=job.status,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        cancel_requested=job.cancel_requested,
        progress=job.progress,
        message=job.message,
        result=job.result,
        result_hash=job.result_hash,
        error=job.error,
        created_by_name=job.created_by_name,
        created_at=job.created_at,
        updated_at=job.updated_at,
        finished_at=job.finished_at,
        events=[_event(e) for e in events or []],
    )


def _job(request: Request, repo_id: str, job_id: str) -> Job:
    job = _store(request).get(job_id)
    if job is None or job.repo_id != repo_id:
        raise HTTPException(404, "No such job")
    return job


@router.get("/api/operations")
def operations(request: Request, session: Viewer) -> list[OperationInfo]:
    """What jobs this studio can run, and whether a worker for each is connected."""
    runner = _runner(request)
    found: list[OperationInfo] = []
    for op in runner.operations.values():
        missing = runner.missing(op)
        found.append(
            OperationInfo(
                name=op.name,
                title=op.title,
                requirements=list(op.requirements),
                available=not missing,
                needs=f"Needs {explain_missing(missing)}; none is connected" if missing else None,
            )
        )
    return found


@router.post("/api/repositories/{repo_id}/jobs", status_code=201)
def submit_job(
    request: Request, response: Response, repo_id: str, body: NewJob, session: Editor
) -> JobInfo:
    """Start a job. It is pinned to the repository's revision now; the same
    idempotency key returns the existing job (status 200) instead of a second one."""
    repo = _repo(request, repo_id)
    runner = _runner(request)
    op = runner.operations.get(body.operation)
    if op is None:
        raise HTTPException(400, f"No operation {body.operation!r}")
    if session.user.role < op.role:
        raise HTTPException(403, f"{op.title} needs the {op.role.label} role")
    if op.name == "repository.publish" and repo.publish is None:
        raise HTTPException(
            409,
            "This repository has no publish target"
            + ("; clients read it directly, so every save is live" if repo.live else ""),
        )
    missing = runner.missing(op)
    if missing:
        raise HTTPException(409, f"{op.title} needs {explain_missing(missing)}; none is connected")
    allowed = op.remote.arguments if op.remote else frozenset[str]()
    unknown = sorted(set(body.arguments) - allowed)
    if unknown:
        raise HTTPException(400, f"{op.title} does not take {', '.join(unknown)}")
    if op.remote and op.remote.upload_argument:
        upload_id = body.arguments.get(op.remote.upload_argument)
        if not isinstance(upload_id, str):
            raise HTTPException(400, f"{op.title} needs an upload")
        upload = owned_upload(request, session, repo.id, upload_id)
        if not upload.complete:
            raise HTTPException(409, "The upload has not finished")
        folder = body.arguments.get("subdirectory", "")
        if not isinstance(folder, str):
            raise HTTPException(400, "subdirectory must be text")
        if folder:
            try:
                if normalize(folder) != folder or folder.startswith(("pkgs/", "pkgsinfo/")):
                    raise PathEscapeError(folder)
            except PathEscapeError:
                raise HTTPException(400, "subdirectory must be a plain relative folder") from None
    if op.remote and op.remote.applies_to:
        if not repo.writable:
            raise HTTPException(403, "This repository is read-only in the studio's configuration")
        if repo.live and body.arguments.get("acknowledge_live") is not True:
            raise HTTPException(
                428,
                "Clients read this repository directly; confirm that the new "
                f"{op.remote.applies_to} go live at once",
            )
    job, created = _store(request).submit(
        repo_id=repo.id,
        operation=op.name,
        arguments=body.arguments,
        input_revision=repository_revision(request.app.state.index, repo),
        requirements=list(op.requirements),
        created_by=session.user.subject,
        created_by_name=session.user.name,
        idempotency_key=body.idempotency_key,
        max_attempts=op.max_attempts,
        timeout_seconds=op.timeout_seconds,
    )
    if not created:
        response.status_code = 200
    return _info(request, job)


@router.get("/api/repositories/{repo_id}/jobs")
def list_jobs(
    request: Request, repo_id: str, session: Viewer, limit: int = 50, before: str | None = None
) -> list[JobInfo]:
    """Recent jobs, newest first: the Activity view."""
    repo = _repo(request, repo_id)
    jobs = _store(request).list(repo.id, limit=max(1, min(limit, 200)), before=before)
    return [_info(request, j) for j in jobs]


@router.get("/api/repositories/{repo_id}/jobs/{job_id}")
def get_job(
    request: Request, repo_id: str, job_id: str, session: Viewer, after: int = 0
) -> JobInfo:
    """One job and its events after `after`, for clients that poll."""
    job = _job(request, repo_id, job_id)
    return _info(request, job, _store(request).events(job.id, after))


@router.post("/api/repositories/{repo_id}/jobs/{job_id}/cancel")
def cancel_job(request: Request, repo_id: str, job_id: str, session: Editor) -> JobInfo:
    """Cancel a queued job, or ask a running one to stop at its next checkpoint."""
    job = _job(request, repo_id, job_id)
    if job.created_by != session.user.subject and session.user.role < Role.ADMINISTRATOR:
        raise HTTPException(
            403, "Only the person who started a job, or an administrator, can cancel it"
        )
    if job.finished:
        raise HTTPException(409, f"This job already {job.status}")
    cancelled = _store(request).cancel(job.id, session.user.name)
    if cancelled is None:
        raise HTTPException(404, "No such job")
    return _info(request, cancelled)


@router.get(
    "/api/repositories/{repo_id}/jobs/{job_id}/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def job_events(
    request: Request,
    repo_id: str,
    job_id: str,
    session: Viewer,
    last_event_id: Annotated[int | None, Header()] = None,
    after: int = 0,
) -> StreamingResponse:
    """Server-sent events for one job: each event as it happens, then `end`."""
    job = _job(request, repo_id, job_id)
    store = _store(request)
    start = last_event_id if last_event_id is not None else after

    async def stream() -> AsyncGenerator[str]:
        seen = start
        while True:
            for event in store.events(job.id, seen):
                seen = event.id
                data = json.dumps(_event(event).model_dump())
                yield f"id: {event.id}\nevent: {event.kind}\ndata: {data}\n\n"
            current = store.get(job.id)
            if current is None or current.finished:
                state = _info(request, current).model_dump() if current else {}
                yield f"event: end\ndata: {json.dumps(state)}\n\n"
                return
            if await request.is_disconnected():
                return
            await asyncio.to_thread(store.wait, KEEPALIVE_SECONDS)
            yield ": keepalive\n\n"

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
    )
