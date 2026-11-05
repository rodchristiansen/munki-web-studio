# Benchmark

Run from the repository root. It generates a synthetic repository of 10,000
pkginfo and 2,000 manifests, then times a cold scan, a warm rescan, and five
searches through the API in-process:

```sh
uv run python tests/bench/benchmark.py
```

## Results

Reference machine: Mac16,11, Apple M4 Pro, 48 GB, macOS 27.2. Run on 2026-11-05.

| Measure | Result | Target |
| --- | --- | --- |
| Files scanned | 12,000 | |
| Cold scan | 282.5 s | |
| Warm rescan, nothing changed | 0.59 s | |
| Warm search, all packages | 69 ms | under 300 ms |
| Warm search, one name | 68 ms | under 300 ms |
| Warm search, a category | 100 ms | under 300 ms |
| Warm search, no matches | 68 ms | under 300 ms |

The cold scan on this machine is dominated by the operating system opening each
file (about 23 ms per file in the temporary folder, where the Mac's security
software inspects new files); the scanner's own work is about 0.4 ms per file.
A cold scan only happens the first time a repository is indexed, or after the
index format changes. Rescans after that read only files whose size or time
changed.
