"""The worker side of jobs, and the Settings view of connected workers.

Workers connect out to the studio with a token whose hash is in the config,
say what they are and what they can run, then claim jobs, fetch their inputs,
upload artifacts and finish them. The studio checks every artifact's hash
before it records a result, and keeps artifacts in its state folder, never in
the repository.
"""

import contextlib
import hashlib
import hmac
import io
import secrets
import shutil
import tarfile
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from munki_web_studio import platform
from munki_web_studio.api.read import _repo  # pyright: ignore[reportPrivateUsage]
from munki_web_studio.auth.model import Role
from munki_web_studio.auth.routes import require
from munki_web_studio.auth.sessions import Session
from munki_web_studio.changes.apply import SaveError
from munki_web_studio.codec import ParseError, load
from munki_web_studio.jobs.apply import SwapAbortedError, swap_folder
from munki_web_studio.jobs.runner import WORKER_MAX_AGE, Runner, repository_revision
from munki_web_studio.jobs.store import Job, JobStore, LeaseLostError
from munki_web_studio.repository.config import Repository, Settings, WorkerEntry
from munki_web_studio.repository.index import Index
from munki_web_studio.repository.paths import PathEscapeError, normalize, read_contained
from munki_web_studio.repository.scanner import scan

router = APIRouter(prefix="/api/worker", tags=["workers"])
settings_router = APIRouter(tags=["workers"])
Viewer = Annotated[Session, Depends(require(Role.VIEWER))]

LEASE_SECONDS = 30
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_INPUT_FILE_BYTES = 16 * 1024 * 1024
CHANGED = "The repository changed after this job was queued; start it again"


def _worker(
    request: Request,
    x_studio_worker: Annotated[str, Header()],
    authorization: Annotated[str, Header()],
) -> WorkerEntry:
    """The worker making this request, checked against its token's hash."""
    settings: Settings = request.app.state.settings
    scheme, _, token = authorization.partition(" ")
    entry = next((w for w in settings.workers if w.id == x_studio_worker), None)
    digest = hashlib.sha256(token.encode()).hexdigest()
    if (
        entry is None
        or scheme.lower() != "bearer"
        or not hmac.compare_digest(digest, entry.token_sha256)
    ):
        raise HTTPException(401, "Unknown worker or wrong token")
    return entry


Worker = Annotated[WorkerEntry, Depends(_worker)]


def _store(request: Request) -> JobStore:
    return request.app.state.jobs


def _runner(request: Request) -> Runner:
    return request.app.state.runner


def _artifact_dir(request: Request, job_id: str) -> Path:
    return request.app.state.settings.state_dir / "artifacts" / job_id


def _audit(request: Request, worker: str, action: str, job: Job, detail: dict[str, Any]) -> None:
    request.app.state.audit.record(
        actor=f"worker:{worker}",
        actor_name=worker,
        interface="worker",
        action=action,
        repo_id=job.repo_id,
        target=job.id,
        detail=detail,
    )


def _leased(request: Request, worker: WorkerEntry, job_id: str) -> Job:
    job = _store(request).get(job_id)
    if job is None or job.status != "running" or job.lease_owner != worker.id:
        raise HTTPException(409, "This worker does not hold that job")
    return job


class Hello(BaseModel):
    platform: str = Field(max_length=40)
    os_version: str = Field(max_length=40)
    tools: dict[str, str] = Field(description="Tool name to version, for the tools it found")
    capabilities: list[str]


class HelloReply(BaseModel):
    worker: str
    capabilities: list[str] = Field(description="The capabilities the studio accepted")
    heartbeat_seconds: float
    lease_seconds: float


@router.post("/hello")
def hello(request: Request, body: Hello, worker: Worker) -> HelloReply:
    """The handshake: what this worker is and can do. Repeat it every so often."""
    known = set(platform.CAPABILITIES)
    accepted = sorted(
        c
        for c in body.capabilities
        if c in known and all(t in body.tools for t in platform.CAPABILITIES[c])
    )
    tools = {k[:40]: v[:80] for k, v in body.tools.items() if k in platform.WORKER_TOOLS}
    _store(request).hello(
        worker.id,
        platform=body.platform,
        os_version=body.os_version,
        tools=tools,
        capabilities=accepted,
    )
    return HelloReply(
        worker=worker.id,
        capabilities=accepted,
        heartbeat_seconds=WORKER_MAX_AGE / 3,
        lease_seconds=LEASE_SECONDS,
    )


class ClaimedJob(BaseModel):
    id: str
    repo_id: str
    operation: str
    arguments: dict[str, Any]
    input_revision: str
    lease_seconds: float


@router.post("/claim", response_model=ClaimedJob, responses={204: {"description": "Nothing to do"}})
def claim(request: Request, worker: Worker) -> Any:
    """Take the oldest job this worker can run, if there is one."""
    store = _store(request)
    store.seen(worker.id)
    info = store.worker(worker.id)
    if info is None:
        raise HTTPException(409, "Say hello first")
    operations = [
        op.name
        for op in _runner(request).operations.values()
        if op.remote is not None and op.remote.capability in info.capabilities
    ]
    job = store.claim(worker.id, operations, LEASE_SECONDS)
    if job is None:
        return Response(status_code=204)
    return ClaimedJob(
        id=job.id,
        repo_id=job.repo_id,
        operation=job.operation,
        arguments=job.arguments,
        input_revision=job.input_revision,
        lease_seconds=LEASE_SECONDS,
    )


@router.get("/jobs/{job_id}/inputs", response_class=Response)
def inputs(request: Request, job_id: str, worker: Worker) -> Response:
    """The repository files the job needs, as a gzipped tar. Refused when the
    repository has changed since the job was queued, so the job runs on exactly
    the revision it was asked for."""
    job = _leased(request, worker, job_id)
    op = _runner(request).operations[job.operation]
    if op.remote is None:
        raise HTTPException(409, "This job runs in the studio")
    repo = _repo(request, job.repo_id)
    if repository_revision(request.app.state.index, repo) != job.input_revision:
        with contextlib.suppress(LeaseLostError):
            _store(request).fail(job.id, worker.id, CHANGED)
        raise HTTPException(409, "The repository changed after this job was queued")
    buffer = io.BytesIO()
    skipped: list[str] = []
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for folder in op.remote.inputs:
            base = repo.root / folder
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if path.is_symlink() or not path.is_file():
                    continue
                if path.name in platform.IGNORED_NAMES:
                    continue
                relative = path.relative_to(repo.root).as_posix()
                try:
                    read = read_contained(repo.root, relative, MAX_INPUT_FILE_BYTES)
                except PathEscapeError, OSError:
                    # A file swapped for a link, or removed, since the listing.
                    skipped.append(relative)
                    continue
                if read.raw is None:
                    skipped.append(relative)
                    continue
                member = tarfile.TarInfo(relative)
                member.size = len(read.raw)
                member.mode = 0o644
                archive.addfile(member, io.BytesIO(read.raw))
    return Response(
        buffer.getvalue(),
        media_type="application/gzip",
        headers={"X-Input-Revision": job.input_revision, "X-Skipped-Inputs": str(len(skipped))},
    )


class Beat(BaseModel):
    progress: float | None = Field(default=None, ge=0, le=1)
    message: str | None = Field(default=None, max_length=500)


class BeatReply(BaseModel):
    cancel: bool


@router.post("/jobs/{job_id}/heartbeat")
def heartbeat(request: Request, job_id: str, body: Beat, worker: Worker) -> BeatReply:
    """Keep the lease and report progress; the reply says whether to stop."""
    store = _store(request)
    store.seen(worker.id)
    try:
        cancel = store.heartbeat(
            job_id,
            worker.id,
            progress=body.progress,
            message=body.message,
            lease_seconds=LEASE_SECONDS,
        )
    except LeaseLostError as error:
        raise HTTPException(409, str(error)) from None
    return BeatReply(cancel=cancel)


def _artifact_path(request: Request, job_id: str, path: str) -> Path:
    try:
        relative = normalize(path)
    except PathEscapeError:
        raise HTTPException(400, "Invalid artifact path") from None
    return _artifact_dir(request, job_id) / relative


@router.put("/jobs/{job_id}/artifacts/{path:path}")
async def upload_artifact(
    request: Request,
    job_id: str,
    path: str,
    worker: Worker,
    x_content_sha256: Annotated[str, Header()],
) -> dict[str, Any]:
    """One output file. Its hash must match the header, or it is refused."""
    _leased(request, worker, job_id)
    target = _artifact_path(request, job_id, path)
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_ARTIFACT_BYTES:
            raise HTTPException(413, "Artifact is too large")
    digest = hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(digest, x_content_sha256.lower()):
        raise HTTPException(422, "The artifact's hash does not match")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(bytes(data))
    return {"path": path, "sha256": digest, "size": len(data)}


class Artifact(BaseModel):
    path: str
    sha256: str
    size: int


class Completion(BaseModel):
    artifacts: list[Artifact] = Field(max_length=1000)
    tool_version: str = Field(max_length=80)
    output: str = Field(default="", max_length=8000)


@router.post("/jobs/{job_id}/complete")
def complete(request: Request, job_id: str, body: Completion, worker: Worker) -> dict[str, Any]:
    """Finish a job. Every listed artifact must already be uploaded with that hash,
    and only artifacts listed here count; anything else uploaded is removed."""
    job = _leased(request, worker, job_id)
    folder = _artifact_dir(request, job_id)
    listed: set[Path] = set()
    for artifact in body.artifacts:
        stored = _artifact_path(request, job_id, artifact.path)
        if not stored.is_file():
            raise HTTPException(422, f"{artifact.path} was not uploaded")
        if hashlib.sha256(stored.read_bytes()).hexdigest() != artifact.sha256.lower():
            raise HTTPException(422, f"{artifact.path} does not match its hash")
        listed.add(stored)
    if folder.is_dir():
        for extra in folder.rglob("*"):
            if extra.is_file() and extra not in listed:
                extra.unlink()
    result: dict[str, Any] = {
        "artifacts": [a.model_dump() for a in body.artifacts],
        "tool_version": body.tool_version,
        "output": body.output[-4000:],
        "input_revision": job.input_revision,
        "worker": worker.id,
    }
    spec = _runner(request).operations[job.operation].remote
    if spec is not None and spec.applies_to:
        try:
            private = _private_copy(
                request, job, folder / spec.outputs, body.artifacts, spec.outputs
            )
            try:
                result["applied"] = _apply(request, worker, job, private)
            finally:
                shutil.rmtree(private.parent, ignore_errors=True)
        except CancelledApplyError, SwapAbortedError:
            try:
                cancelled = _store(request).cancelled(job.id, worker.id)
            except LeaseLostError as lost:
                raise HTTPException(409, str(lost)) from None
            return {"status": cancelled.status}
        except (ValueError, OSError, SaveError, ParseError) as error:
            message = f"The {spec.applies_to} were not replaced: {error}"
            try:
                failed = _store(request).fail(job.id, worker.id, message)
            except LeaseLostError as lost:
                raise HTTPException(409, str(lost)) from None
            _audit(request, worker.id, "job fail", failed, {"reason": message})
            return {"status": failed.status}
    try:
        done = _store(request).complete(job.id, worker.id, result)
    except LeaseLostError as error:
        raise HTTPException(409, str(error)) from None
    _audit(request, worker.id, "job complete", done, {"artifacts": len(body.artifacts)})
    return {"status": done.status}


def _private_copy(
    request: Request, job: Job, output: Path, artifacts: list[Artifact], prefix: str
) -> Path:
    """Copy the outputs where uploads cannot reach, checking each hash again, so
    the files that pass the check are the files that are swapped in."""
    target = request.app.state.settings.state_dir / "applying" / f"{job.id}-{secrets.token_hex(4)}"
    copy = target / prefix
    copy.mkdir(parents=True)
    wanted = {a.path: a.sha256.lower() for a in artifacts}
    for path in sorted(output.rglob("*")) if output.is_dir() else []:
        if path.is_symlink() or not path.is_file():
            continue
        relative = f"{prefix}/{path.relative_to(output).as_posix()}"
        data = path.read_bytes()
        if wanted.get(relative) != hashlib.sha256(data).hexdigest():
            raise ValueError(f"{relative} changed after it was uploaded")
        destination = copy / path.relative_to(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    return copy


class CancelledApplyError(Exception):
    """Cancellation was asked for before the swap."""


def _sources(repo: Repository, index: Index) -> list[dict[str, Any]]:
    """Every readable pkginfo as the studio reads it, to check generated catalogs by."""
    found: list[dict[str, Any]] = []
    for obj in index.all(repo.id, "pkginfo"):
        if obj.status != "ok":
            continue
        read = read_contained(repo.root, obj.path, MAX_INPUT_FILE_BYTES)
        if read.raw is None:
            continue
        data = load(read.raw).data
        if isinstance(data, dict):
            found.append(cast(dict[str, Any], data))
    return found


def _apply(request: Request, worker: WorkerEntry, job: Job, output: Path) -> dict[str, Any]:
    """Check a job's outputs and swap them into the repository. Raises before
    touching anything if the repository moved on or the outputs fail the check."""
    op = _runner(request).operations[job.operation]
    spec = op.remote
    if spec is None or spec.applies_to is None:
        return {}
    repo = _repo(request, job.repo_id)
    index = request.app.state.index
    if not repo.writable:
        raise ValueError("the repository is read-only in the studio's configuration")
    scan(repo, index)
    if repository_revision(index, repo) != job.input_revision:
        raise ValueError("the repository changed while the job ran; start it again")
    summary = spec.check(output, _sources(repo, index)) if spec.check else {}
    store = _store(request)

    def still_wanted() -> bool:
        # Renews the lease, so a retry cannot start meanwhile, and reports a cancel.
        return not store.heartbeat(job.id, worker.id, message=f"Replacing {spec.applies_to}")

    if not still_wanted():
        raise CancelledApplyError
    swap_folder(repo, spec.applies_to, output, request.app.state.journal_dir, still_wanted)
    scan(repo, index)
    request.app.state.audit.record(
        actor=f"worker:{worker.id}",
        actor_name=worker.id,
        interface="worker",
        action=f"replace {spec.applies_to}",
        repo_id=repo.id,
        target=job.id,
        detail={"started_by": job.created_by_name, "items": summary},
    )
    return {"folder": spec.applies_to, "counts": summary}


class Failure(BaseModel):
    error: str = Field(max_length=2000)
    retryable: bool = False
    cancelled: bool = Field(default=False, description="The worker stopped because it was asked to")


@router.post("/jobs/{job_id}/fail")
def fail(request: Request, job_id: str, body: Failure, worker: Worker) -> dict[str, Any]:
    job = _leased(request, worker, job_id)
    try:
        store = _store(request)
        if body.cancelled and job.cancel_requested:
            done = store.cancelled(job.id, worker.id)
        else:
            done = store.fail(job.id, worker.id, body.error, retryable=body.retryable)
    except LeaseLostError as error:
        raise HTTPException(409, str(error)) from None
    _audit(request, worker.id, "job fail", done, {"retryable": body.retryable})
    return {"status": done.status}


class WorkerView(BaseModel):
    id: str
    configured: bool
    online: bool
    platform: str | None
    os_version: str | None
    tools: dict[str, str]
    capabilities: list[str]
    last_seen: float | None


@settings_router.get("/api/workers")
def list_workers(request: Request, session: Viewer) -> list[WorkerView]:
    """Every configured worker, whether it is connected, and what it can do."""
    store = _store(request)
    seen = {w.id: w for w in store.workers()}
    views: list[WorkerView] = []
    for entry in request.app.state.settings.workers:
        info = seen.get(entry.id)
        views.append(
            WorkerView(
                id=entry.id,
                configured=True,
                online=bool(info and store.online(info, WORKER_MAX_AGE)),
                platform=info.platform if info else None,
                os_version=info.os_version if info else None,
                tools=info.tools if info else {},
                capabilities=info.capabilities if info else [],
                last_seen=info.last_seen if info else None,
            )
        )
    return views


@settings_router.get(
    "/api/repositories/{repo_id}/jobs/{job_id}/artifacts/{path:path}",
    response_class=FileResponse,
)
def download_artifact(
    request: Request, repo_id: str, job_id: str, path: str, session: Viewer
) -> FileResponse:
    """A file a finished job produced."""
    job = _store(request).get(job_id)
    if job is None or job.repo_id != repo_id or job.status != "succeeded":
        raise HTTPException(404, "No such job")
    result = cast(dict[str, Any], job.result or {})
    listed = {str(a["path"]) for a in cast(list[dict[str, Any]], result.get("artifacts", []))}
    if path not in listed:
        raise HTTPException(404, "No such artifact")
    target = _artifact_path(request, job_id, path)
    if not target.is_file():
        raise HTTPException(404, "The artifact is gone")
    return FileResponse(target, filename=Path(path).name)
