"""Benchmark scanning and search on a large generated repository.

    uv run python tests/bench/benchmark.py [--pkginfo 10000] [--manifests 2000]

Prints cold scan, warm rescan, and warm search times. Search is timed through
the API in-process, so it measures server time without the network.
"""

import argparse
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.fixtures.generate_large_repo import generate

from munki_web_studio.app import create_app
from munki_web_studio.repository.config import Repository, Settings
from munki_web_studio.repository.index import Index
from munki_web_studio.repository.scanner import scan

QUERIES = ["", "ExamplePackage00042", "Creative", "1.5", "nothing-matches-this"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark scanning and search.")
    parser.add_argument("--pkginfo", type=int, default=10_000)
    parser.add_argument("--manifests", type=int, default=2_000)
    parser.add_argument("--keep", type=Path, help="Generate into this folder and keep it.")
    args = parser.parse_args()

    scratch = Path(tempfile.mkdtemp())
    root = (args.keep or scratch / "repo").resolve()
    try:
        if not root.exists():
            generate(root, pkginfo=args.pkginfo, manifests=args.manifests)
        repo = Repository(id="bench", name="Benchmark", root=root)
        index = Index(scratch / "state" / "index.sqlite")

        started = time.perf_counter()
        cold = scan(repo, index)
        cold_s = time.perf_counter() - started
        started = time.perf_counter()
        scan(repo, index)
        warm_s = time.perf_counter() - started
        index.close()

        settings = Settings(repositories=(repo,), state_dir=scratch / "state", rescan_seconds=0)
        with TestClient(
            create_app(settings, static_dir=scratch / "none"), base_url="http://localhost"
        ) as client:
            timings: dict[str, list[float]] = {q: [] for q in QUERIES}
            for _ in range(5):
                for q in QUERIES:
                    started = time.perf_counter()
                    response = client.get(
                        "/api/repositories/bench/packages", params={"q": q, "limit": 100}
                    )
                    timings[q].append((time.perf_counter() - started) * 1000)
                    response.raise_for_status()

        print(f"Files: {cold.files}")
        print(f"Cold scan: {cold_s:.1f} s")
        print(f"Warm rescan: {warm_s:.2f} s")
        for q, times in timings.items():
            print(f"Search {q or '(all)'!r}: median {statistics.median(times[1:]):.0f} ms")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
