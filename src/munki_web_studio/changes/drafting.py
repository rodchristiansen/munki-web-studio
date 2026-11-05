"""Adding edits to a draft, the same way from every front door.

The API, the MCP server, and the command line all come through here, so a field
edit from an assistant is checked, merged, and recorded exactly like one from the
browser. Nothing here writes to the repository; only saving does that.
"""

from dataclasses import dataclass
from typing import Any, cast

from munki_web_studio.changes.fields import (
    FieldError,
    field_changes,
    manifest_changes,
    merge,
)
from munki_web_studio.changes.model import ChangeSet, FileDraft
from munki_web_studio.changes.store import ChangeSetStore
from munki_web_studio.codec import Document, ParseError, load
from munki_web_studio.codec.model import MAX_FILE_BYTES, apply_to_data
from munki_web_studio.repository.config import Repository
from munki_web_studio.repository.index import Index
from munki_web_studio.repository.paths import PathEscapeError, normalize, read_contained


class DraftError(Exception):
    """An edit the draft refused. `status` is the matching HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Target:
    path: str
    document: Document
    previous: FileDraft | None

    @property
    def drafted(self) -> Any:
        """The file's data with the draft's earlier edits applied."""
        if self.previous is None:
            return self.document.data
        return apply_to_data(self.document.data, self.previous.changes)


def open_target(
    repo: Repository, index: Index, change_set: ChangeSet, path: str, kind: str
) -> Target:
    if change_set.status != "open":
        raise DraftError(409, f"This change set is {change_set.status}")
    try:
        relative = normalize(path)
    except PathEscapeError:
        raise DraftError(400, "Invalid path") from None
    obj = index.get(repo.id, relative)
    if obj is None or obj.kind != kind or obj.status != "ok":
        raise DraftError(404, f"No readable {kind} at {relative!r}")
    try:
        read = read_contained(repo.root, relative, MAX_FILE_BYTES)
        if read.raw is None:
            raise DraftError(413, "File is too large")
        document = load(read.raw)
    except (PathEscapeError, ParseError, OSError) as error:
        raise DraftError(409, str(error)) from None
    previous = next((f for f in change_set.files if f.path == relative), None)
    return Target(relative, document, previous)


def _check_base(target: Target, base_revision: str | None) -> str:
    current = target.document.revision
    if base_revision is not None and base_revision != current:
        raise DraftError(409, "The file changed since you opened it; reload and edit again")
    if target.previous is not None and target.previous.base_revision != current:
        raise DraftError(
            409,
            "The file changed after this draft was made; reapply or drop its edits first",
        )
    return current


def _stored(store: ChangeSetStore, change_id: str) -> ChangeSet:
    updated = store.get(change_id)
    if updated is None:
        raise DraftError(404, "No such change set")
    return updated


def edit_fields(
    repo: Repository,
    index: Index,
    store: ChangeSetStore,
    change_set: ChangeSet,
    path: str,
    fields: dict[str, Any],
    base_revision: str | None = None,
) -> ChangeSet:
    """Set named pkginfo fields (None removes one). Earlier edits to the same fields
    are replaced; values equal to the file's are dropped."""
    target = open_target(repo, index, change_set, path, "pkginfo")
    base = _check_base(target, base_revision)
    try:
        new = field_changes(target.document.data, fields)
    except (FieldError, ValueError, TypeError) as error:
        raise DraftError(400, str(error)) from None
    touched = [tuple(name.split(".")) for name in fields]
    combined = merge(target.previous.changes if target.previous else [], new, touched)
    store.put_file(change_set.id, target.path, base, combined)
    return _stored(store, change_set.id)


def edit_sections(
    repo: Repository,
    index: Index,
    store: ChangeSetStore,
    change_set: ChangeSet,
    path: str,
    sections: dict[str, list[str]],
    base_revision: str | None = None,
) -> ChangeSet:
    """Set whole manifest lists; the draft records the smallest change to each."""
    target = open_target(repo, index, change_set, path, "manifest")
    base = _check_base(target, base_revision)
    try:
        new = manifest_changes(target.document.data, dict(sections))
    except FieldError as error:
        raise DraftError(400, str(error)) from None
    touched = [(name,) for name in sections]
    previous = target.previous.changes if target.previous else []
    combined = [c for c in previous if c.path[:1] not in touched] + new
    store.put_file(change_set.id, target.path, base, combined)
    return _stored(store, change_set.id)


def drafted_list(target: Target, key: str) -> list[str]:
    """A top-level list as the draft currently leaves it."""
    data = target.drafted
    value = cast(dict[str, Any], data).get(key) if isinstance(data, dict) else None
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in cast(list[Any], value)):
        raise DraftError(400, f"{key} is not a list of names in {target.path}")
    return list(cast(list[str], value))
