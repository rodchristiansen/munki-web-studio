"""Server-side sessions in SQLite. The browser holds only a random session id."""

import datetime as dt
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from munki_web_studio.auth.model import Role, User

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id_hash TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    name TEXT NOT NULL,
    email TEXT,
    role INTEGER NOT NULL,
    csrf TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS logins (
    state TEXT PRIMARY KEY,
    nonce TEXT NOT NULL,
    verifier TEXT NOT NULL,
    next TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Session:
    user: User
    csrf: str


@dataclass(frozen=True)
class PendingLogin:
    nonce: str
    verifier: str
    next: str


def _hash(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class SessionStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def create(self, user: User, hours: float) -> tuple[str, Session]:
        """A new session; returns the token for the cookie. Only its hash is stored."""
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        expires = (_now() + dt.timedelta(hours=hours)).isoformat()
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_hash(token), user.subject, user.name, user.email, int(user.role), csrf, expires),
            )
        return token, Session(user, csrf)

    def get(self, token: str) -> Session | None:
        with self._lock:
            row = self._db.execute(
                "SELECT subject, name, email, role, csrf, expires_at FROM sessions "
                "WHERE id_hash = ?",
                (_hash(token),),
            ).fetchone()
        if row is None or dt.datetime.fromisoformat(row[5]) < _now():
            return None
        return Session(User(row[0], row[1], row[2], Role(row[3])), row[4])

    def delete(self, token: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM sessions WHERE id_hash = ?", (_hash(token),))

    def start_login(self, state: str, nonce: str, verifier: str, next_path: str) -> None:
        expires = (_now() + dt.timedelta(minutes=10)).isoformat()
        with self._lock, self._db:
            self._db.execute("DELETE FROM logins WHERE expires_at < ?", (_now().isoformat(),))
            self._db.execute(
                "INSERT INTO logins VALUES (?, ?, ?, ?, ?)",
                (state, nonce, verifier, next_path, expires),
            )

    def finish_login(self, state: str) -> PendingLogin | None:
        """Each login state works once and only for ten minutes."""
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT nonce, verifier, next, expires_at FROM logins WHERE state = ?", (state,)
            ).fetchone()
            self._db.execute("DELETE FROM logins WHERE state = ?", (state,))
        if row is None or dt.datetime.fromisoformat(row[3]) < _now():
            return None
        return PendingLogin(row[0], row[1], row[2])
