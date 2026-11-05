"""An append-only audit log, hash-chained so later edits to old entries show.

Every change made through the studio is recorded with who made it and through
which interface. Rows are only ever inserted; each row's hash covers the row and
the previous row's hash, and verify() walks the chain.
"""

import datetime as dt
import hashlib
import json
import sqlite3
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    actor_name TEXT NOT NULL,
    interface TEXT NOT NULL,
    action TEXT NOT NULL,
    repo_id TEXT,
    target TEXT,
    outcome TEXT NOT NULL,
    detail TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'audit entries cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'audit entries cannot be deleted'); END;
"""

GENESIS = "0" * 64


@dataclass(frozen=True)
class AuditEntry:
    id: int
    at: str
    actor: str
    actor_name: str
    interface: str
    action: str
    repo_id: str | None
    target: str | None
    outcome: str
    detail: dict[str, Any]


def _digest(prev: str, fields: dict[str, Any]) -> str:
    body = json.dumps(fields, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256((prev + body).encode()).hexdigest()


class AuditLog:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def record(
        self,
        *,
        actor: str,
        actor_name: str,
        interface: str,
        action: str,
        outcome: str = "ok",
        repo_id: str | None = None,
        target: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "at": dt.datetime.now(dt.UTC).isoformat(),
            "actor": actor,
            "actor_name": actor_name,
            "interface": interface,
            "action": action,
            "repo_id": repo_id,
            "target": target,
            "outcome": outcome,
            "detail": json.dumps(detail or {}, sort_keys=True, default=str),
        }
        with self._lock, self._db:
            row = self._db.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
            prev = row["hash"] if row else GENESIS
            self._db.execute(
                "INSERT INTO audit (at, actor, actor_name, interface, action, repo_id, target, "
                "outcome, detail, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*fields.values(), prev, _digest(prev, fields)),
            )

    def entries(self, *, limit: int = 100, before: int | None = None) -> list[AuditEntry]:
        with self._lock:
            if before is None:
                rows = self._db.execute(
                    "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM audit WHERE id < ? ORDER BY id DESC LIMIT ?", (before, limit)
                ).fetchall()
        return [
            AuditEntry(
                id=r["id"],
                at=r["at"],
                actor=r["actor"],
                actor_name=r["actor_name"],
                interface=r["interface"],
                action=r["action"],
                repo_id=r["repo_id"],
                target=r["target"],
                outcome=r["outcome"],
                detail=json.loads(r["detail"]),
            )
            for r in rows
        ]

    def verify(self) -> int | None:
        """The id of the first entry whose hash does not match, or None if all do."""
        prev = GENESIS
        with self._lock:
            rows = self._db.execute("SELECT * FROM audit ORDER BY id").fetchall()
        for r in rows:
            fields = {
                k: r[k]
                for k in (
                    "at",
                    "actor",
                    "actor_name",
                    "interface",
                    "action",
                    "repo_id",
                    "target",
                    "outcome",
                    "detail",
                )
            }
            if r["prev_hash"] != prev or r["hash"] != _digest(prev, fields):
                return int(r["id"])
            prev = r["hash"]
        return None


def as_dict(entry: AuditEntry) -> dict[str, Any]:
    return asdict(entry)
