import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.build_repo_fixture import REPO

from munki_web_studio.app import create_app
from munki_web_studio.changes.fields import FieldError, manifest_changes
from munki_web_studio.codec import Append, Delete, Set
from munki_web_studio.repository.config import Repository, Settings

LAB = "manifests/groups/design-lab"


def test_smallest_change_for_each_section() -> None:
    data = {"catalogs": ["testing", "production"], "managed_installs": ["A", "B"]}
    assert manifest_changes(data, {"catalogs": ["testing", "production"]}) == []
    assert manifest_changes(data, {"managed_installs": ["A", "B", "C", "D"]}) == [
        Append(("managed_installs",), "C"),
        Append(("managed_installs",), "D"),
    ]
    assert manifest_changes(data, {"catalogs": ["production", "testing"]}) == [
        Set(("catalogs",), ["production", "testing"])
    ]
    assert manifest_changes(data, {"managed_installs": []}) == [Delete(("managed_installs",))]
    assert manifest_changes(data, {"optional_installs": ["X"]}) == [
        Set(("optional_installs",), ["X"])
    ]
    with pytest.raises(FieldError, match="not a manifest section"):
        manifest_changes(data, {"conditional_items": []})


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    root = tmp_path / "repo"
    shutil.copytree(REPO, root)
    repo = Repository(id="fixture", name="Fixture", root=root.resolve(), writable=True)
    settings = Settings(repositories=(repo,), state_dir=tmp_path / "state", rescan_seconds=0)
    with TestClient(
        create_app(settings, static_dir=tmp_path / "none"), base_url="http://localhost"
    ) as c:
        c.headers["X-CSRF-Token"] = c.get("/api/session").json()["csrf_token"]
        yield c


def edit(client: TestClient, change_id: str, base: str, sections: dict[str, list[str]]) -> Any:
    return client.patch(
        f"/api/repositories/fixture/changesets/{change_id}/manifest/{LAB}",
        json={"base_revision": base, "sections": sections},
    )


def changed(diff: str) -> list[str]:
    return [
        line for line in diff.splitlines() if line[:1] in "+-" and line[:3] not in ("+++", "---")
    ]


def test_adding_an_item_is_one_line(client: TestClient) -> None:
    change_id = client.post("/api/repositories/fixture/changesets", json={}).json()["id"]
    base = client.get(f"/api/repositories/fixture/objects/{LAB}").json()["revision"]
    [file] = edit(
        client,
        change_id,
        base,
        {"managed_installs": ["ExampleEditor", "ExampleViewer", "ExampleCLI"]},
    ).json()["files"]
    assert changed(file["diff"]) == ["+\t\t<string>ExampleCLI</string>"]


def test_catalog_order_moves_only_when_asked(client: TestClient) -> None:
    change_id = client.post("/api/repositories/fixture/changesets", json={}).json()["id"]
    base = client.get(f"/api/repositories/fixture/objects/{LAB}").json()["revision"]
    [file] = edit(client, change_id, base, {"catalogs": ["production", "testing"]}).json()["files"]
    assert file["surgical"]
    # Swapping two items is one element moved: two changed lines, nothing else.
    assert len(changed(file["diff"])) == 2
    later = edit(client, change_id, base, {"managed_installs": ["ExampleEditor"]}).json()
    paths = {tuple(c["path"]) for c in later["files"][0]["changes"]}
    assert paths == {("catalogs",), ("managed_installs",)}


def test_manifest_edits_refuse_stale_files_and_pkginfo(client: TestClient) -> None:
    change_id = client.post("/api/repositories/fixture/changesets", json={}).json()["id"]
    assert edit(client, change_id, "stale", {"catalogs": ["testing"]}).status_code == 409
    base = client.get(f"/api/repositories/fixture/objects/{LAB}").json()["revision"]
    assert edit(client, change_id, base, {"bogus": ["x"]}).status_code == 400
    pkginfo = client.patch(
        f"/api/repositories/fixture/changesets/{change_id}/manifest/pkgsinfo/tools/ExampleRuntime-5.0.plist",
        json={"base_revision": base, "sections": {"catalogs": ["testing"]}},
    )
    assert pkginfo.status_code == 404
