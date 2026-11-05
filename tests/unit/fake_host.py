"""A minimal host, standing in for FleetMate Web Admin: it signs people in with a
header of its own, mounts the studio under its prefix, runs the studio's
lifespan, and mounts the studio's MCP server into its own with a tool prefix."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastmcp import FastMCP

from munki_web_studio import hosting
from munki_web_studio.auth.model import Role, User
from munki_web_studio.repository.config import Settings

# The host's own people, keyed by the header its sign-in sets.
PEOPLE = {
    "ada": (User("host:ada", "Ada Host", "ada@example.org", Role.EDITOR), "csrf-ada"),
    "val": (User("host:val", "Val Viewer", None, Role.VIEWER), "csrf-val"),
}


def identity(request: Request) -> hosting.HostedIdentity | None:
    person = PEOPLE.get(request.headers.get("x-host-user", ""))
    return hosting.HostedIdentity(*person) if person else None


def build_host(settings: Settings, static_dir: Path) -> tuple[FastAPI, FastAPI, FastMCP]:
    studio = hosting.create_hosted_app(settings, identity, static_dir=static_dir)
    host_mcp = FastMCP(name="Fake host")
    host_mcp.mount(hosting.mcp_server(studio), namespace=hosting.manifest().mcp_prefix)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        async with hosting.studio_lifespan(studio):
            yield

    host = FastAPI(lifespan=lifespan)

    @host.get("/diagnostics")
    def diagnostics() -> dict[str, object]:  # pyright: ignore[reportUnusedFunction]
        return {"munki": hosting.health(studio), "jobs": hosting.job_types()}

    host.mount(hosting.manifest().prefix, studio)
    return host, studio, host_mcp
