"""The command line."""

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
) -> None:
    """Run the web app, API, and MCP server."""
    import uvicorn

    from munki_web_studio.app import create_app

    uvicorn.run(create_app(), host=host, port=port)


def main() -> None:
    cli()
