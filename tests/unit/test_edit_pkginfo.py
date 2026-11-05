import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.build_repo_fixture import REPO

from munki_web_studio import tagged
from munki_web_studio.app import create_app
from munki_web_studio.changes.fields import FieldError, field_changes
from munki_web_studio.codec import Delete, Set
from munki_web_studio.repository.config import Repository, Settings

EDITOR = "pkgsinfo/apps/creative/ExampleEditor-2.10.plist"


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


def edit(client: TestClient, change_id: str, base: str, fields: dict[str, Any]) -> Any:
    return client.patch(
        f"/api/repositories/fixture/changesets/{change_id}/pkginfo/{EDITOR}",
        json={"base_revision": base, "fields": {k: tagged.encode(v) for k, v in fields.items()}},
    )


def changed_lines(diff: str) -> int:
    return sum(
        1 for line in diff.splitlines() if line[:1] in "+-" and line[:3] not in ("+++", "---")
    )


def test_field_changes_check_types_and_skip_no_ops() -> None:
    data = {"display_name": "Example Editor", "unattended_install": True, "catalogs": ["testing"]}
    assert field_changes(data, {"display_name": "Example Editor"}) == []
    assert field_changes(data, {"display_name": "Pro", "unattended_install": None}) == [
        Set(("display_name",), "Pro"),
        Delete(("unattended_install",)),
    ]
    with pytest.raises(FieldError, match="true or false"):
        field_changes(data, {"unattended_install": "yes"})
    with pytest.raises(FieldError, match="list of text"):
        field_changes(data, {"catalogs": "testing"})
    with pytest.raises(FieldError, match="not an editable field"):
        field_changes(data, {"installer_item_hash": "x"})


def test_field_edit_previews_a_two_line_diff_and_keeps_unknown_keys(client: TestClient) -> None:
    change_id = client.post(
        "/api/repositories/fixture/changesets", json={"title": "Rename"}
    ).json()["id"]
    base = client.get(f"/api/repositories/fixture/objects/{EDITOR}").json()["revision"]
    result = edit(client, change_id, base, {"display_name": "Example Editor Pro"}).json()
    [file] = result["files"]
    assert changed_lines(file["diff"]) == 2 and file["surgical"]
    assert "x_unknown_site_key" not in file["diff"]
    combined = edit(client, change_id, base, {"catalogs": ["testing"]}).json()
    [file] = combined["files"]
    assert changed_lines(file["diff"]) == 3
    assert {c["path"][0] for c in file["changes"]} == {"display_name", "catalogs"}


def test_reverting_a_field_drops_it_from_the_draft(client: TestClient) -> None:
    change_id = client.post("/api/repositories/fixture/changesets", json={}).json()["id"]
    base = client.get(f"/api/repositories/fixture/objects/{EDITOR}").json()["revision"]
    edit(client, change_id, base, {"display_name": "Example Editor Pro"})
    reverted = edit(client, change_id, base, {"display_name": "Example Editor"}).json()
    assert reverted["files"] == []


def test_stale_or_invalid_edits_are_refused(client: TestClient) -> None:
    change_id = client.post("/api/repositories/fixture/changesets", json={}).json()["id"]
    assert edit(client, change_id, "stale", {"display_name": "x"}).status_code == 409
    base = client.get(f"/api/repositories/fixture/objects/{EDITOR}").json()["revision"]
    assert edit(client, change_id, base, {"unattended_install": "yes"}).status_code == 400
    manifest = client.patch(
        f"/api/repositories/fixture/changesets/{change_id}/pkginfo/manifests/site_default",
        json={"base_revision": base, "fields": {"display_name": "x"}},
    )
    assert manifest.status_code == 404


def test_field_list_is_published(client: TestClient) -> None:
    fields = client.get("/api/fields").json()
    assert fields["display_name"] == "string" and fields["catalogs"] == "string-list"
