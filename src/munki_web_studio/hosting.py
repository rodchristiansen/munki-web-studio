"""The hosting contract (docs/hosting.md): what a host such as FleetMate Web
Admin needs to run this studio inside its own app.

A host builds the studio with `create_hosted_app`, mounts it under the prefix
from `manifest()`, runs `studio_lifespan` inside its own lifespan, and mounts
`mcp_server` into its own MCP server with the manifest's prefix. The host signs
people in and hands the studio each request's user through the identity hook.
Nothing here imports the host; the host imports this.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.auth import TokenVerifier

from munki_web_studio.app import STATIC_DIR, create_app
from munki_web_studio.auth.routes import HostedIdentity, IdentityHook
from munki_web_studio.jobs.runner import OPERATIONS, WORKER_MAX_AGE
from munki_web_studio.repository.config import ConfigError, Settings, parse_settings
from munki_web_studio.studio import STUDIO, __version__

# Bumped when anything a host relies on here changes incompatibly.
CONTRACT_VERSION = 1

__all__ = [
    "CONTRACT_VERSION",
    "HostedIdentity",
    "IdentityHook",
    "Manifest",
    "create_hosted_app",
    "health",
    "job_types",
    "manifest",
    "mcp_server",
    "studio_lifespan",
    "validate_settings",
]


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    contract_version: int
    platform: str
    package: str
    prefix: str
    mcp_prefix: str
    accent: str
    icon: str
    ui: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def manifest() -> Manifest:
    """Who this studio is, and where a host should put it."""
    return Manifest(
        name=STUDIO.name,
        version=__version__,
        contract_version=CONTRACT_VERSION,
        platform=STUDIO.platform,
        package=STUDIO.package,
        prefix=f"/{STUDIO.prefix}",
        mcp_prefix=STUDIO.prefix,
        accent=STUDIO.accent,
        icon=STUDIO.icon,
        ui=str(STATIC_DIR),
    )


def create_hosted_app(
    settings: Settings,
    identity: IdentityHook,
    *,
    mcp_auth: TokenVerifier | None = None,
    static_dir: Path = STATIC_DIR,
) -> FastAPI:
    """The studio's API, MCP server and browser workspace, with sign-in and host
    checks left to the host. Mount it with `host.mount(manifest().prefix, app)`."""
    return create_app(settings, static_dir=static_dir, identity=identity, mcp_auth=mcp_auth)


@asynccontextmanager
async def studio_lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Run the studio's startup and shutdown inside the host's lifespan: mounted
    apps do not get their own."""
    async with app.router.lifespan_context(app):
        yield


def mcp_server(app: FastAPI) -> FastMCP:
    """The studio's MCP server, for `host_mcp.mount(server, namespace=...)`. Hosted
    tools read the caller from the host's access token, which must carry the
    person's subject and the claims `name` and `role` (Viewer to Administrator)."""
    return app.state.mcp


def validate_settings(raw: dict[str, Any], base: Path) -> tuple[Settings | None, str | None]:
    """Parse the studio's settings the way the studio does, for a host's settings
    page. Returns the settings, or the reason they were refused."""
    try:
        return parse_settings(raw, base), None
    except ConfigError as error:
        return None, str(error)


def job_types() -> list[dict[str, Any]]:
    """Every job this studio can run, and the capability each needs, for the
    host's combined Activity view."""
    return [
        {
            "name": op.name,
            "title": op.title,
            "requirements": list(op.requirements),
            "role": op.role.label,
            "runs_on": "worker" if op.remote else "studio",
        }
        for op in OPERATIONS.values()
    ]


def health(app: FastAPI) -> dict[str, Any]:
    """Status for the host's diagnostics page. Call it while the studio is running."""
    state = app.state
    settings: Settings = state.settings
    store = state.jobs
    workers = [
        {"id": w.id, "online": store.online(w, WORKER_MAX_AGE), "capabilities": w.capabilities}
        for w in store.workers()
    ]
    repositories = [
        {
            "id": repo.id,
            "name": repo.name,
            "files": len(state.index.all(repo.id)),
            "writable": repo.writable,
            "live": repo.live,
        }
        for repo in settings.repositories
    ]
    return {
        "status": "ok",
        "name": STUDIO.name,
        "version": __version__,
        "contract_version": CONTRACT_VERSION,
        "repositories": repositories,
        "workers": workers,
    }
