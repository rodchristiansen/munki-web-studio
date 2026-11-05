from typer.testing import CliRunner

from munki_web_studio import STUDIO, __version__
from munki_web_studio.cli import cli

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"{STUDIO.command} {__version__}"


def test_help_lists_serve() -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "serve" in result.stdout
