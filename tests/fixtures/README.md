# Test fixtures

Every value here is invented. Never add data copied from a real repository.

## `repo/`

A small Munki repository with the real layout (`pkgsinfo`, `manifests`,
`catalogs`, `pkgs`, `icons`). It covers the cases later slices test:

| Case | Where |
| --- | --- |
| Nested pkginfo directories | `pkgsinfo/apps/*`, `pkgsinfo/tools` |
| Several versions of one name | `ExampleBrowser` 135.0 and 136.0 |
| Same name and version in two files | `apps/browsers` and `apps/browsers/duplicates` |
| XML, binary, and YAML pkginfo | `ExampleEditor`, `ExampleViewer` (binary), `ExampleCLI` (YAML) |
| Scripts, dependencies, unknown keys, large integers | `ExampleEditor`, `ExampleEditorPlugin` |
| Invalid files | `pkgsinfo/invalid` |
| Nested manifests, includes, conditions | `manifests/groups`, `manifests/computers` |
| Missing references | `manifests/groups/broken-refs` |
| Include cycle | `manifests/groups/loop-a` and `loop-b` |

`build_repo_fixture.py` writes everything except the catalogs. The catalogs
are real `makecatalogs` output, so later slices can compare against the tool.
Rebuild both from the repository root:

```sh
python3 -I tests/fixtures/build_repo_fixture.py && /usr/local/munki/makecatalogs -s tests/fixtures/repo
```

## `codec/`

The single files from the codec spike, used by codec round-trip tests.
`codec/make_munki_fixtures.py` regenerates the plists.

## Large repository

`generate_large_repo.py` writes a repository of any size for benchmarks. It is
deterministic for a given seed. The default is 10,000 pkginfo and 2,000
manifests:

```sh
python3 -I tests/fixtures/generate_large_repo.py /tmp/large-munki-repo
```
