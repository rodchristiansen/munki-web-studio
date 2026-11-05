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

from munki_web_studio import platform, remote, tagged
from munki_web_studio.codec import ParseError, load
from munki_web_studio.codec.model import MAX_FILE_BYTES
from munki_web_studio.repository.config import Repository
from munki_web_studio.repository.index import Index, IndexedObject
from munki_web_studio.repository.paths import PathEscapeError, normalize, read_contained
from munki_web_studio.repository.references import Issue, Reference, graph_for, validate
from munki_web_studio.repository.scanner import scan
from munki_web_studio.studio import STUDIO

RepoOption = Annotated[
    Path, typer.Option("--repo", "-r", help="Repository folder.", file_okay=False, exists=True)
]
IndexOption = Annotated[
    Path | None, typer.Option("--index", help="Keep the scan index in this file between runs.")
]
JsonOption = Annotated[bool, typer.Option("--json", help="Print tagged JSON instead of text.")]
UrlOption = Annotated[
    str | None,
    typer.Option(
        "--url",
        envvar=f"{STUDIO.command.upper()}_URL",
        help="Ask a running studio instead of reading a folder. Sign in first with login.",
    ),
]
RemoteRepoOption = Annotated[
    str | None,
    typer.Option("--repository", help="Repository id on the studio; the first if omitted."),
]


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
    url: UrlOption = None,
    repository_id: RemoteRepoOption = None,
) -> None:
    """Find pkginfo, manifests, and catalogs."""
    if url:
        repo_id = remote.first_repository(url, repository_id)
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = remote.get(
                url,
                f"/api/repositories/{repo_id}/objects",
                q=query,
                kind=kind,
                limit=500,
                offset=offset,
            )
            rows += page["items"]
            offset += 500
            if offset >= page["total"]:
                break
        _print_search(rows, as_json)
        return
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
    _print_search([_row(o) for o in found], as_json)


def _print_search(rows: list[dict[str, Any]], as_json: bool) -> None:
    if as_json:
        _emit(
            [
                {k: r.get(k) for k in ("path", "kind", "status", "name", "version", "catalogs")}
                for r in rows
            ]
        )
        return
    for r in rows:
        label = " ".join(v for v in (r.get("name"), r.get("version")) if v) or "(unreadable)"
        typer.echo(f"{r['path']}\t{label}")
    if not rows:
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
    url: UrlOption = None,
    repository_id: RemoteRepoOption = None,
) -> None:
    """List the manifests and packages that refer to a name."""
    if url:
        repo_id = remote.first_repository(url, repository_id)
        found = remote.get(url, f"/api/repositories/{repo_id}/where-used/{name}")
        refs = [Reference(r["source"], r["field"], r["target"], r["condition"]) for r in found]
    else:
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
    url: UrlOption = None,
    repository_id: RemoteRepoOption = None,
) -> None:
    """Check the repository. Exits 1 when there are errors, so CI can run it."""
    if url:
        repo_id = remote.first_repository(url, repository_id)
        report = remote.get(url, f"/api/repositories/{repo_id}/validation")
        issues = [Issue(**i) for i in report["issues"]]
    else:
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


def login_command(
    url: Annotated[
        str,
        typer.Option(
            "--url", envvar=f"{STUDIO.command.upper()}_URL", help="The studio to sign in to."
        ),
    ],
) -> None:
    """Sign in to a running studio from this terminal."""
    remote.login(url)


def logout_command(
    url: Annotated[str, typer.Option("--url", envvar=f"{STUDIO.command.upper()}_URL")],
) -> None:
    """Sign out of a studio and forget its token."""
    with remote.api(url) as client:
        client.post("/auth/logout")
    remote.forget(url)
    typer.echo(f"Signed out of {url}")


def token_command(
    url: Annotated[str, typer.Option("--url", envvar=f"{STUDIO.command.upper()}_URL")],
) -> None:
    """Print the stored bearer token, for an MCP client's configuration."""
    token = remote.stored_token(url)
    if not token:
        typer.echo(f"Not signed in to {url}", err=True)
        raise typer.Exit(2)
    typer.echo(token)


def register(cli: typer.Typer) -> None:
    from munki_web_studio.drafts import drafts

    cli.add_typer(drafts, name="drafts")
    from munki_web_studio.worker import worker_cli

    cli.add_typer(worker_cli, name="worker")
    cli.command("login")(login_command)
    cli.command("logout")(logout_command)
    cli.command("token")(token_command)
    cli.command()(search)
    cli.command()(show)
    cli.command("where-used")(where_used)
    cli.command("validate")(validate_command)
