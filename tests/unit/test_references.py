import shutil
from pathlib import Path

import pytest
from tests.fixtures.build_repo_fixture import REPO

from munki_web_studio.repository.config import Repository
from munki_web_studio.repository.index import Index
from munki_web_studio.repository.references import graph_for, validate
from munki_web_studio.repository.scanner import scan


@pytest.fixture
def scanned(tmp_path: Path) -> tuple[Repository, Index]:
    root = tmp_path / "repo"
    shutil.copytree(REPO, root)
    repo = Repository(id="fixture", name="Fixture", root=root.resolve())
    index = Index(tmp_path / "index.sqlite")
    scan(repo, index)
    return repo, index


def codes(issues: list, code: str) -> set[tuple[str, str | None]]:  # type: ignore[type-arg]
    return {(i.path, i.target) for i in issues if i.code == code}


def test_where_used_finds_manifests_and_dependents(scanned: tuple[Repository, Index]) -> None:
    repo, index = scanned
    graph = graph_for(index, repo)
    used = {(r.source, r.field) for r in graph.where_used("ExampleEditor")}
    assert ("manifests/groups/design-lab", "managed_installs") in used
    assert ("pkgsinfo/apps/creative/ExampleEditorPlugin-1.0.plist", "update_for") in used
    plugin = graph.where_used("ExampleEditorPlugin")
    assert plugin[0].condition == 'machine_type == "laptop" AND os_vers_major >= 14'
    includes = {r.source for r in graph.where_used("site_default")}
    assert "manifests/groups/design-lab" in includes


def test_versioned_items_resolve(scanned: tuple[Repository, Index]) -> None:
    repo, index = scanned
    graph = graph_for(index, repo)
    assert graph.resolve_package("ExampleBrowser--136.0") == "ExampleBrowser"
    assert graph.resolve_package("ExampleBrowser-135.0") == "ExampleBrowser"
    assert graph.resolve_package("ExampleBrowser--9.9") is None
    assert graph.resolve_package("ExampleMissing") is None


def test_validation_reports_every_fixture_problem(scanned: tuple[Repository, Index]) -> None:
    repo, index = scanned
    issues = validate(index, repo)
    assert codes(issues, "parse-error") >= {
        ("pkgsinfo/invalid/Broken-1.0.plist", None),
        ("pkgsinfo/invalid/NotADict-1.0.plist", None),
    }
    assert ("manifests/groups/broken-refs", "groups/does-not-exist") in codes(
        issues, "missing-manifest"
    )
    missing = codes(issues, "missing-package")
    assert ("manifests/groups/broken-refs", "ExampleMissing") in missing
    assert ("manifests/groups/design-lab", "ExampleLegacy") in missing
    cycles = [i for i in issues if i.code == "include-cycle"]
    assert (
        len(cycles) == 1
        and "groups/loop-a" in cycles[0].message
        and "groups/loop-b" in cycles[0].message
    )
    payloads = codes(issues, "missing-payload")
    assert (
        "pkgsinfo/apps/creative/ExampleEditor-2.10.plist",
        "pkgs/apps/ExampleEditor-2.10.dmg",
    ) in payloads
    assert all(i.severity == "warning" for i in issues if i.code == "missing-payload")


def test_present_payloads_are_not_reported(scanned: tuple[Repository, Index]) -> None:
    repo, index = scanned
    payload = repo.root / "pkgs/apps/ExampleEditor-2.10.dmg"
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(b"")
    paths = {p for p, _ in codes(validate(index, repo), "missing-payload")}
    assert "pkgsinfo/apps/creative/ExampleEditor-2.10.plist" not in paths


def test_payload_paths_cannot_escape(scanned: tuple[Repository, Index], tmp_path: Path) -> None:
    repo, index = scanned
    (tmp_path / "secret").write_bytes(b"")
    target = repo.root / "pkgsinfo/tools/Escape-1.0.plist"
    target.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>'
        "<key>name</key><string>Escape</string>"
        "<key>installer_item_location</key><string>../../secret</string></dict></plist>\n"
    )
    scan(repo, index)
    assert ("pkgsinfo/tools/Escape-1.0.plist", "pkgs/../../secret") in codes(
        validate(index, repo), "missing-payload"
    )


def test_every_fixture_condition_reads_as_a_rule(scanned: tuple[Repository, Index]) -> None:
    from munki_web_studio import conditions
    from munki_web_studio.repository.references import object_conditions

    repo, index = scanned
    graph = graph_for(index, repo)
    texts = [
        t for obj in graph.objects if obj.status == "ok" for t in object_conditions(graph, obj)
    ]
    assert texts
    assert all(conditions.analyze(t).rule for t in texts)
    assert not codes(validate(index, repo), "condition-syntax")


def test_broken_and_custom_conditions_are_reported(scanned: tuple[Repository, Index]) -> None:
    import plistlib

    repo, index = scanned
    manifest = {
        "catalogs": ["testing"],
        "conditional_items": [
            {"condition": "arch == ", "managed_installs": ["ExampleEditor"]},
            {"condition": 'site_room == "201"', "managed_installs": ["ExampleViewer"]},
        ],
    }
    (repo.root / "manifests/groups/conditions").write_bytes(plistlib.dumps(manifest))
    scan(repo, index)
    issues = validate(index, repo)
    assert ("manifests/groups/conditions", "arch == ") in codes(issues, "condition-syntax")
    assert ("manifests/groups/conditions", "site_room") in codes(issues, "unknown-fact")
