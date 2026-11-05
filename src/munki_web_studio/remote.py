"""Talking to a running studio from the command line: device sign-in, stored
tokens, and API calls with them."""

import json
import os
import time
from pathlib import Path
from typing import Any, cast

import httpx
import typer

from munki_web_studio.studio import STUDIO

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


def credentials_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / STUDIO.command / "credentials.json"


def _load() -> dict[str, dict[str, Any]]:
    try:
        return cast(dict[str, dict[str, Any]], json.loads(credentials_path().read_text()))
    except OSError, ValueError:
        return {}


def _save(data: dict[str, dict[str, Any]]) -> None:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(json.dumps(data, indent=2))


def stored_token(url: str) -> str | None:
    entry = _load().get(url.rstrip("/"))
    if not entry or entry.get("expires_at", 0) < time.time():
        return None
    return str(entry["token"])


def forget(url: str) -> None:
    data = _load()
    data.pop(url.rstrip("/"), None)
    _save(data)


# Both are module functions so tests can route requests to in-process servers.
def studio_http(url: str) -> httpx.Client:
    return httpx.Client(base_url=url.rstrip("/"), timeout=30)


def provider_http() -> httpx.Client:
    return httpx.Client(timeout=30)


def api(url: str) -> httpx.Client:
    client = studio_http(url)
    client.headers["X-Studio-Client"] = "cli"
    token = stored_token(url)
    if token:
        client.headers["Authorization"] = f"Bearer {token}"
    return client


def get(url: str, path: str, **params: Any) -> Any:
    with api(url) as client:
        response = client.get(path, params={k: v for k, v in params.items() if v is not None})
    if response.status_code == 401:
        typer.echo(f"Not signed in to {url}; run `{STUDIO.command} login --url {url}`", err=True)
        raise typer.Exit(2)
    if response.status_code >= 400:
        typer.echo(
            f"{response.status_code}: {response.json().get('detail', response.text)}", err=True
        )
        raise typer.Exit(1)
    return response.json()


def first_repository(url: str, repository: str | None) -> str:
    if repository:
        return repository
    repos = cast(list[dict[str, Any]], get(url, "/api/repositories"))
    if not repos:
        typer.echo("That studio has no repositories", err=True)
        raise typer.Exit(2)
    return str(repos[0]["id"])


def login(url: str) -> None:
    """Sign in with the provider's device flow and keep a studio token for `url`."""
    with studio_http(url) as client:
        config = cast(dict[str, Any], client.get("/api/auth/config").raise_for_status().json())
    if config["sign_in"] == "none":
        typer.echo("This studio has sign-in turned off; no login is needed.")
        return
    device_endpoint = config.get("device_authorization_endpoint")
    if not device_endpoint:
        typer.echo("The studio's sign-in provider does not offer device sign-in.", err=True)
        raise typer.Exit(1)
    with provider_http() as provider:
        start = (
            provider.post(
                device_endpoint,
                data={"client_id": config["client_id"], "scope": " ".join(config["scopes"])},
            )
            .raise_for_status()
            .json()
        )
        link = start.get("verification_uri_complete") or start["verification_uri"]
        typer.echo(f"Open {link} and enter the code {start['user_code']}")
        interval = float(start.get("interval", 5))
        deadline = time.time() + float(start.get("expires_in", 900))
        id_token = None
        while time.time() < deadline:
            reply = provider.post(
                config["token_endpoint"],
                data={
                    "grant_type": DEVICE_GRANT,
                    "device_code": start["device_code"],
                    "client_id": config["client_id"],
                },
            )
            body = cast(dict[str, Any], reply.json())
            if reply.status_code == 200 and body.get("id_token"):
                id_token = body["id_token"]
                break
            error = body.get("error")
            if error == "slow_down":
                interval += 5
            elif error != "authorization_pending":
                typer.echo(f"Sign-in failed: {error or reply.status_code}", err=True)
                raise typer.Exit(1)
            time.sleep(interval)
    if id_token is None:
        typer.echo("Sign-in timed out", err=True)
        raise typer.Exit(1)
    with studio_http(url) as client:
        reply = client.post(
            "/auth/token", json={"id_token": id_token}, headers={"X-Studio-Client": "cli"}
        )
    if reply.status_code != 200:
        typer.echo(f"The studio refused the sign-in: {reply.json().get('detail')}", err=True)
        raise typer.Exit(1)
    issued = reply.json()
    data = _load()
    data[url.rstrip("/")] = {
        "token": issued["access_token"],
        "expires_at": time.time() + issued["expires_in"],
        "name": issued["name"],
        "role": issued["role"],
    }
    _save(data)
    typer.echo(f"Signed in to {url} as {issued['name']} ({issued['role']})")
