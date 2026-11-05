"""Property lists: read with plistlib, edit XML in place by byte span."""

from __future__ import annotations

import plistlib
import re
from dataclasses import dataclass, field
from typing import Any
from xml.parsers.expat import ExpatError
from xml.sax.saxutils import escape, unescape

from munki_web_studio.codec.model import (
    Append,
    Change,
    CodecError,
    Delete,
    ParseError,
    Path,
    Set,
)


def load(raw: bytes) -> Any:
    try:
        return plistlib.loads(raw)
    except ExpatError as error:
        raise ParseError(str(error).split(":")[0], error.lineno, error.offset + 1) from error
    except (plistlib.InvalidFileException, ValueError, TypeError) as error:
        raise ParseError(f"Not a valid property list: {error}") from error


def dump(data: Any, *, binary: bool) -> bytes:
    fmt = plistlib.FMT_BINARY if binary else plistlib.FMT_XML
    return plistlib.dumps(data, fmt=fmt, sort_keys=False)


# Elements, comments, processing instructions, and the doctype, in document order.
_TOKEN = re.compile(
    r"<!--.*?-->|<\?.*?\?>|<!DOCTYPE[^>]*>"
    r"""|<(/?)([A-Za-z]+)(?:\s+(?:[^>"/]|"[^"]*")*)?(/?)>""",
    re.S,
)


@dataclass
class Node:
    tag: str
    start: int
    end: int = 0
    inner_start: int = 0
    inner_end: int = 0
    children: list[Node] = field(default_factory=lambda: [])


def _tree(text: str) -> Node:
    root = Node("#root", 0, len(text))
    stack = [root]
    for match in _TOKEN.finditer(text):
        closing, tag, empty = match.group(1), match.group(2), match.group(3)
        if tag is None:
            continue
        if closing:
            node = stack.pop()
            if node.tag != tag:
                raise CodecError(f"Mismatched </{tag}> for <{node.tag}>")
            node.inner_end, node.end = match.start(), match.end()
        else:
            node = Node(tag, match.start(), inner_start=match.end())
            stack[-1].children.append(node)
            if empty:
                node.end = node.inner_end = match.end()
            else:
                stack.append(node)
    return root


def _value_root(text: str) -> Node:
    plist = next((n for n in _tree(text).children if n.tag == "plist"), None)
    if plist is None or len(plist.children) != 1:
        raise CodecError("No single root value inside <plist>")
    return plist.children[0]


def _dict_entries(text: str, node: Node) -> list[tuple[Node, Node]]:
    kids = node.children
    return list(zip(kids[0::2], kids[1::2], strict=True))


def _key_text(text: str, node: Node) -> str:
    return unescape(text[node.inner_start : node.inner_end])


def _find(text: str, path: Path) -> tuple[Node | None, Node]:
    """Return (key node or None, value node) for a path that exists."""
    key_node: Node | None = None
    node = _value_root(text)
    for key in path:
        if node.tag == "dict" and isinstance(key, str):
            for k, v in _dict_entries(text, node):
                if _key_text(text, k) == key:
                    key_node, node = k, v
                    break
            else:
                raise KeyError(key)
        elif node.tag == "array" and isinstance(key, int):
            key_node, node = None, node.children[key]
        else:
            raise CodecError(f"Cannot follow {key!r} into <{node.tag}>")
    return key_node, node


def _line_start(text: str, offset: int) -> int:
    return text.rfind("\n", 0, offset) + 1


def _indent_before(text: str, offset: int) -> str:
    start = _line_start(text, offset)
    prefix = text[start:offset]
    return prefix if prefix.strip() == "" else ""


def _indent_unit(text: str) -> str:
    root = _value_root(text)
    if root.children:
        unit = _indent_before(text, root.children[0].start)
        if unit:
            return unit
    return "\t"


def _render(value: Any, indent: str, unit: str) -> str:
    """Render one value as plist XML, starting at `indent` on its first line."""
    body = plistlib.dumps(value, sort_keys=False).decode("utf-8")
    lines = body.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("<plist")) + 1
    end = max(i for i, line in enumerate(lines) if line.startswith("</plist>"))
    rendered: list[str] = []
    for line in lines[start:end]:
        stripped = line.lstrip("\t")
        depth = len(line) - len(stripped)
        rendered.append(unit * depth + stripped)
    return ("\n" + indent).join(rendered)


def edit(raw: bytes, changes: list[Change]) -> bytes:
    """Apply changes to XML plist bytes by replacing only the affected spans."""
    text = raw.decode("utf-8")
    unit = _indent_unit(text)
    for change in changes:
        text = _apply(text, change, unit)
    return text.encode("utf-8")


def _apply(text: str, change: Change, unit: str) -> str:
    if not change.path:
        raise CodecError("Replacing the whole document is not a surgical edit")
    parent_path, last = change.path[:-1], change.path[-1]
    match change:
        case Set(value=value):
            try:
                _, node = _find(text, change.path)
            except KeyError:
                return _insert_key(text, parent_path, str(last), value, unit)
            indent = _indent_before(text, node.start)
            return text[: node.start] + _render(value, indent, unit) + text[node.end :]
        case Delete():
            key_node, node = _find(text, change.path)
            first = key_node or node
            start = _line_start(text, first.start)
            if text[start : first.start].strip():
                start = first.start
            end = node.end
            if text[end : end + 1] == "\n" and start == _line_start(text, first.start):
                end += 1
            return text[:start] + text[end:]
        case Append(value=value):
            _, node = _find(text, change.path)
            if node.tag != "array" or not node.children:
                raise CodecError("Append needs a non-empty <array>")
            last_child = node.children[-1]
            indent = _indent_before(text, last_child.start)
            addition = "\n" + indent + _render(value, indent, unit)
            return text[: last_child.end] + addition + text[last_child.end :]


def _insert_key(text: str, parent_path: Path, key: str, value: Any, unit: str) -> str:
    _, parent = _find(text, parent_path)
    if parent.tag != "dict" or not parent.children:
        raise CodecError("New keys go into a non-empty <dict>")
    last = parent.children[-1]
    indent = _indent_before(text, last.start)
    addition = f"\n{indent}<key>{escape(key)}</key>\n{indent}{_render(value, indent, unit)}"
    return text[: last.end] + addition + text[last.end :]
