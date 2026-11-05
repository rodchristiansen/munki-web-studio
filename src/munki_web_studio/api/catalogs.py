"""Catalogs: what each one holds, and whether it still matches the pkginfo.

Catalogs are derived from pkginfo, so the studio never edits them. It shows
where they have fallen behind (a pkginfo names a catalog it is missing from, or
a catalog still lists something no pkginfo puts there) and offers the
generation job that brings them up to date.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from munki_web_studio import catalogs
from munki_web_studio.api.read import _repo  # pyright: ignore[reportPrivateUsage]
from munki_web_studio.auth.model import Role
from munki_web_studio.auth.routes import require
from munki_web_studio.auth.sessions import Session
from munki_web_studio.codec import ParseError
from munki_web_studio.codec.model import MAX_FILE_BYTES
from munki_web_studio.repository.config import Repository
from munki_web_studio.repository.index import Index
from munki_web_studio.repository.paths import PathEscapeError, read_contained

router = APIRouter(prefix="/api/repositories", tags=["catalogs"])
Viewer = Annotated[Session, Depends(require(Role.VIEWER))]


class CatalogItem(BaseModel):
    name: str
    version: str
    pkginfo: str | None


class CatalogSummary(BaseModel):
    name: str
    path: str | None
    items: int
    expected: int
    up_to_date: bool
    error: str | None


class CatalogDetail(CatalogSummary):
    members: list[CatalogItem]
    missing: list[CatalogItem]
    extra: list[CatalogItem]


Key = tuple[str, str]


def _expected(index: Index, repo: Repository) -> dict[str, dict[Key, str]]:
    """Catalog name to the (name, version) of each readable pkginfo that lists it."""
    found: dict[str, dict[Key, str]] = {}
    for obj in index.all(repo.id, "pkginfo"):
        if obj.status != "ok" or not obj.name:
            continue
        for name in [*obj.catalogs, catalogs.ALL]:
            found.setdefault(name, {})[(obj.name, obj.version or "")] = obj.path
    return found


def _actual(repo: Repository, path: str) -> list[Key]:
    read = read_contained(repo.root, path, MAX_FILE_BYTES)
    if read.raw is None:
        raise ParseError("File is too large")
    return [catalogs.key(i) for i in catalogs.entries(read.raw)]


def _build(index: Index, repo: Repository) -> list[CatalogDetail]:
    expected = _expected(index, repo)
    files = {catalogs.name_of(o.path): o for o in index.all(repo.id, "catalog")}
    details: list[CatalogDetail] = []
    for name in sorted({*files, *expected}, key=lambda n: (n != catalogs.ALL, n.lower())):
        obj = files.get(name)
        wanted = expected.get(name, {})
        error = None
        actual: list[Key] = []
        if obj is not None:
            try:
                actual = _actual(repo, obj.path)
            except (ParseError, catalogs.CatalogError, PathEscapeError, OSError) as failure:
                error = str(failure)
        present = set(actual)
        details.append(
            CatalogDetail(
                name=name,
                path=obj.path if obj else None,
                items=len(actual),
                expected=len(wanted),
                up_to_date=error is None and obj is not None and present == set(wanted),
                error=error if obj is not None else "Not generated yet",
                members=[
                    CatalogItem(name=k[0], version=k[1], pkginfo=wanted.get(k)) for k in actual
                ],
                missing=[
                    CatalogItem(name=k[0], version=k[1], pkginfo=p)
                    for k, p in wanted.items()
                    if k not in present
                ],
                extra=[
                    CatalogItem(name=k[0], version=k[1], pkginfo=None)
                    for k in actual
                    if k not in wanted
                ],
            )
        )
    return details


@router.get("/{repo_id}/catalogs")
def list_catalogs(request: Request, repo_id: str, session: Viewer) -> list[CatalogSummary]:
    """Every catalog on disk or named by a pkginfo, and whether it is up to date."""
    repo = _repo(request, repo_id)
    return [
        CatalogSummary(**d.model_dump(include=set(CatalogSummary.model_fields)))
        for d in _build(request.app.state.index, repo)
    ]


@router.get("/{repo_id}/catalogs/{name}")
def get_catalog(request: Request, repo_id: str, name: str, session: Viewer) -> CatalogDetail:
    """One catalog's members, and what generating catalogs would add or remove."""
    repo = _repo(request, repo_id)
    for detail in _build(request.app.state.index, repo):
        if detail.name == name:
            return detail
    raise HTTPException(404, f"No catalog {name!r}")
