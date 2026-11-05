"""The FastAPI application."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from munki_web_studio.api import manifests, read
from munki_web_studio.mcp import create_mcp
from munki_web_studio.repository.config import Settings, load_settings
from munki_web_studio.repository.index import Index
from munki_web_studio.repository.scanner import scan
from munki_web_studio.services import Services
from munki_web_studio.studio import STUDIO, __version__

# The built browser workspace. `pnpm build` in web/ writes here.
STATIC_DIR = Path(__file__).parent / "static"


class Health(BaseModel):
    status: str
    name: str
    version: str


def settings_from_environment() -> Settings:
    """Settings from the config file named by the studio's environment variable."""
    config = os.environ.get(STUDIO.config_env)
    if config:
        return load_settings(Path(config))
    return Settings(repositories=(), state_dir=Path(".state").resolve())


def create_app(settings: Settings | None = None, static_dir: Path = STATIC_DIR) -> FastAPI:
    settings = settings if settings is not None else settings_from_environment()
    services = Services(settings)
    mcp_app = create_mcp(services).http_app(path="/")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        index = Index(settings.state_dir / "index.sqlite")
        app.state.settings = settings
        app.state.index = index
        services.index = index
        for repo in settings.repositories:
            scan(repo, index)
        async with mcp_app.lifespan(app):
            yield
        index.close()

    app = FastAPI(title=STUDIO.name, version=__version__, lifespan=lifespan)
    # Refuse requests addressed to any other host name, so a web page cannot use
    # DNS rebinding to read the API through a browser on this machine.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))

    @app.get("/api/health")
    def health() -> Health:  # pyright: ignore[reportUnusedFunction]
        return Health(status="ok", name=STUDIO.name, version=__version__)

    app.include_router(read.router)
    app.include_router(manifests.router)
    app.mount("/mcp", mcp_app)

    if (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="workspace")
    else:

        @app.get("/", response_class=PlainTextResponse, include_in_schema=False)
        def missing_workspace() -> str:  # pyright: ignore[reportUnusedFunction]
            return f"{STUDIO.name} API is running. Build the workspace with `pnpm build` in web/."

    return app
