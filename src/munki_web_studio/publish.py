"""Publishing a repository as a complete snapshot that clients are served from.

A release is built in a hidden folder under `target/releases`, every file is
checked against the hash taken from the repository, and only then is the folder
given its final name and `target/current` switched to it. A web server serving
`target/current` therefore sees the old release or the new one, never part of
one. A release is named by a hash of everything in it, so publishing again with
nothing changed reuses the release and reports that nothing changed.

On POSIX `current` is a symlink replaced with one rename, which is atomic. On
Windows it is a directory junction. Windows cannot rename over a directory, so
an existing junction is instead pointed at the new release in place
(FSCTL_SET_REPARSE_POINT), which readers also see in one step. The first
publish, or a `current` that is not a junction, creates a junction beside it
and renames it in, with a moment between the renames in which `current` does
not exist.
"""

import contextlib
import datetime as dt
import hashlib
import json
import os
import secrets
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from munki_web_studio import platform
from munki_web_studio.repository.config import Publish, Repository
from munki_web_studio.repository.paths import is_link, replace

RELEASE_FILE = ".release.json"
CHUNK = 1 << 20

Progress = Callable[[float, str], None]


class PublishError(Exception):
    pass


@dataclass(frozen=True)
class Release:
    id: str
    published_at: str
    repository_revision: str
    files: int
    bytes: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def contents(repo: Repository) -> dict[str, tuple[int, str]]:
    """Every file clients are served, as path to (size, sha256). Links and hidden
    files are left out, as the scanner leaves them out."""
    found: dict[str, tuple[int, str]] = {}
    root = repo.root.resolve()
    for folder in platform.PUBLISHED_FOLDERS:
        base = root / folder
        if not base.is_dir() or is_link(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(
                d for d in dirnames if not d.startswith(".") and not is_link(Path(dirpath) / d)
            )
            for name in sorted(filenames):
                path = Path(dirpath) / name
                if name.startswith(".") or name in platform.IGNORED_NAMES or is_link(path):
                    continue
                if not path.resolve().is_relative_to(root):
                    continue
                relative = path.relative_to(root).as_posix()
                found[relative] = (path.stat().st_size, _sha256(path))
    return found


def release_id(files: dict[str, tuple[int, str]]) -> str:
    listing = json.dumps(sorted((p, s, h) for p, (s, h) in files.items()))
    return hashlib.sha256(listing.encode()).hexdigest()[:20]


def _read_release(folder: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((folder / RELEASE_FILE).read_text())
    except OSError, ValueError:
        return None
    return cast(dict[str, Any], data) if isinstance(data, dict) else None


def _summary(data: dict[str, Any]) -> Release:
    files = cast(dict[str, list[Any]], data["files"])
    return Release(
        id=str(data["id"]),
        published_at=str(data["published_at"]),
        repository_revision=str(data.get("repository_revision", "")),
        files=len(files),
        bytes=sum(int(v[0]) for v in files.values()),
    )


def _link_or_copy(source: Path, destination: Path, hardlink: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if hardlink:
        try:
            os.link(source, destination)
            return
        except OSError:
            pass
    shutil.copyfile(source, destination)


def _fsync(path: Path) -> None:
    with contextlib.suppress(OSError), path.open("r+b") as handle:
        os.fsync(handle.fileno())


def current_release(publish: Publish) -> Release | None:
    data = _read_release(publish.target / "current")
    return _summary(data) if data else None


def releases(publish: Publish) -> list[Release]:
    folder = publish.target / "releases"
    found: list[Release] = []
    for path in sorted(folder.iterdir()) if folder.is_dir() else []:
        if path.name.startswith(".") or not path.is_dir():
            continue
        data = _read_release(path)
        if data is not None and data.get("id") == path.name:
            found.append(_summary(data))
    return sorted(found, key=lambda r: r.published_at, reverse=True)


if sys.platform == "win32":

    def _retarget_junction(junction: Path, release: Path) -> None:
        """Point an existing junction at another folder in one step, by replacing
        its mount-point data. Opening the junction itself needs no admin rights."""
        import ctypes
        import struct
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel32.CreateFileW
        create.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create.restype = wintypes.HANDLE
        control = kernel32.DeviceIoControl
        control.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        )
        control.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

        folder = str(release.resolve(strict=True))
        substitute = ("\\??\\" + folder).encode("utf-16-le")
        printed = folder.encode("utf-16-le")
        names = substitute + b"\0\0" + printed + b"\0\0"
        offsets = struct.pack("<HHHH", 0, len(substitute), len(substitute) + 2, len(printed))
        body = offsets + names
        mount_point, set_reparse_point = 0xA0000003, 0x000900A4
        buffer = struct.pack("<IHH", mount_point, len(body), 0) + body
        generic_write, share_all, open_existing = 0x40000000, 0x7, 3
        open_reparse_point, backup_semantics = 0x00200000, 0x02000000
        handle = create(
            str(junction),
            generic_write,
            share_all,
            None,
            open_existing,
            open_reparse_point | backup_semantics,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            written = wintypes.DWORD()
            ok = control(
                handle, set_reparse_point, buffer, len(buffer), None, 0, ctypes.byref(written), None
            )
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel32.CloseHandle(handle)


def _switch(target: Path, release: str) -> None:
    current = target / "current"
    staged = target / f".current-{secrets.token_hex(4)}"
    if sys.platform == "win32":
        import _winapi

        if os.path.isjunction(current):
            _retarget_junction(current, target / "releases" / release)
            return
        _winapi.CreateJunction(str(target / "releases" / release), str(staged))  # type: ignore[attr-defined]
        old = target / f".current-old-{secrets.token_hex(4)}"
        if current.exists() or is_link(current):
            replace(current, old)
        try:
            replace(staged, current)
        except BaseException:
            if old.exists() or is_link(old):
                replace(old, current)
            raise
        if is_link(old):
            os.rmdir(old)
    else:
        staged.symlink_to(Path("releases") / release)
        os.replace(staged, current)


def _points_at(target: Path, release: str) -> bool:
    try:
        return (target / "current").resolve() == (target / "releases" / release).resolve()
    except OSError:
        return False


def publish_repository(
    repo: Repository,
    repository_revision: str,
    progress: Progress,
    check: Callable[[], None],
) -> dict[str, Any]:
    """Publish `repo` to its snapshot target. Returns what happened."""
    publish = repo.publish
    if publish is None:
        raise PublishError("This repository has no publish target")
    target = publish.target
    (target / "releases").mkdir(parents=True, exist_ok=True)
    progress(0.05, "Reading the repository")
    files = contents(repo)
    check()
    release = release_id(files)
    final = target / "releases" / release
    existing = _read_release(final)
    built = False
    if existing is None or existing.get("files") != {p: list(v) for p, v in files.items()}:
        building = target / "releases" / f".building-{release}-{secrets.token_hex(4)}"
        try:
            total = max(len(files), 1)
            for number, (path, (size, digest)) in enumerate(sorted(files.items()), 1):
                written = building / path
                _link_or_copy(repo.root / path, written, publish.hardlink)
                if written.stat().st_size != size or _sha256(written) != digest:
                    raise PublishError(f"{path} changed while it was being published")
                if number % 200 == 0:
                    check()
                    progress(0.1 + 0.75 * number / total, f"Copied {number} of {total} files")
            record = {
                "id": release,
                "published_at": dt.datetime.now(dt.UTC).isoformat(),
                "repository_revision": repository_revision,
                "files": {p: list(v) for p, v in files.items()},
            }
            (building / RELEASE_FILE).write_text(json.dumps(record, indent=1, sort_keys=True))
            for path in building.rglob("*"):
                if path.is_file():
                    _fsync(path)
            check()
            # A folder already under this name is incomplete or was altered; it is
            # moved aside only now that its replacement is ready.
            stale = target / "releases" / f".stale-{release}-{secrets.token_hex(4)}"
            if final.exists():
                replace(final, stale)
            replace(building, final)
            shutil.rmtree(stale, ignore_errors=True)
            built = True
        finally:
            if building.exists():
                shutil.rmtree(building, ignore_errors=True)
    already = _points_at(target, release)
    if not already:
        progress(0.9, "Switching clients to the new release")
        check()
        _switch(target, release)
        if not _points_at(target, release):
            raise PublishError("current does not point at the new release after the switch")
    return {
        "release": release,
        "files": len(files),
        "bytes": sum(size for size, _ in files.values()),
        "built": built,
        "switched": not already,
        "already_published": already and not built,
        "pruned": _prune(publish, release),
    }


def _prune(publish: Publish, keep_id: str) -> list[str]:
    """Remove the oldest releases beyond `keep`, never the current one."""
    found = releases(publish)
    keep = {keep_id, *(r.id for r in found[: publish.keep])}
    removed: list[str] = []
    for release in found:
        if release.id not in keep:
            shutil.rmtree(publish.target / "releases" / release.id, ignore_errors=True)
            removed.append(release.id)
    return removed
