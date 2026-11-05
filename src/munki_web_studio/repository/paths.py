"""Keep every repository path inside its root, including through symlinks."""

from pathlib import Path, PurePosixPath


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
