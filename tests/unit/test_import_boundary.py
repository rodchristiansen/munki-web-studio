"""This studio must run alone: no imports from FleetMate or the other studio."""

import ast
from pathlib import Path

from munki_web_studio.studio import FORBIDDEN_IMPORTS

ROOT = Path(__file__).resolve().parents[2]


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_no_forbidden_imports() -> None:
    offenders = [
        f"{path.relative_to(ROOT)}: {name}"
        for folder in ("src", "tests")
        for path in sorted((ROOT / folder).rglob("*.py"))
        for name in imported_roots(path) & set(FORBIDDEN_IMPORTS)
    ]
    assert offenders == []


def test_check_detects_a_forbidden_import(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text("import fleetmate.shell\nfrom cimian_web_studio import x\n")
    assert imported_roots(sample) & set(FORBIDDEN_IMPORTS) == {"fleetmate", "cimian_web_studio"}
