"""MCP draft tools: they add to a person's draft, which the studio shows for review,
and nothing an assistant can call writes to the repository."""

import asyncio
import hashlib
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.exceptions import ToolError
from tests.fixtures.build_repo_fixture import REPO

from munki_web_studio import mcp as studio_mcp
from munki_web_studio.app import create_app
from munki_web_studio.audit import AuditLog
from munki_web_studio.auth.model import Role, User
from munki_web_studio.changes.store import ChangeSetStore
from munki_web_studio.repository.config import Repository, Settings
from munki_web_studio.repository.index import Index
from munki_web_studio.repository.scanner import scan
from munki_web_studio.services import Services

EDITOR = "pkgsinfo/apps/creative/ExampleEditor-2.10.plist"
BROWSER = "pkgsinfo/apps/browsers/ExampleBrowser-136.0.plist"
SITE = "manifests/site_default"


def tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    root = tmp_path / "repo"
    shutil.copytree(REPO, root)
    repo = Repository(id="fixture", name="Fixture", root=root.resolve(), writable=True)
    return Settings(repositories=(repo,), state_dir=tmp_path / "state", rescan_seconds=0)


@pytest.fixture
def services(settings: Settings) -> Iterator[Services]:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    index = Index(settings.state_dir / "index.sqlite")
    scan(settings.repositories[0], index)
    changes = ChangeSetStore(settings.state_dir / "drafts.sqlite")
    audit = AuditLog(settings.state_dir / "audit.sqlite")
    yield Services(settings, index, changes, audit)
    changes.close()
    audit.close()
    index.close()


def call(services: Services, tool: str, **arguments: Any) -> Any:
    async def run() -> Any:
        async with Client(studio_mcp.create_mcp(services)) as client:
            return (await client.call_tool(tool, arguments)).structured_content

    return asyncio.run(run())


def test_pkginfo_edit_drafts_without_writing(services: Services, settings: Settings) -> None:
    root = settings.repositories[0].root
    before = tree(root)
    result = call(
        services,
        "draft_pkginfo_edit",
        path=EDITOR,
        fields={"display_name": "Example Editor Pro"},
    )
    assert tree(root) == before
    [file] = result["files"]
    assert file["path"] == EDITOR
    assert "+\t<string>Example Editor Pro</string>" in file["diff"]
    assert "cannot save" in result["next_step"]
    entry = services.audit.entries()[0] if services.audit else None
    assert entry is not None and entry.interface == "mcp" and entry.action == "draft"


def test_later_edits_join_the_same_draft(services: Services) -> None:
    first = call(services, "draft_pkginfo_edit", path=EDITOR, fields={"display_name": "A"})
    second = call(services, "draft_pkginfo_edit", path=BROWSER, fields={"category": "Web"})
    assert first["id"] == second["id"]
    assert [f["path"] for f in second["files"]] == [BROWSER, EDITOR]
    listed = call(services, "list_changes")
    assert [c["id"] for c in listed["result"]] == [first["id"]]


def test_mcp_draft_appears_in_the_studio(services: Services, settings: Settings) -> None:
    drafted = call(services, "draft_pkginfo_edit", path=EDITOR, fields={"display_name": "X"})
    app = create_app(settings, static_dir=settings.state_dir / "none")
    with TestClient(app, base_url="http://localhost") as web:
        [change_set] = web.get("/api/repositories/fixture/changesets").json()
    assert change_set["id"] == drafted["id"]
    assert change_set["files"][0]["path"] == EDITOR
    assert "<string>X</string>" in change_set["files"][0]["diff"]


def test_manifest_edit_adds_removes_and_moves(services: Services) -> None:
    result = call(
        services,
        "draft_manifest_edit",
        path=SITE,
        order={"managed_installs": ["ExampleRuntime", "ExampleBrowser"]},
        add={
            "managed_installs": ["ExampleEditor"],
            "optional_installs": ["ExampleCLI"],
        },
    )
    diff = result["files"][0]["diff"]
    assert "+\t\t<string>ExampleEditor</string>" in diff
    assert "<key>optional_installs</key>" in diff
    again = call(
        services,
        "draft_manifest_edit",
        path=SITE,
        remove={"managed_installs": ["ExampleBrowser"]},
    )
    changes = again["files"][0]["changes"]
    installs = next(c for c in changes if c["path"] == ["managed_installs"])
    assert installs["value"] == ["ExampleRuntime", "ExampleEditor"]


def test_catalog_move(services: Services) -> None:
    result = call(
        services,
        "draft_catalog_move",
        paths=[EDITOR, BROWSER],
        add=["production"],
        remove=["testing"],
    )
    by_path = {f["path"]: f["changes"] for f in result["files"]}
    assert by_path[EDITOR] == [{"op": "set", "path": ["catalogs"], "value": ["production"]}]
    assert by_path[BROWSER] == [{"op": "set", "path": ["catalogs"], "value": ["production"]}]


def test_no_tool_can_save(services: Services, settings: Settings) -> None:
    async def names() -> set[str]:
        async with Client(studio_mcp.create_mcp(services)) as client:
            return {t.name for t in await client.list_tools()}

    tools = asyncio.run(names())
    assert not {t for t in tools if any(w in t for w in ("save", "commit", "publish", "write"))}
    root = settings.repositories[0].root
    before = tree(root)
    call(services, "draft_pkginfo_edit", path=EDITOR, fields={"display_name": "Y"})
    call(
        services,
        "draft_manifest_edit",
        path=SITE,
        add={"managed_installs": ["ExampleEditor"]},
    )
    call(services, "draft_catalog_move", paths=[BROWSER], add=["production"])
    assert tree(root) == before
    assert services.changes is not None
    [open_set] = services.changes.list("fixture")
    assert open_set.status == "open"


def test_stale_revision_and_bad_fields_are_refused(services: Services) -> None:
    with pytest.raises(ToolError, match="changed since"):
        call(
            services,
            "draft_pkginfo_edit",
            path=EDITOR,
            fields={"display_name": "Z"},
            base_revision="0" * 64,
        )
    with pytest.raises(ToolError, match="not an editable field"):
        call(
            services,
            "draft_pkginfo_edit",
            path=EDITOR,
            fields={"installer_item_hash": "x"},
        )
    with pytest.raises(ToolError, match="Invalid path"):
        call(
            services,
            "draft_pkginfo_edit",
            path="../outside",
            fields={"display_name": "Z"},
        )


def test_viewers_cannot_draft(services: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    viewer = User(subject="v", name="Viewer", email=None, role=Role.VIEWER)
    monkeypatch.setattr(studio_mcp, "caller", lambda: viewer)
    with pytest.raises(ToolError, match="Editor role"):
        call(services, "draft_pkginfo_edit", path=EDITOR, fields={"display_name": "Z"})
    with pytest.raises(ToolError, match="Editor role"):
        call(services, "list_changes")
