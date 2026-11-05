"""Sign-in routes and the checks every API route runs."""

import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from munki_web_studio.auth.config import AuthSettings
from munki_web_studio.auth.model import Role, User
from munki_web_studio.auth.oidc import OIDCClient, SignInError, pkce_pair
from munki_web_studio.auth.sessions import Session, SessionStore

COOKIE = "studio_session"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOCAL_USER = User(subject="local", name="Local administrator", email=None, role=Role.ADMINISTRATOR)


@dataclass
class AuthState:
    settings: AuthSettings
    store: SessionStore
    client: OIDCClient | None
    # With sign-in off, one token per process still guards against cross-site posts.
    local_csrf: str = ""

    @classmethod
    def create(
        cls, settings: AuthSettings, store: SessionStore, http: httpx.Client | None = None
    ) -> AuthState:
        client = None
        if settings.enabled:
            env = settings.client_secret_env
            client = OIDCClient(settings, os.environ.get(env) if env else None, http)
        return cls(settings, store, client, secrets.token_urlsafe(32))


class SessionInfo(BaseModel):
    name: str
    email: str | None
    role: str
    csrf_token: str
    sign_in: str


def _auth(request: Request) -> AuthState:
    return request.app.state.auth


def current_session(request: Request) -> Session | None:
    auth = _auth(request)
    if not auth.settings.enabled:
        return Session(LOCAL_USER, auth.local_csrf)
    token = request.cookies.get(COOKIE)
    return auth.store.get(token) if token else None


def require(role: Role) -> Callable[[Request], Session]:
    """A dependency: the caller is signed in with at least `role`, and unsafe
    methods carry the session's CSRF token."""

    def check(request: Request) -> Session:
        session = current_session(request)
        if session is None:
            raise HTTPException(401, "Sign in required", headers={"X-Sign-In": "/auth/login"})
        if session.user.role < role:
            raise HTTPException(403, f"This needs the {role.label} role")
        if request.method not in SAFE_METHODS:
            sent = request.headers.get(CSRF_HEADER, "")
            if not sent or not secrets.compare_digest(sent, session.csrf):
                raise HTTPException(403, "Missing or wrong CSRF token")
        return session

    return check


router = APIRouter(tags=["auth"])


@router.get("/api/session")
def get_session(request: Request) -> SessionInfo:
    """Who is signed in, and the token to send with changes."""
    session = current_session(request)
    if session is None:
        raise HTTPException(401, "Sign in required", headers={"X-Sign-In": "/auth/login"})
    mode = "oidc" if _auth(request).settings.enabled else "none"
    return SessionInfo(
        name=session.user.name,
        email=session.user.email,
        role=session.user.role.label,
        csrf_token=session.csrf,
        sign_in=mode,
    )


def _safe_next(next_path: str | None) -> str:
    if (
        not next_path
        or not next_path.startswith("/")
        or next_path.startswith("//")
        or "\\" in next_path
    ):
        return "/"
    return next_path


@router.get("/auth/login", include_in_schema=False)
def login(request: Request, next: str | None = None) -> RedirectResponse:
    auth = _auth(request)
    if not auth.settings.enabled or auth.client is None:
        return RedirectResponse(_safe_next(next), status_code=303)
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier, challenge = pkce_pair()
    auth.store.start_login(state, nonce, verifier, _safe_next(next))
    return RedirectResponse(auth.client.authorize_url(state, nonce, challenge), status_code=303)


@router.get("/auth/callback", include_in_schema=False)
def callback(request: Request, code: str = "", state: str = "", error: str = "") -> Response:
    auth = _auth(request)
    if not auth.settings.enabled or auth.client is None:
        raise HTTPException(404)
    if error:
        raise HTTPException(400, f"Sign-in was not completed: {error}")
    log = request.app.state.audit
    pending = auth.store.finish_login(state) if state else None
    if pending is None or not code:
        log.record(
            actor="anonymous",
            actor_name="Not signed in",
            interface="web",
            action="sign-in",
            outcome="refused",
            detail={"reason": "unknown or expired state"},
        )
        raise HTTPException(400, "This sign-in link has expired or was already used; start again")
    try:
        claims = auth.client.exchange(code, pending.verifier, pending.nonce)
    except SignInError as failure:
        log.record(
            actor="anonymous",
            actor_name="Not signed in",
            interface="web",
            action="sign-in",
            outcome="refused",
            detail={"reason": str(failure)},
        )
        raise HTTPException(400, str(failure)) from failure
    role = auth.settings.role_for(claims.values)
    if role is None:
        log.record(
            actor=claims.subject,
            actor_name=claims.name,
            interface="web",
            action="sign-in",
            outcome="refused",
            detail={"reason": "no role"},
        )
        raise HTTPException(403, "Your account has no role in this studio; ask an administrator")
    log.record(
        actor=claims.subject,
        actor_name=claims.name,
        interface="web",
        action="sign-in",
        detail={"role": role.label},
    )
    token, _ = auth.store.create(
        User(claims.subject, claims.name, claims.email, role), auth.settings.session_hours
    )
    response = RedirectResponse(pending.next, status_code=303)
    response.set_cookie(
        COOKIE,
        token,
        max_age=int(auth.settings.session_hours * 3600),
        httponly=True,
        secure=auth.settings.public_url.startswith("https://"),
        samesite="lax",
        path="/",
    )
    return response


@router.post("/auth/logout", include_in_schema=False)
def logout(request: Request, session: Session = Depends(require(Role.VIEWER))) -> Response:  # noqa: B008
    del session
    token = request.cookies.get(COOKIE)
    if token:
        _auth(request).store.delete(token)
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, path="/")
    return response


def sign_in_redirect(path: str) -> str:
    return f"/auth/login?next={quote(path, safe='/')}"
