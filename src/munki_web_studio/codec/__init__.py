"""Read and edit Munki repository files without disturbing what was not changed.

`load` detects the format and returns a `Document` holding the original bytes
and their typed reading. `edit` applies changes by replacing only the affected
bytes where it can, falls back to a whole-file rewrite where it cannot, and
re-reads the result to prove it holds exactly the intended data.
"""

from typing import Any

from munki_web_studio.codec import plistfile, yamlfile
from munki_web_studio.codec.model import (
    MAX_FILE_BYTES,
    Append,
    Change,
    CodecError,
    Delete,
    Document,
    EditResult,
    Format,
    ParseError,
    Set,
    apply_to_data,
    check_bounds,
    first_difference,
    revision_of,
)

__all__ = [
    "Append",
    "Change",
    "CodecError",
    "Delete",
    "Document",
    "EditResult",
    "Format",
    "ParseError",
    "Set",
    "detect_format",
    "edit",
    "load",
    "revision_of",
]


def detect_format(raw: bytes) -> Format:
    if raw.startswith(b"bplist"):
        return Format.BINARY_PLIST
    head = raw.lstrip(b"\xef\xbb\xbf \t\r\n")[:64]
    if head.startswith((b"<?xml", b"<!DOCTYPE plist", b"<plist")):
        return Format.XML_PLIST
    return Format.YAML


def _parse(raw: bytes, fmt: Format) -> Any:
    try:
        data = yamlfile.load(raw) if fmt is Format.YAML else plistfile.load(raw)
    except RecursionError as error:
        raise ParseError("Nesting is too deep to read") from error
    check_bounds(data)
    return data


def load(raw: bytes) -> Document:
    if len(raw) > MAX_FILE_BYTES:
        raise ParseError(f"File is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    fmt = detect_format(raw)
    return Document(raw=raw, format=fmt, data=_parse(raw, fmt))


def edit(document: Document, changes: list[Change]) -> EditResult:
    """Return new bytes for the document with the changes applied."""
    expected = apply_to_data(document.data, changes)
    if not changes or first_difference(expected, document.data) is None:
        return EditResult(raw=document.raw, surgical=True)

    candidates: list[tuple[bytes, bool]] = []
    if document.format is not Format.BINARY_PLIST:
        try:
            if document.format is Format.XML_PLIST:
                candidates.append((plistfile.edit(document.raw, changes), True))
            else:
                candidates.append((yamlfile.edit(document.raw, changes), True))
        except CodecError, KeyError, IndexError, ValueError:
            pass
    if document.format is Format.YAML:
        candidates.append((yamlfile.rewrite(document.raw, changes), False))
    else:
        binary = document.format is Format.BINARY_PLIST
        candidates.append((plistfile.dump(expected, binary=binary), False))

    problems: list[str] = []
    for raw, surgical in candidates:
        try:
            actual = _parse(raw, document.format)
        except ParseError as error:
            problems.append(str(error))
            continue
        difference = first_difference(expected, actual)
        if difference is None:
            return EditResult(raw=raw, surgical=surgical)
        problems.append(difference)
    raise CodecError("Edit could not be verified: " + "; ".join(problems))
