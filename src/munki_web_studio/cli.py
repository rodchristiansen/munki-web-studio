"""The command line."""

from pathlib import Path
from typing import Annotated

import typer

from munki_web_studio.studio import STUDIO, __version__

cli = typer.Typer(name=STUDIO.command, help=f"{STUDIO.name} command line.", no_args_is_help=True)


def _show_version(value: bool) -> None:
    if value:
        typer.echo(f"{STUDIO.command} {__version__}")
        raise typer.Exit


@cli.callback()
def root(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_show_version, is_eager=True, help="Show the version."),
    ] = False,
) -> None:
    """Work with a repository from the terminal."""


@cli.command()
def serve(
    host: Annotated[str, typer.Option(help="Address to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 3186,
    config: Annotated[
        Path | None,
        typer.Option(help=f"Repository config file. Defaults to ${STUDIO.config_env}."),
    ] = None,
) -> None:
    """Run the web app, API, and MCP server."""
    import uvicorn

    from munki_web_studio.app import create_app
    from munki_web_studio.repository.config import load_settings

    settings = load_settings(config) if config else None
    uvicorn.run(create_app(settings), host=host, port=port)


@cli.command()
def openapi(
    output: Annotated[Path | None, typer.Option(help="Write here instead of stdout.")] = None,
) -> None:
    """Print the API's OpenAPI schema."""
    import json

    from munki_web_studio.app import create_app

    text = json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"
    if output:
        output.write_text(text, encoding="utf-8")
    else:
        typer.echo(text, nl=False)


@cli.command()
def mcp(
    repo: Annotated[
        Path | None,
        typer.Option("--repo", "-r", help="Serve this repository folder.", file_okay=False),
    ] = None,
    config: Annotated[
        Path | None, typer.Option(help="Serve the repositories in this config file.")
    ] = None,
) -> None:
    """Run the MCP server over stdio, for assistants on this machine.

    With --config, drafts go into the same state folder as `serve`, so they show
    up for review in the studio. With --repo they last only as long as the server.
    """
    import tempfile

    from munki_web_studio.audit import AuditLog
    from munki_web_studio.changes.store import ChangeSetStore
    from munki_web_studio.mcp import create_mcp
    from munki_web_studio.repository.config import Repository, Settings, load_settings
    from munki_web_studio.repository.index import Index
    from munki_web_studio.repository.scanner import scan
    from munki_web_studio.services import Services

    with tempfile.TemporaryDirectory() as scratch:
        if config:
            settings = load_settings(config)
        else:
            root = (repo or Path(".")).resolve()
            settings = Settings(
                repositories=(Repository(id="local", name=root.name, root=root),),
                state_dir=Path(scratch),
            )
        index = Index(settings.state_dir / "index.sqlite")
        for repository in settings.repositories:
            scan(repository, index)
        changes = ChangeSetStore(settings.state_dir / "drafts.sqlite")
        audit = AuditLog(settings.state_dir / "audit.sqlite")
        try:
            create_mcp(Services(settings, index, changes, audit)).run(show_banner=False)
        finally:
            changes.close()
            audit.close()
            index.close()


from munki_web_studio import local  # noqa: E402

local.register(cli)


def main() -> None:
    cli()
