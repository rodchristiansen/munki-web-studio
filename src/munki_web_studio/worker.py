"""The worker: a separate process, usually on a Mac, that runs platform tools for
the studio.

It connects out to the studio, so the studio never needs to reach it. It reports
its OS and the versions of the tools it found, claims jobs it can run, copies
each job's inputs into a fresh staging folder, runs one allowlisted tool with a
fixed argument list, and uploads the output files with their hashes. It never
runs a shell or anything from the repository.

    mwsctl worker run --url https://studio.example.org --id mac-1 --token-file ~/.mac-1.token
"""

import hashlib
import io
import logging
import os
import platform as host
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, cast

import httpx
import typer

from munki_web_studio import platform
from munki_web_studio.jobs.remote import RemoteSpec
from munki_web_studio.studio import STUDIO

logger = logging.getLogger(__name__)
OUTPUT_TAIL = 4000


class JobError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, cancelled: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.cancelled = cancelled


@dataclass
class Tools:
    """The allowlisted tools found on this machine, and their versions."""

    paths: dict[str, Path] = field(default_factory=lambda: dict[str, Path]())
    versions: dict[str, str] = field(default_factory=lambda: dict[str, str]())

    @classmethod
    def probe(cls, overrides: dict[str, Path] | None = None) -> Tools:
        found = cls()
        for name, default in platform.WORKER_TOOLS.items():
            path = (overrides or {}).get(name, Path(default))
            if not path.is_file() or not os.access(path, os.X_OK):
                continue
            try:
                done = subprocess.run(  # noqa: S603 - a fixed, allowlisted tool path
                    [str(path), *platform.TOOL_VERSION_ARGS],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
            except OSError, subprocess.TimeoutExpired:
                continue
            version = (done.stdout or done.stderr).strip().splitlines()
            found.paths[name] = path
            found.versions[name] = version[0][:80] if version else "unknown"
        return found

    def capabilities(self) -> list[str]:
        return sorted(
            c for c, needed in platform.CAPABILITIES.items() if all(t in self.paths for t in needed)
        )


def os_version() -> str:
    if sys.platform == "darwin":
        return host.mac_ver()[0] or host.release()
    if sys.platform == "win32":
        return host.version()
    return host.release()


def safe_extract(data: bytes, staging: Path, folders: tuple[str, ...]) -> int:
    """Unpack job inputs. Only plain files inside the expected folders are taken;
    links, devices, absolute paths and `..` are refused."""
    count = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            name = PurePosixPath(member.name)
            if (
                not member.isfile()
                or name.is_absolute()
                or ".." in name.parts
                or not name.parts
                or name.parts[0] not in folders
            ):
                raise JobError(f"Refused an unexpected input: {member.name}")
            target = staging.joinpath(*name.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise JobError(f"Could not read input {member.name}")
            target.write_bytes(source.read())
            count += 1
    return count


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run_tool(
    argv: list[str],
    staging: Path,
    timeout: float,
    should_stop: Callable[[], bool],
) -> tuple[int, str]:
    """Run one tool with no shell and a minimal environment. Stops it when asked."""
    env = {"PATH": os.defpath, "HOME": str(staging), "LANG": "en_US.UTF-8"}
    if sys.platform == "win32":
        env |= {k: v for k, v in os.environ.items() if k.upper() in ("SYSTEMROOT", "TEMP", "TMP")}
    options: dict[str, Any] = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(  # noqa: S603 - allowlisted tool, argument list, no shell
            argv,
            cwd=staging,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            **options,
        )
        deadline = time.monotonic() + timeout
        stopped = None
        while process.poll() is None:
            if should_stop():
                stopped = "Cancelled"
            elif time.monotonic() > deadline:
                stopped = f"{Path(argv[0]).name} ran longer than {int(timeout)} seconds"
            if stopped:
                _kill(process)
                process.wait(10)
                raise JobError(stopped, cancelled=stopped == "Cancelled")
            time.sleep(0.2)
        log.seek(0)
        output = log.read().decode("utf-8", "replace")
    return process.returncode, output[-OUTPUT_TAIL:]


def _kill(process: subprocess.Popen[Any]) -> None:
    try:
        if sys.platform == "win32":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class Worker:
    def __init__(
        self,
        http: httpx.Client,
        worker_id: str,
        token: str,
        tools: Tools,
        *,
        poll_seconds: float = 2,
    ) -> None:
        self.http = http
        self.http.headers.update({"Authorization": f"Bearer {token}", "X-Studio-Worker": worker_id})
        self.id = worker_id
        self.tools = tools
        self.poll_seconds = poll_seconds
        self.specs = {s.name: s for s in platform.REMOTE_OPERATIONS}
        self.heartbeat_seconds = 30.0
        self.lease_seconds = 30.0
        self._last_hello = 0.0

    def _post(self, path: str, body: Any = None) -> httpx.Response:
        response = self.http.post(path, json=body)
        if response.status_code == 401:
            raise SystemExit("The studio refused this worker's id or token")
        return response

    def hello(self) -> list[str]:
        reply = (
            self._post(
                "/api/worker/hello",
                {
                    "platform": platform.WORKER_PLATFORM,
                    "os_version": os_version(),
                    "tools": self.tools.versions,
                    "capabilities": self.tools.capabilities(),
                },
            )
            .raise_for_status()
            .json()
        )
        self.heartbeat_seconds = float(reply["heartbeat_seconds"])
        self.lease_seconds = float(reply["lease_seconds"])
        self._last_hello = time.monotonic()
        return cast(list[str], reply["capabilities"])

    def run(self, *, once: bool = False, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        self.hello()
        while not stop.is_set():
            if time.monotonic() - self._last_hello > self.heartbeat_seconds:
                self.hello()
            if self.run_once():
                if once:
                    return
            elif once:
                return
            else:
                stop.wait(self.poll_seconds)

    def run_once(self) -> bool:
        reply = self._post("/api/worker/claim")
        if reply.status_code == 204:
            return False
        job = reply.raise_for_status().json()
        self._run(job)
        return True

    def _run(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        spec = self.specs.get(job["operation"])
        cancel = threading.Event()
        done = threading.Event()
        progress: dict[str, Any] = {"message": "Starting"}

        def beat() -> None:
            while not done.wait(min(self.lease_seconds / 3, 2.0)):
                reply = self._post(f"/api/worker/jobs/{job_id}/heartbeat", dict(progress))
                if reply.status_code == 409 or (reply.is_success and reply.json()["cancel"]):
                    cancel.set()

        pulse = threading.Thread(target=beat, daemon=True)
        pulse.start()
        staging = Path(tempfile.mkdtemp(prefix="studio-job-"))
        try:
            if spec is None:
                raise JobError(f"This worker does not know {job['operation']}")
            artifacts, version, output = self._execute(job_id, spec, staging, cancel, progress)
            done.set()
            pulse.join()
            if cancel.is_set():
                raise JobError("Cancelled", cancelled=True)
            self._post(
                f"/api/worker/jobs/{job_id}/complete",
                {"artifacts": artifacts, "tool_version": version, "output": output},
            ).raise_for_status()
        except JobError as error:
            done.set()
            pulse.join()
            self._post(
                f"/api/worker/jobs/{job_id}/fail",
                {
                    "error": str(error)[:2000],
                    "retryable": error.retryable,
                    "cancelled": error.cancelled,
                },
            )
        except httpx.HTTPError as error:
            done.set()
            pulse.join()
            logger.warning("Lost touch with the studio during job %s: %s", job_id, error)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _execute(
        self,
        job_id: str,
        spec: RemoteSpec,
        staging: Path,
        cancel: threading.Event,
        progress: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str, str]:
        tool = self.tools.paths.get(spec.tool)
        if tool is None:
            raise JobError(f"{spec.tool} is not installed on {self.id}")
        progress.update(message="Fetching inputs", progress=0.1)
        reply = self.http.get(f"/api/worker/jobs/{job_id}/inputs")
        if reply.status_code == 409:
            raise JobError(reply.json().get("detail", "The studio refused the inputs"))
        reply.raise_for_status()
        count = safe_extract(reply.content, staging, spec.inputs)
        progress.update(message=f"Running {spec.tool} on {count} files", progress=0.3)
        argv = [str(tool), *(a.replace("{staging}", str(staging)) for a in spec.args)]
        code, output = run_tool(argv, staging, spec.timeout_seconds, cancel.is_set)
        if code != 0:
            raise JobError(f"{spec.tool} exited with status {code}: {output[-1500:]}")
        progress.update(message="Uploading results", progress=0.8)
        artifacts: list[dict[str, Any]] = []
        out = staging / spec.outputs
        for path in sorted(out.rglob("*")) if out.is_dir() else []:
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(staging).as_posix()
            digest = _sha256(path)
            self.http.put(
                f"/api/worker/jobs/{job_id}/artifacts/{relative}",
                content=path.read_bytes(),
                headers={"X-Content-SHA256": digest},
            ).raise_for_status()
            artifacts.append({"path": relative, "sha256": digest, "size": path.stat().st_size})
        return artifacts, self.tools.versions.get(spec.tool, "unknown"), output


# Command line.

worker_cli = typer.Typer(help="Run a worker, or make a token for one.")


@worker_cli.command("token")
def token_command(
    worker_id: Annotated[str, typer.Option("--id", help="The worker's id, such as mac-1.")],
) -> None:
    """Make a new worker token. The token goes to the worker; the hash goes in the
    studio's config file."""
    import secrets

    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    typer.echo(f"Token for the worker (keep it secret):\n{token}\n", err=True)
    typer.echo(f'[[workers]]\nid = "{worker_id}"\ntoken_sha256 = "{digest}"')


@worker_cli.command("run")
def run_command(
    url: Annotated[
        str,
        typer.Option("--url", envvar=f"{STUDIO.command.upper()}_URL", help="The studio."),
    ],
    worker_id: Annotated[str, typer.Option("--id", help="This worker's id in the config.")],
    token_file: Annotated[
        Path, typer.Option(help="File holding the worker token.", exists=True, dir_okay=False)
    ],
    tool: Annotated[
        list[str] | None,
        typer.Option(help="name=path, to use a tool from another place. Names stay allowlisted."),
    ] = None,
    once: Annotated[bool, typer.Option(help="Run at most one job, then exit.")] = False,
) -> None:
    """Connect to a studio and run its jobs until stopped."""
    if sys.platform != "win32" and token_file.stat().st_mode & 0o077:
        typer.echo(f"{token_file} is readable by others; run chmod 600 on it", err=True)
        raise typer.Exit(2)
    overrides: dict[str, Path] = {}
    for item in tool or []:
        name, sep, path = item.partition("=")
        if not sep or name not in platform.WORKER_TOOLS:
            typer.echo(
                f"--tool takes name=path for one of {', '.join(platform.WORKER_TOOLS)}", err=True
            )
            raise typer.Exit(2)
        overrides[name] = Path(path)
    tools = Tools.probe(overrides)
    for name, version in tools.versions.items():
        typer.echo(f"{name} {version}", err=True)
    with httpx.Client(base_url=url.rstrip("/"), timeout=60) as http:
        worker = Worker(http, worker_id, token_file.read_text().strip(), tools)
        accepted = worker.hello()
        typer.echo(
            f"Connected to {url} as {worker_id}: {', '.join(accepted) or 'no capabilities'}",
            err=True,
        )
        try:
            worker.run(once=once)
        except KeyboardInterrupt:
            typer.echo("Stopped", err=True)
