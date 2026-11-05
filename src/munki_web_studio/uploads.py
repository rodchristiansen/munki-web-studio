"""Resumable uploads into private staging, outside the repository.

An upload is declared with its name, size and SHA-256, then sent in chunks,
each tagged with the offset it starts at. A chunk for the wrong offset is
refused, so a client that lost its connection asks how much arrived and
carries on from there. When the last byte arrives the whole file is hashed,
and only an upload whose hash matches the declared one is marked complete.
"""

import hashlib
import json
import os
import re
import secrets
import shutil
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from munki_web_studio import platform

MAX_UPLOAD_BYTES = 16 * 1024**3
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._+()-]{0,200}")
# Names Windows treats as devices whatever the extension, including the
# superscript-digit forms it also reserves.
_DEVICES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"{port}{n}" for port in ("COM", "LPT") for n in [*"0123456789", "¹", "²", "³"]}
)


def safe_filename(name: str) -> bool:
    """A name that means the same file on macOS, Linux and Windows: plain
    characters, no trailing dot or space, and not a Windows device name."""
    if not _SAFE_NAME.fullmatch(name) or name.endswith((".", " ")):
        return False
    stem = name.split(".")[0].rstrip(" ").upper()
    return stem not in _DEVICES


class UploadError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Upload:
    id: str
    repo_id: str
    owner: str
    filename: str
    size: int
    sha256: str
    received: int = 0
    complete: bool = False


class UploadStore:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._lock = threading.Lock()

    def _dir(self, upload_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{24}", upload_id):
            raise UploadError(404, "No such upload")
        return self.folder / upload_id

    def _save(self, upload: Upload) -> None:
        meta = self._dir(upload.id) / "upload.json"
        temp = meta.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(upload)))
        os.replace(temp, meta)

    def create(self, repo_id: str, owner: str, filename: str, size: int, sha256: str) -> Upload:
        if not safe_filename(filename):
            raise UploadError(400, "Use a plain file name: letters, digits, spaces and . _ + ( ) -")
        if not filename.lower().endswith(platform.IMPORT_SUFFIXES):
            raise UploadError(400, f"Packages must end in {', '.join(platform.IMPORT_SUFFIXES)}")
        if not 0 < size <= MAX_UPLOAD_BYTES:
            raise UploadError(413, f"Uploads must be between 1 byte and {MAX_UPLOAD_BYTES} bytes")
        if not re.fullmatch(r"[0-9a-f]{64}", sha256.lower()):
            raise UploadError(400, "sha256 must be 64 hex digits")
        upload = Upload(secrets.token_hex(12), repo_id, owner, filename, size, sha256.lower())
        self._dir(upload.id).mkdir(parents=True)
        (self._dir(upload.id) / "data").touch()
        self._save(upload)
        return upload

    def get(self, upload_id: str) -> Upload:
        try:
            data: dict[str, Any] = json.loads((self._dir(upload_id) / "upload.json").read_text())
        except OSError, ValueError:
            raise UploadError(404, "No such upload") from None
        upload = Upload(**data)
        upload.received = (self._dir(upload_id) / "data").stat().st_size
        return upload

    def path(self, upload: Upload) -> Path:
        return self._dir(upload.id) / "data"

    def append(self, upload_id: str, offset: int, chunk: bytes) -> Upload:
        """Add bytes at `offset`, which must be exactly how much has arrived."""
        with self._lock:
            upload = self.get(upload_id)
            if upload.complete:
                raise UploadError(409, "This upload is already complete")
            if offset != upload.received:
                raise UploadError(409, f"Expected offset {upload.received}, not {offset}")
            if upload.received + len(chunk) > upload.size:
                raise UploadError(413, "More bytes than were declared")
            with self.path(upload).open("ab") as handle:
                handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            upload.received += len(chunk)
            if upload.received == upload.size:
                if file_sha256(self.path(upload)) != upload.sha256:
                    self.path(upload).write_bytes(b"")
                    upload.received = 0
                    self._save(upload)
                    raise UploadError(422, "The file's SHA-256 does not match; send it again")
                upload.complete = True
            self._save(upload)
            return upload

    def delete(self, upload_id: str) -> None:
        shutil.rmtree(self._dir(upload_id), ignore_errors=True)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()
