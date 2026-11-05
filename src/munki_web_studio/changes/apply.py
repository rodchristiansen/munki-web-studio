"""Preview a change set, and save it through a journal so a crash mid-save can be
finished or reported instead of leaving a half-written batch.

A save, under the repository's writer lock:
  1. re-reads every file and refuses if any no longer has its base revision,
  2. computes every new file and writes a journal holding all of them,
  3. writes each new file to a temporary file beside its target,
  4. checks the base revisions once more, then renames each temporary file over
     its target, and
  5. deletes the journal.
recover() finishes a journal that a crash left behind, file by file, and keeps it
(blocking further saves) for any file someone else changed in the meantime.
"""

import base64
import contextlib
import datetime as dt
import difflib
import json
import os
import secrets
import threading
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from munki_web_studio.changes.model import ChangeSet
from munki_web_studio.codec import CodecError, Format, ParseError, edit, load, revision_of
from munki_web_studio.codec.model import MAX_FILE_BYTES
from munki_web_studio.repository.config import Repository
from munki_web_studio.repository.paths import PathEscapeError, contained, read_contained

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None


class SaveError(Exception):
    pass


class ConflictError(SaveError):
    def __init__(self, paths: list[str]) -> None:
        super().__init__("Changed since the draft was made: " + ", ".join(paths))
        self.paths = paths


class RecoveryNeededError(SaveError):
    pass


@dataclass
class FilePreview:
    path: str
    base_revision: str
    current_revision: str | None
    conflict: bool = False
    error: str | None = None
    surgical: bool = True
    diff: str = ""
    new_raw: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True)
class RecoveryReport:
    journal: str
    finished: list[str]
    conflicts: list[str]


# Formats whose bytes are not text, so a line diff means nothing.
BINARY_FORMATS: frozenset[str] = frozenset({"binary-plist"})


def _diff(path: str, before: bytes, after: bytes, fmt: Format) -> str:
    if fmt.value in BINARY_FORMATS:
        return f"{path}: binary file rewritten ({len(before)} → {len(after)} bytes)"
    lines = difflib.unified_diff(
        before.decode("utf-8", "replace").splitlines(keepends=True),
        after.decode("utf-8", "replace").splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    )
    return "".join(lines)


def preview(repo: Repository, change_set: ChangeSet) -> list[FilePreview]:
    """What saving would do to each file, without writing anything."""
    results: list[FilePreview] = []
    for draft in change_set.files:
        result = FilePreview(draft.path, draft.base_revision, None)
        try:
            read = read_contained(repo.root, draft.path, MAX_FILE_BYTES)
            if read.raw is None:
                raise ParseError("File is too large")
            result.current_revision = revision_of(read.raw)
            if result.current_revision != draft.base_revision:
                result.conflict = True
                result.error = "The file changed after this draft was made"
            else:
                document = load(read.raw)
                edited = edit(document, draft.changes)
                result.new_raw = edited.raw
                result.surgical = edited.surgical
                result.diff = _diff(draft.path, read.raw, edited.raw, document.format)
        except (PathEscapeError, ParseError, CodecError, OSError) as error:
            result.error = str(error)
        results.append(result)
    return results


# One writer per repository in this process; a file lock covers other processes.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


@contextlib.contextmanager
def writer_lock(repo: Repository, journal_dir: Path) -> Generator[None]:
    with _locks_guard:
        lock = _locks.setdefault(repo.id, threading.Lock())
    journal_dir.mkdir(parents=True, exist_ok=True)
    with lock, (journal_dir / f"{repo.id}.lock").open("a") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_new(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _current_revision(repo: Repository, relative: str) -> str | None:
    try:
        read = read_contained(repo.root, relative, MAX_FILE_BYTES)
    except PathEscapeError, OSError:
        return None
    return revision_of(read.raw) if read.raw is not None else None


def journals(repo: Repository, journal_dir: Path) -> list[Path]:
    return sorted(journal_dir.glob(f"{repo.id}-*.journal.json")) if journal_dir.is_dir() else []


def commit(
    repo: Repository,
    change_set: ChangeSet,
    journal_dir: Path,
    *,
    crash_after: int | None = None,
) -> list[FilePreview]:
    """Save every file in the change set, or none of them. `crash_after` stops
    after that many renames, for tests of recovery."""
    with writer_lock(repo, journal_dir):
        if journals(repo, journal_dir):
            raise RecoveryNeededError(
                "An earlier save did not finish; recover it before saving again"
            )
        previews = preview(repo, change_set)
        conflicts = [p.path for p in previews if p.conflict]
        if conflicts:
            raise ConflictError(conflicts)
        failed = [f"{p.path}: {p.error}" for p in previews if p.error]
        if failed:
            raise SaveError("; ".join(failed))
        changed = [(p, p.new_raw) for p in previews if p.new_raw is not None]
        save_id = secrets.token_hex(6)
        entries: list[dict[str, Any]] = []
        for p, new_raw in changed:
            target = contained(repo.root, p.path)
            temp = target.with_name(f".{target.name}.studio-{save_id}.tmp")
            entries.append(
                {
                    "path": p.path,
                    "temp": temp.name,
                    "base_revision": p.base_revision,
                    "new_revision": revision_of(new_raw),
                    "data": base64.b64encode(new_raw).decode("ascii"),
                }
            )
        journal = journal_dir / f"{repo.id}-{save_id}.journal.json"
        record = {
            "repo_id": repo.id,
            "change_set": change_set.id,
            "created_at": dt.datetime.now(dt.UTC).isoformat(),
            "files": entries,
        }
        _write_new(journal, json.dumps(record).encode())
        _fsync_dir(journal_dir)
        temps: list[tuple[Path, Path]] = []
        try:
            for entry, (_, new_raw) in zip(entries, changed, strict=True):
                target = contained(repo.root, entry["path"])
                temp = target.with_name(entry["temp"])
                _write_new(temp, new_raw)
                temps.append((temp, target))
            moved = [
                e["path"]
                for e in entries
                if _current_revision(repo, e["path"]) != e["base_revision"]
            ]
            if moved:
                raise ConflictError(moved)
        except BaseException:
            for temp, _ in temps:
                temp.unlink(missing_ok=True)
            journal.unlink(missing_ok=True)
            raise
        for count, (temp, target) in enumerate(temps, start=1):
            os.replace(temp, target)
            _fsync_dir(target.parent)
            if crash_after is not None and count >= crash_after:
                raise SaveError("Simulated crash")
        journal.unlink()
        _fsync_dir(journal_dir)
        return previews


def recover(repo: Repository, journal_dir: Path) -> list[RecoveryReport]:
    """Finish or report every unfinished save for this repository."""
    reports: list[RecoveryReport] = []
    with writer_lock(repo, journal_dir):
        for journal in journals(repo, journal_dir):
            record = json.loads(journal.read_text())
            finished: list[str] = []
            conflicts: list[str] = []
            for entry in record["files"]:
                target = contained(repo.root, entry["path"])
                temp = target.with_name(entry["temp"])
                current = _current_revision(repo, entry["path"])
                if current == entry["new_revision"]:
                    finished.append(entry["path"])
                elif current == entry["base_revision"]:
                    temp.unlink(missing_ok=True)
                    _write_new(temp, base64.b64decode(entry["data"]))
                    os.replace(temp, target)
                    _fsync_dir(target.parent)
                    finished.append(entry["path"])
                else:
                    conflicts.append(entry["path"])
                temp.unlink(missing_ok=True)
            if not conflicts:
                journal.unlink()
            reports.append(RecoveryReport(journal.name, finished, conflicts))
    return reports
