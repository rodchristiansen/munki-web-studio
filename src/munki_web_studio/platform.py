"""What a Munki repository looks like. The only platform-specific part of scanning."""

from typing import Any, cast

# Top-level folders that hold metadata, and the kind of object each file is.
KINDS = {"pkgsinfo": "pkginfo", "manifests": "manifest", "catalogs": "catalog"}

# Munki accepts pkginfo and manifest files with any name or extension.
SUFFIXES: frozenset[str] | None = None

IGNORED_NAMES = frozenset({".DS_Store", ".gitkeep", "Thumbs.db"})


# Manifest sections that name packages, and sections that name other manifests.
ITEM_SECTIONS = (
    "managed_installs",
    "managed_uninstalls",
    "managed_updates",
    "optional_installs",
    "featured_items",
    "default_installs",
)
INCLUDE_SECTIONS = ("included_manifests",)
# pkginfo keys that name other packages.
DEPENDENCY_KEYS = ("requires", "update_for")
# Where a pkginfo's payload lives, relative to the repository root.
PAYLOAD_FOLDER = "pkgs"


def summarize(kind: str, data: Any) -> dict[str, Any]:
    """The searchable fields of one parsed file, plus what it refers to."""
    if kind == "catalog":
        return {"items": len(cast(list[Any], data)) if isinstance(data, list) else 0}
    if not isinstance(data, dict):
        return {}
    mapping = cast(dict[str, Any], data)
    catalogs = mapping.get("catalogs")
    summary: dict[str, Any] = {
        "name": _text(mapping.get("name")),
        "version": _text(mapping.get("version")),
        "display_name": _text(mapping.get("display_name")),
        "category": _text(mapping.get("category")),
        "catalogs": _texts(catalogs),
        "refs": _references(kind, mapping),
    }
    if kind == "pkginfo":
        location = _text(mapping.get("installer_item_location"))
        # Munki treats a pkginfo without installer_type as an Apple package, or as
        # nopkg when it has no installer item at all.
        summary["installer_type"] = _text(mapping.get("installer_type")) or (
            "pkg" if location else "nopkg"
        )
        summary["payload"] = f"{PAYLOAD_FOLDER}/{location.lstrip('/')}" if location else None
    return summary


def _references(
    kind: str, mapping: dict[str, Any], condition: str | None = None
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if kind == "pkginfo":
        for key in DEPENDENCY_KEYS:
            refs += [
                {"field": key, "target": t, "condition": None} for t in _texts(mapping.get(key))
            ]
        return refs
    for key in (*INCLUDE_SECTIONS, *ITEM_SECTIONS):
        refs += [
            {"field": key, "target": t, "condition": condition} for t in _texts(mapping.get(key))
        ]
    items = mapping.get("conditional_items")
    if isinstance(items, list):
        for item in cast(list[Any], items):
            if isinstance(item, dict):
                entry = cast(dict[str, Any], item)
                refs += _references(kind, entry, _text(entry.get("condition")) or condition)
    return refs


def split_item(item: str) -> tuple[str, str | None]:
    """Munki manifest items may pin a version: `Name--1.0` or `Name-1.0`."""
    for separator in ("--", "-"):
        if separator in item:
            name, _, version = item.rpartition(separator)
            if name and version[:1].isdigit():
                return name, version
    return item, None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _texts(value: Any) -> list[str]:
    return (
        [v for v in cast(list[Any], value) if isinstance(v, str)] if isinstance(value, list) else []
    )
