"""The MCP server: read tools for AI assistants. See docs/mcp.md.

Tools work through the same services as the API, so an assistant sees exactly
what the browser shows. Results are bounded; nothing here writes.
"""

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from munki_web_studio import conditions, platform, tagged
from munki_web_studio.codec import load
from munki_web_studio.codec.model import MAX_FILE_BYTES
from munki_web_studio.repository.config import Repository
from munki_web_studio.repository.index import Index, IndexedObject
from munki_web_studio.repository.paths import normalize, read_contained
from munki_web_studio.repository.references import graph_for, object_conditions, validate
from munki_web_studio.services import Services
from munki_web_studio.studio import STUDIO

Repo = Annotated[
    str | None, Field(description="Repository id; the first configured one if omitted")
]
Limit = Annotated[int, Field(ge=1, le=100, description="Most results to return")]


def _summary(obj: IndexedObject) -> dict[str, Any]:
    return {
        "path": obj.path,
        "kind": obj.kind,
        "status": obj.status,
        "name": obj.name,
        "version": obj.version,
        "category": obj.category,
        "catalogs": obj.catalogs,
        "error": obj.error,
    }


def create_mcp(services: Services | None = None) -> FastMCP:
    mcp = FastMCP(
        name=STUDIO.name,
        instructions=(
            f"Read-only tools for a {STUDIO.platform} repository. Paths are relative to the "
            "repository root and are the identity of every file. Values that JSON cannot "
            "carry exactly (dates, data, large integers, reals) are tagged with $type."
        ),
    )
    if services is None:
        return mcp

    def context(repo_id: str | None) -> tuple[Repository, Index]:
        return services.repository(repo_id), services.require_index()

    @mcp.tool(annotations={"readOnlyHint": True})
    def search_packages(
        query: Annotated[
            str, Field(description="Text in the name, display name, version, category, or path")
        ] = "",
        catalog: Annotated[str | None, Field(description="Only packages in this catalog")] = None,
        category: str | None = None,
        repository: Repo = None,
        limit: Limit = 25,
    ) -> dict[str, Any]:
        """Find pkginfo files. Each match is one version of a package."""
        repo, index = context(repository)
        needle = query.lower()
        found = [
            o
            for o in index.all(repo.id, "pkginfo")
            if (not catalog or catalog in o.catalogs)
            and (not category or o.category == category)
            and needle
            in " ".join(
                v for v in (o.path, o.name, o.display_name, o.version, o.category) if v
            ).lower()
        ]
        return {"total": len(found), "items": [_summary(o) for o in found[:limit]]}

    @mcp.tool(annotations={"readOnlyHint": True})
    def get_pkginfo(
        path: Annotated[str, Field(description="Relative path of the pkginfo file")],
        repository: Repo = None,
    ) -> dict[str, Any]:
        """One pkginfo's contents as tagged JSON, with its path and revision."""
        repo, index = context(repository)
        relative = normalize(path)
        obj = index.get(repo.id, relative)
        if obj is None or obj.kind != "pkginfo":
            raise ValueError(f"No pkginfo at {relative!r}")
        result = _summary(obj) | {"revision": obj.revision, "data": None}
        if obj.status == "ok":
            read = read_contained(repo.root, relative, MAX_FILE_BYTES)
            if read.raw is not None:
                document = load(read.raw)
                result |= {"revision": document.revision, "data": tagged.encode(document.data)}
        return result

    @mcp.tool(annotations={"readOnlyHint": True})
    def get_manifest(
        path: Annotated[str, Field(description="Relative path of the manifest file")],
        repository: Repo = None,
    ) -> dict[str, Any]:
        """One manifest: catalogs, includes, item sections, conditions as rules, and problems."""
        from munki_web_studio.api.manifests import build_manifest_view

        repo, index = context(repository)
        view = build_manifest_view(index, repo, normalize(path))
        if view is None:
            raise ValueError(f"No manifest at {path!r}")
        return view.model_dump()

    @mcp.tool(annotations={"readOnlyHint": True})
    def where_used(
        name: Annotated[str, Field(description="A package name or a manifest name")],
        repository: Repo = None,
        limit: Limit = 50,
    ) -> dict[str, Any]:
        """Manifests and packages that refer to a name, with the condition each sits under."""
        repo, index = context(repository)
        refs = graph_for(index, repo).where_used(name)
        return {"total": len(refs), "items": [vars(r) for r in refs[:limit]]}

    @mcp.tool(annotations={"readOnlyHint": True})
    def explain_conditions(
        path: Annotated[str, Field(description="Relative path of a manifest")],
        repository: Repo = None,
    ) -> list[dict[str, Any]]:
        """Each condition in a manifest as a readable rule, or its error and column."""
        repo, index = context(repository)
        obj = index.get(repo.id, normalize(path))
        if obj is None:
            raise ValueError(f"No file at {path!r}")
        graph = graph_for(index, repo)
        return [vars(conditions.analyze(t)) for t in object_conditions(graph, obj)]

    @mcp.tool(annotations={"readOnlyHint": True})
    def validate_repository(
        severity: Annotated[
            str | None, Field(description="error or warning; both if omitted")
        ] = None,
        repository: Repo = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 100,
    ) -> dict[str, Any]:
        """Parse errors, missing references, include cycles, condition problems, and payloads."""
        repo, index = context(repository)
        issues = validate(index, repo)
        errors = sum(1 for i in issues if i.severity == "error")
        chosen = [i for i in issues if severity is None or i.severity == severity]
        return {
            "errors": errors,
            "warnings": len(issues) - errors,
            "items": [vars(i) for i in chosen[:limit]],
        }

    @mcp.resource("studio://layout", mime_type="application/json")
    def layout() -> dict[str, Any]:
        """How this repository type is organized and which fields hold references."""
        return {
            "platform": STUDIO.platform,
            "folders": platform.KINDS,
            "manifest_item_sections": list(platform.ITEM_SECTIONS),
            "manifest_include_sections": list(platform.INCLUDE_SECTIONS),
            "pkginfo_dependency_keys": list(platform.DEPENDENCY_KEYS),
            "known_condition_facts": conditions.KNOWN_FACTS,
        }

    return mcp
