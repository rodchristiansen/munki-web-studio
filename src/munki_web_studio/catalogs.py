"""Munki catalogs: how they are named and read, and the checks a generated set
must pass before it replaces the repository's catalogs."""

from pathlib import Path
from typing import Any, cast

from munki_web_studio.codec import ParseError, load

FOLDER = "catalogs"
ALL = "all"


class CatalogError(ValueError):
    pass


def name_of(path: str) -> str:
    """The catalog a file holds: Munki catalogs are named by their file name."""
    return path.rsplit("/", 1)[-1]


def file_for(name: str) -> str:
    return f"{FOLDER}/{name}"


def entries(raw: bytes) -> list[dict[str, Any]]:
    """A catalog's items. Each is a pkginfo dictionary."""
    data = load(raw).data
    if not isinstance(data, list):
        raise CatalogError("A catalog must be a list of pkginfo dictionaries")
    items = cast(list[Any], data)
    if not all(isinstance(i, dict) for i in items):
        raise CatalogError("Every catalog item must be a dictionary")
    return cast(list[dict[str, Any]], items)


def key(item: dict[str, Any]) -> tuple[str, str]:
    return str(item.get("name", "")), str(item.get("version", ""))


def check_output(folder: Path, pkginfo_count: int) -> dict[str, int]:
    """Read every generated catalog and refuse a set that is not plausible: no
    `all` catalog, an unreadable file, an item with no name, or more items in
    `all` than there are pkginfo files. Returns each catalog's item count."""
    counts: dict[str, int] = {}
    for path in sorted(folder.iterdir()) if folder.is_dir() else []:
        if path.is_symlink() or not path.is_file():
            raise CatalogError(f"{path.name} is not a plain file")
        try:
            items = entries(path.read_bytes())
        except (ParseError, CatalogError) as error:
            raise CatalogError(f"{path.name} cannot be read: {error}") from None
        if any(not key(i)[0] for i in items):
            raise CatalogError(f"{path.name} has an item with no name")
        counts[name_of(path.name)] = len(items)
    if ALL not in counts:
        raise CatalogError(f"No {ALL!r} catalog was generated")
    if counts[ALL] > pkginfo_count:
        raise CatalogError(
            f"The {ALL!r} catalog lists {counts[ALL]} items but there are {pkginfo_count} pkginfo"
        )
    return counts
