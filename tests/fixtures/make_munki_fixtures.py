"""Generate the synthetic Munki fixtures.

The plistlib-written files match what makepkginfo and munkiimport produce. The
hand-formatted file mimics a pkginfo edited in a text editor or another tool.
All values are invented.
"""

import datetime
import pathlib
import plistlib

here = pathlib.Path(__file__).parent

pkginfo = {
    "name": "ExampleEditor",
    "display_name": "Example Editor",
    "version": "2.10",
    "catalogs": ["testing", "production"],
    "category": "Productivity",
    "description": "An invented editor used only as a test fixture.",
    "installer_item_location": "apps/ExampleEditor-2.10.dmg",
    "installer_item_hash": "0" * 64,
    "installer_item_size": 184320,
    "installed_size": 9007199254740993,
    "installer_type": "copy_from_dmg",
    "items_to_copy": [
        {"source_item": "Example Editor.app", "destination_path": "/Applications"}
    ],
    "minimum_os_version": "13.0",
    "unattended_install": True,
    "uninstallable": False,
    "_metadata": {
        "created_by": "fixture",
        "creation_date": datetime.datetime(2026, 10, 20, 9, 30, 0),
    },
    "postinstall_script": '#!/bin/sh\necho "done & <ok>"\nexit 0\n',
    "icon_data": b"\x89PNG\r\n\x1a\n\x00\x00",
    "x_unknown_site_key": {"nested": [1, 2.5, "three"]},
}

(here / "pkginfo-plistlib.plist").write_bytes(plistlib.dumps(pkginfo, sort_keys=False))
(here / "pkginfo-binary.plist").write_bytes(
    plistlib.dumps(pkginfo, fmt=plistlib.FMT_BINARY, sort_keys=False)
)

manifest = {
    "catalogs": ["testing", "production"],
    "included_manifests": ["site/base", "site/browsers"],
    "managed_installs": ["ExampleEditor", "ExampleViewer"],
    "conditional_items": [
        {
            "condition": 'machine_type == "laptop" AND os_vers_major >= 14',
            "managed_installs": ["ExampleVPN"],
        }
    ],
}
(here / "manifest-plistlib.plist").write_bytes(
    plistlib.dumps(manifest, sort_keys=False)
)
