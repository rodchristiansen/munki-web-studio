"""The worker: handshake, capabilities, staged tool runs and hashed artifacts."""

import hashlib
import io
import plistlib
import shutil
import tarfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.fixtures.build_repo_fixture import REPO

from munki_web_studio.app import create_app
from munki_web_studio.repository.config import Repository, Settings, WorkerEntry
from munki_web_studio.worker import JobError, Tools, Worker, safe_extract

TOKEN = "worker-token-for-tests"  # noqa: S105
REAL_MAKECATALOGS = Path("/usr/local/munki/makecatalogs")

FAKE_TOOL = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "9.9.9-test"; exit 0; fi
if [ -n "$FAKE_FAIL" ]; then echo "no catalogs for you" >&2; exit 3; fi
staging="$2"
mkdir -p "$staging/catalogs"
find "$staging/pkgsinfo" -type f | sort > "$staging/catalogs/all"
echo "wrote catalogs"
"""

SLOW_TOOL = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "slow"; exit 0; fi
sleep 30
"""


def tool(tmp_path: Path, body: str, name: str = "makecatalogs") -> Path:
    path = tmp_path / "bin" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


def only(tmp_path: Path, body: str) -> Tools:
    """A fake makecatalogs and nothing else, whatever is installed on this machine."""
    path = tool(tmp_path, body)
    return Tools.probe({"makecatalogs": path, "makepkginfo": path.parent / "missing"})


@pytest.fixture
def root(tmp_path: Path) -> Path:
    copy = tmp_path / "repo"
    shutil.copytree(REPO, copy)
    return copy


@pytest.fixture
def studio(tmp_path: Path, root: Path) -> Iterator[TestClient]:
    settings = Settings(
        repositories=(Repository(id="fixture", name="Fixture", root=root),),
        state_dir=tmp_path / "state",
        rescan_seconds=0,
        workers=(WorkerEntry("mac-1", hashlib.sha256(TOKEN.encode()).hexdigest()),),
    )
    app = create_app(settings, static_dir=tmp_path / "none")
    with TestClient(app, base_url="http://localhost") as client:
        client.headers["X-CSRF-Token"] = client.get("/api/session").json()["csrf_token"]
        yield client


def worker_for(studio: TestClient, tools: Tools) -> Worker:
    # A second client on the same app, so the worker's headers stay its own.
    client = TestClient(studio.app, base_url="http://localhost")
    return Worker(cast(httpx.Client, client), "mac-1", TOKEN, tools, poll_seconds=0.05)


def submit(studio: TestClient, operation: str = "catalog.preview") -> Any:
    return studio.post("/api/repositories/fixture/jobs", json={"operation": operation})


def test_missing_worker_names_the_tool(studio: TestClient) -> None:
    refused = submit(studio)
    assert refused.status_code == 409
    assert "macOS worker with makecatalogs" in refused.json()["detail"]
    ops = {o["name"]: o for o in studio.get("/api/operations").json()}
    assert not ops["catalog.preview"]["available"]
    assert "makecatalogs" in ops["catalog.preview"]["needs"]
    [entry] = studio.get("/api/workers").json()
    assert entry["id"] == "mac-1" and not entry["online"]


def test_handshake_reports_tools_and_capabilities(studio: TestClient, tmp_path: Path) -> None:
    tools = only(tmp_path, FAKE_TOOL)
    assert tools.versions["makecatalogs"] == "9.9.9-test"
    assert worker_for(studio, tools).hello() == ["catalog.generate"]
    [entry] = studio.get("/api/workers").json()
    assert entry["online"] and entry["tools"]["makecatalogs"] == "9.9.9-test"
    assert entry["capabilities"] == ["catalog.generate"] and entry["platform"] == "macOS"
    ops = {o["name"]: o for o in studio.get("/api/operations").json()}
    assert ops["catalog.preview"]["available"] and ops["catalog.preview"]["needs"] is None


def test_a_worker_cannot_claim_capabilities_without_their_tools(studio: TestClient) -> None:
    client = TestClient(studio.app, base_url="http://localhost")
    client.headers.update({"Authorization": f"Bearer {TOKEN}", "X-Studio-Worker": "mac-1"})
    reply = client.post(
        "/api/worker/hello",
        json={
            "platform": "macOS",
            "os_version": "1",
            "tools": {},
            "capabilities": ["catalog.generate", "root"],
        },
    ).json()
    assert reply["capabilities"] == []


def test_wrong_token_or_id_is_refused(studio: TestClient) -> None:
    for worker, token in (("mac-1", "wrong"), ("mac-2", TOKEN)):
        reply = studio.post(
            "/api/worker/hello",
            json={"platform": "macOS", "os_version": "1", "tools": {}, "capabilities": []},
            headers={"Authorization": f"Bearer {token}", "X-Studio-Worker": worker},
        )
        assert reply.status_code == 401


def wait_for(studio: TestClient, job_id: str, status: str) -> dict[str, Any]:
    job: dict[str, Any] = {}
    for _ in range(200):
        job = studio.get(f"/api/repositories/fixture/jobs/{job_id}").json()
        if job["status"] == status:
            return job
        time.sleep(0.05)
    raise AssertionError(f"{job_id} never reached {status}: {job}")


def test_a_job_runs_on_the_worker_and_returns_hashed_artifacts(
    studio: TestClient, tmp_path: Path
) -> None:
    worker = worker_for(studio, only(tmp_path, FAKE_TOOL))
    worker.hello()
    job_id = submit(studio).json()["id"]
    assert worker.run_once()
    job = wait_for(studio, job_id, "succeeded")
    [artifact] = job["result"]["artifacts"]
    assert artifact["path"] == "catalogs/all"
    assert job["result"]["tool_version"] == "9.9.9-test" and job["result"]["worker"] == "mac-1"
    body = studio.get(f"/api/repositories/fixture/jobs/{job_id}/artifacts/catalogs/all")
    assert hashlib.sha256(body.content).hexdigest() == artifact["sha256"]
    assert b"pkgsinfo/apps/creative/ExampleEditor-2.10.plist" in body.content
    assert not worker.run_once()


def test_the_tool_does_not_inherit_the_worker_environment(
    studio: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = worker_for(studio, only(tmp_path, FAKE_TOOL))
    worker.hello()
    job_id = submit(studio).json()["id"]
    monkeypatch.setattr("munki_web_studio.worker.os.defpath", "/usr/bin:/bin")
    monkeypatch.setenv("FAKE_FAIL", "1")  # Not passed through: the tool's environment is minimal.
    assert worker.run_once()
    assert wait_for(studio, job_id, "succeeded")


def test_tool_failures_are_reported(studio: TestClient, tmp_path: Path) -> None:
    failing = tool(tmp_path, FAKE_TOOL.replace('[ -n "$FAKE_FAIL" ]', "true"))
    worker = worker_for(
        studio, Tools.probe({"makecatalogs": failing, "makepkginfo": failing.parent / "missing"})
    )
    worker.hello()
    job_id = submit(studio).json()["id"]
    assert worker.run_once()
    job = wait_for(studio, job_id, "failed")
    assert "exited with status 3" in job["error"] and "no catalogs for you" in job["error"]


def test_cancelling_stops_the_tool(studio: TestClient, tmp_path: Path) -> None:
    worker = worker_for(studio, only(tmp_path, SLOW_TOOL))
    worker.hello()
    job_id = submit(studio).json()["id"]
    runner = threading.Thread(target=worker.run_once)
    started = time.monotonic()
    runner.start()
    wait_for(studio, job_id, "running")
    studio.post(f"/api/repositories/fixture/jobs/{job_id}/cancel").raise_for_status()
    runner.join(20)
    assert not runner.is_alive() and time.monotonic() - started < 20
    assert wait_for(studio, job_id, "cancelled")["result"] is None


def test_inputs_are_refused_after_the_repository_changes(
    studio: TestClient, tmp_path: Path, root: Path
) -> None:
    worker = worker_for(studio, only(tmp_path, FAKE_TOOL))
    worker.hello()
    job_id = submit(studio).json()["id"]
    target = root / "pkgsinfo/apps/creative/ExampleEditor-2.10.plist"
    target.write_bytes(target.read_bytes().replace(b"Example Editor", b"Changed Editor"))
    studio.post("/api/repositories/fixture/rescan").raise_for_status()
    assert worker.run_once()
    job = wait_for(studio, job_id, "failed")
    assert "changed after this job was queued" in job["error"]


def test_artifacts_must_match_their_hash(studio: TestClient, tmp_path: Path) -> None:
    worker = worker_for(studio, only(tmp_path, SLOW_TOOL))
    worker.hello()
    job_id = submit(studio).json()["id"]
    claimed = worker.http.post("/api/worker/claim").json()
    assert claimed["id"] == job_id
    bad = worker.http.put(
        f"/api/worker/jobs/{job_id}/artifacts/catalogs/all",
        content=b"data",
        headers={"X-Content-SHA256": "0" * 64},
    )
    assert bad.status_code == 422
    missing = worker.http.post(
        f"/api/worker/jobs/{job_id}/complete",
        json={
            "artifacts": [{"path": "catalogs/all", "sha256": "0" * 64, "size": 4}],
            "tool_version": "x",
        },
    )
    assert missing.status_code == 422
    escape = worker.http.put(
        f"/api/worker/jobs/{job_id}/artifacts/../../evil",
        content=b"x",
        headers={"X-Content-SHA256": hashlib.sha256(b"x").hexdigest()},
    )
    assert escape.status_code in (400, 404)


def _archive(members: list[tarfile.TarInfo], data: bytes = b"x") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for member in members:
            if member.isfile():
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            else:
                archive.addfile(member)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../evil", tarfile.REGTYPE),
        ("/etc/evil", tarfile.REGTYPE),
        ("manifests/x", tarfile.REGTYPE),
        ("pkgsinfo/link", tarfile.SYMTYPE),
    ],
)
def test_staging_refuses_unexpected_inputs(tmp_path: Path, name: str, kind: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.type = kind
    if kind == tarfile.SYMTYPE:
        member.linkname = "/etc/passwd"
    with pytest.raises(JobError, match="unexpected input"):
        safe_extract(_archive([member]), tmp_path, ("pkgsinfo",))
    assert not (tmp_path.parent / "evil").exists()


@pytest.mark.skipif(not REAL_MAKECATALOGS.is_file(), reason="needs Munki's makecatalogs")
def test_real_makecatalogs_on_this_mac(studio: TestClient) -> None:
    tools = Tools.probe()
    assert "makecatalogs" in tools.versions
    worker = worker_for(studio, tools)
    assert "catalog.generate" in worker.hello()
    job_id = submit(studio).json()["id"]
    assert worker.run_once()
    job = wait_for(studio, job_id, "succeeded")
    paths = {a["path"] for a in job["result"]["artifacts"]}
    assert "catalogs/all" in paths and "catalogs/testing" in paths
    body = studio.get(f"/api/repositories/fixture/jobs/{job_id}/artifacts/catalogs/all").content
    names = {item["name"] for item in plistlib.loads(body)}
    assert "ExampleEditor" in names


def test_worker_token_prints_a_config_entry_matching_the_token() -> None:
    from typer.testing import CliRunner

    from munki_web_studio.cli import cli
    from munki_web_studio.repository.config import parse_settings

    result = CliRunner().invoke(cli, ["worker", "token", "--id", "mac-1"])
    assert result.exit_code == 0
    token = result.stderr.strip().splitlines()[-1]
    import tomllib

    entry = tomllib.loads(result.stdout)
    settings = parse_settings(entry, base=Path("."))
    assert settings.workers[0].token_sha256 == hashlib.sha256(token.encode()).hexdigest()
