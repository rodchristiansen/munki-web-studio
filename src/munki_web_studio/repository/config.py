"""Repositories come from an administrator's config file, never from a request.

[[repositories]]
id = "main"
name = "Main repository"
path = "/srv/repo"
writable = false

state_dir = "/var/lib/studio"

[[workers]]
id = "mac-1"
token_sha256 = "…"   # from `<command> worker token`; the worker keeps the token
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from munki_web_studio.auth.config import AuthConfigError, AuthSettings, parse_auth


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Repository:
    id: str
    name: str
    root: Path
    writable: bool = False
    live: bool = False


@dataclass(frozen=True)
class WorkerEntry:
    """A worker allowed to connect. Only the token's hash is kept here."""

    id: str
    token_sha256: str


LOCAL_HOSTS = ("localhost", "127.0.0.1")


@dataclass(frozen=True)
class Settings:
    repositories: tuple[Repository, ...]
    state_dir: Path
    allowed_hosts: tuple[str, ...] = LOCAL_HOSTS
    rescan_seconds: float = 30
    auth: AuthSettings = field(default_factory=AuthSettings)
    workers: tuple[WorkerEntry, ...] = ()

    def repository(self, repo_id: str) -> Repository:
        for repo in self.repositories:
            if repo.id == repo_id:
                return repo
        raise KeyError(repo_id)


def load_settings(path: Path) -> Settings:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"Cannot read {path}: {error}") from error
    return parse_settings(raw, base=path.parent)


def parse_settings(raw: dict[str, Any], base: Path) -> Settings:
    entries = raw.get("repositories", [])
    if not isinstance(entries, list):
        raise ConfigError("repositories must be a list")
    repos: list[Repository] = []
    for entry in cast(list[Any], entries):
        if not isinstance(entry, dict):
            raise ConfigError("Each repository must be a table")
        item = cast(dict[str, Any], entry)
        repo_id, path = item.get("id"), item.get("path")
        if not isinstance(repo_id, str) or not repo_id.isidentifier():
            raise ConfigError(f"Repository id must be a simple name, not {repo_id!r}")
        if not isinstance(path, str):
            raise ConfigError(f"Repository {repo_id} needs a path")
        root = (base / path).resolve(strict=False)
        if not root.is_dir():
            raise ConfigError(f"Repository {repo_id}: {root} is not a directory")
        if any(r.id == repo_id for r in repos):
            raise ConfigError(f"Repository id {repo_id} is used twice")
        repos.append(
            Repository(
                id=repo_id,
                name=str(item.get("name", repo_id)),
                root=root,
                writable=bool(item.get("writable", False)),
                live=bool(item.get("live", False)),
            )
        )
    state = raw.get("state_dir", ".state")
    if not isinstance(state, str):
        raise ConfigError("state_dir must be a path")
    hosts = raw.get("allowed_hosts", [])
    if not isinstance(hosts, list) or not all(
        isinstance(h, str) and h for h in cast(list[Any], hosts)
    ):
        raise ConfigError("allowed_hosts must be a list of host names")
    if "*" in hosts:
        raise ConfigError("allowed_hosts cannot be '*'; list the host names instead")
    allowed = tuple(dict.fromkeys([*LOCAL_HOSTS, *cast(list[str], hosts)]))
    rescan = raw.get("rescan_seconds", 30)
    if not isinstance(rescan, int | float) or isinstance(rescan, bool) or rescan < 0:
        raise ConfigError("rescan_seconds must be a number of seconds, or 0 to turn it off")
    try:
        auth = parse_auth(raw.get("auth"))
    except AuthConfigError as error:
        raise ConfigError(str(error)) from error
    if not auth.enabled and any(h not in LOCAL_HOSTS for h in allowed):
        raise ConfigError(
            'Set up sign-in ([auth] mode = "oidc") before serving other host names; '
            "without it the studio is for this machine only"
        )
    workers = _parse_workers(raw.get("workers", []))
    return Settings(
        repositories=tuple(repos),
        workers=workers,
        state_dir=(base / state).resolve(strict=False),
        allowed_hosts=allowed,
        rescan_seconds=float(rescan),
        auth=auth,
    )


def _parse_workers(entries: Any) -> tuple[WorkerEntry, ...]:
    if not isinstance(entries, list):
        raise ConfigError("workers must be a list")
    found: list[WorkerEntry] = []
    for entry in cast(list[Any], entries):
        if not isinstance(entry, dict):
            raise ConfigError("Each worker must be a table")
        item = cast(dict[str, Any], entry)
        worker_id, digest = item.get("id"), item.get("token_sha256")
        if not isinstance(worker_id, str) or not worker_id.replace("-", "_").isidentifier():
            raise ConfigError(f"Worker id must be a simple name, not {worker_id!r}")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ConfigError(f"Worker {worker_id} needs token_sha256, a 64-character hex digest")
        if any(w.id == worker_id for w in found):
            raise ConfigError(f"Worker id {worker_id} is used twice")
        found.append(WorkerEntry(worker_id, digest))
    return tuple(found)
