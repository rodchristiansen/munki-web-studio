"""Publishing snapshots: complete releases switched in at once, the same
content published once, and a failed or partial build never shown as done."""

import shutil
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.build_repo_fixture import REPO
from tests.unit.fake_idp import FakeIdP
from tests.unit.test_auth import AUTH, sign_in

from munki_web_studio import publish
from munki_web_studio.app import create_app
from munki_web_studio.repository.config import (
    ConfigError,
    Publish,
    Repository,
    Settings,
    parse_settings,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    copy = tmp_path / "repo"
    shutil.copytree(REPO, copy)
    (copy / "pkgs" / "apps").mkdir(parents=True, exist_ok=True)
    (copy / "pkgs" / "apps" / "Example-1.0.pkg").write_bytes(b"payload" * 1000)
    return copy


def settings_for(tmp_path: Path, root: Path, **publish_options: Any) -> Settings:
    target = tmp_path / "published"
    repo = Repository(
        id="fixture",
        name="Fixture",
        root=root,
        writable=True,
        publish=Publish(target=target, **publish_options),
    )
    return Settings(repositories=(repo,), state_dir=tmp_path / "state", rescan_seconds=0)


def make(settings: Settings, tmp_path: Path, **kwargs: Any) -> Iterator[TestClient]:
    app = create_app(settings, static_dir=tmp_path / "none", **kwargs)
    with TestClient(app, base_url="http://localhost", follow_redirects=False) as client:
        app.state.runner.poll_seconds = 0.05
        if not settings.auth.enabled:
            client.headers["X-CSRF-Token"] = client.get("/api/session").json()["csrf_token"]
        yield client


@pytest.fixture
def studio(tmp_path: Path, root: Path) -> Iterator[TestClient]:
    yield from make(settings_for(tmp_path, root), tmp_path)


def run_publish(studio: TestClient, status: str = "succeeded") -> dict[str, Any]:
    created = studio.post(
        "/api/repositories/fixture/jobs", json={"operation": "repository.publish"}
    )
    assert created.status_code == 201, created.text
    job: dict[str, Any] = {}
    for _ in range(400):
        job = studio.get(f"/api/repositories/fixture/jobs/{created.json()['id']}").json()
        if job["status"] in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(0.05)
    assert job["status"] == status, job
    return job


def tree(folder: Path) -> dict[str, bytes]:
    return {
        p.relative_to(folder).as_posix(): p.read_bytes()
        for p in sorted(folder.rglob("*"))
        if p.is_file() and not p.name.startswith(".")
    }


def test_a_release_holds_what_clients_download_and_becomes_current(
    studio: TestClient, root: Path, tmp_path: Path
) -> None:
    result = run_publish(studio)["result"]
    assert result["built"] and result["switched"] and not result["already_published"]
    current = tmp_path / "published" / "current"
    assert current.is_symlink() and current.resolve().name == result["release"]
    published = tree(current)
    expected = {
        path: data
        for path, data in tree(root).items()
        if path.split("/")[0] in ("catalogs", "manifests", "pkgs", "icons", "client_resources")
    }
    assert published == expected and "pkgs/apps/Example-1.0.pkg" in published
    assert not any(p.startswith("pkgsinfo/") for p in published)
    info = studio.get("/api/repositories/fixture/publishing").json()
    assert info["mode"] == "snapshot" and info["current"]["id"] == result["release"]
    assert not info["unpublished_changes"]


def test_publishing_the_same_content_twice_changes_nothing(
    studio: TestClient, tmp_path: Path
) -> None:
    first = run_publish(studio)["result"]
    current = tmp_path / "published" / "current"
    before = current.lstat().st_mtime_ns
    again = run_publish(studio)["result"]
    assert again["release"] == first["release"]
    assert again["already_published"] and not again["built"] and not again["switched"]
    assert current.lstat().st_mtime_ns == before
    assert len(studio.get("/api/repositories/fixture/publishing").json()["releases"]) == 1


def test_a_change_makes_a_new_release_and_old_ones_are_pruned(tmp_path: Path, root: Path) -> None:
    for client in make(settings_for(tmp_path, root, keep=1), tmp_path):
        first = run_publish(client)["result"]["release"]
        (root / "manifests" / "site_default").write_bytes(
            (root / "manifests" / "site_default").read_bytes().replace(b"ExampleRuntime", b"X")
        )
        client.post("/api/repositories/fixture/rescan").raise_for_status()
        assert client.get("/api/repositories/fixture/publishing").json()["unpublished_changes"]
        second = run_publish(client)["result"]
        assert second["release"] != first and second["pruned"] == [first]
        names = [
            r["id"] for r in client.get("/api/repositories/fixture/publishing").json()["releases"]
        ]
        assert names == [second["release"]]
        assert (
            b"<string>X</string>"
            in (tmp_path / "published/current/manifests/site_default").read_bytes()
        )


def test_a_partial_build_is_never_success_and_never_current(
    studio: TestClient, tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = run_publish(studio)["result"]["release"]
    (root / "manifests" / "site_default").write_text("changed")
    copied = 0
    original = publish._link_or_copy  # pyright: ignore[reportPrivateUsage]

    def fail_on_third(source: Path, destination: Path, hardlink: bool) -> None:
        nonlocal copied
        copied += 1
        if copied == 3:
            raise OSError("disk full")
        original(source, destination, hardlink)

    monkeypatch.setattr(publish, "_link_or_copy", fail_on_third)
    job = run_publish(studio, "failed")
    assert job["result"] is None
    current = tmp_path / "published" / "current"
    assert current.resolve().name == first
    leftovers = [p.name for p in (tmp_path / "published" / "releases").iterdir()]
    assert leftovers == [first]


def test_a_file_changing_mid_publish_fails_it(
    studio: TestClient, tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = publish._link_or_copy  # pyright: ignore[reportPrivateUsage]

    def change_first(source: Path, destination: Path, hardlink: bool) -> None:
        original(source, destination, hardlink)
        destination.write_bytes(destination.read_bytes() + b"x")

    monkeypatch.setattr(publish, "_link_or_copy", change_first)
    job = run_publish(studio, "failed")
    assert "changed while it was being published" in job["error"]
    assert not (tmp_path / "published" / "current").exists()


def test_a_crash_after_the_build_finishes_on_the_next_publish(
    studio: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(target: Path, release: str) -> None:
        raise OSError("power cut")

    monkeypatch.setattr(publish, "_switch", crash)
    run_publish(studio, "failed")
    assert not (tmp_path / "published" / "current").exists()
    monkeypatch.undo()
    result = run_publish(studio)["result"]
    assert not result["built"] and result["switched"]


def test_hard_links_are_used_only_when_asked(tmp_path: Path, root: Path) -> None:
    for client in make(settings_for(tmp_path, root, hardlink=True), tmp_path):
        run_publish(client)
    payload = tmp_path / "published/current/pkgs/apps/Example-1.0.pkg"
    assert payload.stat().st_ino == (root / "pkgs/apps/Example-1.0.pkg").stat().st_ino


def test_live_and_unconfigured_repositories(tmp_path: Path, root: Path) -> None:
    for live in (True, False):
        repo = Repository(id="fixture", name="Fixture", root=root, writable=True, live=live)
        settings = Settings(repositories=(repo,), state_dir=tmp_path / f"s{live}", rescan_seconds=0)
        for client in make(settings, tmp_path):
            info = client.get("/api/repositories/fixture/publishing").json()
            assert info["mode"] == ("live" if live else "none")
            refused = client.post(
                "/api/repositories/fixture/jobs", json={"operation": "repository.publish"}
            )
            assert refused.status_code == 409
            assert ("every save is live" in refused.json()["detail"]) is live


def test_publish_config_is_checked(tmp_path: Path, root: Path) -> None:
    base = {"repositories": [{"id": "main", "path": str(root)}]}

    def with_publish(**entry: Any) -> dict[str, Any]:
        return {"repositories": [{**base["repositories"][0], **entry}]}

    good = parse_settings(with_publish(publish={"target": str(tmp_path / "out")}), tmp_path)
    assert good.repositories[0].publish == Publish(target=(tmp_path / "out").resolve())
    for bad, message in (
        ({"publish": {"target": str(root / "inside")}}, "must not contain each other"),
        ({"publish": {"target": str(tmp_path)}}, "must not contain each other"),
        ({"publish": {"target": str(tmp_path / "o"), "keep": 0}}, "keep must be 1"),
        ({"publish": {"target": str(tmp_path / "o"), "hardlink": "yes"}}, "true or false"),
        ({"publish": {"target": str(tmp_path / "o")}, "live": True}, "choose one"),
    ):
        with pytest.raises(ConfigError, match=message):
            parse_settings(with_publish(**bad), tmp_path)


def test_publishing_needs_the_publisher_role(tmp_path: Path, root: Path) -> None:
    idp = FakeIdP()
    settings = replace(settings_for(tmp_path, root), auth=AUTH)
    for client in make(settings, tmp_path, oidc_http=idp.http()):
        sign_in(client, idp, roles=["editors"])
        client.headers["X-CSRF-Token"] = client.get("/api/session").json()["csrf_token"]
        refused = client.post(
            "/api/repositories/fixture/jobs", json={"operation": "repository.publish"}
        )
        assert refused.status_code == 403 and "Publisher" in refused.json()["detail"]
