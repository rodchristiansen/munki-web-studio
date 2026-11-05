from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.build_repo_fixture import REPO
from tests.unit.fake_idp import ISSUER, FakeIdP

from munki_web_studio.app import create_app
from munki_web_studio.auth.config import AuthSettings, parse_auth
from munki_web_studio.auth.model import Role
from munki_web_studio.repository.config import Repository, Settings

AUTH = parse_auth(
    {
        "mode": "oidc",
        "issuer": ISSUER,
        "client_id": "studio",
        "cli_client_id": "studio-cli",
        "public_url": "http://localhost",
        "roles": {"Viewer": ["viewers"], "Editor": ["editors"], "Administrator": ["admins"]},
    }
)


@pytest.fixture
def idp() -> FakeIdP:
    return FakeIdP()


@pytest.fixture
def client(tmp_path: Path, idp: FakeIdP) -> Iterator[TestClient]:
    settings = Settings(
        repositories=(Repository(id="fixture", name="Fixture", root=REPO.resolve()),),
        state_dir=tmp_path / "state",
        rescan_seconds=0,
        auth=AUTH,
    )
    app = create_app(settings, static_dir=tmp_path / "none", oidc_http=idp.http())
    with TestClient(app, base_url="http://localhost", follow_redirects=False) as test_client:
        yield test_client


def sign_in(client: TestClient, idp: FakeIdP, *, roles: list[str], next_path: str = "/") -> None:
    start = client.get("/auth/login", params={"next": next_path})
    assert start.status_code == 303
    query = parse_qs(urlparse(start.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"] and query["code_challenge"]
    idp.sign_in("code-1", query["nonce"][0], sub="user-1", name="Example Admin", roles=roles)
    done = client.get("/auth/callback", params={"code": "code-1", "state": query["state"][0]})
    assert done.status_code == 303, done.text
    assert done.headers["location"] == next_path


def test_signed_out_requests_are_refused(client: TestClient) -> None:
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/session").status_code == 401
    assert client.get("/api/repositories").status_code == 401
    assert client.get("/mcp/").status_code == 401


def test_every_api_route_needs_sign_in(client: TestClient) -> None:
    open_paths = {"/api/health", "/api/session"}
    for route in client.app.routes:  # type: ignore[attr-defined]
        path = getattr(route, "path", "")
        if not path.startswith("/api/") or path in open_paths:
            continue
        concrete = path.replace("{repo_id}", "fixture").replace(
            "{path:path}", "manifests/site_default"
        )
        concrete = concrete.replace("{name}", "ExampleEditor")
        for method in getattr(route, "methods", {"GET"}):
            response = client.request(method, concrete)
            assert response.status_code == 401, f"{method} {path} answered {response.status_code}"


def test_sign_in_sets_a_session_with_the_mapped_role(client: TestClient, idp: FakeIdP) -> None:
    sign_in(client, idp, roles=["editors", "viewers"], next_path="/?area=manifests")
    session = client.get("/api/session").json()
    assert session["role"] == "Editor" and session["name"] == "Example Admin"
    assert client.get("/api/repositories").status_code == 200
    cookie = client.cookies.get("studio_session")
    assert cookie and len(cookie) > 30


def test_people_without_a_role_are_refused(client: TestClient, idp: FakeIdP) -> None:
    start = client.get("/auth/login")
    query = parse_qs(urlparse(start.headers["location"]).query)
    idp.sign_in("code-2", query["nonce"][0], sub="user-2", roles=["nobody"])
    done = client.get("/auth/callback", params={"code": "code-2", "state": query["state"][0]})
    assert done.status_code == 403


def test_changes_need_the_csrf_token(client: TestClient, idp: FakeIdP) -> None:
    sign_in(client, idp, roles=["viewers"])
    token = client.get("/api/session").json()["csrf_token"]
    assert client.post("/api/repositories/fixture/rescan").status_code == 403
    assert (
        client.post(
            "/api/repositories/fixture/rescan", headers={"X-CSRF-Token": "wrong"}
        ).status_code
        == 403
    )
    assert (
        client.post("/api/repositories/fixture/rescan", headers={"X-CSRF-Token": token}).status_code
        == 200
    )


def test_logout_ends_the_session(client: TestClient, idp: FakeIdP) -> None:
    sign_in(client, idp, roles=["admins"])
    token = client.get("/api/session").json()["csrf_token"]
    assert client.post("/auth/logout", headers={"X-CSRF-Token": token}).status_code == 204
    assert client.get("/api/repositories").status_code == 401


def test_login_state_works_once(client: TestClient, idp: FakeIdP) -> None:
    start = client.get("/auth/login")
    query = parse_qs(urlparse(start.headers["location"]).query)
    idp.sign_in("code-3", query["nonce"][0], sub="u", roles=["viewers"])
    assert (
        client.get(
            "/auth/callback", params={"code": "code-3", "state": query["state"][0]}
        ).status_code
        == 303
    )
    idp.sign_in("code-4", query["nonce"][0], sub="u", roles=["viewers"])
    assert (
        client.get(
            "/auth/callback", params={"code": "code-4", "state": query["state"][0]}
        ).status_code
        == 400
    )
    assert client.get("/auth/callback", params={"code": "x", "state": "made-up"}).status_code == 400


@pytest.mark.parametrize("problem", ["wrong_nonce", "wrong_audience"])
def test_tampered_tokens_are_refused(client: TestClient, idp: FakeIdP, problem: str) -> None:
    setattr(idp, problem, True)
    start = client.get("/auth/login")
    query = parse_qs(urlparse(start.headers["location"]).query)
    idp.sign_in("code-5", query["nonce"][0], sub="u", roles=["admins"])
    done = client.get("/auth/callback", params={"code": "code-5", "state": query["state"][0]})
    assert done.status_code == 400
    assert client.get("/api/repositories").status_code == 401


@pytest.mark.parametrize("target", ["https://evil.example/", "//evil.example/", "/\\evil.example"])
def test_next_cannot_leave_the_studio(client: TestClient, idp: FakeIdP, target: str) -> None:
    start = client.get("/auth/login", params={"next": target})
    query = parse_qs(urlparse(start.headers["location"]).query)
    idp.sign_in("code-6", query["nonce"][0], sub="u", roles=["viewers"])
    done = client.get("/auth/callback", params={"code": "code-6", "state": query["state"][0]})
    assert done.headers["location"] == "/"


def test_role_mapping_takes_the_highest_role() -> None:
    assert AUTH.role_for(["viewers", "admins"]) is Role.ADMINISTRATOR
    assert AUTH.role_for(["nobody"]) is None
    assert AuthSettings(mode="oidc", default_role=Role.VIEWER).role_for([]) is Role.VIEWER


def test_auth_config_rules() -> None:
    with pytest.raises(ValueError, match="https"):
        parse_auth(
            {
                "mode": "oidc",
                "issuer": "x",
                "client_id": "y",
                "public_url": "http://studio.example.org",
            }
        )
    with pytest.raises(ValueError, match="needs"):
        parse_auth({"mode": "oidc"})
    with pytest.raises(ValueError, match="Unknown role"):
        parse_auth(
            {
                "mode": "oidc",
                "issuer": "x",
                "client_id": "y",
                "public_url": "https://s",
                "roles": {"Owner": []},
            }
        )
