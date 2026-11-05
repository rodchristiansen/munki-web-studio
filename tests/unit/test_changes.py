import datetime as dt
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.build_repo_fixture import REPO

from munki_web_studio.app import create_app
from munki_web_studio.changes.apply import (
    ConflictError,
    RecoveryNeededError,
    SaveError,
    commit,
    journals,
    preview,
    recover,
)
from munki_web_studio.changes.store import ChangeSetStore
from munki_web_studio.codec import Append, Set, load
from munki_web_studio.repository.config import Repository, Settings

EDITOR = "pkgsinfo/apps/creative/ExampleEditor-2.10.plist"
SITE = "manifests/site_default"


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    root = tmp_path / "repo"
    shutil.copytree(REPO, root)
    return Repository(id="fixture", name="Fixture", root=root.resolve(), writable=True)


@pytest.fixture
def store(tmp_path: Path) -> ChangeSetStore:
    return ChangeSetStore(tmp_path / "drafts.sqlite")


def revision(repo: Repository, path: str) -> str:
    return load((repo.root / path).read_bytes()).revision


def draft_two_files(repo: Repository, store: ChangeSetStore) -> str:
    change_set = store.create(repo.id, "u1", "User One", "Two files")
    store.put_file(
        change_set.id,
        EDITOR,
        revision(repo, EDITOR),
        [Set(("display_name",), "Example Editor Pro")],
    )
    store.put_file(
        change_set.id, SITE, revision(repo, SITE), [Append(("managed_installs",), "ExampleCLI")]
    )
    return change_set.id


def test_drafts_keep_typed_values(store: ChangeSetStore) -> None:
    change_set = store.create("fixture", "u1", "User One", "Typed")
    when = dt.datetime(2026, 11, 5, 8, 0)
    store.put_file(
        change_set.id, EDITOR, "rev", [Set(("installed_size",), 2**60), Set(("when",), when)]
    )
    loaded = store.get(change_set.id)
    assert loaded is not None
    assert loaded.files[0].changes == [Set(("installed_size",), 2**60), Set(("when",), when)]
    store.put_file(change_set.id, EDITOR, "rev", [])
    assert store.get(change_set.id).files == []  # type: ignore[union-attr]


def test_preview_shows_a_small_diff_and_writes_nothing(
    repo: Repository, store: ChangeSetStore
) -> None:
    before = (repo.root / EDITOR).read_bytes()
    change_set = store.get(draft_two_files(repo, store))
    assert change_set is not None
    previews = {p.path: p for p in preview(repo, change_set)}
    assert "+\t<string>Example Editor Pro</string>" in previews[EDITOR].diff
    assert (
        sum(
            1
            for line in previews[EDITOR].diff.splitlines()
            if line[:1] in "+-" and line[:3] not in ("+++", "---")
        )
        == 2
    )
    assert (repo.root / EDITOR).read_bytes() == before


def test_stale_base_revision_is_a_conflict(
    repo: Repository, store: ChangeSetStore, tmp_path: Path
) -> None:
    change_set_id = draft_two_files(repo, store)
    target = repo.root / SITE
    target.write_bytes(target.read_bytes().replace(b"ExampleRuntime", b"ExampleRuntimeX"))
    change_set = store.get(change_set_id)
    assert change_set is not None
    assert {p.path: p.conflict for p in preview(repo, change_set)} == {EDITOR: False, SITE: True}
    with pytest.raises(ConflictError) as error:
        commit(repo, change_set, tmp_path / "journal")
    assert error.value.paths == [SITE]
    assert b"Example Editor Pro" not in (repo.root / EDITOR).read_bytes()


def test_commit_writes_every_file_and_clears_the_journal(
    repo: Repository, store: ChangeSetStore, tmp_path: Path
) -> None:
    change_set = store.get(draft_two_files(repo, store))
    assert change_set is not None
    commit(repo, change_set, tmp_path / "journal")
    assert load((repo.root / EDITOR).read_bytes()).data["display_name"] == "Example Editor Pro"
    assert "ExampleCLI" in load((repo.root / SITE).read_bytes()).data["managed_installs"]
    assert journals(repo, tmp_path / "journal") == []
    assert not list(repo.root.rglob("*.studio-*.tmp"))


def test_interrupted_save_recovers_from_the_journal(
    repo: Repository, store: ChangeSetStore, tmp_path: Path
) -> None:
    change_set = store.get(draft_two_files(repo, store))
    assert change_set is not None
    journal_dir = tmp_path / "journal"
    with pytest.raises(SaveError, match="Simulated crash"):
        commit(repo, change_set, journal_dir, crash_after=1)
    written = [load((repo.root / p).read_bytes()).data for p in (EDITOR, SITE)]
    assert (
        sum(
            1
            for d in written
            if d.get("display_name") == "Example Editor Pro"
            or "ExampleCLI" in d.get("managed_installs", [])
        )
        == 1
    )
    with pytest.raises(RecoveryNeededError):
        commit(repo, change_set, journal_dir)
    [report] = recover(repo, journal_dir)
    assert sorted(report.finished) == sorted([EDITOR, SITE]) and report.conflicts == []
    assert load((repo.root / EDITOR).read_bytes()).data["display_name"] == "Example Editor Pro"
    assert "ExampleCLI" in load((repo.root / SITE).read_bytes()).data["managed_installs"]
    assert journals(repo, journal_dir) == []
    assert not list(repo.root.rglob("*.studio-*.tmp"))


def test_recovery_will_not_overwrite_someone_elses_edit(
    repo: Repository, store: ChangeSetStore, tmp_path: Path
) -> None:
    change_set = store.get(draft_two_files(repo, store))
    assert change_set is not None
    journal_dir = tmp_path / "journal"
    with pytest.raises(SaveError):
        commit(repo, change_set, journal_dir, crash_after=1)
    untouched = ""
    for path in (EDITOR, SITE):
        target = repo.root / path
        if (
            b"Example Editor Pro" not in target.read_bytes()
            and b"ExampleCLI" not in target.read_bytes()
        ):
            target.write_bytes(target.read_bytes() + b"\n")
            untouched = path
    [report] = recover(repo, journal_dir)
    assert report.conflicts == [untouched]
    assert len(journals(repo, journal_dir)) == 1


@pytest.fixture
def client(tmp_path: Path, repo: Repository) -> Iterator[TestClient]:
    settings = Settings(repositories=(repo,), state_dir=tmp_path / "state", rescan_seconds=0)
    with TestClient(
        create_app(settings, static_dir=tmp_path / "none"), base_url="http://localhost"
    ) as c:
        c.headers["X-CSRF-Token"] = c.get("/api/session").json()["csrf_token"]
        yield c


def test_draft_api_round_trip(client: TestClient, repo: Repository) -> None:
    created = client.post("/api/repositories/fixture/changesets", json={"title": "Rename"}).json()
    base = client.get(f"/api/repositories/fixture/objects/{EDITOR}").json()["revision"]
    edit = {
        "base_revision": base,
        "changes": [{"op": "set", "path": ["display_name"], "value": "Example Editor Pro"}],
    }
    updated = client.put(
        f"/api/repositories/fixture/changesets/{created['id']}/files/{EDITOR}", json=edit
    ).json()
    [file] = updated["files"]
    assert file["conflict"] is False and "Example Editor Pro" in file["diff"]
    assert [c["id"] for c in client.get("/api/repositories/fixture/changesets").json()] == [
        created["id"]
    ]
    bad = client.put(
        f"/api/repositories/fixture/changesets/{created['id']}/files/{EDITOR}",
        json={"base_revision": base, "changes": [{"op": "rename", "path": ["x"]}]},
    )
    assert bad.status_code == 400
    assert (
        client.delete(f"/api/repositories/fixture/changesets/{created['id']}").json()["status"]
        == "discarded"
    )
    assert client.get("/api/repositories/fixture/changesets").json() == []
    assert b"Example Editor Pro" not in (repo.root / EDITOR).read_bytes()
