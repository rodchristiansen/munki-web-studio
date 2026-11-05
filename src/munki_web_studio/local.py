"""Commands that read a repository on disk directly, with no server.

    mwsctl search editor --repo /srv/repo
    mwsctl validate --repo . --json

Each run scans into a throwaway index unless --index names a file to keep, so
repeat runs on a large repository only re-read what changed.
"""

import json
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, cast

import typer

from munki_web_studio import platform, tagged
from munki_web_studio.codec import ParseError, load
from munki_web_studio.codec.model import MAX_FILE_BYTES
from munki_web_studio.repository.config import Repository
from munki_web_studio.repository.index import Index, IndexedObject
from munki_web_studio.repository.paths import PathEscapeError, normalize, read_contained
from munki_web_studio.repository.references import graph_for, validate
from munki_web_studio.repository.scanner import scan

RepoOption = Annotated[
    Path, typer.Option("--repo", "-r", help="Repository folder.", file_okay=False, exists=True)
]
IndexOption = Annotated[
    Path | None, typer.Option("--index", help="Keep the scan index in this file between runs.")
]
JsonOption = Annotated[bool, typer.Option("--json", help="Print tagged JSON instead of text.")]


@contextmanager
def opened(repo_path: Path, index_path: Path | None) -> Generator[tuple[Repository, Index]]:
    root = repo_path.resolve()
    if not any((root / folder).is_dir() for folder in platform.KINDS):
        typer.echo(f"{root} has none of {', '.join(platform.KINDS)}; is it a repository?", err=True)
        raise typer.Exit(2)
    repo = Repository(id="local", name=root.name, root=root)
    with tempfile.TemporaryDirectory() as scratch:
        index = Index(index_path or Path(scratch) / "index.sqlite")
        try:
            scan(repo, index)
            yield repo, index
        finally:
            index.close()


def _emit(value: Any) -> None:
    typer.echo(json.dumps(tagged.encode(value), indent=2, ensure_ascii=False))


def _row(obj: IndexedObject) -> dict[str, Any]:
    return {
        "path": obj.path,
        "kind": obj.kind,
        "status": obj.status,
        "name": obj.name,
        "version": obj.version,
        "catalogs": obj.catalogs,
    }


def search(
    query: Annotated[
        str, typer.Argument(help="Text to find in names, versions, categories, or paths.")
    ],
    repo: RepoOption = Path("."),
    kind: Annotated[str | None, typer.Option(help="pkginfo, manifest, or catalog.")] = None,
    index: IndexOption = None,
    as_json: JsonOption = False,
) -> None:
    """Find pkginfo, manifests, and catalogs."""
    with opened(repo, index) as (repository, idx):
        needle = query.lower()
        found = [
            o
            for o in idx.all(repository.id, kind)
            if needle
            in " ".join(
                v for v in (o.path, o.name, o.display_name, o.version, o.category) if v
            ).lower()
        ]
    if as_json:
        _emit([_row(o) for o in found])
        return
    for o in found:
        label = " ".join(v for v in (o.name, o.version) if v) or "(unreadable)"
        typer.echo(f"{o.path}\t{label}")
    if not found:
        typer.echo("No matches", err=True)


def show(
    path: Annotated[str, typer.Argument(help="Relative path inside the repository.")],
    repo: RepoOption = Path("."),
    as_json: JsonOption = False,
) -> None:
    """Print one file's contents."""
    root = repo.resolve()
    try:
        read = read_contained(root, normalize(path), MAX_FILE_BYTES)
        if read.raw is None:
            raise ParseError("File is too large")
        document = load(read.raw)
    except PathEscapeError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(2) from None
    except ParseError as error:
        typer.echo(f"{path}: {error}", err=True)
        raise typer.Exit(1) from None
    if as_json:
        _emit(
            {
                "path": normalize(path),
                "format": document.format.value,
                "revision": document.revision,
                "data": document.data,
            }
        )
        return
    typer.echo(f"# {normalize(path)} ({document.format.value}, revision {document.revision[:12]})")
    contents: Any = document.data
    data: dict[str, Any] = (
        cast(dict[str, Any], contents) if isinstance(contents, dict) else {"": contents}
    )
    for key, value in data.items():
        text = (
            value
            if isinstance(value, str)
            else json.dumps(tagged.encode(value), ensure_ascii=False)
        )
        typer.echo(f"{key}: {text}")


def where_used(
    name: Annotated[str, typer.Argument(help="A package name or manifest name.")],
    repo: RepoOption = Path("."),
    index: IndexOption = None,
    as_json: JsonOption = False,
) -> None:
    """List the manifests and packages that refer to a name."""
    with opened(repo, index) as (repository, idx):
        refs = graph_for(idx, repository).where_used(name)
    if as_json:
        _emit([vars(r) for r in refs])
        return
    for r in refs:
        suffix = f"  when {r.condition}" if r.condition else ""
        typer.echo(f"{r.source}\t{r.field}{suffix}")
    if not refs:
        typer.echo(f"Nothing refers to {name}", err=True)


def validate_command(
    repo: RepoOption = Path("."),
    index: IndexOption = None,
    as_json: JsonOption = False,
    strict: Annotated[bool, typer.Option("--strict", help="Fail on warnings too.")] = False,
) -> None:
    """Check the repository. Exits 1 when there are errors, so CI can run it."""
    with opened(repo, index) as (repository, idx):
        issues = validate(idx, repository)
    errors = [i for i in issues if i.severity == "error"]
    if as_json:
        _emit(
            {
                "errors": len(errors),
                "warnings": len(issues) - len(errors),
                "issues": [vars(i) for i in issues],
            }
        )
    else:
        for i in issues:
            typer.echo(f"{i.severity.upper()}\t{i.path}\t{i.code}\t{i.message}")
        typer.echo(f"{len(errors)} errors, {len(issues) - len(errors)} warnings", err=True)
    if errors or (strict and issues):
        raise typer.Exit(1)


def register(cli: typer.Typer) -> None:
    cli.command()(search)
    cli.command()(show)
    cli.command("where-used")(where_used)
    cli.command("validate")(validate_command)
