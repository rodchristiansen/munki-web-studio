"""The hosting contract, exercised by a fake host that mounts the studio."""

import asyncio
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastmcp import Client, FastMCP
from tests.fixtures.build_repo_fixture import REPO
from tests.unit.fake_host import build_host

from munki_web_studio import hosting
from munki_web_studio.repository.config import Repository, Settings

EDITOR = "pkgsinfo/apps/creative/ExampleEditor-2.10.plist"


@pytest.fixture
def mounted(tmp_path: Path) -> Iterator[tuple[TestClient, FastAPI, FastMCP]]:
    root = tmp_path / "repo"
    shutil.copytree(REPO, root)
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text('<html><head><base href="/" /></head><body></body></html>')
    settings = Settings(
        repositories=(Repository(id="fixture", name="Fixture", root=root, writable=True),),
        state_dir=tmp_path / "state",
        rescan_seconds=0,
        # A host checks host names itself; the studio must not refuse its name.
        allowed_hosts=("localhost", "127.0.0.1"),
    )
    host, studio, host_mcp = build_host(settings, static)
    with TestClient(host, base_url="http://fleetmate.example.org") as client:
        yield client, studio, host_mcp


def as_user(client: TestClient, name: str) -> dict[str, str]:
    return {"X-Host-User": name}


def test_the_manifest_describes_the_studio() -> None:
    info = hosting.manifest()
    assert info.prefix == "/munki" and info.mcp_prefix == "munki"
    assert info.contract_version == hosting.CONTRACT_VERSION and info.platform == "Munki"
    assert set(info.to_dict()) >= {"name", "version", "accent", "icon", "ui"}


def test_the_host_mounts_the_studio_under_its_prefix(
    mounted: tuple[TestClient, FastAPI, FastMCP],
) -> None:
    client, _, _ = mounted
    assert client.get("/munki/api/health").json()["status"] == "ok"
    repos = client.get("/munki/api/repositories", headers=as_user(client, "ada")).json()
    assert [r["id"] for r in repos] == ["fixture"]
    page = client.get("/munki/").text
    assert '<base href="/munki/" />' in page


def test_the_hosts_identity_replaces_the_studios_sign_in(
    mounted: tuple[TestClient, FastAPI, FastMCP],
) -> None:
    client, _, _ = mounted
    assert client.get("/munki/api/repositories").status_code == 401
    session = client.get("/munki/api/session", headers=as_user(client, "ada")).json()
    assert session == {
        "name": "Ada Host",
        "email": "ada@example.org",
        "role": "Editor",
        "csrf_token": "csrf-ada",
        "sign_in": "host",
    }
    for path in ("/munki/auth/login", "/munki/api/auth/config"):
        assert client.get(path).status_code == 404
    assert client.post("/munki/auth/token", json={"id_token": "x"}).status_code == 404


def test_roles_and_csrf_come_from_the_host(
    mounted: tuple[TestClient, FastAPI, FastMCP],
) -> None:
    client, _, _ = mounted
    body = {"title": "Hosted edit"}
    no_token = client.post(
        "/munki/api/repositories/fixture/changesets", json=body, headers=as_user(client, "ada")
    )
    assert no_token.status_code == 403
    headers = {**as_user(client, "ada"), "X-CSRF-Token": "csrf-ada"}
    created = client.post("/munki/api/repositories/fixture/changesets", json=body, headers=headers)
    assert created.status_code == 200 and created.json()["owner"] == "host:ada"
    viewer = {**as_user(client, "val"), "X-CSRF-Token": "csrf-val"}
    assert (
        client.post(
            "/munki/api/repositories/fixture/changesets", json=body, headers=viewer
        ).status_code
        == 403
    )


def test_jobs_settings_and_health_for_the_host(
    mounted: tuple[TestClient, FastAPI, FastMCP], tmp_path: Path
) -> None:
    client, _, _ = mounted
    report = client.get("/diagnostics").json()
    assert report["munki"]["repositories"][0]["files"] > 0
    names = {j["name"] for j in report["jobs"]}
    assert {"repository.validate", "catalog.generate", "package.import"} <= names
    good, error = hosting.validate_settings(
        {"repositories": [{"id": "main", "path": str(REPO)}]}, tmp_path
    )
    assert good is not None and error is None
    bad, error = hosting.validate_settings({"repositories": [{"id": "x y", "path": "."}]}, tmp_path)
    assert bad is None and error and "simple name" in error


def test_the_host_mounts_the_studios_mcp_tools_with_a_prefix(
    mounted: tuple[TestClient, FastAPI, FastMCP],
) -> None:
    _, _, host_mcp = mounted

    async def names() -> set[str]:
        async with Client(host_mcp) as mcp:
            return {t.name for t in await mcp.list_tools()}

    tools = asyncio.run(names())
    assert {"munki_search_packages", "munki_draft_pkginfo_edit"} <= tools
    assert not any(not t.startswith("munki_") for t in tools)


def test_standalone_behaviour_is_unchanged(tmp_path: Path) -> None:
    from munki_web_studio.app import create_app

    settings = Settings(
        repositories=(Repository(id="fixture", name="Fixture", root=REPO.resolve()),),
        state_dir=tmp_path / "state",
        rescan_seconds=0,
    )
    with TestClient(
        create_app(settings, static_dir=tmp_path / "none"), base_url="http://evil.example"
    ) as c:
        assert c.get("/api/health").status_code == 400


def test_hosted_mcp_drafting_needs_the_hosts_token(
    mounted: tuple[TestClient, FastAPI, FastMCP],
) -> None:
    from fastmcp.exceptions import ToolError

    _, _, host_mcp = mounted

    async def draft() -> None:
        async with Client(host_mcp) as mcp:
            await mcp.call_tool(
                "munki_draft_pkginfo_edit", {"path": EDITOR, "fields": {"display_name": "X"}}
            )

    with pytest.raises(ToolError, match="Sign in through the host"):
        asyncio.run(draft())


def test_a_hosted_studio_has_no_open_mcp_endpoint(
    mounted: tuple[TestClient, FastAPI, FastMCP],
) -> None:
    client, _, host_mcp = mounted
    # Nothing answers there: the static workspace refuses the POST.
    assert client.post("/munki/mcp/", json={}).status_code in (404, 405)

    from fastmcp.exceptions import ToolError

    async def search() -> None:
        async with Client(host_mcp) as mcp:
            await mcp.call_tool("munki_search_packages", {"query": "editor"})

    with pytest.raises(ToolError, match="Sign in through the host"):
        asyncio.run(search())
