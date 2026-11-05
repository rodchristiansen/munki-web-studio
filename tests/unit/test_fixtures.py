import hashlib
import plistlib
import re
from pathlib import Path

from tests.fixtures.build_repo_fixture import REPO
from tests.fixtures.generate_large_repo import generate


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_generator_is_deterministic(tmp_path: Path) -> None:
    generate(tmp_path / "a", pkginfo=60, manifests=20, seed=7)
    generate(tmp_path / "b", pkginfo=60, manifests=20, seed=7)
    assert tree_digest(tmp_path / "a") == tree_digest(tmp_path / "b")
    assert len(list((tmp_path / "a" / "pkgsinfo").rglob("*.plist"))) == 60
    assert len([p for p in (tmp_path / "a" / "manifests").rglob("*") if p.is_file()]) == 20


def test_repo_fixture_has_the_cases_later_slices_need() -> None:
    pkgsinfo = REPO / "pkgsinfo"
    assert (pkgsinfo / "tools/ExampleCLI-3.0.yaml").is_file()
    assert (pkgsinfo / "apps/creative/ExampleViewer-1.0.plist").read_bytes().startswith(b"bplist00")
    duplicates = [p for p in pkgsinfo.rglob("ExampleBrowser-136.0.plist")]
    assert len(duplicates) == 2
    for name in ("all", "testing", "production"):
        assert isinstance(plistlib.loads((REPO / "catalogs" / name).read_bytes()), list)


def test_repo_fixture_contains_no_real_identifiers() -> None:
    text = b"".join(p.read_bytes() for p in REPO.rglob("*") if p.is_file())
    for marker in (b"ecuad", b"emilycarr", b"focused.systems"):
        assert marker not in text.lower()
    assert re.search(rb"[\w.+-]+@[\w-]+\.[\w.]+", text) is None
