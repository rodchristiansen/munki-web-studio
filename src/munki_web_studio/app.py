"""The FastAPI application."""

from fastapi import FastAPI
from pydantic import BaseModel

from munki_web_studio.mcp import create_mcp
from munki_web_studio.studio import STUDIO, __version__


class Health(BaseModel):
    status: str
    name: str
    version: str


def create_app() -> FastAPI:
    mcp_app = create_mcp().http_app(path="/")
    app = FastAPI(title=STUDIO.name, version=__version__, lifespan=mcp_app.lifespan)

    @app.get("/api/health")
    def health() -> Health:  # pyright: ignore[reportUnusedFunction]
        return Health(status="ok", name=STUDIO.name, version=__version__)

    app.mount("/mcp", mcp_app)
    return app
