"""Change sets live in the studio's state database, never in the repository."""

import datetime as dt
import json
import secrets
import sqlite3
import threading
from pathlib import Path
from typing import Any

from munki_web_studio import tagged
from munki_web_studio.changes.model import (
    ChangeSet,
    FileDraft,
    NewFile,
    Payload,
    change_from_json,
    change_to_json,
)
from munki_web_studio.codec import Change

SCHEMA = """
CREATE TABLE IF NOT EXISTS change_sets (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    owner_name TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'saved', 'discarded')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS new_files (
    change_set TEXT NOT NULL REFERENCES change_sets(id),
    path TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (change_set, path)
);
CREATE TABLE IF NOT EXISTS draft_payloads (
    change_set TEXT NOT NULL REFERENCES change_sets(id),
    path TEXT NOT NULL,
    upload_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    PRIMARY KEY (change_set, path)
);
CREATE TABLE IF NOT EXISTS saved_files (
    change_set TEXT NOT NULL REFERENCES change_sets(id),
    path TEXT NOT NULL,
    revision TEXT NOT NULL,
    PRIMARY KEY (change_set, path)
);
CREATE TABLE IF NOT EXISTS commits (
    change_set TEXT PRIMARY KEY REFERENCES change_sets(id),
    commit_id TEXT NOT NULL,
    committed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS file_drafts (
    change_set TEXT NOT NULL REFERENCES change_sets(id),
    path TEXT NOT NULL,
    base_revision TEXT NOT NULL,
    changes TEXT NOT NULL,
    PRIMARY KEY (change_set, path)
);
"""


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


class ChangeSetStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def create(self, repo_id: str, owner: str, owner_name: str, title: str) -> ChangeSet:
        change_id = secrets.token_hex(8)
        now = _now()
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO change_sets VALUES (?, ?, ?, ?, ?, 'open', ?, ?)",
                (change_id, repo_id, owner, owner_name, title, now, now),
            )
        found = self.get(change_id)
        if found is None:
            raise RuntimeError("Change set vanished after creation")
        return found

    def get(self, change_id: str) -> ChangeSet | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM change_sets WHERE id = ?", (change_id,)
            ).fetchone()
            if row is None:
                return None
            files = self._db.execute(
                "SELECT path, base_revision, changes FROM file_drafts "
                "WHERE change_set = ? ORDER BY path",
                (change_id,),
            ).fetchall()
            created = self._db.execute(
                "SELECT path, data FROM new_files WHERE change_set = ? ORDER BY path",
                (change_id,),
            ).fetchall()
            payloads = self._db.execute(
                "SELECT path, upload_id, sha256, size FROM draft_payloads "
                "WHERE change_set = ? ORDER BY path",
                (change_id,),
            ).fetchall()
        return ChangeSet(
            id=row["id"],
            repo_id=row["repo_id"],
            owner=row["owner"],
            owner_name=row["owner_name"],
            title=row["title"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            files=[
                FileDraft(
                    f["path"],
                    f["base_revision"],
                    [change_from_json(c) for c in json.loads(f["changes"])],
                )
                for f in files
            ],
            new_files=[NewFile(n["path"], tagged.decode(json.loads(n["data"]))) for n in created],
            payloads=[Payload(p["path"], p["upload_id"], p["sha256"], p["size"]) for p in payloads],
        )

    def list(self, repo_id: str, owner: str | None = None, status: str = "open") -> list[ChangeSet]:
        with self._lock:
            if owner is None:
                rows = self._db.execute(
                    "SELECT id FROM change_sets WHERE repo_id = ? AND status = ? "
                    "ORDER BY updated_at DESC",
                    (repo_id, status),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT id FROM change_sets WHERE repo_id = ? AND owner = ? AND status = ? "
                    "ORDER BY updated_at DESC",
                    (repo_id, owner, status),
                ).fetchall()
        return [cs for r in rows if (cs := self.get(r["id"])) is not None]

    def put_file(
        self, change_id: str, path: str, base_revision: str, changes: list[Change]
    ) -> None:
        """Set one file's edits in a draft. An empty list removes the file from it."""
        with self._lock, self._db:
            if changes:
                self._db.execute(
                    "INSERT OR REPLACE INTO file_drafts VALUES (?, ?, ?, ?)",
                    (
                        change_id,
                        path,
                        base_revision,
                        json.dumps([change_to_json(c) for c in changes]),
                    ),
                )
            else:
                self._db.execute(
                    "DELETE FROM file_drafts WHERE change_set = ? AND path = ?", (change_id, path)
                )
            self._db.execute(
                "UPDATE change_sets SET updated_at = ? WHERE id = ?", (_now(), change_id)
            )

    def set_status(self, change_id: str, status: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE change_sets SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), change_id),
            )

    def record_saved(self, change_id: str, revisions: dict[str, str]) -> None:
        """The revision each file had right after this change set was saved."""
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO saved_files VALUES (?, ?, ?)",
                [(change_id, path, rev) for path, rev in revisions.items()],
            )

    def saved_revisions(self, change_id: str) -> dict[str, str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT path, revision FROM saved_files WHERE change_set = ? ORDER BY path",
                (change_id,),
            ).fetchall()
        return {r["path"]: r["revision"] for r in rows}

    def record_commit(self, change_id: str, commit_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO commits VALUES (?, ?, ?)", (change_id, commit_id, _now()))

    def commit_of(self, change_id: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT commit_id FROM commits WHERE change_set = ?", (change_id,)
            ).fetchone()
        return row["commit_id"] if row else None

    def put_new_file(self, change_id: str, path: str, data: Any) -> None:
        """Add a file the draft creates, or replace its contents in the draft."""
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO new_files VALUES (?, ?, ?)",
                (change_id, path, json.dumps(tagged.encode(data))),
            )
            self._db.execute(
                "UPDATE change_sets SET updated_at = ? WHERE id = ?", (_now(), change_id)
            )

    def put_payload(self, change_id: str, payload: Payload) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO draft_payloads VALUES (?, ?, ?, ?, ?)",
                (change_id, payload.path, payload.upload_id, payload.sha256, payload.size),
            )
