"""A manifest laid out for reading: sections, includes, and conditions as rules."""

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request

from munki_web_studio import conditions, platform
from munki_web_studio.api.models import (
    ConditionalBlock,
    ConditionInfo,
    Diagnostic,
    IncludedManifest,
    ManifestItem,
    ManifestSection,
    ManifestView,
    ValidationIssue,
)
from munki_web_studio.api.read import _index, _repo  # pyright: ignore[reportPrivateUsage]
from munki_web_studio.codec import ParseError, load
from munki_web_studio.codec.model import MAX_FILE_BYTES
from munki_web_studio.repository.config import Repository
from munki_web_studio.repository.index import Index
from munki_web_studio.repository.paths import PathEscapeError, normalize, read_contained
from munki_web_studio.repository.references import Graph, graph_for, manifest_name, validate

router = APIRouter(prefix="/api/repositories", tags=["manifests"])


def _texts(value: Any) -> list[str]:
    return (
        [v for v in cast(list[Any], value) if isinstance(v, str)] if isinstance(value, list) else []
    )


def _layout(
    graph: Graph, mapping: dict[str, Any]
) -> tuple[list[IncludedManifest], list[ManifestSection]]:
    includes = [
        IncludedManifest(name=n, path=graph.manifests[n].path if n in graph.manifests else None)
        for key in platform.INCLUDE_SECTIONS
        for n in _texts(mapping.get(key))
    ]
    sections = [
        ManifestSection(
            field=key,
            items=[
                ManifestItem(name=i, package=graph.resolve_package(i))
                for i in _texts(mapping.get(key))
            ],
        )
        for key in platform.ITEM_SECTIONS
        if _texts(mapping.get(key))
    ]
    return includes, sections


def build_manifest_view(index: Index, repo: Repository, relative: str) -> ManifestView | None:
    """One manifest laid out for reading, or None when there is no manifest there."""
    obj = index.get(repo.id, relative)
    if obj is None or obj.kind != "manifest":
        return None
    graph = graph_for(index, repo)
    name = manifest_name(relative)
    view = ManifestView(
        path=relative,
        name=name,
        revision=obj.revision,
        status=obj.status,
        diagnostic=Diagnostic(message=obj.error, line=obj.error_line, column=obj.error_column)
        if obj.error
        else None,
        catalogs=obj.catalogs,
        includes=[],
        sections=[],
        conditional=[],
        included_by=sorted(
            {r.source for r in graph.where_used(name) if r.field in platform.INCLUDE_SECTIONS}
        ),
        issues=[ValidationIssue(**vars(i)) for i in validate(index, repo) if i.path == relative],
    )
    if obj.status != "ok":
        return view
    try:
        read = read_contained(repo.root, relative, MAX_FILE_BYTES)
        if read.raw is None:
            raise ParseError("File is too large")
        document = load(read.raw)
        data = document.data
        view.revision = document.revision
    except (PathEscapeError, ParseError, OSError) as error:
        view.status = "invalid"
        view.diagnostic = Diagnostic(message=str(error))
        return view
    mapping = cast(dict[str, Any], data) if isinstance(data, dict) else {}
    view.includes, view.sections = _layout(graph, mapping)
    blocks = mapping.get("conditional_items")
    for block in cast(list[Any], blocks) if isinstance(blocks, list) else []:
        if not isinstance(block, dict):
            continue
        entry = cast(dict[str, Any], block)
        text = platform.condition_text(entry)
        includes, sections = _layout(graph, entry)
        view.conditional.append(
            ConditionalBlock(
                condition=ConditionInfo(**vars(conditions.analyze(text))) if text else None,
                includes=includes,
                sections=sections,
            )
        )
    return view


@router.get("/{repo_id}/manifests/{path:path}")
def get_manifest(request: Request, repo_id: str, path: str) -> ManifestView:
    repo = _repo(request, repo_id)
    try:
        relative = normalize(path)
    except PathEscapeError:
        raise HTTPException(400, "Invalid path") from None
    view = build_manifest_view(_index(request), repo, relative)
    if view is None:
        raise HTTPException(404, f"No manifest at {relative!r}")
    return view
