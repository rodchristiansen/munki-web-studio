import hashlib
import shutil
import threading
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.build_repo_fixture import REPO

from munki_web_studio.app import create_app
from munki_web_studio.codec import load
from munki_web_studio.repository.config import Repository, Settings

EDITOR = "pkgsinfo/apps/creative/ExampleEditor-2.10.plist"
RUNTIME = "pkgsinfo/tools/ExampleRuntime-5.0.plist"


def tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def make_client(tmp_path: Path, **repo_options: Any) -> Iterator[TestClient]:
    root = tmp_path / "repo"
    if not root.exists():
        shutil.copytree(REPO, root)
    repo = Repository(id="fixture", name="Fixture", root=root.resolve(), writable=True)
    repo = replace(repo, **repo_options)
    settings = Settings(repositories=(repo,), state_dir=tmp_path / "state", rescan_seconds=0)
    with TestClient(
        create_app(settings, static_dir=tmp_path / "none"), base_url="http://localhost"
    ) as c:
        c.headers["X-CSRF-Token"] = c.get("/api/session").json()["csrf_token"]
        yield c


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    yield from make_client(tmp_path)


def draft(client: TestClient, path: str, display_name: str) -> str:
    change_id = client.post(
        "/api/repositories/fixture/changesets", json={"title": display_name}
    ).json()["id"]
    base = client.get(f"/api/repositories/fixture/objects/{path}").json()["revision"]
    client.patch(
        f"/api/repositories/fixture/changesets/{change_id}/pkginfo/{path}",
        json={"base_revision": base, "fields": {"display_name": display_name}},
    ).raise_for_status()
    return change_id


def save(client: TestClient, change_id: str, **body: Any) -> Any:
    return client.post(f"/api/repositories/fixture/changesets/{change_id}/save", json=body)


def test_save_writes_the_draft_and_updates_the_index(client: TestClient, tmp_path: Path) -> None:
    change_id = draft(client, EDITOR, "Example Editor Pro")
    result = save(client, change_id)
    assert result.status_code == 200 and result.json()["status"] == "saved"
    assert (
        load((tmp_path / "repo" / EDITOR).read_bytes()).data["display_name"] == "Example Editor Pro"
    )
    detail = client.get(f"/api/repositories/fixture/objects/{EDITOR}").json()
    assert detail["display_name"] == "Example Editor Pro"
    assert save(client, change_id).status_code == 409
    assert any(e["action"] == "save" for e in client.get("/api/audit").json())


def test_two_editors_cannot_overwrite_each_other(client: TestClient, tmp_path: Path) -> None:
    first = draft(client, EDITOR, "First")
    second = draft(client, EDITOR, "Second")
    assert save(client, first).status_code == 200
    refused = save(client, second)
    assert refused.status_code == 409 and refused.json()["detail"]["paths"] == [EDITOR]
    assert load((tmp_path / "repo" / EDITOR).read_bytes()).data["display_name"] == "First"


def test_an_outside_writer_is_not_overwritten(client: TestClient, tmp_path: Path) -> None:
    change_id = draft(client, EDITOR, "From the studio")
    target = tmp_path / "repo" / EDITOR
    target.write_bytes(target.read_bytes().replace(b"Example Editor", b"From munkiimport"))
    assert save(client, change_id).status_code == 409
    assert b"From munkiimport" in target.read_bytes()


def test_concurrent_saves_of_one_file_let_exactly_one_win(client: TestClient) -> None:
    drafts = [draft(client, EDITOR, f"Editor {i}") for i in range(6)]
    codes: list[int] = []

    def run(change_id: str) -> None:
        codes.append(save(client, change_id).status_code)

    threads = [threading.Thread(target=run, args=(d,)) for d in drafts]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [200, 409, 409, 409, 409, 409]


def test_a_failed_save_leaves_the_repository_unchanged(client: TestClient, tmp_path: Path) -> None:
    change_id = draft(client, EDITOR, "Example Editor Pro")
    base = client.get(f"/api/repositories/fixture/objects/{RUNTIME}").json()["revision"]
    broken = {"base_revision": base, "changes": [{"op": "append", "path": ["name"], "value": "x"}]}
    client.put(
        f"/api/repositories/fixture/changesets/{change_id}/files/{RUNTIME}", json=broken
    ).raise_for_status()
    before = tree(tmp_path / "repo")
    assert save(client, change_id).status_code == 422
    assert tree(tmp_path / "repo") == before


def test_read_only_repositories_refuse_saves(tmp_path: Path) -> None:
    for c in make_client(tmp_path, writable=False):
        assert save(c, draft(c, EDITOR, "x")).status_code == 403


def test_live_repositories_need_acknowledgement(tmp_path: Path) -> None:
    for c in make_client(tmp_path, live=True):
        assert c.get("/api/repositories").json()[0]["live"] is True
        change_id = draft(c, EDITOR, "Live edit")
        assert save(c, change_id).status_code == 428
        assert save(c, change_id, acknowledge_live=True).status_code == 200
