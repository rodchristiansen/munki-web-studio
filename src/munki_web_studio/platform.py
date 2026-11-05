"""What a Munki repository looks like. The only platform-specific part of scanning."""

from typing import Any, cast

# Top-level folders that hold metadata, and the kind of object each file is.
KINDS = {"pkgsinfo": "pkginfo", "manifests": "manifest", "catalogs": "catalog"}

# Munki accepts pkginfo and manifest files with any name or extension.
SUFFIXES: frozenset[str] | None = None

IGNORED_NAMES = frozenset({".DS_Store", ".gitkeep", "Thumbs.db"})


def summarize(kind: str, data: Any) -> dict[str, Any]:
    """The searchable fields of one parsed file."""
    if kind == "catalog":
        return {"items": len(cast(list[Any], data)) if isinstance(data, list) else 0}
    if not isinstance(data, dict):
        return {}
    mapping = cast(dict[str, Any], data)
    catalogs = mapping.get("catalogs")
    return {
        "name": _text(mapping.get("name")),
        "version": _text(mapping.get("version")),
        "display_name": _text(mapping.get("display_name")),
        "category": _text(mapping.get("category")),
        "catalogs": [c for c in cast(list[Any], catalogs) if isinstance(c, str)]
        if isinstance(catalogs, list)
        else [],
    }


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None
