"""Record every change request, and let Administrators read the log."""

import re
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel

from munki_web_studio.audit import AuditLog, as_dict
from munki_web_studio.auth.model import Role
from munki_web_studio.auth.routes import SAFE_METHODS, current_session, require

_REPO = re.compile(r"^/api/repositories/([^/]+)")

router = APIRouter(prefix="/api", tags=["audit"])


class AuditEntryInfo(BaseModel):
    id: int
    at: str
    actor: str
    actor_name: str
    interface: str
    action: str
    repo_id: str | None
    target: str | None
    outcome: str
    detail: dict[str, Any]


def interface_of(request: Request) -> str:
    client = request.headers.get("x-studio-client", "").lower()
    return client if client in ("cli", "mcp") else "web"


def audit_log(request: Request) -> AuditLog:
    return request.app.state.audit


async def record_changes(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """HTTP middleware: one audit entry for every request that is not a read."""
    path = request.url.path
    # Workers check in every second; their routes record what matters themselves.
    if request.method in SAFE_METHODS or path.startswith(("/mcp", "/api/worker/")):
        return await call_next(request)
    session = current_session(request)
    response = await call_next(request)
    match = _REPO.match(path)
    audit_log(request).record(
        actor=session.user.subject if session else "anonymous",
        actor_name=session.user.name if session else "Not signed in",
        interface=interface_of(request),
        action=f"{request.method} {path}",
        outcome="ok" if response.status_code < 400 else f"refused {response.status_code}",
        repo_id=match.group(1) if match else None,
        detail={"status": response.status_code},
    )
    return response


@router.get("/audit")
def read_audit(
    request: Request,
    _: Annotated[object, Depends(require(Role.ADMINISTRATOR))],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before: Annotated[int | None, Query(ge=1)] = None,
) -> list[AuditEntryInfo]:
    """Newest first. Page back with `before` set to the last id you saw."""
    return [
        AuditEntryInfo(**as_dict(e)) for e in audit_log(request).entries(limit=limit, before=before)
    ]
