"""Scan a repository into the index. Reads files; never writes to the repository."""

import datetime as dt
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from munki_web_studio import platform
from munki_web_studio.codec import ParseError, load, revision_of
from munki_web_studio.codec.model import MAX_FILE_BYTES
from munki_web_studio.repository.config import Repository
from munki_web_studio.repository.index import Index
from munki_web_studio.repository.paths import PathEscapeError, contained, read_contained


@dataclass
class ScanResult:
    files: int = 0
    read: int = 0
    unchanged: int = 0
    removed: int = 0
    invalid: int = 0
    skipped: list[str] = field(default_factory=lambda: [])
    changed: list[str] = field(default_factory=lambda: [])


def _candidates(root: Path) -> list[tuple[str, str]]:
    """(relative path, kind) for every metadata file, in a stable order."""
    found: list[tuple[str, str]] = []
    for folder, kind in platform.KINDS.items():
        base = root / folder
        if not base.is_dir() or base.is_symlink():
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                if name.startswith(".") or name in platform.IGNORED_NAMES:
                    continue
                if platform.SUFFIXES is not None and Path(name).suffix not in platform.SUFFIXES:
                    continue
                relative = (Path(dirpath) / name).relative_to(root).as_posix()
                found.append((relative, kind))
    return found


def scan(repo: Repository, index: Index, *, full: bool = False) -> ScanResult:
    """Bring the index up to date with the repository.

    Files whose size and modification time are unchanged are skipped unless
    `full` is set. Changed files are re-read; a file with identical bytes keeps
    its entry and only its timestamp is updated.
    """
    started = dt.datetime.now(dt.UTC).isoformat()
    result = ScanResult()
    known = index.stats(repo.id)
    seen: set[str] = set()
    with index.transaction() as db:
        for relative, kind in _candidates(repo.root):
            try:
                path = contained(repo.root, relative)
            except PathEscapeError:
                result.skipped.append(relative)
                continue
            if not path.is_file():
                result.skipped.append(relative)
                continue
            stat = path.stat()
            result.files += 1
            seen.add(relative)
            previous = known.get(relative)
            if not full and previous and previous[:2] == (stat.st_size, stat.st_mtime_ns):
                result.unchanged += 1
                continue
            try:
                read = read_contained(repo.root, relative, MAX_FILE_BYTES)
            except PathEscapeError:
                result.files -= 1
                seen.discard(relative)
                result.skipped.append(relative)
                continue
            raw = read.raw or b""
            too_large = read.raw is None
            revision = f"size:{read.size}:{read.mtime_ns}" if too_large else revision_of(raw)
            if previous and previous[2] == revision:
                db.execute(
                    "UPDATE objects SET size = ?, mtime_ns = ? WHERE repo_id = ? AND path = ?",
                    (read.size, read.mtime_ns, repo.id, relative),
                )
                result.unchanged += 1
                continue
            result.read += 1
            result.changed.append(relative)
            row: dict[str, object] = {
                "repo_id": repo.id,
                "path": relative,
                "kind": kind,
                "format": None,
                "size": read.size,
                "mtime_ns": read.mtime_ns,
                "revision": revision,
                "status": "ok",
                "error": None,
                "error_line": None,
                "error_column": None,
                "name": None,
                "version": None,
                "display_name": None,
                "category": None,
                "catalogs": "[]",
                "summary": "{}",
            }
            try:
                if too_large:
                    raise ParseError(f"File is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
                document = load(raw)
            except ParseError as error:
                result.invalid += 1
                row |= {
                    "status": "invalid",
                    "error": error.message,
                    "error_line": error.line,
                    "error_column": error.column,
                }
            else:
                summary = platform.summarize(kind, document.data)
                if kind != "catalog" and not isinstance(document.data, dict):
                    result.invalid += 1
                    row |= {
                        "status": "invalid",
                        "error": "The file's top level is not a dictionary",
                    }
                row |= {
                    "format": document.format.value,
                    "name": summary.get("name"),
                    "version": summary.get("version"),
                    "display_name": summary.get("display_name"),
                    "category": summary.get("category"),
                    "catalogs": json.dumps(summary.get("catalogs", [])),
                    "summary": json.dumps(summary),
                }
            columns = ", ".join(row)
            placeholders = ", ".join("?" for _ in row)
            db.execute(
                f"INSERT OR REPLACE INTO objects ({columns}) VALUES ({placeholders})",  # noqa: S608
                tuple(row.values()),
            )
        gone = [p for p in known if p not in seen]
        db.executemany(
            "DELETE FROM objects WHERE repo_id = ? AND path = ?", [(repo.id, p) for p in gone]
        )
        result.removed = len(gone)
        result.changed += gone
        db.execute(
            "INSERT OR REPLACE INTO scans VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                repo.id,
                started,
                dt.datetime.now(dt.UTC).isoformat(),
                result.files,
                result.read,
                result.removed,
                # Only the most recent paths; enough for a browser to spot its own file.
                json.dumps(result.changed[:500]),
            ),
        )
    return result
