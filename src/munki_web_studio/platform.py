"""What a Munki repository looks like. The only platform-specific part of scanning."""

from typing import Any, cast

from munki_web_studio import catalogs
from munki_web_studio.jobs.remote import RemoteSpec

# Top-level folders that hold metadata, and the kind of object each file is.
KINDS = {"pkgsinfo": "pkginfo", "manifests": "manifest", "catalogs": "catalog"}

# Munki accepts pkginfo and manifest files with any name or extension.
SUFFIXES: frozenset[str] | None = None

IGNORED_NAMES = frozenset({".DS_Store", ".gitkeep", "Thumbs.db"})


# Folders a Munki client downloads, which publishing copies into a release.
PUBLISHED_FOLDERS = ("catalogs", "manifests", "pkgs", "icons", "client_resources")

# Installer files an import accepts, and the format a new pkginfo is written in.
IMPORT_SUFFIXES = (".pkg", ".mpkg", ".dmg")
NEW_PKGINFO_SUFFIX = ".plist"

# Workers. A Mac worker runs these Munki tools and nothing else, from these paths.
WORKER_PLATFORM = "macOS"
WORKER_TOOLS = {
    "makecatalogs": "/usr/local/munki/makecatalogs",
    "makepkginfo": "/usr/local/munki/makepkginfo",
}
# How each tool prints its version.
TOOL_VERSION_ARGS: dict[str, tuple[str, ...]] = {
    "makecatalogs": ("--version",),
    "makepkginfo": ("--version",),
}
# Each capability a worker can offer, and the tools it needs.
CAPABILITIES = {
    "catalog.generate": ("makecatalogs",),
    "installer.inspect": ("makepkginfo",),
}
REMOTE_OPERATIONS = (
    RemoteSpec(
        name="catalog.generate",
        title="Generate catalogs",
        capability="catalog.generate",
        tool="makecatalogs",
        # Payloads are not sent to the worker, so their existence is not checked.
        args=("--skip-pkg-check", "{staging}"),
        inputs=("pkgsinfo",),
        outputs="catalogs",
        applies_to="catalogs",
        check=catalogs.check_output,
        arguments=frozenset({"acknowledge_live"}),
    ),
    RemoteSpec(
        name="package.import",
        title="Import a package",
        capability="installer.inspect",
        tool="makepkginfo",
        # makepkginfo reads the installer and prints a pkginfo; it installs nothing.
        args=("create", "{upload}"),
        inputs=(),
        outputs="out",
        upload_argument="upload",
        stdout_file="out/pkginfo.plist",
        creates_draft=True,
        arguments=frozenset({"upload", "subdirectory"}),
        max_attempts=1,
    ),
)


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


# pkginfo fields the editor may set, and the type each must have. Dotted names
# reach into nested dictionaries. Anything else is edited through raw changes.
PKGINFO_FIELDS: dict[str, str] = {
    "name": "string",
    "display_name": "string",
    "version": "string",
    "description": "text",
    "category": "string",
    "developer": "string",
    "catalogs": "string-list",
    "minimum_os_version": "string",
    "maximum_os_version": "string",
    "supported_architectures": "string-list",
    "unattended_install": "boolean",
    "unattended_uninstall": "boolean",
    "uninstallable": "boolean",
    "restart_action": "string",
    "blocking_applications": "string-list",
    "requires": "string-list",
    "update_for": "string-list",
    "installcheck_script": "text",
    "uninstallcheck_script": "text",
    "preinstall_script": "text",
    "postinstall_script": "text",
    "preuninstall_script": "text",
    "postuninstall_script": "text",
    "uninstall_script": "text",
}


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
                refs += _references(kind, entry, condition_text(entry) or condition)
    return refs


def condition_text(entry: dict[str, Any]) -> str | None:
    """The condition of one conditional item, as text."""
    return _text(entry.get("condition"))


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


def dump_new_pkginfo(data: Any) -> bytes:
    """The bytes of a new pkginfo file: an XML plist, as makepkginfo writes."""
    from munki_web_studio.codec import plistfile

    return plistfile.dump(data, binary=False)


def import_paths(name: str, version: str, filename: str, subdirectory: str) -> tuple[str, str, str]:
    """Where an imported package's pkginfo and installer go, and the
    installer_item_location that points from one to the other."""
    folder = f"{subdirectory}/" if subdirectory else ""
    location = f"{folder}{filename}"
    return f"pkgsinfo/{folder}{name}-{version}{NEW_PKGINFO_SUFFIX}", f"pkgs/{location}", location


def prepare_import(pkginfo: dict[str, Any], sha256: str, location: str) -> None:
    """Check that makepkginfo's pkginfo describes the uploaded file, then point
    it at the installer's place in the repository. Raises ValueError otherwise."""
    if pkginfo.get("installer_item_hash") != sha256:
        raise ValueError("the pkginfo's installer_item_hash is not the upload's SHA-256")
    pkginfo["installer_item_location"] = location
