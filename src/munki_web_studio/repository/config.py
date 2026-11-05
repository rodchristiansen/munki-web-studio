"""Repositories come from an administrator's config file, never from a request.

[[repositories]]
id = "main"
name = "Main repository"
path = "/srv/repo"
writable = false

state_dir = "/var/lib/studio"
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Repository:
    id: str
    name: str
    root: Path
    writable: bool = False


LOCAL_HOSTS = ("localhost", "127.0.0.1")


@dataclass(frozen=True)
class Settings:
    repositories: tuple[Repository, ...]
    state_dir: Path
    allowed_hosts: tuple[str, ...] = LOCAL_HOSTS
    rescan_seconds: float = 30

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
    return Settings(
        repositories=tuple(repos),
        state_dir=(base / state).resolve(strict=False),
        allowed_hosts=allowed,
        rescan_seconds=float(rescan),
    )
