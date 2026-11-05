import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from tests.fixtures.build_repo_fixture import REPO

from munki_web_studio.repository.config import ConfigError, Repository, load_settings
from munki_web_studio.repository.index import Index
from munki_web_studio.repository.paths import PathEscapeError, contained, normalize
from munki_web_studio.repository.scanner import scan


def _can_symlink() -> bool:
    """Windows creates symlinks only with Developer Mode or as an administrator."""
    probe = Path(tempfile.mkdtemp())
    try:
        (probe / "link").symlink_to(probe / "target")
    except OSError:
        return False
    finally:
        shutil.rmtree(probe)
    return True


needs_symlinks = pytest.mark.skipif(
    not _can_symlink(), reason="symlinks need Developer Mode or admin on Windows"
)


def snapshot(root: Path) -> dict[str, tuple[str, int]]:
    return {
        p.relative_to(root).as_posix(): (
            hashlib.sha256(p.read_bytes()).hexdigest(),
            p.stat().st_mtime_ns,
        )
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    root = tmp_path / "repo"
    shutil.copytree(REPO, root)
    return Repository(id="fixture", name="Fixture", root=root.resolve())


@pytest.fixture
def index(tmp_path: Path) -> Index:
    return Index(tmp_path / "state" / "index.sqlite")


def test_scan_never_changes_the_repository(index: Index) -> None:
    repo = Repository(id="fixture", name="Fixture", root=REPO.resolve())
    before = snapshot(REPO)
    scan(repo, index)
    scan(repo, index, full=True)
    assert snapshot(REPO) == before


def test_scan_indexes_every_metadata_file(repo: Repository, index: Index) -> None:
    result = scan(repo, index)
    objects = index.all(repo.id)
    kinds = {o.kind for o in objects}
    assert kinds == {"pkginfo", "manifest", "catalog"}
    assert result.files == len(objects)
    browsers = [o for o in objects if o.name == "ExampleBrowser"]
    assert sorted(o.version or "" for o in browsers) == ["135.0", "136.0", "136.0"]
    assert len({o.path for o in browsers}) == 3
    editor = next(o for o in objects if o.name == "ExampleEditor")
    assert editor.catalogs == ["testing", "production"]
    assert editor.format == "xml-plist"
    assert next(o for o in objects if o.name == "ExampleCLI").format == "yaml"


def test_invalid_files_are_indexed_with_diagnostics(repo: Repository, index: Index) -> None:
    scan(repo, index)
    broken = index.get(repo.id, "pkgsinfo/invalid/Broken-1.0.plist")
    assert broken is not None and broken.status == "invalid"
    assert broken.error and broken.error_line is not None
    not_a_dict = index.get(repo.id, "pkgsinfo/invalid/NotADict-1.0.plist")
    assert not_a_dict is not None and not_a_dict.status == "invalid"
    assert "not a dictionary" in (not_a_dict.error or "")


def test_rescan_reads_only_changed_files(repo: Repository, index: Index) -> None:
    first = scan(repo, index)
    second = scan(repo, index)
    assert second.read == 0 and second.unchanged == first.files
    target = repo.root / "manifests/site_default"
    target.write_bytes(target.read_bytes().replace(b"ExampleRuntime", b"ExampleRuntime2"))
    os.utime(target, ns=(1, 1))
    third = scan(repo, index)
    assert third.read == 1
    touched = repo.root / "manifests/groups/loop-a"
    os.utime(touched, ns=(2, 2))
    fourth = scan(repo, index)
    assert fourth.read == 0 and fourth.unchanged == first.files
    (repo.root / "manifests/groups/loop-b").unlink()
    assert scan(repo, index).removed == 1
    assert index.get(repo.id, "manifests/groups/loop-b") is None


@needs_symlinks
def test_symlinks_out_of_the_repository_are_skipped(
    repo: Repository, index: Index, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.plist"
    outside.write_bytes((REPO / "manifests/site_default").read_bytes())
    (repo.root / "manifests/escape").symlink_to(outside)
    (repo.root / "pkgsinfo/linked-dir").symlink_to(tmp_path, target_is_directory=True)
    result = scan(repo, index)
    assert "manifests/escape" in result.skipped
    assert index.get(repo.id, "manifests/escape") is None
    assert not any(o.path.startswith("pkgsinfo/linked-dir") for o in index.all(repo.id))


@pytest.mark.parametrize(
    "bad", ["../etc/passwd", "/etc/passwd", "a/../../b", "", "a\\b", "a//b", "a\x00b"]
)
def test_path_escapes_are_rejected(repo: Repository, bad: str) -> None:
    with pytest.raises(PathEscapeError):
        contained(repo.root, bad)


def test_contained_paths_resolve(repo: Repository) -> None:
    assert normalize("manifests/site_default") == "manifests/site_default"
    assert contained(repo.root, "manifests/site_default") == repo.root / "manifests/site_default"


def test_settings_load_from_toml(tmp_path: Path, repo: Repository) -> None:
    config = tmp_path / "studio.toml"
    root = repo.root.as_posix()
    config.write_text(f'state_dir = "state"\n[[repositories]]\nid = "main"\npath = "{root}"\n')
    settings = load_settings(config)
    assert settings.repository("main").root == repo.root
    assert settings.repository("main").writable is False
    assert settings.state_dir == (tmp_path / "state").resolve()
    config.write_text('[[repositories]]\nid = "main"\npath = "missing"\n')
    with pytest.raises(ConfigError, match="not a directory"):
        load_settings(config)


@needs_symlinks
def test_reads_are_bounded_and_regular_files_only(repo: Repository, tmp_path: Path) -> None:
    from munki_web_studio.repository.paths import read_contained

    big = repo.root / "manifests/huge"
    big.write_bytes(b"x" * 2048)
    assert read_contained(repo.root, "manifests/huge", 1024).raw is None
    assert read_contained(repo.root, "manifests/huge", 4096).raw == b"x" * 2048
    with pytest.raises(PathEscapeError):
        read_contained(repo.root, "manifests", 1024)
    (repo.root / "manifests/inside-link").symlink_to(repo.root / "manifests/site_default")
    assert read_contained(repo.root, "manifests/inside-link", 1 << 20).raw
    (repo.root / "manifests/outside-link").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(PathEscapeError):
        read_contained(repo.root, "manifests/outside-link", 1024)


def test_oversized_files_are_indexed_without_being_parsed(
    repo: Repository, index: Index, monkeypatch: pytest.MonkeyPatch
) -> None:
    from munki_web_studio.repository import scanner

    monkeypatch.setattr(scanner, "MAX_FILE_BYTES", 1024)
    (repo.root / "manifests/huge").write_bytes(b"x" * 2048)
    scan(repo, index)
    huge = index.get(repo.id, "manifests/huge")
    assert huge is not None and huge.status == "invalid" and "larger than" in (huge.error or "")


def test_allowed_hosts_always_include_local_names(tmp_path: Path, repo: Repository) -> None:
    config = tmp_path / "studio.toml"
    config.write_text(
        'allowed_hosts = ["studio.example.org"]\n'
        '[auth]\nmode = "oidc"\nissuer = "https://id.example.org"\nclient_id = "studio"\n'
        'public_url = "https://studio.example.org"\n'
        f'[[repositories]]\nid = "main"\npath = "{repo.root.as_posix()}"\n'
    )
    assert load_settings(config).allowed_hosts == ("localhost", "127.0.0.1", "studio.example.org")
    config.write_text('allowed_hosts = ["*"]\n')
    with pytest.raises(ConfigError, match="cannot be"):
        load_settings(config)


def test_index_serves_many_threads_at_once(repo: Repository, index: Index) -> None:
    from concurrent.futures import ThreadPoolExecutor

    scan(repo, index)
    expected = len(index.all(repo.id))
    with ThreadPoolExecutor(max_workers=16) as pool:
        counts = list(pool.map(lambda _: len(index.all(repo.id)), range(200)))
    assert counts == [expected] * 200


windows_only = pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")


def _junction(link: Path, target: Path) -> None:
    import _winapi

    _winapi.CreateJunction(str(target), str(link))  # type: ignore[attr-defined]


@windows_only
def test_junctions_out_of_the_repository_are_not_listed(
    repo: Repository, index: Index, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Escaped-1.0.yaml").write_bytes((REPO / "manifests/site_default").read_bytes())
    _junction(repo.root / "pkgsinfo/linked-junction", outside)
    result = scan(repo, index)
    assert not any(p.startswith("pkgsinfo/linked-junction") for p in result.skipped)
    assert not any(o.path.startswith("pkgsinfo/linked-junction") for o in index.all(repo.id))


@windows_only
def test_a_junction_swapped_in_after_the_check_is_refused(
    repo: Repository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from munki_web_studio.repository import paths

    folder = repo.root / "manifests/swap"
    folder.mkdir()
    (folder / "late.plist").write_bytes(b"inside: true\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "late.plist").write_bytes(b"outside: true\n")
    checked = paths.contained

    def check_then_swap(root: Path, relative: str) -> Path:
        target = checked(root, relative)
        shutil.rmtree(folder)
        _junction(folder, outside)
        return target

    monkeypatch.setattr(paths, "contained", check_then_swap)
    with pytest.raises(PathEscapeError, match="leaves the repository"):
        paths.read_contained(repo.root, "manifests/swap/late.plist", 1024)


def test_a_refused_rename_is_retried_only_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from munki_web_studio.repository import paths

    calls: list[int] = []
    real = os.replace

    def busy_twice(source: Path, destination: Path) -> None:
        calls.append(1)
        if len(calls) <= 2:
            raise PermissionError(13, "in use")
        real(source, destination)

    (tmp_path / "a").mkdir()
    monkeypatch.setattr(paths.os, "replace", busy_twice)
    if sys.platform == "win32":
        paths.replace(tmp_path / "a", tmp_path / "b")
        assert (tmp_path / "b").is_dir() and len(calls) == 3
    else:
        with pytest.raises(PermissionError):
            paths.replace(tmp_path / "a", tmp_path / "b")
        assert len(calls) == 1
