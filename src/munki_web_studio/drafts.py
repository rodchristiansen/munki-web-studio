"""Draft commands: work with change sets on a running studio.

    mwsctl drafts create "Move Editor to production"
    mwsctl drafts edit <id> pkgsinfo/apps/Editor-2.10.plist --set catalogs=testing,production
    mwsctl drafts diff <id>
    mwsctl drafts save <id>

Drafts made here are the same drafts the browser shows, so they can be started in
one and finished in the other. Saving shows the diff and asks first.
"""

import json
from typing import Annotated, Any, cast

import typer

from munki_web_studio import remote
from munki_web_studio.studio import STUDIO

drafts = typer.Typer(help="Create, review, and save draft change sets on a studio.")

Url = Annotated[
    str,
    typer.Option(
        "--url",
        envvar=f"{STUDIO.command.upper()}_URL",
        help="The studio. Sign in with login.",
    ),
]
RepoId = Annotated[
    str | None,
    typer.Option("--repository", help="Repository id; the first if omitted."),
]
ChangeId = Annotated[str, typer.Argument(help="Change set id, from drafts list.")]
AsJson = Annotated[bool, typer.Option("--json", help="Print JSON.")]


def _base(url: str, repository: str | None) -> str:
    return f"/api/repositories/{remote.first_repository(url, repository)}/changesets"


def _print_diffs(change_set: dict[str, Any]) -> None:
    for f in change_set["files"]:
        if f["conflict"] or f["error"]:
            typer.secho(f"# {f['path']}: {f['error']}", fg=typer.colors.YELLOW, err=True)
        elif f["diff"]:
            typer.echo(f["diff"], nl=not f["diff"].endswith("\n"))


def _summary(change_set: dict[str, Any]) -> str:
    files = change_set["files"]
    blocked = sum(1 for f in files if f["conflict"] or f["error"])
    note = f", {blocked} need attention" if blocked else ""
    return f"{change_set['id']}\t{change_set['title']}\t{len(files)} files{note}"


@drafts.command("list")
def list_command(url: Url, repository: RepoId = None, as_json: AsJson = False) -> None:
    """Your open drafts."""
    found = cast(list[dict[str, Any]], remote.get(url, _base(url, repository)))
    if as_json:
        typer.echo(json.dumps(found, indent=2))
        return
    for change_set in found:
        typer.echo(_summary(change_set))
    if not found:
        typer.echo("No open drafts", err=True)


@drafts.command()
def create(
    title: Annotated[str, typer.Argument(help="What the change is for.")],
    url: Url,
    repository: RepoId = None,
) -> None:
    """Start a new draft and print its id."""
    created = remote.call(url, "POST", _base(url, repository), {"title": title})
    typer.echo(created["id"])


@drafts.command()
def show(change_id: ChangeId, url: Url, repository: RepoId = None, as_json: AsJson = False) -> None:
    """Each file a draft changes, with its edits and whether it still applies."""
    change_set = remote.get(url, f"{_base(url, repository)}/{change_id}")
    if as_json:
        typer.echo(json.dumps(change_set, indent=2))
        return
    typer.echo(_summary(change_set))
    for f in change_set["files"]:
        state = "conflict" if f["conflict"] else "error" if f["error"] else "ok"
        typer.echo(f"  {state}\t{f['path']}\t{len(f['changes'])} edits")


@drafts.command()
def diff(change_id: ChangeId, url: Url, repository: RepoId = None) -> None:
    """The exact diff saving the draft would make."""
    _print_diffs(remote.get(url, f"{_base(url, repository)}/{change_id}"))


def _parse(name: str, text: str, kind: str | None) -> Any:
    if kind is None:
        typer.echo(
            f"{name} is not an editable field; see {STUDIO.command} drafts fields",
            err=True,
        )
        raise typer.Exit(2)
    if kind == "boolean":
        lowered = text.strip().lower()
        if lowered not in ("true", "false", "yes", "no"):
            typer.echo(f"{name} takes true or false", err=True)
            raise typer.Exit(2)
        return lowered in ("true", "yes")
    if kind == "string-list":
        return [item.strip() for item in text.split(",") if item.strip()]
    return text


@drafts.command()
def fields(url: Url) -> None:
    """The pkginfo fields edit can set, and the type each takes."""
    for name, kind in cast(dict[str, str], remote.get(url, "/api/fields")).items():
        typer.echo(f"{name}\t{kind}")


@drafts.command()
def edit(
    change_id: ChangeId,
    path: Annotated[str, typer.Argument(help="Relative path of a pkginfo file.")],
    url: Url,
    set_: Annotated[
        list[str] | None,
        typer.Option(
            "--set",
            help="field=value. Lists are comma-separated; booleans true or false.",
        ),
    ] = None,
    unset: Annotated[list[str] | None, typer.Option("--unset", help="Remove a field.")] = None,
    repository: RepoId = None,
) -> None:
    """Add pkginfo field edits to a draft."""
    kinds = cast(dict[str, str], remote.get(url, "/api/fields"))
    values: dict[str, Any] = {}
    for item in set_ or []:
        name, sep, text = item.partition("=")
        if not sep:
            typer.echo(f"--set takes field=value, not {item!r}", err=True)
            raise typer.Exit(2)
        values[name] = _parse(name, text, kinds.get(name))
    for name in unset or []:
        values[name] = None
    if not values:
        typer.echo("Nothing to change; give --set or --unset", err=True)
        raise typer.Exit(2)
    base = _base(url, repository)
    repo_path = base.removesuffix("/changesets")
    current = remote.get(url, f"{repo_path}/objects/{path}")
    updated = remote.call(
        url,
        "PATCH",
        f"{base}/{change_id}/pkginfo/{path}",
        {"base_revision": current["revision"], "fields": values},
    )
    typer.echo(_summary(updated))


@drafts.command()
def save(
    change_id: ChangeId,
    url: Url,
    yes: Annotated[bool, typer.Option("--yes", help="Save without asking.")] = False,
    repository: RepoId = None,
) -> None:
    """Show a draft's diff, ask, and write it into the repository."""
    repo_id = remote.first_repository(url, repository)
    base = _base(url, repo_id)
    change_set = remote.get(url, f"{base}/{change_id}")
    repos = cast(list[dict[str, Any]], remote.get(url, "/api/repositories"))
    live = any(r["id"] == repo_id and r.get("live") for r in repos)
    _print_diffs(change_set)
    if any(f["conflict"] or f["error"] for f in change_set["files"]):
        typer.echo("Some files changed or cannot be edited; fix the draft first", err=True)
        raise typer.Exit(1)
    count = len(change_set["files"])
    question = f"Save {count} {'file' if count == 1 else 'files'} to {repo_id}?"
    if live:
        question = f"{repo_id} is live: clients see this at once. {question}"
    if not yes and not typer.confirm(question):
        raise typer.Exit(1)
    remote.call(url, "POST", f"{base}/{change_id}/save", {"acknowledge_live": live})
    typer.echo(f"Saved {count} {'file' if count == 1 else 'files'}")


@drafts.command()
def discard(
    change_id: ChangeId,
    url: Url,
    yes: Annotated[bool, typer.Option("--yes", help="Discard without asking.")] = False,
    repository: RepoId = None,
) -> None:
    """Throw a draft away."""
    if not yes and not typer.confirm(f"Discard draft {change_id}?"):
        raise typer.Exit(1)
    remote.call(url, "DELETE", f"{_base(url, repository)}/{change_id}")
    typer.echo("Discarded")
