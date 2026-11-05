from pathlib import Path

import pytest
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


def test_serve_refuses_an_open_address_without_sign_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn
    from typer.testing import CliRunner

    from munki_web_studio.cli import UNAUTHENTICATED_ENV, cli

    config = tmp_path / "studio.toml"
    config.write_text(f'state_dir = "{(tmp_path / "s").as_posix()}"\n')
    started: list[str] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: started.append(host))
    monkeypatch.delenv(UNAUTHENTICATED_ENV, raising=False)
    everywhere = "0.0.0.0"  # noqa: S104 - the case under test

    def serve(host: str) -> int:
        result = CliRunner().invoke(cli, ["serve", "--host", host, "--config", str(config)])
        assert result.exit_code == 0 or "without sign-in" in result.stderr
        return result.exit_code

    assert serve(everywhere) == 2 and not started
    assert serve("127.0.0.1") == 0
    monkeypatch.setenv(UNAUTHENTICATED_ENV, "1")
    assert serve(everywhere) == 0
    assert started == ["127.0.0.1", everywhere]
