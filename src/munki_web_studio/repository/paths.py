"""Keep every repository path inside its root, including through symlinks."""

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

# A reparse tag with this bit set names another path: symlinks and junctions.
# Cloud-file and dedup reparse points do not set it and are ordinary files.
_NAME_SURROGATE = 0x20000000


def is_link(path: Path) -> bool:
    """True for a symlink, or on Windows a junction or other name-surrogate reparse point."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_reparse_tag", 0) & _NAME_SURROGATE)


class PathEscapeError(ValueError):
    """A path pointed outside the repository root."""


def normalize(relative: str) -> str:
    """Canonical form of a repository-relative path, or PathEscapeError."""
    if not relative or "\x00" in relative or "\\" in relative:
        raise PathEscapeError(f"Invalid path {relative!r}")
    pure = PurePosixPath(relative)
    raw_parts = relative.split("/")
    if pure.is_absolute() or any(part in ("..", ".", "") for part in raw_parts):
        raise PathEscapeError(f"Path {relative!r} leaves the repository")
    return pure.as_posix()


def contained(root: Path, relative: str) -> Path:
    """Resolve a relative path under root. Symlinks may not lead outside it."""
    real_root = root.resolve(strict=True)
    target = (real_root / normalize(relative)).resolve(strict=False)
    if not target.is_relative_to(real_root):
        raise PathEscapeError(f"Path {relative!r} leaves the repository")
    return target


@dataclass(frozen=True)
class FileRead:
    """A file read from inside the repository. `raw` is None when it was too large."""

    raw: bytes | None
    size: int
    mtime_ns: int


def _descriptor_path(fd: int) -> Path | None:
    """The real path of an open file, where the platform can tell us."""
    get_path: int | None = getattr(fcntl, "F_GETPATH", None) if fcntl is not None else None
    if fcntl is not None and get_path is not None:  # macOS
        buffer = fcntl.fcntl(fd, get_path, b"\0" * 1024)
        return Path(os.fsdecode(buffer.rstrip(b"\0")))
    proc = Path(f"/proc/self/fd/{fd}")
    if proc.exists():  # Linux
        return Path(os.readlink(proc))
    if sys.platform == "win32":
        return _handle_path(fd)
    return None


if sys.platform == "win32":

    def _handle_path(fd: int) -> Path:
        """The final path of an open file on Windows, after every link and junction."""
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        final_path = kernel32.GetFinalPathNameByHandleW
        final_path.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
        final_path.restype = wintypes.DWORD
        handle = msvcrt.get_osfhandle(fd)
        size = 1024
        while True:
            buffer = ctypes.create_unicode_buffer(size)
            length = final_path(handle, buffer, size, 0)
            if length == 0:
                raise OSError(ctypes.get_last_error(), "GetFinalPathNameByHandleW failed")
            if length < size:
                break
            size = length + 1
        path = buffer.value
        if path.startswith("\\\\?\\UNC\\"):
            path = "\\\\" + path[len("\\\\?\\UNC\\") :]
        elif path.startswith("\\\\?\\"):
            path = path[len("\\\\?\\") :]
        return Path(path)


def read_contained(root: Path, relative: str, limit: int) -> FileRead:
    """Read a regular file under root without following a swapped-in symlink.

    The path is resolved and checked, then opened with O_NOFOLLOW, and the open
    descriptor's real path is checked again, so replacing a file or directory
    with a link between the check and the read cannot redirect it. At most
    `limit` + 1 bytes are read.

    Windows has no O_NOFOLLOW, so the open follows links and junctions; the
    handle's final path (GetFinalPathNameByHandleW) is then checked against
    the root, which refuses a link swapped in after the first check.
    """
    real_root = root.resolve(strict=True)
    target = contained(real_root, relative)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(target, flags)
    except OSError as error:
        raise PathEscapeError(f"Cannot open {relative!r}: {error.strerror}") from error
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise PathEscapeError(f"{relative!r} is not a regular file")
        opened = _descriptor_path(fd)
        if opened is not None and not opened.resolve().is_relative_to(real_root):
            raise PathEscapeError(f"Path {relative!r} leaves the repository")
        if info.st_size > limit:
            return FileRead(raw=None, size=info.st_size, mtime_ns=info.st_mtime_ns)
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(limit + 1)
        if len(raw) > limit:
            return FileRead(raw=None, size=len(raw), mtime_ns=info.st_mtime_ns)
        return FileRead(raw=raw, size=info.st_size, mtime_ns=info.st_mtime_ns)
    finally:
        os.close(fd)
