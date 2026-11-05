"""Generate a large synthetic Munki repository for benchmarks.

Deterministic from the seed: the same arguments always produce the same bytes.
All names are invented.

    python3 -I tests/fixtures/generate_large_repo.py OUT_DIR --pkginfo 10000 --manifests 2000
"""

import argparse
import datetime
import plistlib
import random
from pathlib import Path

CATEGORIES = ("Browsers", "Creative", "Media", "Utilities", "Science", "Education", "Developer")
CREATED = datetime.datetime(2026, 1, 1, 0, 0, 0)


def generate(out: Path, *, pkginfo: int, manifests: int, seed: int = 1) -> None:
    rng = random.Random(seed)
    names = [f"ExamplePackage{i:05d}" for i in range(max(1, pkginfo // 4))]
    for i in range(pkginfo):
        name = names[i % len(names)]
        version = f"{1 + i // len(names)}.{rng.randrange(10)}.{rng.randrange(100)}"
        category = CATEGORIES[rng.randrange(len(CATEGORIES))]
        item = {
            "name": name,
            "version": version,
            "catalogs": ["testing", "production"] if rng.random() < 0.6 else ["testing"],
            "category": category,
            "description": f"Invented package {i}.",
            "installer_item_location": f"apps/{category.lower()}/{name}-{version}.dmg",
            "installer_item_hash": f"{rng.getrandbits(256):064x}",
            "installer_item_size": rng.randrange(1, 4_000_000),
            "minimum_os_version": "13.0",
            "_metadata": {"creation_date": CREATED},
        }
        path = out / "pkgsinfo" / category.lower() / f"{name}-{version}-{i}.plist"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps(item, sort_keys=False))

    groups = max(1, manifests // 20)
    for i in range(manifests):
        is_group = i < groups
        body: dict[str, object] = {
            "catalogs": ["testing", "production"],
            "managed_installs": rng.sample(names, k=min(len(names), rng.randrange(1, 15))),
        }
        if not is_group:
            body["included_manifests"] = [f"groups/group-{rng.randrange(groups):04d}"]
        path = (
            out
            / "manifests"
            / ("groups" if is_group else "computers")
            / (f"group-{i:04d}" if is_group else f"EXAMPLE-{i:05d}")
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps(body, sort_keys=False))

    for folder in ("catalogs", "pkgs", "icons"):
        (out / folder).mkdir(parents=True, exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a large synthetic Munki repository.")
    parser.add_argument("out", type=Path)
    parser.add_argument("--pkginfo", type=int, default=10_000)
    parser.add_argument("--manifests", type=int, default=2_000)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    generate(args.out, pkginfo=args.pkginfo, manifests=args.manifests, seed=args.seed)


if __name__ == "__main__":
    main()
