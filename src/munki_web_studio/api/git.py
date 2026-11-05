"""Git for repositories that are Git working trees: status, committing a saved
draft, and pushing. Each is a separate, explicit action."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from munki_web_studio.api.read import _repo  # pyright: ignore[reportPrivateUsage]
from munki_web_studio.auth.model import Role
from munki_web_studio.auth.routes import require
from munki_web_studio.auth.sessions import Session
from munki_web_studio.changes.store import ChangeSetStore
from munki_web_studio.codec import revision_of
from munki_web_studio.gitrepo import Git, GitError
from munki_web_studio.repository.config import Repository
from munki_web_studio.repository.paths import PathEscapeError, read_contained

router = APIRouter(prefix="/api/repositories", tags=["git"])
Viewer = Annotated[Session, Depends(require(Role.VIEWER))]
Editor = Annotated[Session, Depends(require(Role.EDITOR))]
Publisher = Annotated[Session, Depends(require(Role.PUBLISHER))]


class GitInfo(BaseModel):
    enabled: bool
    reason: str | None = Field(description="Why Git is not used for this repository")
    branch: str | None
    head: str | None
    upstream: str | None
    ahead: int
    behind: int
    changed: list[str] = Field(description="Paths with changes Git has not committed")


class CommitRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class CommitInfo(BaseModel):
    commit: str
    paths: list[str]


def _git(request: Request, repo: Repository) -> Git:
    return Git(repo, request.app.state.settings.state_dir)


def _changes(request: Request) -> ChangeSetStore:
    return request.app.state.changes


@router.get("/{repo_id}/git")
def git_status(request: Request, repo_id: str, session: Viewer) -> GitInfo:
    """Branch, commit and uncommitted paths, or why Git is not used here."""
    status = _git(request, _repo(request, repo_id)).status()
    return GitInfo(**vars(status))


@router.post("/{repo_id}/changesets/{change_id}/commit")
def commit_change_set(
    request: Request, repo_id: str, change_id: str, body: CommitRequest, session: Editor
) -> CommitInfo:
    """Commit the files a saved change set wrote, and nothing else. Refused if any
    of them changed again after the save, so the commit is exactly what was
    reviewed. Hooks do not run."""
    repo = _repo(request, repo_id)
    store = _changes(request)
    change_set = store.get(change_id)
    if change_set is None or change_set.repo_id != repo.id:
        raise HTTPException(404, "No such change set")
    if change_set.owner != session.user.subject and session.user.role < Role.ADMINISTRATOR:
        raise HTTPException(404, "No such change set")
    if change_set.status != "saved":
        raise HTTPException(409, "Only a saved change set can be committed")
    if store.commit_of(change_id):
        raise HTTPException(409, "This change set is already committed")
    saved = store.saved_revisions(change_id)
    if not saved:
        raise HTTPException(409, "The studio has no record of what this save wrote")
    for path, revision in saved.items():
        try:
            read = read_contained(repo.root, path, 64 * 1024 * 1024)
        except PathEscapeError, OSError:
            raise HTTPException(409, f"{path} is gone or unreadable since the save") from None
        if read.raw is None or revision_of(read.raw) != revision:
            raise HTTPException(409, f"{path} changed after it was saved; commit it yourself")
    git = _git(request, repo)
    email = session.user.email or f"{session.user.subject}@studio.invalid"
    try:
        commit = git.commit(sorted(saved), body.message, session.user.name, email)
        paths = git.committed_paths(commit)
    except GitError as error:
        raise HTTPException(409, str(error)) from None
    store.record_commit(change_id, commit)
    return CommitInfo(commit=commit, paths=paths)


@router.post("/{repo_id}/git/push")
def push(request: Request, repo_id: str, session: Publisher) -> dict[str, str]:
    """Push the current branch to its upstream. Hooks do not run."""
    try:
        message = _git(request, _repo(request, repo_id)).push()
    except GitError as error:
        raise HTTPException(409, str(error)) from None
    return {"message": message}
