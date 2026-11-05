"""`mwsctl drafts`: the same drafts as the browser, from a terminal."""

import asyncio
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastmcp import Client
from tests.fixtures.build_repo_fixture import REPO
from tests.unit.fake_idp import FakeIdP
from tests.unit.test_auth import AUTH
from tests.unit.test_remote import URL, _Shared  # pyright: ignore[reportPrivateUsage]
from typer.testing import CliRunner

from munki_web_studio import mcp as studio_mcp
from munki_web_studio import remote
from munki_web_studio.app import create_app
from munki_web_studio.auth.mcp import StudioTokenVerifier
from munki_web_studio.cli import cli
from munki_web_studio.repository.config import Repository, Settings
from munki_web_studio.services import Services

runner = CliRunner()
EDITOR = "pkgsinfo/apps/creative/ExampleEditor-2.10.plist"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    copy = tmp_path / "repo"
    shutil.copytree(REPO, copy)
    return copy


@pytest.fixture
def studio(tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    idp = FakeIdP()
    settings = Settings(
        repositories=(Repository(id="fixture", name="Fixture", root=root, writable=True),),
        state_dir=tmp_path / "state",
        rescan_seconds=0,
        auth=AUTH,
    )
    app = create_app(settings, static_dir=tmp_path / "none", oidc_http=idp.http())
    with TestClient(app, base_url=URL, follow_redirects=False) as client:
        monkeypatch.setattr(remote, "studio_http", lambda url: _Shared(app, base_url=URL))
        monkeypatch.setattr(remote, "provider_http", idp.http)
        monkeypatch.setattr(remote.time, "sleep", lambda _: None)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        idp.device_claims = {
            "sub": "user-9",
            "name": "Terminal User",
            "roles": ["editors"],
        }
        assert runner.invoke(cli, ["login", "--url", URL]).exit_code == 0
        yield client


def run(*args: str, stdin: str | None = None) -> tuple[int, str, str]:
    result = runner.invoke(cli, ["drafts", *args, "--url", URL], input=stdin)
    return result.exit_code, result.stdout, result.stderr


def test_create_edit_diff_and_save(studio: TestClient, root: Path) -> None:
    code, out, _ = run("create", "Rename the editor")
    assert code == 0
    change_id = out.strip()
    code, out, err = run(
        "edit",
        change_id,
        EDITOR,
        "--set",
        "display_name=Example Editor Pro",
        "--set",
        "catalogs=production",
        "--set",
        "unattended_install=no",
    )
    assert code == 0, err
    assert "1 files" in out
    _, diff, _ = run("diff", change_id)
    assert "+\t<string>Example Editor Pro</string>" in diff
    assert "<false/>" in diff
    before = (root / EDITOR).read_bytes()

    code, _, _ = run("save", change_id, stdin="n\n")
    assert code == 1 and (root / EDITOR).read_bytes() == before

    code, out, _ = run("save", change_id, stdin="y\n")
    assert code == 0 and "Saved 1 file" in out
    assert b"<string>Example Editor Pro</string>" in (root / EDITOR).read_bytes()
    _, _, err = run("list")
    assert "No open drafts" in err


def test_bad_values_are_refused_before_sending(studio: TestClient) -> None:
    change_id = run("create", "x")[1].strip()
    code, _, err = run("edit", change_id, EDITOR, "--set", "unattended_install=maybe")
    assert code == 2 and "true or false" in err
    code, _, err = run("edit", change_id, EDITOR, "--set", "installer_item_hash=abc")
    assert code == 2 and "not an editable field" in err


def test_a_draft_from_an_mcp_token_shows_in_the_terminal(
    studio: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An assistant using this person's token drafts under their name."""
    token = runner.invoke(cli, ["token", "--url", URL]).stdout.strip()
    app = cast(FastAPI, studio.app)
    store = app.state.auth.store
    access = asyncio.run(StudioTokenVerifier(lambda: store).verify_token(token))
    monkeypatch.setattr(studio_mcp, "get_access_token", lambda: access)
    services = Services(app.state.settings, app.state.index, app.state.changes, app.state.audit)

    async def draft() -> dict[str, Any]:
        async with Client(studio_mcp.create_mcp(services)) as client:
            result = await client.call_tool(
                "draft_pkginfo_edit",
                {"path": EDITOR, "fields": {"display_name": "From MCP"}, "title": "From MCP"},
            )
            return cast(dict[str, Any], result.structured_content)

    drafted = asyncio.run(draft())
    assert drafted["owner"] == "Terminal User"
    _, out, _ = run("list")
    assert drafted["id"] in out and "From MCP" in out


def test_discard_asks_first(studio: TestClient) -> None:
    change_id = run("create", "x")[1].strip()
    assert run("discard", change_id, stdin="n\n")[0] == 1
    assert change_id in run("list")[1]
    assert run("discard", change_id, "--yes")[0] == 0
    assert change_id not in run("list")[1]
