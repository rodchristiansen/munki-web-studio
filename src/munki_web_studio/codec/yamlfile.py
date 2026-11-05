# ruamel.yaml ships without type information; this module is its only boundary.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
"""YAML: read with ruamel.yaml in round-trip mode, which builds only plain data
types (never arbitrary Python objects), and edit single-line scalars in place.
"""

from __future__ import annotations

import datetime as dt
import io
import json
from typing import Any, cast

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import MarkedYAMLError, YAMLError

from munki_web_studio.codec.model import (
    MAX_DEPTH,
    Append,
    Change,
    CodecError,
    Delete,
    ParseError,
    Path,
    Set,
)


def _round_trip() -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 4096
    return yaml


def _reject_aliases_and_depth(text: str) -> None:
    """Munki and Cimian files never need anchors or aliases; refuse them.

    Aliases let a tiny file expand into an enormous tree, and deep nesting can
    exhaust the stack, so both are checked on the event stream before loading.
    """
    depth = 0
    for event in YAML().parse(text):
        name = type(event).__name__
        mark = event.start_mark
        if name == "AliasEvent" or getattr(event, "anchor", None):
            raise ParseError(
                "YAML anchors and aliases are not supported", mark.line + 1, mark.column + 1
            )
        if name in ("MappingStartEvent", "SequenceStartEvent"):
            depth += 1
            if depth > MAX_DEPTH:
                raise ParseError(
                    f"Nesting deeper than {MAX_DEPTH} levels", mark.line + 1, mark.column + 1
                )
        elif name in ("MappingEndEvent", "SequenceEndEvent"):
            depth -= 1


def load_tree(raw: bytes) -> Any:
    try:
        text = raw.decode("utf-8")
        _reject_aliases_and_depth(text)
        return _round_trip().load(text)
    except UnicodeDecodeError as error:
        raise ParseError(f"Not UTF-8 text: {error.reason}") from error
    except MarkedYAMLError as error:
        mark = error.problem_mark or error.context_mark
        message = error.problem or error.context or "Invalid YAML"
        if mark is None:
            raise ParseError(message) from error
        raise ParseError(message, mark.line + 1, mark.column + 1) from error
    except YAMLError as error:
        raise ParseError(f"Invalid YAML: {error}") from error


def plain(
    value: Any, lines: list[str] | None = None, text_keys: frozenset[str] = frozenset()
) -> Any:
    """Convert ruamel's round-trip types into plain Python values.

    Keys in `text_keys` are text fields whose consumer reads the scalar as
    written: an unquoted `version: 2.10` stays "2.10" and a zero hash stays a
    string, instead of becoming 2.1 or 0. The text comes from the source line.
    """
    if isinstance(value, CommentedMap) and lines is not None and text_keys:
        result: dict[str, Any] = {}
        for key, item in value.items():
            if (
                str(key) in text_keys
                and isinstance(item, int | float)
                and not isinstance(item, bool)
            ):
                line_no, col = cast(tuple[int, int], value.lc.value(key))
                line = lines[line_no]
                result[str(key)] = line[col : _scalar_end(line, col)]
            else:
                result[str(key)] = plain(item, lines, text_keys)
        return result
    if isinstance(value, dict):
        mapping = cast(dict[Any, Any], value)
        return {str(k): plain(v, lines, text_keys) for k, v in mapping.items()}
    if isinstance(value, list):
        return [plain(v, lines, text_keys) for v in cast(list[Any], value)]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    if isinstance(value, dt.datetime):
        return dt.datetime(
            value.year,
            value.month,
            value.day,
            value.hour,
            value.minute,
            value.second,
            value.microsecond,
            value.tzinfo,
        )
    if isinstance(value, dt.date):
        return dt.date(value.year, value.month, value.day)
    return value


def load(raw: bytes, text_keys: frozenset[str] = frozenset()) -> Any:
    tree = load_tree(raw)
    lines = _lines(raw.decode("utf-8")) if text_keys else None
    return plain(tree, lines, text_keys)


def _render_scalar(value: Any, original: str) -> str:
    """Render a scalar on one line, keeping the original quote style for strings."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, int | float):
        return repr(value)
    if not isinstance(value, str):
        raise CodecError(f"Cannot render {type(value).__name__} in place")
    if original.startswith("'"):
        return "'" + value.replace("'", "''") + "'"
    if original.startswith('"'):
        return json.dumps(value, ensure_ascii=False)
    probe = YAML(typ="safe").load(f"k: {value}\n")
    if probe == {"k": value} and "#" not in value and value.strip() == value:
        return value
    return json.dumps(value, ensure_ascii=False)


def _scalar_end(line: str, col: int, *, flow: bool = False) -> int:
    """End column of the single-line scalar token that starts at col.

    In a flow collection (`[a, b]`, `{k: v}`) a plain scalar also ends at a
    comma or closing bracket.
    """
    first = line[col : col + 1]
    if first == "'":
        i = col + 1
        while True:
            i = line.index("'", i)
            if line[i + 1 : i + 2] == "'":
                i += 2
                continue
            return i + 1
    if first == '"':
        i = col + 1
        while line[i] != '"':
            i += 2 if line[i] == "\\" else 1
        return i + 1
    if first in ("|", ">", "[", "{", "&", "*", "!", ""):
        raise CodecError("Not a single-line scalar")
    end = len(line.rstrip("\r\n"))
    comment = line.find(" #", col)
    if comment != -1:
        end = comment
    if flow:
        stops = [i for i in (line.find(c, col, end) for c in ",]}") if i != -1]
        if stops:
            end = min(stops)
    return len(line[:end].rstrip())


def _position(tree: Any, path: Path) -> tuple[Any, tuple[int, int]]:
    parent = tree
    for key in path[:-1]:
        parent = parent[key]
    last = path[-1]
    if isinstance(parent, CommentedMap):
        if last not in parent:
            raise KeyError(last)
        return parent, cast(tuple[int, int], parent.lc.value(last))
    if isinstance(parent, CommentedSeq) and isinstance(last, int):
        return parent, cast(tuple[int, int], parent.lc.item(last))
    raise CodecError(f"Cannot follow {last!r}")


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def edit(raw: bytes, changes: list[Change]) -> bytes:
    """Apply changes in place. Raises CodecError when a change needs a rewrite."""
    text = raw.decode("utf-8")
    for change in changes:
        tree = load_tree(text.encode("utf-8"))
        text = _apply(text, tree, change)
    return text.encode("utf-8")


def _apply(text: str, tree: Any, change: Change) -> str:
    if not change.path:
        raise CodecError("Replacing the whole document is not a surgical edit")
    match change:
        case Set(value=value):
            try:
                parent, (line_no, col) = _position(tree, change.path)
            except KeyError as error:
                raise CodecError("New keys need a rewrite") from error
            current = parent[change.path[-1]]
            if isinstance(current, dict | list) or isinstance(value, dict | list):
                raise CodecError("Container values need a rewrite")
            lines = _lines(text)
            line = lines[line_no]
            flow = bool(parent.fa.flow_style())
            end = _scalar_end(line, col, flow=flow)
            rendered = _render_scalar(value, line[col:end])
            if flow and any(c in rendered for c in ",[]{}") and not rendered.startswith(('"', "'")):
                rendered = json.dumps(value, ensure_ascii=False)
            lines[line_no] = line[:col] + rendered + line[end:]
            return "".join(lines)
        case Append(value=value):
            seq = tree
            for key in change.path:
                seq = seq[key]
            if not isinstance(seq, CommentedSeq) or not seq or seq.fa.flow_style():
                raise CodecError("Append in place needs a non-empty block list")
            if isinstance(value, dict | list) or isinstance(seq[-1], dict | list):
                raise CodecError("Appending containers needs a rewrite")
            line_no, col = cast(tuple[int, int], seq.lc.item(len(seq) - 1))
            lines = _lines(text)
            last = lines[line_no]
            if not last.endswith("\n"):
                lines[line_no] = last + "\n"
            newline = "\r\n" if last.endswith("\r\n") else "\n"
            lines.insert(line_no + 1, last[:col] + _render_scalar(value, "") + newline)
            return "".join(lines)
        case Delete():
            raise CodecError("Deleting needs a rewrite")


def rewrite(raw: bytes, changes: list[Change]) -> bytes:
    """Apply changes through a round-trip load and dump. Keeps most comments."""
    tree = load_tree(raw)
    for change in changes:
        parent = tree
        for key in change.path[:-1]:
            parent = parent[key]
        last = change.path[-1] if change.path else None
        match change:
            case Set(value=value):
                if last is None:
                    tree = value
                else:
                    parent[last] = value
            case Delete():
                del parent[last]
            case Append(value=value):
                (parent[last] if last is not None else parent).append(value)
    out = io.StringIO()
    _round_trip().dump(tree, out)
    return out.getvalue().encode("utf-8")
