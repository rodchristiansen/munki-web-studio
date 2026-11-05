import datetime as dt
import difflib
import plistlib
from pathlib import Path

import pytest

from munki_web_studio.codec import (
    Append,
    Delete,
    Format,
    ParseError,
    Set,
    detect_format,
    edit,
    load,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CODEC = FIXTURES / "codec"
REPO = FIXTURES / "repo"


def changed_lines(before: bytes, after: bytes) -> int:
    diff = difflib.unified_diff(
        before.decode().splitlines(), after.decode().splitlines(), lineterm="", n=0
    )
    return sum(1 for line in diff if line[:1] in "+-" and not line.startswith(("+++", "---")))


def text_files() -> list[Path]:
    files = [
        p
        for p in sorted(REPO.rglob("*"))
        if p.is_file()
        and "invalid" not in p.parts
        and "catalogs" not in p.parts
        and p.name != ".gitkeep"
        and not p.read_bytes().startswith(b"bplist")
    ]
    return [
        *files,
        CODEC / "pkginfo-plistlib.plist",
        CODEC / "pkginfo-handformatted.plist",
        CODEC / "manifest-plistlib.plist",
        CODEC / "pkginfo.yaml",
    ]


def test_detects_formats() -> None:
    assert detect_format((CODEC / "pkginfo-plistlib.plist").read_bytes()) is Format.XML_PLIST
    assert detect_format((CODEC / "pkginfo-binary.plist").read_bytes()) is Format.BINARY_PLIST
    assert detect_format((CODEC / "pkginfo.yaml").read_bytes()) is Format.YAML
    assert detect_format((REPO / "manifests/site_default").read_bytes()) is Format.XML_PLIST


def test_reads_typed_values() -> None:
    doc = load((CODEC / "pkginfo-plistlib.plist").read_bytes())
    assert doc.data["version"] == "2.10"
    assert doc.data["installed_size"] == 9007199254740993
    assert isinstance(doc.data["_metadata"]["creation_date"], dt.datetime)
    assert isinstance(doc.data["icon_data"], bytes)
    assert doc.data["unattended_install"] is True
    yaml_doc = load((CODEC / "pkginfo.yaml").read_bytes())
    assert yaml_doc.data["version"] == "3.0"
    assert yaml_doc.data["receipts"][0]["version"] == "3.0"
    assert yaml_doc.data["unattended_install"] is True
    assert type(yaml_doc.data["name"]) is str


@pytest.mark.parametrize("path", text_files(), ids=lambda p: p.name)
def test_no_op_edit_is_byte_identical(path: Path) -> None:
    doc = load(path.read_bytes())
    assert edit(doc, []).raw == doc.raw
    first_key = next(iter(doc.data))
    assert edit(doc, [Set((first_key,), doc.data[first_key])]).raw == doc.raw


def first_string_path(data: dict[str, object]) -> tuple[str | int, ...]:
    for key, value in data.items():
        if isinstance(value, str) and "\n" not in value:
            return (key,)
    for key, value in data.items():
        if isinstance(value, list) and value and isinstance(value[0], str):
            return (key, 0)
    raise AssertionError("no editable string")


@pytest.mark.parametrize("path", text_files(), ids=lambda p: p.name)
def test_single_value_edit_changes_two_lines(path: Path) -> None:
    doc = load(path.read_bytes())
    where = first_string_path(doc.data)
    current = doc.data[where[0]] if len(where) == 1 else doc.data[where[0]][where[1]]
    result = edit(doc, [Set(where, current + " Pro")])
    assert result.surgical
    assert changed_lines(doc.raw, result.raw) == 2
    reread = load(result.raw).data
    assert (reread[where[0]] if len(where) == 1 else reread[where[0]][where[1]]) == current + " Pro"


def test_comments_survive_edits() -> None:
    hand = load((CODEC / "pkginfo-handformatted.plist").read_bytes())
    result = edit(hand, [Set(("display_name",), "Example Viewer Pro")])
    assert b"<!-- Edited by hand; comment must survive. -->" in result.raw
    assert b"    <key>name</key>" in result.raw
    yaml_doc = load((CODEC / "pkginfo.yaml").read_bytes())
    result = edit(yaml_doc, [Set(("display_name",), "Example CLI Pro")])
    assert b"# Munki YAML pkginfo, invented values." in result.raw
    assert b"# exit 0 means install is needed" in result.raw


def test_append_adds_one_line() -> None:
    for path in (CODEC / "manifest-plistlib.plist", REPO / "manifests/site_default"):
        doc = load(path.read_bytes())
        result = edit(doc, [Append(("managed_installs",), "ExampleExtra")])
        assert result.surgical
        assert changed_lines(doc.raw, result.raw) == 1
    doc = load((CODEC / "pkginfo.yaml").read_bytes())
    result = edit(doc, [Append(("catalogs",), "production")])
    assert result.surgical
    assert changed_lines(doc.raw, result.raw) == 1


def test_plist_new_key_delete_and_container() -> None:
    doc = load((CODEC / "pkginfo-handformatted.plist").read_bytes())
    added = edit(doc, [Set(("notes",), "Added & <checked>")])
    assert added.surgical and changed_lines(doc.raw, added.raw) == 2
    assert load(added.raw).data["notes"] == "Added & <checked>"
    removed = edit(doc, [Delete(("restart_action",))])
    assert removed.surgical and changed_lines(doc.raw, removed.raw) == 2
    replaced = edit(doc, [Set(("catalogs",), ["testing", "production"])])
    assert replaced.surgical
    assert load(replaced.raw).data["catalogs"] == ["testing", "production"]
    nested = edit(doc, [Set(("receipts", 0, "optional"), True)])
    assert nested.surgical and changed_lines(doc.raw, nested.raw) == 2


def test_type_changes_are_written_with_the_right_type() -> None:
    doc = load((CODEC / "pkginfo-plistlib.plist").read_bytes())
    result = edit(doc, [Set(("installed_size",), 9007199254740995)])
    assert load(result.raw).data["installed_size"] == 9007199254740995
    yaml_doc = load((CODEC / "pkginfo.yaml").read_bytes())
    for value in ("4.0", "yes", "null", "  padded"):
        changed = edit(yaml_doc, [Set(("version",), value)])
        assert load(changed.raw).data["version"] == value


def test_yaml_falls_back_to_a_verified_rewrite() -> None:
    doc = load((CODEC / "pkginfo.yaml").read_bytes())
    result = edit(doc, [Set(("notes",), "new key")])
    assert not result.surgical
    assert load(result.raw).data["notes"] == "new key"
    assert b"# exit 0 means install is needed" in result.raw


def test_binary_plist_is_rewritten_and_verified() -> None:
    doc = load((CODEC / "pkginfo-binary.plist").read_bytes())
    result = edit(doc, [Set(("display_name",), "Example Editor Pro")])
    assert result.raw.startswith(b"bplist00")
    data = load(result.raw).data
    assert data["display_name"] == "Example Editor Pro"
    assert data["installed_size"] == 9007199254740993


def test_invalid_files_report_a_location() -> None:
    with pytest.raises(ParseError) as plist_error:
        load((REPO / "pkgsinfo/invalid/Broken-1.0.plist").read_bytes())
    assert plist_error.value.line is not None
    with pytest.raises(ParseError) as yaml_error:
        load(b"name: Broken\nversion: '1.0'\n  catalogs:\n - testing\n")
    assert yaml_error.value.line == 3


def test_revision_tracks_bytes() -> None:
    raw = (CODEC / "pkginfo.yaml").read_bytes()
    assert load(raw).revision == load(raw).revision
    assert load(raw).revision != load(raw + b"\n").revision


def test_rejects_yaml_aliases_and_alias_bombs() -> None:
    bomb = b"a: &a [x, x, x, x, x, x, x, x, x]\nb: &b [*a, *a, *a, *a, *a, *a, *a, *a, *a]\n"
    with pytest.raises(ParseError, match="aliases"):
        load(bomb)


def test_rejects_deep_nesting() -> None:
    with pytest.raises(ParseError, match="Nesting"):
        load(b"k: " + b"[" * 200 + b"]" * 200 + b"\n")
    deep: list[object] = []
    current = deep
    for _ in range(200):
        nxt: list[object] = []
        current.append(nxt)
        current = nxt
    with pytest.raises(ParseError, match="Nesting"):
        load(plistlib.dumps({"k": deep}, fmt=plistlib.FMT_BINARY))


def test_rejects_binary_plist_amplification() -> None:
    shared = ["x"] * 1000
    level = [shared] * 1000
    raw = plistlib.dumps({"k": [level] * 2}, fmt=plistlib.FMT_BINARY)
    assert len(raw) < 100_000
    with pytest.raises(ParseError, match="expands"):
        load(raw)


def test_rejects_oversized_files() -> None:
    with pytest.raises(ParseError, match="larger than"):
        load(b"#" * (17 * 1024 * 1024))


def test_flow_list_items_edit_in_place() -> None:
    doc = load(b"catalogs: [testing, production]  # both\nname: x\n")
    result = edit(doc, [Set(("catalogs", 1), "prod, eu")])
    assert result.surgical
    assert result.raw == b'catalogs: [testing, "prod, eu"]  # both\nname: x\n'
    assert load(result.raw).data["catalogs"] == ["testing", "prod, eu"]


def test_block_lists_are_replaced_in_place() -> None:
    raw = b"catalogs:\n  - testing   # first\n  - production\n\nrequires:\n  - x\n"
    doc = load(raw)
    result = edit(doc, [Set(("catalogs",), ["testing", "staging"])])
    assert result.surgical
    assert result.raw == b"catalogs:\n  - testing   # first\n  - staging\n\nrequires:\n  - x\n"


def test_changes_that_do_not_fit_are_codec_errors() -> None:
    from munki_web_studio.codec import CodecError

    doc = load(b"name: x\nlist: [a]\n")
    for change in (Append(("name",), "y"), Set(("missing", "deeper"), 1), Set(("list", 5), "z")):
        with pytest.raises(CodecError, match="does not fit"):
            edit(doc, [change])
