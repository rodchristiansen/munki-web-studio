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


# What makecatalogs changes between a pkginfo and its catalog entry. Anything
# else in an entry must match the pkginfo exactly.
DROPPED_KEYS = frozenset({"notes", "_metadata"})
ADDED_KEYS: dict[str, type] = {"loop_fingerprint": str}


def _comparable(item: dict[str, Any], drop: frozenset[str]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k not in drop}


def check_output(folder: Path, sources: list[dict[str, Any]]) -> dict[str, int]:
    """Refuse generated catalogs that are not exactly what the pkginfo say.

    The worker is trusted to run makecatalogs, not to decide what clients
    install, so every entry is compared with the studio's own reading of the
    pkginfo: each must match a pkginfo field for field (allowing only the keys
    makecatalogs drops or adds), each catalog must hold exactly the pkginfo
    that list it, and no catalog may appear that no pkginfo names. Returns each
    catalog's item count."""
    expected: dict[str, list[dict[str, Any]]] = {ALL: []}
    for source in sources:
        comparable = _comparable(source, DROPPED_KEYS)
        expected[ALL].append(comparable)
        listed = source.get("catalogs", [])
        for name in cast(list[Any], listed) if isinstance(listed, list) else []:
            expected.setdefault(str(name), []).append(comparable)
    counts: dict[str, int] = {}
    for path in sorted(folder.iterdir()) if folder.is_dir() else []:
        if path.is_symlink() or not path.is_file():
            raise CatalogError(f"{path.name} is not a plain file")
        name = name_of(path.name)
        if name not in expected:
            raise CatalogError(f"{path.name} is not a catalog any pkginfo names")
        try:
            items = entries(path.read_bytes())
        except (ParseError, CatalogError) as error:
            raise CatalogError(f"{path.name} cannot be read: {error}") from None
        remaining = list(expected[name])
        for item in items:
            for added, kind in ADDED_KEYS.items():
                if added in item and not isinstance(item[added], kind):
                    raise CatalogError(f"{path.name}: {added} has the wrong type")
            body = _comparable(item, frozenset(ADDED_KEYS))
            match = next((i for i, want in enumerate(remaining) if want == body), None)
            if match is None:
                label = " ".join(key(item)).strip() or "an item"
                raise CatalogError(f"{path.name}: {label} does not match any pkginfo")
            remaining.pop(match)
        if remaining:
            missing = ", ".join(sorted({" ".join(key(i)) for i in remaining}))
            raise CatalogError(f"{path.name} is missing {missing}")
        counts[name] = len(items)
    absent = sorted(set(expected) - set(counts))
    if absent:
        raise CatalogError(f"No catalog was generated for {', '.join(absent)}")
    return counts
