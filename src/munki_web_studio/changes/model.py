"""Change sets: a person's draft edits, each pinned to the revision it was made on."""

from dataclasses import dataclass
from typing import Any, cast

from munki_web_studio import tagged
from munki_web_studio.codec import Append, Change, Delete, Set


class ChangeFormatError(ValueError):
    pass


@dataclass(frozen=True)
class FileDraft:
    path: str
    base_revision: str
    changes: list[Change]


@dataclass(frozen=True)
class ChangeSet:
    id: str
    repo_id: str
    owner: str
    owner_name: str
    title: str
    status: str  # open, saved, or discarded
    created_at: str
    updated_at: str
    files: list[FileDraft]


def change_to_json(change: Change) -> dict[str, Any]:
    match change:
        case Set(path=path, value=value):
            return {"op": "set", "path": list(path), "value": tagged.encode(value)}
        case Append(path=path, value=value):
            return {"op": "append", "path": list(path), "value": tagged.encode(value)}
        case Delete(path=path):
            return {"op": "delete", "path": list(path)}


def change_from_json(data: Any) -> Change:
    if not isinstance(data, dict):
        raise ChangeFormatError("Each change must be an object")
    entry = cast(dict[str, Any], data)
    raw_path = entry.get("path")
    parts: list[str | int] = []
    for part in cast(list[Any], raw_path) if isinstance(raw_path, list) else []:
        if isinstance(part, bool) or not isinstance(part, str | int):
            raise ChangeFormatError("Path parts must be keys or list positions")
        parts.append(part)
    if not parts:
        raise ChangeFormatError("A change needs a non-empty path of keys and list positions")
    key_path = tuple(parts)
    match entry.get("op"):
        case "set":
            return Set(key_path, tagged.decode(entry.get("value")))
        case "append":
            return Append(key_path, tagged.decode(entry.get("value")))
        case "delete":
            return Delete(key_path)
        case other:
            raise ChangeFormatError(f"Unknown change op {other!r}; use set, append, or delete")
