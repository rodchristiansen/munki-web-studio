"""Turn field edits ({"display_name": "New"}) into codec changes, checked against
the platform's field types. Edits that match the current value are dropped, so
saving an unchanged form writes nothing."""

from typing import Any, cast

from munki_web_studio import platform
from munki_web_studio.codec import Change, Delete, Set


class FieldError(ValueError):
    pass


def _check(name: str, kind: str, value: Any) -> None:
    ok = {
        "string": isinstance(value, str) and "\n" not in value,
        "text": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "string-list": isinstance(value, list)
        and all(isinstance(v, str) for v in cast(list[Any], value)),
    }.get(kind, False)
    if not ok:
        expected = {
            "string": "one line of text",
            "text": "text",
            "boolean": "true or false",
            "string-list": "a list of text values",
        }.get(kind, kind)
        raise FieldError(f"{name} must be {expected}")


def _current(data: dict[str, Any], parts: tuple[str, ...]) -> tuple[bool, Any]:
    value: Any = data
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = cast(dict[str, Any], value)[part]
    return True, value


def field_changes(data: Any, fields: dict[str, Any]) -> list[Change]:
    """Changes for these field edits. A value of None removes the field."""
    if not isinstance(data, dict):
        raise FieldError("This file's top level is not a dictionary")
    mapping = cast(dict[str, Any], data)
    changes: list[Change] = []
    for name, value in fields.items():
        kind = platform.PKGINFO_FIELDS.get(name)
        if kind is None:
            raise FieldError(f"{name} is not an editable field")
        parts = tuple(name.split("."))
        exists, current = _current(mapping, parts)
        if len(parts) > 1 and not _current(mapping, parts[:-1])[0]:
            raise FieldError(f"{name} needs {'.'.join(parts[:-1])} to exist first")
        if value is None:
            if exists:
                changes.append(Delete(parts))
            continue
        _check(name, kind, value)
        if exists and current == value and type(current) is type(value):
            continue
        changes.append(Set(parts, value))
    return changes


def merge(
    existing: list[Change], new: list[Change], touched: list[tuple[str, ...]]
) -> list[Change]:
    """Replace earlier edits to the same fields with the new ones."""
    keep = [c for c in existing if tuple(c.path) not in touched]
    return keep + new
