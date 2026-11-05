"""The FastAPI application."""

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from munki_web_studio.api import audit as audit_routes
from munki_web_studio.api import changes as change_routes
from munki_web_studio.api import jobs as job_routes
from munki_web_studio.api import manifests, read
from munki_web_studio.api import workers as worker_routes
from munki_web_studio.audit import AuditLog
from munki_web_studio.auth import routes as auth_routes
from munki_web_studio.auth.mcp import StudioTokenVerifier
from munki_web_studio.auth.model import Role
from munki_web_studio.auth.sessions import SessionStore
from munki_web_studio.changes.apply import recover
from munki_web_studio.changes.store import ChangeSetStore
from munki_web_studio.jobs.runner import Runner
from munki_web_studio.jobs.store import JobStore
from munki_web_studio.mcp import create_mcp
from munki_web_studio.repository.config import Settings, load_settings
from munki_web_studio.repository.index import Index
from munki_web_studio.repository.scanner import scan
from munki_web_studio.services import Services
from munki_web_studio.studio import STUDIO, __version__

logger = logging.getLogger(__name__)

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


async def _watch(settings: Settings, index: Index) -> None:
    """Rescan on an interval so edits from other tools show up without a restart."""
    while True:
        await asyncio.sleep(settings.rescan_seconds)
        for repo in settings.repositories:
            try:
                await asyncio.to_thread(scan, repo, index)
            except Exception:
                logger.exception("Rescan of %s failed", repo.id)


def create_app(
    settings: Settings | None = None,
    static_dir: Path = STATIC_DIR,
    oidc_http: httpx.Client | None = None,
) -> FastAPI:
    settings = settings if settings is not None else settings_from_environment()
    services = Services(settings)
    holder: dict[str, SessionStore] = {}
    verifier = StudioTokenVerifier(lambda: holder.get("store")) if settings.auth.enabled else None
    mcp_app = create_mcp(services, auth=verifier).http_app(path="/")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        index = Index(settings.state_dir / "index.sqlite")
        store = SessionStore(settings.state_dir / "state.sqlite")
        holder["store"] = store
        audit = AuditLog(settings.state_dir / "audit.sqlite")
        app.state.audit = audit
        drafts = ChangeSetStore(settings.state_dir / "drafts.sqlite")
        app.state.changes = drafts
        services.changes = drafts
        jobs = JobStore(settings.state_dir / "jobs.sqlite")
        app.state.jobs = jobs
        services.audit = audit
        app.state.journal_dir = settings.state_dir / "journal"
        app.state.auth = auth_routes.AuthState.create(settings.auth, store, oidc_http)
        app.state.settings = settings
        app.state.index = index
        services.index = index
        for repo in settings.repositories:
            for report in recover(repo, app.state.journal_dir):
                if report.conflicts:
                    logger.error(
                        "Unfinished save %s blocked by edits to %s",
                        report.journal,
                        report.conflicts,
                    )
            scan(repo, index)
        # The runner starts after the first scan, so jobs see a current index.
        runner = Runner(jobs, settings, index)
        app.state.runner = runner
        runner.start()
        watcher = (
            asyncio.create_task(_watch(settings, index)) if settings.rescan_seconds > 0 else None
        )
        try:
            async with mcp_app.lifespan(app):
                yield
        finally:
            if watcher:
                watcher.cancel()
            runner.stop()
            jobs.close()
            index.close()
            store.close()
            audit.close()
            drafts.close()

    app = FastAPI(title=STUDIO.name, version=__version__, lifespan=lifespan)
    # Refuse requests addressed to any other host name, so a web page cannot use
    # DNS rebinding to read the API through a browser on this machine.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))

    @app.get("/api/health")
    def health() -> Health:  # pyright: ignore[reportUnusedFunction]
        return Health(status="ok", name=STUDIO.name, version=__version__)

    viewer = [Depends(auth_routes.require(Role.VIEWER))]
    app.middleware("http")(audit_routes.record_changes)
    app.include_router(auth_routes.router)
    app.include_router(audit_routes.router)
    app.include_router(read.router, dependencies=viewer)
    app.include_router(manifests.router, dependencies=viewer)
    app.include_router(change_routes.router)
    app.include_router(change_routes.fields_router, dependencies=viewer)
    app.include_router(job_routes.router)
    app.include_router(worker_routes.router)
    app.include_router(worker_routes.settings_router)
    # With sign-in on, MCP requests need a studio bearer token (see auth/mcp.py).
    app.mount("/mcp", mcp_app)

    if (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="workspace")
    else:

        @app.get("/", response_class=PlainTextResponse, include_in_schema=False)
        def missing_workspace() -> str:  # pyright: ignore[reportUnusedFunction]
            return f"{STUDIO.name} API is running. Build the workspace with `pnpm build` in web/."

    return app
