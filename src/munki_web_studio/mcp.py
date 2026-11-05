"""The MCP server: read and draft tools for AI assistants. See docs/mcp.md.

Tools work through the same services as the API, so an assistant sees exactly
what the browser shows. Draft tools add edits to the caller's open change set,
which then shows up in the studio's review; no tool saves, so nothing an assistant
does reaches the repository until a person reviews it and saves it there.
"""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.server.auth import TokenVerifier
from fastmcp.server.dependencies import get_access_token, get_context
from pydantic import Field

from munki_web_studio import conditions, platform, tagged
from munki_web_studio.auth.model import LOCAL_USER, Role, User
from munki_web_studio.changes import drafting
from munki_web_studio.changes.apply import preview
from munki_web_studio.changes.model import ChangeSet, change_to_json
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
ChangeId = Annotated[
    str | None,
    Field(description="Change set to add to; your most recent open one, or a new one, if omitted"),
]
DRAFT = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True}
MAX_DIFF_CHARS = 20_000


def caller() -> User:
    """Who is calling: the signed-in user behind the bearer token, or the local
    administrator when the studio runs without sign-in."""
    token = get_access_token()
    if token is None:
        return LOCAL_USER
    claims = token.claims
    return User(
        subject=str(token.subject or claims.get("sub", "")),
        name=str(claims.get("name", "")),
        email=None,
        role=Role.parse(str(claims.get("role", "Viewer"))),
    )


def client_name() -> str:
    try:
        info = get_context().session.client_params
        return info.client_info.name if info else "mcp"
    except Exception:
        return "mcp"


@contextmanager
def refused() -> Generator[None]:
    """Report a refused draft edit to the assistant as a tool error."""
    try:
        yield
    except drafting.DraftError as error:
        raise ValueError(str(error)) from None


def _bounded(diff: str) -> str:
    if len(diff) <= MAX_DIFF_CHARS:
        return diff
    return (
        diff[:MAX_DIFF_CHARS]
        + "\n… diff truncated; open the draft in the studio to see all of it\n"
    )


def describe(repo: Repository, change_set: ChangeSet) -> dict[str, Any]:
    previews = {p.path: p for p in preview(repo, change_set)}
    files: list[dict[str, Any]] = []
    for draft in change_set.files:
        p = previews.get(draft.path)
        files.append(
            {
                "path": draft.path,
                "changes": [change_to_json(c) for c in draft.changes],
                "conflict": p.conflict if p else False,
                "error": p.error if p else None,
                "diff": _bounded(p.diff) if p else "",
            }
        )
    return {
        "id": change_set.id,
        "repository": change_set.repo_id,
        "title": change_set.title,
        "owner": change_set.owner_name,
        "updated_at": change_set.updated_at,
        "files": files,
        "next_step": "Review and save this draft in the studio; assistants cannot save.",
    }


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


def create_mcp(services: Services | None = None, auth: TokenVerifier | None = None) -> FastMCP:
    mcp = FastMCP(
        name=STUDIO.name,
        auth=auth,
        instructions=(
            f"Read-only tools for a {STUDIO.platform} repository. Paths are relative to the "
            "repository root and are the identity of every file. Values that JSON cannot "
            "carry exactly (dates, data, large integers, reals) are tagged with $type."
        ),
    )
    if services is None:
        return mcp

    def context(repo_id: str | None) -> tuple[Repository, Index]:
        if services.hosted and get_access_token() is None:
            raise PermissionError("Sign in through the host to use these tools")
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

    def editor() -> User:
        if services.hosted and get_access_token() is None:
            raise PermissionError("Sign in through the host to draft changes")
        user = caller()
        if user.role < Role.EDITOR:
            raise PermissionError("Drafting needs the Editor role")
        return user

    def draft_for(repo: Repository, user: User, change_id: str | None, title: str) -> ChangeSet:
        store = services.require_changes()
        if change_id:
            found = store.get(change_id)
            if (
                found is None
                or found.repo_id != repo.id
                or (found.owner != user.subject and user.role < Role.ADMINISTRATOR)
            ):
                raise ValueError(f"No change set {change_id!r}")
            return found
        mine = store.list(repo.id, user.subject)
        if mine:
            return mine[0]
        return store.create(repo.id, user.subject, user.name, title)

    def audited(
        user: User, repo: Repository, action: str, change_set: ChangeSet, detail: dict[str, Any]
    ) -> None:
        if services.audit is not None:
            services.audit.record(
                actor=user.subject,
                actor_name=user.name,
                interface="mcp",
                action=action,
                repo_id=repo.id,
                target=change_set.id,
                detail=detail | {"client": client_name()},
            )

    @mcp.tool(annotations={"readOnlyHint": True})
    def list_changes(repository: Repo = None) -> list[dict[str, Any]]:
        """Your open draft change sets, each file's edits, and the diff saving would make."""
        user = editor()
        repo = services.repository(repository)
        owner = None if user.role >= Role.ADMINISTRATOR else user.subject
        return [describe(repo, cs) for cs in services.require_changes().list(repo.id, owner)]

    @mcp.tool(annotations=DRAFT)
    def draft_pkginfo_edit(
        path: Annotated[str, Field(description="Relative path of the pkginfo file")],
        fields: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Field values as tagged JSON; null removes a field. "
                    "The studio://fields resource lists the fields and their types."
                )
            ),
        ],
        base_revision: Annotated[
            str | None,
            Field(description="Revision from get_pkginfo; refuses the edit if the file changed"),
        ] = None,
        change_set: ChangeId = None,
        title: Annotated[str, Field(max_length=200)] = "Drafted by an assistant",
        repository: Repo = None,
    ) -> dict[str, Any]:
        """Propose field changes to one pkginfo. Adds to a draft; does not save."""
        user = editor()
        repo, index = context(repository)
        draft = draft_for(repo, user, change_set, title)
        values = {k: tagged.decode(v) for k, v in fields.items()}
        with refused():
            updated = drafting.edit_fields(
                repo, index, services.require_changes(), draft, path, values, base_revision
            )
        audited(user, repo, "draft", updated, {"path": path, "fields": sorted(fields)})
        return describe(repo, updated)

    @mcp.tool(annotations=DRAFT)
    def draft_manifest_edit(
        path: Annotated[str, Field(description="Relative path of the manifest")],
        add: Annotated[
            dict[str, list[str]] | None,
            Field(description="Names to append, by section: {managed_installs: [...]}"),
        ] = None,
        remove: Annotated[
            dict[str, list[str]] | None,
            Field(description="Names to take out, by section"),
        ] = None,
        order: Annotated[
            dict[str, list[str]] | None,
            Field(
                description=(
                    "A section's complete new list, to move items or replace it; "
                    "applied before add and remove"
                )
            ),
        ] = None,
        base_revision: str | None = None,
        change_set: ChangeId = None,
        title: Annotated[str, Field(max_length=200)] = "Drafted by an assistant",
        repository: Repo = None,
    ) -> dict[str, Any]:
        """Propose adding, removing, or moving manifest items, includes, or catalogs.
        Sections are the manifest's top-level lists. Adds to a draft; does not save."""
        user = editor()
        repo, index = context(repository)
        store = services.require_changes()
        draft = draft_for(repo, user, change_set, title)
        sections: dict[str, list[str]] = {}
        with refused():
            target = drafting.open_target(repo, index, draft, path, "manifest")
            adding: dict[str, list[str]] = add or {}
            removing: dict[str, list[str]] = remove or {}
            ordering: dict[str, list[str]] = order or {}
            names: set[str] = {*adding, *removing, *ordering}
            for name in sorted(names):
                items = list(ordering.get(name) or drafting.drafted_list(target, name))
                items += [n for n in adding.get(name, []) if n not in items]
                gone = set(removing.get(name, []))
                sections[name] = [n for n in items if n not in gone]
            if not sections:
                raise ValueError("Give at least one of add, remove, or order")
            updated = drafting.edit_sections(
                repo, index, store, draft, path, sections, base_revision
            )
        audited(user, repo, "draft", updated, {"path": path, "sections": sorted(sections)})
        return describe(repo, updated)

    @mcp.tool(annotations=DRAFT)
    def draft_catalog_move(
        paths: Annotated[
            list[str], Field(min_length=1, max_length=100, description="pkginfo files to change")
        ],
        add: Annotated[list[str] | None, Field(description="Catalogs to add each to")] = None,
        remove: Annotated[
            list[str] | None, Field(description="Catalogs to take each out of")
        ] = None,
        change_set: ChangeId = None,
        title: Annotated[str, Field(max_length=200)] = "Catalog changes drafted by an assistant",
        repository: Repo = None,
    ) -> dict[str, Any]:
        """Propose catalog membership changes for packages, for example moving a version
        from testing to production. Catalog files themselves are rebuilt later by
        makecatalogs. Adds to a draft; does not save."""
        if not add and not remove:
            raise ValueError("Give catalogs to add, remove, or both")
        user = editor()
        repo, index = context(repository)
        store = services.require_changes()
        draft = draft_for(repo, user, change_set, title)
        gone = set(remove or [])
        with refused():
            for path in paths:
                target = drafting.open_target(repo, index, draft, path, "pkginfo")
                current = drafting.drafted_list(target, "catalogs")
                wanted = [c for c in current if c not in gone]
                wanted += [c for c in add or [] if c not in wanted]
                draft = drafting.edit_fields(
                    repo, index, store, draft, path, {"catalogs": wanted or None}
                )
        audited(user, repo, "draft", draft, {"paths": paths, "add": add, "remove": remove})
        return describe(repo, draft)

    @mcp.resource("studio://fields", mime_type="application/json")
    def fields_resource() -> dict[str, str]:
        """pkginfo fields draft_pkginfo_edit can set, and the type each takes."""
        return dict(platform.PKGINFO_FIELDS)

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
