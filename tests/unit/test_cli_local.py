import json
import shutil
from pathlib import Path

import pytest
from tests.fixtures.build_repo_fixture import REPO
from typer.testing import CliRunner

from munki_web_studio import tagged
from munki_web_studio.cli import cli

runner = CliRunner()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(REPO, root)
    return root


def run(*args: str) -> tuple[int, str, str]:
    result = runner.invoke(cli, list(args))
    return result.exit_code, result.stdout, result.stderr


def test_search_finds_packages_and_manifests(repo: Path) -> None:
    code, out, _ = run("search", "editor", "--repo", str(repo))
    assert code == 0
    assert "pkgsinfo/apps/creative/ExampleEditor-2.10.plist\tExampleEditor 2.10" in out
    code, out, _ = run("search", "design", "--repo", str(repo), "--kind", "manifest", "--json")
    assert [r["path"] for r in tagged.decode(json.loads(out))] == ["manifests/groups/design-lab"]


def test_show_prints_contents_and_rejects_escapes(repo: Path) -> None:
    code, out, _ = run(
        "show", "pkgsinfo/apps/creative/ExampleEditor-2.10.plist", "--repo", str(repo), "--json"
    )
    assert code == 0
    shown = tagged.decode(json.loads(out))
    assert shown["data"]["installed_size"] == 9007199254740993
    assert run("show", "../outside", "--repo", str(repo))[0] == 2
    code, _, err = run("show", "pkgsinfo/invalid/Broken-1.0.plist", "--repo", str(repo))
    assert code == 1 and "line" in err


def test_where_used(repo: Path) -> None:
    code, out, _ = run("where-used", "ExampleEditorPlugin", "--repo", str(repo))
    assert code == 0
    assert 'manifests/groups/design-lab\tmanaged_installs  when machine_type == "laptop"' in out


def test_validate_fails_on_errors_and_passes_clean_repos(repo: Path, tmp_path: Path) -> None:
    code, out, err = run("validate", "--repo", str(repo))
    assert code == 1
    assert "include-cycle" in out and "errors" in err
    report = tagged.decode(json.loads(run("validate", "--repo", str(repo), "--json")[1]))
    assert report["errors"] > 0
    clean = tmp_path / "clean"
    (clean / "manifests").mkdir(parents=True)
    shutil.copy(repo / "manifests/site_default", clean / "manifests/site_default")
    (clean / "pkgsinfo").mkdir()
    shutil.copy(
        repo / "pkgsinfo/tools/ExampleRuntime-5.0.plist",
        clean / "pkgsinfo/ExampleRuntime-5.0.plist",
    )
    shutil.copy(
        repo / "pkgsinfo/apps/browsers/ExampleBrowser-135.0.plist",
        clean / "pkgsinfo/ExampleBrowser-135.0.plist",
    )
    assert run("validate", "--repo", str(clean))[0] == 0
    assert run("validate", "--repo", str(clean), "--strict")[0] == 1


def test_index_file_is_reused(repo: Path, tmp_path: Path) -> None:
    index = tmp_path / "keep.sqlite"
    assert run("search", "x", "--repo", str(repo), "--index", str(index))[0] == 0
    assert index.is_file()
    assert run("search", "x", "--repo", str(repo), "--index", str(index))[0] == 0


def test_refuses_folders_that_are_not_repositories(tmp_path: Path) -> None:
    code, _, err = run("validate", "--repo", str(tmp_path))
    assert code == 2 and "repository" in err
