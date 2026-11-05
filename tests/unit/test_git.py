"""Optional Git: status, committing exactly what a draft saved, never running
anything the repository names, and leaving non-Git repositories alone."""

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.build_repo_fixture import REPO

from munki_web_studio.app import create_app
from munki_web_studio.repository.config import Repository, Settings

EDITOR = "pkgsinfo/apps/creative/ExampleEditor-2.10.plist"
BROWSER = "pkgsinfo/apps/browsers/ExampleBrowser-136.0.plist"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=t@example.invalid",
            "-C",
            str(root),
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def root(tmp_path: Path) -> Path:
    copy = tmp_path / "repo"
    shutil.copytree(REPO, copy)
    git(copy, "init", "--quiet", "--initial-branch=main")
    git(copy, "add", "-A")
    git(copy, "commit", "--quiet", "-m", "Fixture")
    return copy


def make(tmp_path: Path, root: Path) -> Iterator[TestClient]:
    settings = Settings(
        repositories=(Repository(id="fixture", name="Fixture", root=root, writable=True),),
        state_dir=tmp_path / "state",
        rescan_seconds=0,
    )
    with TestClient(
        create_app(settings, static_dir=tmp_path / "none"), base_url="http://localhost"
    ) as c:
        c.headers["X-CSRF-Token"] = c.get("/api/session").json()["csrf_token"]
        yield c


@pytest.fixture
def studio(tmp_path: Path, root: Path) -> Iterator[TestClient]:
    yield from make(tmp_path, root)


def save_edit(studio: TestClient, path: str, name: str) -> str:
    change_id = studio.post("/api/repositories/fixture/changesets", json={"title": name}).json()[
        "id"
    ]
    base = studio.get(f"/api/repositories/fixture/objects/{path}").json()["revision"]
    studio.patch(
        f"/api/repositories/fixture/changesets/{change_id}/pkginfo/{path}",
        json={"base_revision": base, "fields": {"display_name": name}},
    ).raise_for_status()
    studio.post(
        f"/api/repositories/fixture/changesets/{change_id}/save", json={}
    ).raise_for_status()
    return change_id


def commit(studio: TestClient, change_id: str, message: str = "Rename the editor") -> Any:
    return studio.post(
        f"/api/repositories/fixture/changesets/{change_id}/commit", json={"message": message}
    )


def test_status_shows_branch_head_and_changes(studio: TestClient, root: Path) -> None:
    status = studio.get("/api/repositories/fixture/git").json()
    assert status["enabled"] and status["branch"] == "main" and status["changed"] == []
    assert status["head"] == git(root, "rev-parse", "HEAD").strip()
    (root / "manifests/site_default").write_text("changed")
    assert studio.get("/api/repositories/fixture/git").json()["changed"] == [
        "manifests/site_default"
    ]


def test_a_commit_holds_exactly_the_saved_files(studio: TestClient, root: Path) -> None:
    (root / "manifests/site_default").write_text("someone else's work")
    (root / "notes.txt").write_text("staged elsewhere")
    git(root, "add", "notes.txt")
    change_id = save_edit(studio, EDITOR, "Example Editor Pro")
    done = commit(studio, change_id)
    assert done.status_code == 200, done.text
    assert done.json()["paths"] == [EDITOR]
    head = git(root, "log", "-1", "--format=%an|%s").strip()
    assert head == "Local administrator|Rename the editor"
    assert "notes.txt" in git(root, "diff", "--cached", "--name-only")
    assert "manifests/site_default" in git(root, "diff", "--name-only")
    assert commit(studio, change_id).status_code == 409


def test_a_file_changed_after_the_save_is_not_committed(studio: TestClient, root: Path) -> None:
    change_id = save_edit(studio, EDITOR, "Example Editor Pro")
    (root / EDITOR).write_bytes((root / EDITOR).read_bytes() + b"\n")
    refused = commit(studio, change_id)
    assert refused.status_code == 409 and "changed after it was saved" in refused.json()["detail"]
    assert git(root, "rev-list", "--count", "HEAD").strip() == "1"


def test_hooks_never_run(studio: TestClient, root: Path, tmp_path: Path) -> None:
    marker = tmp_path / "hook-ran"
    for folder in (root / ".git/hooks", root / ".husky"):
        folder.mkdir(exist_ok=True)
        for hook in ("pre-commit", "commit-msg", "post-commit", "pre-push"):
            script = folder / hook
            script.write_text(f"#!/bin/sh\ntouch {marker}\n")
            script.chmod(0o755)
    git(root, "config", "core.hooksPath", ".husky")
    change_id = save_edit(studio, EDITOR, "Hooked")
    assert commit(studio, change_id).status_code == 200
    assert not marker.exists()


@pytest.mark.parametrize(
    "key,value",
    [
        ("filter.evil.clean", "touch {marker}"),
        ("diff.evil.textconv", "touch {marker}"),
        ("core.sshCommand", "touch {marker}"),
        ("include.path", "{marker}.config"),
        ("credential.helper", "!touch {marker}"),
    ],
)
def test_repository_config_that_runs_programs_turns_git_off(
    studio: TestClient, root: Path, tmp_path: Path, key: str, value: str
) -> None:
    marker = tmp_path / "ran"
    (root / ".gitattributes").write_text("* filter=evil diff=evil\n")
    git(root, "config", key, value.format(marker=marker))
    status = studio.get("/api/repositories/fixture/git").json()
    assert not status["enabled"] and key.lower() in status["reason"]
    change_id = save_edit(studio, EDITOR, "Filtered")
    refused = commit(studio, change_id)
    assert refused.status_code == 409 and "would run programs" in refused.json()["detail"]
    assert not marker.exists()


def test_repositories_without_git_work_unchanged(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    shutil.copytree(REPO, plain)
    for client in make(tmp_path, plain):
        status = client.get("/api/repositories/fixture/git").json()
        assert status == {
            "enabled": False,
            "reason": "not a Git repository",
            "branch": None,
            "head": None,
            "upstream": None,
            "ahead": 0,
            "behind": 0,
            "changed": [],
        }
        change_id = save_edit(client, EDITOR, "No Git")
        assert b"<string>No Git</string>" in (plain / EDITOR).read_bytes()
        assert commit(client, change_id).status_code == 409
    assert not (plain / ".git").exists()


def test_push_needs_an_upstream_and_refuses_local_remotes(
    studio: TestClient, root: Path, tmp_path: Path
) -> None:
    refused = studio.post("/api/repositories/fixture/git/push")
    assert refused.status_code == 409 and "no upstream" in refused.json()["detail"]
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "--quiet", str(remote)], check=True)
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "--quiet", "-u", "origin", "main")
    status = studio.get("/api/repositories/fixture/git").json()
    assert status["upstream"] == "origin/main" and status["ahead"] == 0
    change_id = save_edit(studio, EDITOR, "Ahead")
    commit(studio, change_id).raise_for_status()
    assert studio.get("/api/repositories/fixture/git").json()["ahead"] == 1
    refused = studio.post("/api/repositories/fixture/git/push")
    assert refused.status_code == 409 and "https and ssh" in refused.json()["detail"]
