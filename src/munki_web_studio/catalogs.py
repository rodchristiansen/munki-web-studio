"""Munki catalogs: how they are named and read, and the checks a generated set
must pass before it replaces the repository's catalogs."""

import plistlib
from pathlib import Path
from typing import Any, cast
from xml.parsers.expat import ExpatError

from munki_web_studio.codec import load

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


def canonical(raw: bytes) -> list[dict[str, Any]]:
    """A generated catalog's items, but only if the file is exactly what plistlib
    writes for them, as makecatalogs output is. That refuses YAML, binary plists,
    duplicate keys, comments and anything else a client might read differently."""
    try:
        data = plistlib.loads(raw, fmt=plistlib.FMT_XML)
    except (plistlib.InvalidFileException, ExpatError, ValueError, TypeError) as error:
        raise CatalogError(f"not an XML plist: {error}") from None
    if plistlib.dumps(data, fmt=plistlib.FMT_XML) != raw:
        raise CatalogError("not in the form makecatalogs writes")
    if not isinstance(data, list) or not all(isinstance(i, dict) for i in cast(list[Any], data)):
        raise CatalogError("A catalog must be a list of pkginfo dictionaries")
    return cast(list[dict[str, Any]], data)


def same(a: Any, b: Any) -> bool:
    """Equal values of the same types all the way down, so True never stands in
    for 1 or 1.0 for 1."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        x, y = cast(dict[Any, Any], a), cast(dict[Any, Any], b)
        return x.keys() == y.keys() and all(same(x[k], y[k]) for k in x)
    if isinstance(a, list):
        x, y = cast(list[Any], a), cast(list[Any], b)
        return len(x) == len(y) and all(same(i, j) for i, j in zip(x, y, strict=True))
    return bool(a == b)


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
            items = canonical(path.read_bytes())
        except CatalogError as error:
            raise CatalogError(f"{path.name} cannot be read: {error}") from None
        remaining = list(expected[name])
        for item in items:
            for added, kind in ADDED_KEYS.items():
                if added in item and not isinstance(item[added], kind):
                    raise CatalogError(f"{path.name}: {added} has the wrong type")
            body = _comparable(item, frozenset(ADDED_KEYS))
            match = next((i for i, want in enumerate(remaining) if same(want, body)), None)
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
