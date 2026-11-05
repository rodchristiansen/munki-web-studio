"""The FastAPI application."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from munki_web_studio.mcp import create_mcp
from munki_web_studio.studio import STUDIO, __version__

# The built browser workspace. `pnpm build` in web/ writes here.
STATIC_DIR = Path(__file__).parent / "static"


class Health(BaseModel):
    status: str
    name: str
    version: str


def create_app(static_dir: Path = STATIC_DIR) -> FastAPI:
    mcp_app = create_mcp().http_app(path="/")
    app = FastAPI(title=STUDIO.name, version=__version__, lifespan=mcp_app.lifespan)

    @app.get("/api/health")
    def health() -> Health:  # pyright: ignore[reportUnusedFunction]
        return Health(status="ok", name=STUDIO.name, version=__version__)

    app.mount("/mcp", mcp_app)

    if (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="workspace")
    else:

        @app.get("/", response_class=PlainTextResponse)
        def missing_workspace() -> str:  # pyright: ignore[reportUnusedFunction]
            return f"{STUDIO.name} API is running. Build the workspace with `pnpm build` in web/."

    return app
