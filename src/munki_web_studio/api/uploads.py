"""Resumable uploads: declare a file, send it in chunks from where it left off,
and use it once its hash checks out."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from munki_web_studio.api.read import _repo  # pyright: ignore[reportPrivateUsage]
from munki_web_studio.auth.model import Role
from munki_web_studio.auth.routes import require
from munki_web_studio.auth.sessions import Session
from munki_web_studio.uploads import Upload, UploadError, UploadStore

router = APIRouter(prefix="/api/repositories", tags=["uploads"])
Editor = Annotated[Session, Depends(require(Role.EDITOR))]
MAX_CHUNK_BYTES = 64 * 1024 * 1024


class NewUpload(BaseModel):
    filename: str = Field(max_length=200)
    size: int
    sha256: str = Field(min_length=64, max_length=64)


class UploadInfo(BaseModel):
    id: str
    filename: str
    size: int
    sha256: str
    received: int = Field(description="Bytes that have arrived; send the next chunk from here")
    complete: bool


def _store(request: Request) -> UploadStore:
    return request.app.state.uploads


def _info(upload: Upload) -> UploadInfo:
    return UploadInfo(
        id=upload.id,
        filename=upload.filename,
        size=upload.size,
        sha256=upload.sha256,
        received=upload.received,
        complete=upload.complete,
    )


def owned(request: Request, session: Session, repo_id: str, upload_id: str) -> Upload:
    try:
        upload = _store(request).get(upload_id)
    except UploadError as error:
        raise HTTPException(error.status, str(error)) from None
    if upload.repo_id != repo_id or (
        upload.owner != session.user.subject and session.user.role < Role.ADMINISTRATOR
    ):
        raise HTTPException(404, "No such upload")
    return upload


@router.post("/{repo_id}/uploads", status_code=201)
def create_upload(request: Request, repo_id: str, body: NewUpload, session: Editor) -> UploadInfo:
    """Declare an installer to upload. Nothing reaches the repository until a
    reviewed draft that uses it is saved."""
    repo = _repo(request, repo_id)
    try:
        upload = _store(request).create(
            repo.id, session.user.subject, body.filename, body.size, body.sha256
        )
    except UploadError as error:
        raise HTTPException(error.status, str(error)) from None
    return _info(upload)


@router.get("/{repo_id}/uploads/{upload_id}")
def get_upload(request: Request, repo_id: str, upload_id: str, session: Editor) -> UploadInfo:
    """How much has arrived, so an interrupted upload can resume."""
    return _info(owned(request, session, repo_id, upload_id))


@router.patch("/{repo_id}/uploads/{upload_id}")
async def append_upload(
    request: Request,
    repo_id: str,
    upload_id: str,
    session: Editor,
    upload_offset: Annotated[int, Header()],
) -> UploadInfo:
    """Send the next chunk. `Upload-Offset` must equal the bytes received so far."""
    owned(request, session, repo_id, upload_id)
    chunk = bytearray()
    async for part in request.stream():
        chunk += part
        if len(chunk) > MAX_CHUNK_BYTES:
            raise HTTPException(413, f"Send at most {MAX_CHUNK_BYTES} bytes per chunk")
    try:
        upload = await run_in_threadpool(
            _store(request).append, upload_id, upload_offset, bytes(chunk)
        )
    except UploadError as error:
        raise HTTPException(error.status, str(error)) from None
    return _info(upload)
