import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.build_repo_fixture import REPO

from munki_web_studio import tagged
from munki_web_studio.app import create_app
from munki_web_studio.codec import load
from munki_web_studio.repository.config import Repository, Settings

SCHEMA = Path(__file__).resolve().parents[2] / "web" / "openapi.json"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    root = tmp_path / "repo"
    shutil.copytree(REPO, root)
    settings = Settings(
        repositories=(Repository(id="fixture", name="Fixture", root=root.resolve()),),
        state_dir=tmp_path / "state",
    )
    with TestClient(create_app(settings, static_dir=tmp_path / "none")) as test_client:
        yield test_client


def test_lists_repositories_with_scan_state(client: TestClient) -> None:
    repos = client.get("/api/repositories").json()
    assert [r["id"] for r in repos] == ["fixture"]
    assert repos[0]["last_scan"]["files"] > 0
    assert repos[0]["writable"] is False


def test_packages_are_grouped_with_every_version(client: TestClient) -> None:
    page = client.get("/api/repositories/fixture/packages").json()
    names = [p["name"] for p in page["items"]]
    assert names == sorted(names, key=str.lower)
    browser = next(p for p in page["items"] if p["name"] == "ExampleBrowser")
    assert len(browser["versions"]) == 3
    assert len({v["path"] for v in browser["versions"]}) == 3


def test_package_search_and_filters(client: TestClient) -> None:
    base = "/api/repositories/fixture/packages"
    assert [p["name"] for p in client.get(base, params={"q": "plugin"}).json()["items"]] == [
        "ExampleEditorPlugin"
    ]
    production = client.get(base, params={"catalog": "production"}).json()["items"]
    assert all(any("production" in v["catalogs"] for v in p["versions"]) for p in production)
    creative = client.get(base, params={"category": "Creative"}).json()["items"]
    assert {p["category"] for p in creative} == {"Creative"}
    paged = client.get(base, params={"limit": 2, "offset": 1}).json()
    assert len(paged["items"]) == 2 and paged["total"] > 2


def test_object_detail_returns_tagged_contents(client: TestClient) -> None:
    path = "pkgsinfo/apps/creative/ExampleEditor-2.10.plist"
    detail = client.get(f"/api/repositories/fixture/objects/{path}").json()
    expected = load((REPO / path).read_bytes())
    assert detail["path"] == path
    assert detail["revision"] == expected.revision
    assert tagged.decode(detail["data"]) == expected.data


def test_invalid_objects_are_listed_with_diagnostics(client: TestClient) -> None:
    invalid = client.get("/api/repositories/fixture/objects", params={"status": "invalid"}).json()
    paths = {o["path"] for o in invalid["items"]}
    assert "pkgsinfo/invalid/Broken-1.0.plist" in paths
    broken = client.get(
        "/api/repositories/fixture/objects/pkgsinfo/invalid/Broken-1.0.plist"
    ).json()
    assert broken["status"] == "invalid" and broken["diagnostic"]["line"] is not None
    assert broken["data"] is None


def test_manifests_and_catalogs(client: TestClient) -> None:
    manifests = client.get("/api/repositories/fixture/objects", params={"kind": "manifest"}).json()
    assert "manifests/groups/design-lab" in {o["path"] for o in manifests["items"]}
    catalogs = {
        c["name"]: c["items"] for c in client.get("/api/repositories/fixture/catalogs").json()
    }
    assert set(catalogs) == {"testing", "production"}
    production = client.get("/api/repositories/fixture/catalogs/production").json()
    assert production["total"] == catalogs["production"]


@pytest.mark.parametrize(
    "path",
    ["../outside", "pkgsinfo/../../outside", "%2e%2e/outside", "pkgsinfo//x", "/etc/passwd"],
)
def test_paths_outside_the_repository_are_refused(client: TestClient, path: str) -> None:
    response = client.get(f"/api/repositories/fixture/objects/{path}")
    assert response.status_code in (400, 404)
    assert "root:" not in response.text


def test_unknown_repository_is_not_found(client: TestClient) -> None:
    assert client.get("/api/repositories/nope/packages").status_code == 404


def test_committed_openapi_schema_is_current() -> None:
    current = json.loads(json.dumps(create_app().openapi(), sort_keys=True))
    committed = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert current == committed, "Run `uv run mwsctl openapi --output web/openapi.json`"
