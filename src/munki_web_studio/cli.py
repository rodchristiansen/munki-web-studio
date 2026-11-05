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


def main() -> None:
    cli()
