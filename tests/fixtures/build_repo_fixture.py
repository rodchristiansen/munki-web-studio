"""Write the synthetic Munki repository fixture in tests/fixtures/repo.

Every value is invented. Run it again after changing it; the output is
deterministic and committed. Catalogs are generated separately with the real
`makecatalogs` (see README.md) so they show genuine tool output.
"""

import datetime
import plistlib
import shutil
from pathlib import Path
from typing import Any

REPO = Path(__file__).parent / "repo"

EXAMPLE_CLI_YAML = """\
# Munki YAML pkginfo, invented values.
name: ExampleCLI
display_name: Example CLI
version: "3.0"
catalogs:
  - testing
category: Utilities
installer_item_location: apps/ExampleCLI-3.0.pkg
receipts:
  - packageid: com.example.cli
    version: "3.0"
minimum_os_version: "13.0"
unattended_install: true
installcheck_script: |
  #!/bin/zsh
  # exit 0 means install is needed
  [[ -x /usr/local/bin/example ]] && exit 1
  exit 0
x_unknown_site_key: keep-me
"""
CREATED = datetime.datetime(2026, 10, 20, 9, 30, 0)


def pkginfo(
    name: str,
    version: str,
    category: str,
    *,
    catalogs: tuple[str, ...] = ("testing",),
    installer_type: str = "copy_from_dmg",
    **extra: Any,
) -> dict[str, Any]:
    ext = "pkg" if installer_type == "pkg" else "dmg"
    item: dict[str, Any] = {
        "name": name,
        "display_name": name.replace("Example", "Example "),
        "version": version,
        "catalogs": list(catalogs),
        "category": category,
        "developer": "Example Co",
        "description": f"Invented {name} package for tests.",
        "installer_item_location": f"apps/{name}-{version}.{ext}",
        "installer_item_hash": "0" * 64,
        "installer_item_size": 1024,
        "minimum_os_version": "13.0",
        "unattended_install": True,
        "_metadata": {"created_by": "fixture", "creation_date": CREATED},
    }
    if installer_type == "pkg":
        item["receipts"] = [
            {
                "packageid": f"com.example.{name.lower()}",
                "version": version,
                "optional": False,
            }
        ]
    else:
        item["installer_type"] = "copy_from_dmg"
        item["items_to_copy"] = [
            {"source_item": f"{name}.app", "destination_path": "/Applications"}
        ]
        item["installs"] = [
            {
                "type": "application",
                "path": f"/Applications/{name}.app",
                "CFBundleShortVersionString": version,
            }
        ]
    item.update(extra)
    return item


def write_plist(path: Path, data: dict[str, Any], *, binary: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = plistlib.FMT_BINARY if binary else plistlib.FMT_XML
    path.write_bytes(plistlib.dumps(data, fmt=fmt, sort_keys=False))


def manifest(**sections: Any) -> dict[str, Any]:
    return dict(sections)


def build() -> None:
    if REPO.exists():
        shutil.rmtree(REPO)
    p = REPO / "pkgsinfo"

    # Browsers: two versions of one name, one in production.
    write_plist(
        p / "apps/browsers/ExampleBrowser-135.0.plist",
        pkginfo("ExampleBrowser", "135.0", "Browsers", catalogs=("testing", "production")),
    )
    write_plist(
        p / "apps/browsers/ExampleBrowser-136.0.plist",
        pkginfo("ExampleBrowser", "136.0", "Browsers"),
    )
    # The same name and version in two files is valid input and must stay visible.
    write_plist(
        p / "apps/browsers/duplicates/ExampleBrowser-136.0.plist",
        pkginfo("ExampleBrowser", "136.0", "Browsers", notes="Duplicate on purpose."),
    )

    # Creative: scripts, dependencies, unknown keys.
    write_plist(
        p / "apps/creative/ExampleEditor-2.10.plist",
        pkginfo(
            "ExampleEditor",
            "2.10",
            "Creative",
            catalogs=("testing", "production"),
            requires=["ExampleRuntime"],
            postinstall_script='#!/bin/sh\necho "done & <ok>"\nexit 0\n',
            installed_size=9007199254740993,
            x_unknown_site_key={"nested": [1, 2.5, "three"]},
        ),
    )
    write_plist(
        p / "apps/creative/ExampleEditorPlugin-1.0.plist",
        pkginfo(
            "ExampleEditorPlugin",
            "1.0",
            "Creative",
            installer_type="pkg",
            update_for=["ExampleEditor"],
        ),
    )
    write_plist(
        p / "apps/creative/ExampleViewer-1.0.plist",
        pkginfo(
            "ExampleViewer",
            "1.0",
            "Creative",
            catalogs=("testing", "production"),
            icon_data=b"\x89PNG\r\n\x1a\n\x00\x00",
        ),
        binary=True,
    )

    # Tools: a pkg, a runtime dependency, and one pkginfo stored as YAML.
    write_plist(
        p / "tools/ExampleRuntime-5.0.plist",
        pkginfo(
            "ExampleRuntime",
            "5.0",
            "Utilities",
            installer_type="pkg",
            catalogs=("testing", "production"),
        ),
    )
    cli = REPO / "pkgsinfo/tools/ExampleCLI-3.0.yaml"
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text(EXAMPLE_CLI_YAML)

    # Invalid inputs: malformed XML and a plist whose root is not a dict.
    (p / "invalid").mkdir(parents=True, exist_ok=True)
    (p / "invalid/Broken-1.0.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0">\n<dict>\n'
        "    <key>name</key>\n    <string>Broken\n</dict>\n</plist>\n"
    )
    write_plist(p / "invalid/NotADict-1.0.plist", ["not", "a", "dict"])  # type: ignore[arg-type]

    m = REPO / "manifests"
    write_plist(
        m / "site_default",
        manifest(
            catalogs=["production"],
            managed_installs=["ExampleBrowser", "ExampleRuntime"],
        ),
    )
    write_plist(
        m / "groups/design-lab",
        manifest(
            catalogs=["testing", "production"],
            included_manifests=["site_default"],
            managed_installs=["ExampleEditor", "ExampleViewer"],
            optional_installs=["ExampleCLI"],
            managed_uninstalls=["ExampleLegacy"],
            conditional_items=[
                {
                    "condition": 'machine_type == "laptop" AND os_vers_major >= 14',
                    "managed_installs": ["ExampleEditorPlugin"],
                }
            ],
        ),
    )
    # Missing reference: nothing in pkgsinfo is named ExampleMissing.
    write_plist(
        m / "groups/broken-refs",
        manifest(
            catalogs=["testing"],
            included_manifests=["groups/does-not-exist"],
            managed_installs=["ExampleMissing"],
        ),
    )
    # Include cycle: loop-a includes loop-b, which includes loop-a.
    write_plist(
        m / "groups/loop-a",
        manifest(catalogs=["testing"], included_manifests=["groups/loop-b"]),
    )
    write_plist(
        m / "groups/loop-b",
        manifest(catalogs=["testing"], included_manifests=["groups/loop-a"]),
    )
    write_plist(
        m / "computers/EXAMPLE-0001",
        manifest(
            catalogs=["testing", "production"],
            included_manifests=["groups/design-lab"],
        ),
    )

    for folder in ("pkgs", "icons", "catalogs"):
        (REPO / folder).mkdir(parents=True, exist_ok=True)
        (REPO / folder / ".gitkeep").write_text("")


if __name__ == "__main__":
    build()
