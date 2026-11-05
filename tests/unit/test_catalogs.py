"""Generating catalogs: they replace the repository's catalogs only after the
worker's output checks out, and a failed generation leaves the old ones."""

import hashlib
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.build_repo_fixture import REPO
from tests.unit.test_worker import (
    FAILING_TOOL,
    FAKE_TOOL,
    REAL_MAKECATALOGS,
    TOKEN,
    fake,
    only,
    posix_only,
    submit,
    tool,
    wait_for,
    worker_for,
)

from munki_web_studio.app import create_app
from munki_web_studio.jobs.apply import recover_swaps, swap_folder
from munki_web_studio.repository.config import Repository, Settings, WorkerEntry
from munki_web_studio.worker import Tools


def tree(folder: Path) -> dict[str, bytes]:
    return {
        p.relative_to(folder).as_posix(): p.read_bytes()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def root(tmp_path: Path) -> Path:
    copy = tmp_path / "repo"
    shutil.copytree(REPO, copy)
    (copy / "catalogs" / "stale").write_bytes((copy / "catalogs" / "testing").read_bytes())
    return copy


def make_studio(tmp_path: Path, root: Path, **options: Any) -> Iterator[TestClient]:
    repo = replace(Repository(id="fixture", name="Fixture", root=root, writable=True), **options)
    settings = Settings(
        repositories=(repo,),
        state_dir=tmp_path / "state",
        rescan_seconds=0,
        workers=(WorkerEntry("mac-1", hashlib.sha256(TOKEN.encode()).hexdigest()),),
    )
    with TestClient(
        create_app(settings, static_dir=tmp_path / "none"), base_url="http://localhost"
    ) as client:
        client.headers["X-CSRF-Token"] = client.get("/api/session").json()["csrf_token"]
        yield client


@pytest.fixture
def studio(tmp_path: Path, root: Path) -> Iterator[TestClient]:
    yield from make_studio(tmp_path, root)


@posix_only
def test_generated_catalogs_replace_the_old_ones(
    studio: TestClient, tmp_path: Path, root: Path
) -> None:
    worker = worker_for(studio, only(tmp_path, FAKE_TOOL))
    worker.hello()
    job_id = submit(studio).json()["id"]
    assert worker.run_once()
    job = wait_for(studio, job_id, "succeeded")
    counts = job["result"]["applied"]["counts"]
    assert set(counts) == {"all", "production", "testing"} and counts["all"] == 8
    assert sorted(tree(root / "catalogs")) == [".gitkeep", "all", "production", "testing"]
    assert b"<string>fake</string>" in (root / "catalogs" / "all").read_bytes()
    assert not list(root.glob(".catalogs.studio-*"))
    names = [c["name"] for c in studio.get("/api/repositories/fixture/catalogs").json()]
    assert "stale" not in names


@posix_only
def test_a_failed_tool_keeps_the_last_catalogs(
    studio: TestClient, tmp_path: Path, root: Path
) -> None:
    before = tree(root / "catalogs")
    failing = tool(tmp_path, FAILING_TOOL)
    worker = worker_for(
        studio, Tools.probe({"makecatalogs": failing, "makepkginfo": failing.parent / "x"})
    )
    worker.hello()
    job_id = submit(studio).json()["id"]
    assert worker.run_once()
    assert "exited with status 3" in wait_for(studio, job_id, "failed")["error"]
    assert tree(root / "catalogs") == before


@posix_only
def test_output_that_fails_the_check_keeps_the_last_catalogs(
    studio: TestClient, tmp_path: Path, root: Path
) -> None:
    before = tree(root / "catalogs")
    garbage = fake(extra='catalogs["all"] = "not a list"')
    worker = worker_for(studio, only(tmp_path, garbage))
    worker.hello()
    job_id = submit(studio).json()["id"]
    assert worker.run_once()
    error = wait_for(studio, job_id, "failed")["error"]
    assert error.startswith("The catalogs were not replaced") and "all" in error
    assert tree(root / "catalogs") == before


def test_read_only_and_live_repositories(tmp_path: Path, root: Path) -> None:
    for client in make_studio(tmp_path / "ro", root, writable=False):
        refused = client.post(
            "/api/repositories/fixture/jobs", json={"operation": "catalog.generate"}
        )
        assert refused.status_code in (403, 409)
    worker_tools = only(tmp_path, FAKE_TOOL)
    for client in make_studio(tmp_path / "live", root, live=True):
        worker_for(client, worker_tools).hello()
        assert submit(client).status_code == 428
        ok = client.post(
            "/api/repositories/fixture/jobs",
            json={"operation": "catalog.generate", "arguments": {"acknowledge_live": True}},
        )
        assert ok.status_code == 201


def test_a_swap_interrupted_half_way_is_undone(tmp_path: Path, root: Path) -> None:
    repo = Repository(id="fixture", name="Fixture", root=root, writable=True)
    before = tree(root / "catalogs")
    (root / "catalogs").rename(root / ".catalogs.studio-old-abcd")
    (root / ".catalogs.studio-new-abcd").mkdir()
    assert recover_swaps(repo, "catalogs") == [
        "restored catalogs from .catalogs.studio-old-abcd",
        "removed .catalogs.studio-new-abcd",
    ]
    assert tree(root / "catalogs") == before
    new = tmp_path / "new"
    new.mkdir()
    (new / "all").write_bytes(b"x")
    swap_folder(repo, "catalogs", new, tmp_path / "journal")
    assert tree(root / "catalogs") == {".gitkeep": b"", "all": b"x"}
    assert recover_swaps(repo, "catalogs") == []


def test_catalogs_view_shows_membership_and_what_is_out_of_date(
    studio: TestClient, root: Path
) -> None:
    listed = {c["name"]: c for c in studio.get("/api/repositories/fixture/catalogs").json()}
    assert listed["stale"]["expected"] == 0 and not listed["stale"]["up_to_date"]
    testing = studio.get("/api/repositories/fixture/catalogs/testing").json()
    assert testing["members"] and all(m["pkginfo"] for m in testing["members"])
    editor = root / "pkgsinfo/apps/creative/ExampleEditor-2.10.plist"
    editor.write_bytes(
        editor.read_bytes().replace(b"<string>production</string>", b"<string>staging</string>")
    )
    studio.post("/api/repositories/fixture/rescan").raise_for_status()
    staging = studio.get("/api/repositories/fixture/catalogs/staging").json()
    assert staging["path"] is None and staging["error"] == "Not generated yet"
    assert [m["name"] for m in staging["missing"]] == ["ExampleEditor"]
    production = studio.get("/api/repositories/fixture/catalogs/production").json()
    assert not production["up_to_date"]
    assert "ExampleEditor" in [m["name"] for m in production["extra"]]
    assert studio.get("/api/repositories/fixture/catalogs/nope").status_code == 404


@pytest.mark.skipif(not REAL_MAKECATALOGS.is_file(), reason="needs Munki's makecatalogs")
def test_output_matches_running_makecatalogs_directly(
    studio: TestClient, root: Path, tmp_path: Path
) -> None:
    direct = tmp_path / "direct"
    shutil.copytree(REPO, direct)
    command = [str(REAL_MAKECATALOGS), "--skip-pkg-check", str(direct)]
    subprocess.run(command, check=True, capture_output=True)  # noqa: S603
    worker = worker_for(studio, Tools.probe())
    worker.hello()
    job_id = submit(studio).json()["id"]
    assert worker.run_once()
    wait_for(studio, job_id, "succeeded")
    assert tree(root / "catalogs") == tree(direct / "catalogs")
    assert all(c["up_to_date"] for c in studio.get("/api/repositories/fixture/catalogs").json())


@posix_only
@pytest.mark.parametrize(
    "tamper,extra,reason",
    [
        ('item["installer_item_location"] = "evil.pkg"', "", "does not match any pkginfo"),
        ('item["postinstall_script"] = "#!/bin/sh"', "", "does not match any pkginfo"),
        ('item["loop_fingerprint"] = 7', "", "wrong type"),
        ("", 'catalogs["all"].append({"name": "Injected", "version": "1"})', "Injected 1 does not"),
        ("", 'catalogs["all"].pop()', "is missing"),
        ("", 'catalogs["everyone"] = []', "not a catalog any pkginfo names"),
        ("", 'del catalogs["production"]', "No catalog was generated for production"),
        ("", 'catalogs["testing"].append(catalogs["testing"][0])', "does not match any pkginfo"),
    ],
)
def test_a_worker_cannot_change_what_clients_install(
    studio: TestClient, tmp_path: Path, root: Path, tamper: str, extra: str, reason: str
) -> None:
    before = tree(root / "catalogs")
    worker = worker_for(studio, only(tmp_path, fake(tamper, extra)))
    worker.hello()
    job_id = submit(studio).json()["id"]
    assert worker.run_once()
    error = wait_for(studio, job_id, "failed")["error"]
    assert error.startswith("The catalogs were not replaced") and reason in error
    assert tree(root / "catalogs") == before


@posix_only
def test_cancelling_before_the_swap_keeps_the_last_catalogs(
    studio: TestClient, tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from munki_web_studio.api import workers

    before = tree(root / "catalogs")
    worker = worker_for(studio, only(tmp_path, FAKE_TOOL))
    worker.hello()
    job_id = submit(studio).json()["id"]
    checked = workers._sources  # pyright: ignore[reportPrivateUsage]

    def cancel_during_check(repo: Any, index: Any) -> Any:
        studio.post(f"/api/repositories/fixture/jobs/{job_id}/cancel").raise_for_status()
        return checked(repo, index)

    monkeypatch.setattr(workers, "_sources", cancel_during_check)
    assert worker.run_once()
    wait_for(studio, job_id, "cancelled")
    assert tree(root / "catalogs") == before
