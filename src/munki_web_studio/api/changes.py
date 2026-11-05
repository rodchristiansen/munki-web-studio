"""Draft change sets: create, add file edits, preview, discard. Saving comes later."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from munki_web_studio import platform, tagged
from munki_web_studio.api.read import _repo  # pyright: ignore[reportPrivateUsage]
from munki_web_studio.auth.model import Role
from munki_web_studio.auth.routes import require
from munki_web_studio.auth.sessions import Session
from munki_web_studio.changes.apply import preview
from munki_web_studio.changes.fields import FieldError, field_changes, merge
from munki_web_studio.changes.model import (
    ChangeFormatError,
    ChangeSet,
    change_from_json,
    change_to_json,
)
from munki_web_studio.changes.store import ChangeSetStore
from munki_web_studio.codec import ParseError, load
from munki_web_studio.codec.model import MAX_FILE_BYTES
from munki_web_studio.repository.paths import PathEscapeError, normalize, read_contained

router = APIRouter(prefix="/api/repositories", tags=["changes"])
Editor = Annotated[Session, Depends(require(Role.EDITOR))]


class NewChangeSet(BaseModel):
    title: str = Field(default="Untitled change", max_length=200)


class FileEdit(BaseModel):
    base_revision: str = Field(description="Revision of the file the edits were made on")
    changes: list[dict[str, Any]] = Field(
        description=(
            "Edits: {op: set|append|delete, path: [...], value: tagged JSON}; "
            "an empty list removes the file from the draft"
        )
    )


class FieldEdit(BaseModel):
    base_revision: str = Field(description="Revision of the pkginfo the edits were made on")
    fields: dict[str, Any] = Field(
        description="Field values as tagged JSON; null removes a field. GET /api/fields lists them."
    )


class FileDraftInfo(BaseModel):
    path: str
    base_revision: str
    current_revision: str | None
    changes: list[dict[str, Any]]
    conflict: bool
    error: str | None
    surgical: bool
    diff: str


class ChangeSetInfo(BaseModel):
    id: str
    repo_id: str
    owner: str
    owner_name: str
    title: str
    status: str
    created_at: str
    updated_at: str
    files: list[FileDraftInfo]


def _store(request: Request) -> ChangeSetStore:
    return request.app.state.changes


def _info(request: Request, change_set: ChangeSet) -> ChangeSetInfo:
    repo = _repo(request, change_set.repo_id)
    previews = {p.path: p for p in preview(repo, change_set)} if change_set.status == "open" else {}
    files: list[FileDraftInfo] = []
    for draft in change_set.files:
        p = previews.get(draft.path)
        files.append(
            FileDraftInfo(
                path=draft.path,
                base_revision=draft.base_revision,
                current_revision=p.current_revision if p else None,
                changes=[change_to_json(c) for c in draft.changes],
                conflict=p.conflict if p else False,
                error=p.error if p else None,
                surgical=p.surgical if p else True,
                diff=p.diff if p else "",
            )
        )
    return ChangeSetInfo(
        id=change_set.id,
        repo_id=change_set.repo_id,
        owner=change_set.owner,
        owner_name=change_set.owner_name,
        title=change_set.title,
        status=change_set.status,
        created_at=change_set.created_at,
        updated_at=change_set.updated_at,
        files=files,
    )


def _owned(request: Request, session: Session, repo_id: str, change_id: str) -> ChangeSet:
    change_set = _store(request).get(change_id)
    if change_set is None or change_set.repo_id != repo_id:
        raise HTTPException(404, "No such change set")
    if change_set.owner != session.user.subject and session.user.role < Role.ADMINISTRATOR:
        raise HTTPException(404, "No such change set")
    return change_set


@router.post("/{repo_id}/changesets")
def create_change_set(
    request: Request, repo_id: str, body: NewChangeSet, session: Editor
) -> ChangeSetInfo:
    repo = _repo(request, repo_id)
    change_set = _store(request).create(
        repo.id, session.user.subject, session.user.name, body.title
    )
    return _info(request, change_set)


@router.get("/{repo_id}/changesets")
def list_change_sets(request: Request, repo_id: str, session: Editor) -> list[ChangeSetInfo]:
    """Your open change sets; Administrators see everyone's."""
    repo = _repo(request, repo_id)
    owner = None if session.user.role >= Role.ADMINISTRATOR else session.user.subject
    return [_info(request, cs) for cs in _store(request).list(repo.id, owner)]


@router.get("/{repo_id}/changesets/{change_id}")
def get_change_set(
    request: Request, repo_id: str, change_id: str, session: Editor
) -> ChangeSetInfo:
    """A change set with each file's diff and whether it still applies."""
    return _info(request, _owned(request, session, repo_id, change_id))


@router.put("/{repo_id}/changesets/{change_id}/files/{path:path}")
def put_file_edits(
    request: Request, repo_id: str, change_id: str, path: str, body: FileEdit, session: Editor
) -> ChangeSetInfo:
    """Replace one file's edits in a draft."""
    change_set = _owned(request, session, repo_id, change_id)
    if change_set.status != "open":
        raise HTTPException(409, f"This change set is {change_set.status}")
    try:
        relative = normalize(path)
        changes = [change_from_json(c) for c in body.changes]
    except (PathEscapeError, ChangeFormatError) as error:
        raise HTTPException(400, str(error)) from None
    _store(request).put_file(change_set.id, relative, body.base_revision, changes)
    updated = _store(request).get(change_set.id)
    if updated is None:
        raise HTTPException(404, "No such change set")
    return _info(request, updated)


@router.delete("/{repo_id}/changesets/{change_id}")
def discard_change_set(
    request: Request, repo_id: str, change_id: str, session: Editor
) -> ChangeSetInfo:
    change_set = _owned(request, session, repo_id, change_id)
    _store(request).set_status(change_set.id, "discarded")
    discarded = _store(request).get(change_set.id)
    if discarded is None:
        raise HTTPException(404, "No such change set")
    return _info(request, discarded)


@router.patch("/{repo_id}/changesets/{change_id}/pkginfo/{path:path}")
def edit_pkginfo_fields(
    request: Request, repo_id: str, change_id: str, path: str, body: FieldEdit, session: Editor
) -> ChangeSetInfo:
    """Edit named pkginfo fields in a draft. Values are checked against the field's
    type, unchanged values are ignored, and earlier edits to the same fields are
    replaced."""
    change_set = _owned(request, session, repo_id, change_id)
    if change_set.status != "open":
        raise HTTPException(409, f"This change set is {change_set.status}")
    repo = _repo(request, repo_id)
    try:
        relative = normalize(path)
    except PathEscapeError:
        raise HTTPException(400, "Invalid path") from None
    obj = request.app.state.index.get(repo.id, relative)
    if obj is None or obj.kind != "pkginfo" or obj.status != "ok":
        raise HTTPException(404, f"No readable pkginfo at {relative!r}")
    try:
        read = read_contained(repo.root, relative, MAX_FILE_BYTES)
        if read.raw is None:
            raise HTTPException(413, "File is too large")
        document = load(read.raw)
    except (PathEscapeError, ParseError, OSError) as error:
        raise HTTPException(409, str(error)) from None
    if document.revision != body.base_revision:
        raise HTTPException(409, "The file changed since you opened it; reload and edit again")
    try:
        new = field_changes(document.data, {k: tagged.decode(v) for k, v in body.fields.items()})
    except (FieldError, ValueError, TypeError) as error:
        raise HTTPException(400, str(error)) from None
    previous = next((f for f in change_set.files if f.path == relative), None)
    touched = [tuple(name.split(".")) for name in body.fields]
    combined = merge(previous.changes if previous else [], new, touched)
    _store(request).put_file(change_set.id, relative, body.base_revision, combined)
    updated = _store(request).get(change_set.id)
    if updated is None:
        raise HTTPException(404, "No such change set")
    return _info(request, updated)


fields_router = APIRouter(prefix="/api", tags=["changes"])


@fields_router.get("/fields")
def editable_fields() -> dict[str, str]:
    """The pkginfo fields the editor can set, and the type each takes."""
    return dict(platform.PKGINFO_FIELDS)
