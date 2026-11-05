"""Types shared by every codec: documents, edits, errors, and value comparison."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

type Key = str | int
type Path = tuple[Key, ...]


class Format(StrEnum):
    XML_PLIST = "xml-plist"
    BINARY_PLIST = "binary-plist"
    YAML = "yaml"


class CodecError(Exception):
    """An edit could not be applied faithfully."""


class ParseError(CodecError):
    """The file could not be read. Line and column are 1-based when known."""

    def __init__(self, message: str, line: int | None = None, column: int | None = None) -> None:
        location = f" (line {line}, column {column})" if line is not None else ""
        super().__init__(f"{message}{location}")
        self.message = message
        self.line = line
        self.column = column


@dataclass(frozen=True)
class Set:
    """Set the value at a path. The parent must exist; the last key may be new."""

    path: Path
    value: Any


@dataclass(frozen=True)
class Delete:
    """Remove the value at a path."""

    path: Path


@dataclass(frozen=True)
class Append:
    """Append a value to the list at a path."""

    path: Path
    value: Any


type Change = Set | Delete | Append


@dataclass(frozen=True)
class Document:
    """A parsed file. `raw` is authoritative; `data` is its typed reading."""

    raw: bytes
    format: Format
    data: Any

    @property
    def revision(self) -> str:
        return revision_of(self.raw)


@dataclass(frozen=True)
class EditResult:
    """New file bytes. `surgical` is False when the whole file was rewritten."""

    raw: bytes
    surgical: bool


# Limits that keep a hostile file from exhausting memory or the stack. Real
# Munki and Cimian files are far below all three.
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_DEPTH = 64
MAX_NODES = 1_000_000


def check_bounds(data: Any) -> None:
    """Reject data that is too deep, too large, or contains a cycle.

    Shared references are counted every time they are reached, so a small file
    that expands into a huge tree is rejected too. Loading itself stays small:
    YAML aliases are refused before loading, and plistlib keeps a binary plist's
    shared objects shared, so memory is bounded by the file size cap.
    """
    nodes = 1
    stack: list[tuple[Any, int]] = [(data, 0)]
    while stack:
        value, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise ParseError(f"Nesting deeper than {MAX_DEPTH} levels")
        if isinstance(value, dict):
            children = list(cast(dict[Any, Any], value).values())
        elif isinstance(value, list):
            children = cast(list[Any], value)
        else:
            continue
        # Count before queueing, so the walk itself never holds more than the cap.
        nodes += len(children)
        if nodes > MAX_NODES:
            raise ParseError(f"File expands to more than {MAX_NODES} values")
        stack.extend((child, depth + 1) for child in children)


def revision_of(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def apply_to_data(data: Any, changes: list[Change]) -> Any:
    """Apply changes to a copy of plain data; the expected result of an edit."""
    result = copy.deepcopy(data)
    for change in changes:
        parent = result
        for key in change.path[:-1]:
            parent = parent[key]
        last = change.path[-1] if change.path else None
        match change:
            case Set(value=value):
                if last is None:
                    result = copy.deepcopy(value)
                else:
                    parent[last] = copy.deepcopy(value)
            case Delete():
                del parent[last]
            case Append(value=value):
                target = parent[last] if last is not None else parent
                target.append(copy.deepcopy(value))
    return result


def kind_of(value: Any) -> str:
    for name, kind in (
        ("bool", bool),
        ("int", int),
        ("real", float),
        ("str", str),
        ("data", bytes | bytearray),
        ("date", dt.datetime),
        ("day", dt.date),
        ("dict", dict),
        ("list", list),
        ("null", type(None)),
    ):
        if isinstance(value, kind):
            return name
    return type(value).__name__


def first_difference(a: Any, b: Any, path: str = "$") -> str | None:
    """None when equal in value, type, and key order; else where they differ."""
    if kind_of(a) != kind_of(b):
        return f"{path}: {kind_of(a)} vs {kind_of(b)}"
    if isinstance(a, dict):
        if list(a) != list(b):  # pyright: ignore[reportUnknownArgumentType]
            return f"{path}: keys differ"
        for key in a:  # pyright: ignore[reportUnknownVariableType]
            if found := first_difference(a[key], b[key], f"{path}.{key}"):
                return found
        return None
    if isinstance(a, list):
        if len(a) != len(b):  # pyright: ignore[reportUnknownArgumentType]
            return f"{path}: length differs"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
            if found := first_difference(x, y, f"{path}[{i}]"):
                return found
        return None
    return None if a == b else f"{path}: {a!r} != {b!r}"
