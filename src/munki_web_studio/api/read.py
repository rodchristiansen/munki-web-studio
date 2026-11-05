"""Read-only routes over the index and the repository files."""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request

from munki_web_studio import conditions, tagged
from munki_web_studio.api.models import (
    CatalogSummary,
    ConditionInfo,
    Diagnostic,
    ObjectDetail,
    ObjectPage,
    ObjectSummary,
    Package,
    PackagePage,
    PackageVersion,
    ReferenceInfo,
    RepositoryInfo,
    ScanSummary,
    ValidationIssue,
    ValidationReport,
)
from munki_web_studio.codec import ParseError, load
from munki_web_studio.codec.model import MAX_FILE_BYTES
from munki_web_studio.repository.config import Repository, Settings
from munki_web_studio.repository.index import Index, IndexedObject
from munki_web_studio.repository.paths import PathEscapeError, normalize, read_contained
from munki_web_studio.repository.references import graph_for, object_conditions, validate
from munki_web_studio.repository.scanner import scan

router = APIRouter(prefix="/api/repositories", tags=["repositories"])

Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _index(request: Request) -> Index:
    return request.app.state.index


def _repo(request: Request, repo_id: str) -> Repository:
    try:
        return _settings(request).repository(repo_id)
    except KeyError:
        raise HTTPException(404, f"No repository {repo_id!r}") from None


def _summary(obj: IndexedObject) -> ObjectSummary:
    diagnostic = None
    if obj.error:
        diagnostic = Diagnostic(message=obj.error, line=obj.error_line, column=obj.error_column)
    return ObjectSummary(
        path=obj.path,
        kind=obj.kind,
        format=obj.format,
        status=obj.status,
        revision=obj.revision,
        name=obj.name,
        version=obj.version,
        display_name=obj.display_name,
        category=obj.category,
        catalogs=obj.catalogs,
        diagnostic=diagnostic,
    )


def _matches(obj: IndexedObject, q: str | None) -> bool:
    if not q:
        return True
    haystack = " ".join(
        v for v in (obj.path, obj.name, obj.display_name, obj.version, obj.category) if v
    )
    return q.lower() in haystack.lower()


def _scan_summary(scan: dict[str, Any]) -> ScanSummary:
    return ScanSummary(
        started_at=scan["started_at"],
        finished_at=scan["finished_at"],
        files=scan["files"],
        read=scan["read"],
        removed=scan["removed"],
        changed=scan["changed"],
    )


@router.get("")
def list_repositories(request: Request) -> list[RepositoryInfo]:
    index = _index(request)
    result: list[RepositoryInfo] = []
    for repo in _settings(request).repositories:
        scan = index.last_scan(repo.id)
        result.append(
            RepositoryInfo(
                id=repo.id,
                name=repo.name,
                writable=repo.writable,
                live=repo.live,
                last_scan=_scan_summary(scan) if scan else None,
            )
        )
    return result


@router.get("/{repo_id}/packages")
def list_packages(
    request: Request,
    repo_id: str,
    q: str | None = None,
    category: str | None = None,
    catalog: str | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> PackagePage:
    """Packages grouped by name, each with every version that exists."""
    repo = _repo(request, repo_id)
    groups: dict[str, list[IndexedObject]] = {}
    for obj in _index(request).all(repo.id, "pkginfo"):
        if obj.status != "ok" or not obj.name:
            continue
        if category and obj.category != category:
            continue
        if catalog and catalog not in obj.catalogs:
            continue
        if not _matches(obj, q):
            continue
        groups.setdefault(obj.name, []).append(obj)
    names = sorted(groups, key=str.lower)
    page = [_package(name, groups[name]) for name in names[offset : offset + limit]]
    return PackagePage(items=page, total=len(names), limit=limit, offset=offset)


def _package(name: str, objects: list[IndexedObject]) -> Package:
    # The newest version that sets a field wins, so one sparse copy does not hide it.
    display_name = next((o.display_name for o in reversed(objects) if o.display_name), None)
    category = next((o.category for o in reversed(objects) if o.category), None)
    return Package(
        name=name,
        display_name=display_name,
        category=category,
        versions=[
            PackageVersion(
                version=o.version,
                path=o.path,
                catalogs=o.catalogs,
                status=o.status,
                installer_type=o.summary.get("installer_type"),
            )
            for o in objects
        ],
    )


@router.get("/{repo_id}/packages/{name}")
def get_package(request: Request, repo_id: str, name: str) -> Package:
    repo = _repo(request, repo_id)
    objects = [o for o in _index(request).all(repo.id, "pkginfo") if o.name == name]
    if not objects:
        raise HTTPException(404, f"No package named {name!r}")
    return _package(name, objects)


@router.get("/{repo_id}/objects")
def list_objects(
    request: Request,
    repo_id: str,
    kind: str | None = None,
    q: str | None = None,
    status: str | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> ObjectPage:
    """Every indexed file, including invalid ones, filtered by kind, text, or status."""
    repo = _repo(request, repo_id)
    found = [
        o
        for o in _index(request).all(repo.id, kind)
        if _matches(o, q) and (status is None or o.status == status)
    ]
    items = [_summary(o) for o in found[offset : offset + limit]]
    return ObjectPage(items=items, total=len(found), limit=limit, offset=offset)


@router.get("/{repo_id}/objects/{path:path}")
def get_object(request: Request, repo_id: str, path: str) -> ObjectDetail:
    """One file: its index entry and its contents as tagged JSON."""
    repo = _repo(request, repo_id)
    try:
        relative = normalize(path)
    except PathEscapeError:
        raise HTTPException(400, "Invalid path") from None
    obj = _index(request).get(repo.id, relative)
    if obj is None:
        raise HTTPException(404, f"No object at {relative!r}")
    detail = ObjectDetail(**_summary(obj).model_dump())
    if obj.status != "ok":
        return detail
    try:
        read = read_contained(repo.root, relative, MAX_FILE_BYTES)
        if read.raw is None:
            raise ParseError("File is too large")
        document = load(read.raw)
    except (PathEscapeError, ParseError, OSError) as error:
        detail.status = "invalid"
        detail.diagnostic = Diagnostic(message=str(error))
        return detail
    detail.revision = document.revision
    detail.data = tagged.encode(document.data)
    return detail


@router.get("/{repo_id}/catalogs")
def list_catalogs(request: Request, repo_id: str) -> list[CatalogSummary]:
    """Catalog names from pkginfo membership, with how many pkginfo list each."""
    repo = _repo(request, repo_id)
    counts: dict[str, int] = {}
    for obj in _index(request).all(repo.id, "pkginfo"):
        if obj.status == "ok":
            for name in obj.catalogs:
                counts[name] = counts.get(name, 0) + 1
    return [CatalogSummary(name=n, items=counts[n]) for n in sorted(counts)]


@router.get("/{repo_id}/catalogs/{name}")
def get_catalog(
    request: Request, repo_id: str, name: str, limit: Limit = 100, offset: Offset = 0
) -> ObjectPage:
    """The pkginfo files that list this catalog."""
    repo = _repo(request, repo_id)
    found = [
        o
        for o in _index(request).all(repo.id, "pkginfo")
        if o.status == "ok" and name in o.catalogs
    ]
    items = [_summary(o) for o in found[offset : offset + limit]]
    return ObjectPage(items=items, total=len(found), limit=limit, offset=offset)


@router.get("/{repo_id}/where-used/{name}")
def where_used(request: Request, repo_id: str, name: str) -> list[ReferenceInfo]:
    """Manifests and packages that refer to a package or manifest by name."""
    repo = _repo(request, repo_id)
    refs = graph_for(_index(request), repo).where_used(name)
    return [
        ReferenceInfo(source=r.source, field=r.field, target=r.target, condition=r.condition)
        for r in refs
    ]


@router.get("/{repo_id}/validation")
def validation(request: Request, repo_id: str) -> ValidationReport:
    """Parse errors, missing references, include cycles, and missing payloads."""
    repo = _repo(request, repo_id)
    issues = [ValidationIssue(**vars(i)) for i in validate(_index(request), repo)]
    errors = sum(1 for i in issues if i.severity == "error")
    return ValidationReport(errors=errors, warnings=len(issues) - errors, issues=issues)


@router.get("/{repo_id}/conditions/{path:path}")
def explain_conditions(request: Request, repo_id: str, path: str) -> list[ConditionInfo]:
    """Every condition in one manifest, as a readable rule or a located error."""
    repo = _repo(request, repo_id)
    try:
        relative = normalize(path)
    except PathEscapeError:
        raise HTTPException(400, "Invalid path") from None
    obj = _index(request).get(repo.id, relative)
    if obj is None:
        raise HTTPException(404, f"No object at {relative!r}")
    graph = graph_for(_index(request), repo)
    return [ConditionInfo(**vars(conditions.analyze(t))) for t in object_conditions(graph, obj)]


@router.post("/{repo_id}/rescan")
def rescan(request: Request, repo_id: str) -> ScanSummary:
    """Look for changes made to the repository outside the studio, now."""
    repo = _repo(request, repo_id)
    index = _index(request)
    scan(repo, index)
    summary = index.last_scan(repo.id)
    if summary is None:
        raise HTTPException(500, "The scan did not record a result")
    return _scan_summary(summary)
