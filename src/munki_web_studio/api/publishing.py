"""How a repository reaches clients: live on save, a published snapshot, or
neither configured."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from munki_web_studio import publish
from munki_web_studio.api.read import _repo  # pyright: ignore[reportPrivateUsage]
from munki_web_studio.auth.model import Role
from munki_web_studio.auth.routes import require
from munki_web_studio.auth.sessions import Session
from munki_web_studio.jobs.runner import repository_revision

router = APIRouter(prefix="/api/repositories", tags=["publishing"])
Viewer = Annotated[Session, Depends(require(Role.VIEWER))]


class ReleaseInfo(BaseModel):
    id: str
    published_at: str
    repository_revision: str
    files: int
    bytes: int


class PublishingInfo(BaseModel):
    mode: str = Field(description="live: clients read the repository itself; snapshot; or none")
    target: str | None
    current: ReleaseInfo | None
    releases: list[ReleaseInfo]
    unpublished_changes: bool = Field(
        description="The repository changed since the current release was published"
    )


@router.get("/{repo_id}/publishing")
def publishing(request: Request, repo_id: str, session: Viewer) -> PublishingInfo:
    repo = _repo(request, repo_id)
    if repo.live:
        return PublishingInfo(
            mode="live", target=None, current=None, releases=[], unpublished_changes=False
        )
    if repo.publish is None:
        return PublishingInfo(
            mode="none", target=None, current=None, releases=[], unpublished_changes=False
        )
    current = publish.current_release(repo.publish)
    revision = repository_revision(request.app.state.index, repo)
    return PublishingInfo(
        mode="snapshot",
        target=str(repo.publish.target),
        current=ReleaseInfo(**vars(current)) if current else None,
        releases=[ReleaseInfo(**vars(r)) for r in publish.releases(repo.publish)],
        unpublished_changes=current is None or current.repository_revision != revision,
    )
