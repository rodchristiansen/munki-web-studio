"""The search index: a replaceable SQLite reading of the repository's files.

The repository stays authoritative. Deleting the index loses nothing; the next
scan rebuilds it.
"""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
    repo_id TEXT NOT NULL,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    format TEXT,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    revision TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'invalid')),
    error TEXT,
    error_line INTEGER,
    error_column INTEGER,
    name TEXT,
    version TEXT,
    display_name TEXT,
    category TEXT,
    catalogs TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (repo_id, path)
);
CREATE INDEX IF NOT EXISTS objects_name ON objects (repo_id, kind, name);
CREATE TABLE IF NOT EXISTS scans (
    repo_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    files INTEGER NOT NULL,
    read INTEGER NOT NULL,
    removed INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class IndexedObject:
    repo_id: str
    path: str
    kind: str
    format: str | None
    size: int
    mtime_ns: int
    revision: str
    status: str
    error: str | None
    error_line: int | None
    error_column: int | None
    name: str | None
    version: str | None
    display_name: str | None
    category: str | None
    catalogs: list[str]
    summary: dict[str, Any]


class Index:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def transaction(self) -> sqlite3.Connection:
        """Use as `with index.transaction() as db:`; commits or rolls back on exit."""
        return self._db

    def stats(self, repo_id: str) -> dict[str, tuple[int, int, str]]:
        """path -> (size, mtime_ns, revision) for change detection."""
        rows = self._db.execute(
            "SELECT path, size, mtime_ns, revision FROM objects WHERE repo_id = ?", (repo_id,)
        )
        return {r["path"]: (r["size"], r["mtime_ns"], r["revision"]) for r in rows}

    def get(self, repo_id: str, path: str) -> IndexedObject | None:
        row = self._db.execute(
            "SELECT * FROM objects WHERE repo_id = ? AND path = ?", (repo_id, path)
        ).fetchone()
        return _object(row) if row else None

    def all(self, repo_id: str, kind: str | None = None) -> list[IndexedObject]:
        if kind is None:
            rows = self._db.execute(
                "SELECT * FROM objects WHERE repo_id = ? ORDER BY path", (repo_id,)
            )
        else:
            rows = self._db.execute(
                "SELECT * FROM objects WHERE repo_id = ? AND kind = ? ORDER BY path",
                (repo_id, kind),
            )
        return [_object(r) for r in rows]

    def last_scan(self, repo_id: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM scans WHERE repo_id = ?", (repo_id,)).fetchone()
        return dict(row) if row else None


def _object(row: sqlite3.Row) -> IndexedObject:
    return IndexedObject(
        repo_id=row["repo_id"],
        path=row["path"],
        kind=row["kind"],
        format=row["format"],
        size=row["size"],
        mtime_ns=row["mtime_ns"],
        revision=row["revision"],
        status=row["status"],
        error=row["error"],
        error_line=row["error_line"],
        error_column=row["error_column"],
        name=row["name"],
        version=row["version"],
        display_name=row["display_name"],
        category=row["category"],
        catalogs=json.loads(row["catalogs"]),
        summary=json.loads(row["summary"]),
    )
