import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.build_repo_fixture import REPO
from tests.unit.fake_idp import FakeIdP
from tests.unit.test_auth import AUTH
from typer.testing import CliRunner

from munki_web_studio import remote
from munki_web_studio.app import create_app
from munki_web_studio.cli import cli
from munki_web_studio.repository.config import Repository, Settings

URL = "http://localhost"
runner = CliRunner()


@pytest.fixture
def idp() -> FakeIdP:
    return FakeIdP()


@pytest.fixture
def studio(tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    settings = Settings(
        repositories=(Repository(id="fixture", name="Fixture", root=REPO.resolve()),),
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
        yield client


class _Shared(TestClient):
    """Talks to the app the fixture already started, without starting it again."""

    def __enter__(self) -> _Shared:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def log_in(idp: FakeIdP, roles: list[str]) -> None:
    idp.device_claims = {"sub": "user-9", "name": "Terminal User", "roles": roles}
    result = runner.invoke(cli, ["login", "--url", URL])
    assert result.exit_code == 0, result.output
    assert "ABCD-EFGH" in result.stdout and "Terminal User (Viewer)" in result.stdout


def test_remote_commands_need_sign_in(studio: TestClient) -> None:
    result = runner.invoke(cli, ["search", "editor", "--url", URL])
    assert result.exit_code == 2 and "login" in result.stderr


def test_device_sign_in_stores_a_private_token(studio: TestClient, idp: FakeIdP) -> None:
    log_in(idp, ["viewers"])
    path = remote.credentials_path()
    if sys.platform != "win32":  # Windows ignores chmod; the file inherits the profile's ACL.
        assert path.stat().st_mode & 0o777 == 0o600
    token = runner.invoke(cli, ["token", "--url", URL]).stdout.strip()
    assert (
        studio.get("/api/session", headers={"Authorization": f"Bearer {token}"}).json()["role"]
        == "Viewer"
    )


def test_remote_search_matches_local_search(studio: TestClient, idp: FakeIdP) -> None:
    log_in(idp, ["viewers"])
    remote_out = runner.invoke(cli, ["search", "editor", "--url", URL]).stdout
    local_out = runner.invoke(cli, ["search", "editor", "--repo", str(REPO)]).stdout
    assert remote_out == local_out and "ExampleEditor" in remote_out


def test_remote_where_used_and_validate(studio: TestClient, idp: FakeIdP) -> None:
    log_in(idp, ["viewers"])
    used = runner.invoke(cli, ["where-used", "ExampleEditor", "--url", URL])
    assert "manifests/groups/design-lab\tmanaged_installs" in used.stdout
    assert runner.invoke(cli, ["validate", "--url", URL]).exit_code == 1


def test_logout_revokes_the_token(studio: TestClient, idp: FakeIdP) -> None:
    log_in(idp, ["viewers"])
    token = runner.invoke(cli, ["token", "--url", URL]).stdout.strip()
    assert runner.invoke(cli, ["logout", "--url", URL]).exit_code == 0
    assert (
        studio.get("/api/session", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    )
    assert runner.invoke(cli, ["token", "--url", URL]).exit_code == 2


def test_bearer_requests_need_no_csrf_token(studio: TestClient, idp: FakeIdP) -> None:
    log_in(idp, ["viewers"])
    token = runner.invoke(cli, ["token", "--url", URL]).stdout.strip()
    rescan = studio.post(
        "/api/repositories/fixture/rescan", headers={"Authorization": f"Bearer {token}"}
    )
    assert rescan.status_code == 200


def mcp_initialize(client: TestClient, token: str | None) -> Any:
    headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"},
        },
    }
    return client.post("/mcp/", json=body, headers=headers)


def test_mcp_needs_a_studio_token(studio: TestClient, idp: FakeIdP) -> None:
    assert mcp_initialize(studio, None).status_code == 401
    assert mcp_initialize(studio, "made-up").status_code == 401
    log_in(idp, ["viewers"])
    token = runner.invoke(cli, ["token", "--url", URL]).stdout.strip()
    assert mcp_initialize(studio, token).status_code == 200


def test_provider_tokens_for_other_clients_are_refused(studio: TestClient, idp: FakeIdP) -> None:
    idp.device_claims = {"sub": "u", "name": "U", "roles": ["viewers"]}
    idp.wrong_audience = True
    result = runner.invoke(cli, ["login", "--url", URL])
    assert result.exit_code == 1 and "refused" in result.stderr
