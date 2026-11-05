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
from munki_web_studio.auth.model import LOCAL_USER, Role, User
from munki_web_studio.auth.oidc import OIDCClient, SignInError, pkce_pair
from munki_web_studio.auth.sessions import Session, SessionStore

COOKIE = "studio_session"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class HostedIdentity:
    """Who a host says is making a request, and the CSRF token the host gave
    that person's browser. The studio checks roles and the token as usual."""

    user: User
    csrf_token: str


# Given a request, the host's signed-in person, or None when nobody is.
IdentityHook = Callable[[Request], HostedIdentity | None]


@dataclass
class AuthState:
    settings: AuthSettings
    store: SessionStore
    client: OIDCClient | None
    # With sign-in off, one token per process still guards against cross-site posts.
    local_csrf: str = ""
    # Set when a host such as FleetMate signs people in instead of the studio.
    identity: IdentityHook | None = None

    @classmethod
    def create(
        cls,
        settings: AuthSettings,
        store: SessionStore,
        http: httpx.Client | None = None,
        identity: IdentityHook | None = None,
    ) -> AuthState:
        client = None
        if settings.enabled and identity is None:
            env = settings.client_secret_env
            client = OIDCClient(settings, os.environ.get(env) if env else None, http)
        return cls(settings, store, client, secrets.token_urlsafe(32), identity)


class SessionInfo(BaseModel):
    name: str
    email: str | None
    role: str
    csrf_token: str
    sign_in: str


def _auth(request: Request) -> AuthState:
    return request.app.state.auth


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() or None if scheme.lower() == "bearer" else None


def current_session(request: Request) -> Session | None:
    """The browser's cookie session, or the session a bearer token names."""
    auth = _auth(request)
    if auth.identity is not None:
        hosted = auth.identity(request)
        return Session(hosted.user, hosted.csrf_token) if hosted else None
    if not auth.settings.enabled:
        return Session(LOCAL_USER, auth.local_csrf)
    token = bearer_token(request) or request.cookies.get(COOKIE)
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
        # A bearer token is never sent by a browser on its own, so it needs no CSRF token.
        auth = _auth(request)
        uses_bearer = (
            auth.identity is None and bearer_token(request) is not None and auth.settings.enabled
        )
        if request.method not in SAFE_METHODS and not uses_bearer:
            sent = request.headers.get(CSRF_HEADER, "")
            if not sent or not secrets.compare_digest(sent, session.csrf):
                raise HTTPException(403, "Missing or wrong CSRF token")
        return session

    return check


def not_hosted(request: Request) -> None:
    """The studio's own sign-in routes do not exist when a host signs people in."""
    if _auth(request).identity is not None:
        raise HTTPException(404, "Sign-in is handled by the host")


router = APIRouter(tags=["auth"])
NotHosted = Depends(not_hosted)


@router.get("/api/session")
def get_session(request: Request) -> SessionInfo:
    """Who is signed in, and the token to send with changes."""
    session = current_session(request)
    if session is None:
        raise HTTPException(401, "Sign in required", headers={"X-Sign-In": "/auth/login"})
    auth = _auth(request)
    mode = "host" if auth.identity else "oidc" if auth.settings.enabled else "none"
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


@router.get("/auth/login", include_in_schema=False, dependencies=[NotHosted])
def login(request: Request, next: str | None = None) -> RedirectResponse:
    auth = _auth(request)
    if not auth.settings.enabled or auth.client is None:
        return RedirectResponse(_safe_next(next), status_code=303)
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier, challenge = pkce_pair()
    auth.store.start_login(state, nonce, verifier, _safe_next(next))
    return RedirectResponse(auth.client.authorize_url(state, nonce, challenge), status_code=303)


@router.get("/auth/callback", include_in_schema=False, dependencies=[NotHosted])
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


@router.post("/auth/logout", include_in_schema=False, dependencies=[NotHosted])
def logout(request: Request, session: Session = Depends(require(Role.VIEWER))) -> Response:  # noqa: B008
    del session
    token = bearer_token(request) or request.cookies.get(COOKIE)
    if token:
        _auth(request).store.delete(token)
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, path="/")
    return response


def sign_in_redirect(path: str) -> str:
    return f"/auth/login?next={quote(path, safe='/')}"


class AuthConfig(BaseModel):
    sign_in: str
    issuer: str | None = None
    client_id: str | None = None
    scopes: list[str] = []
    device_authorization_endpoint: str | None = None
    token_endpoint: str | None = None


class TokenRequest(BaseModel):
    id_token: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"  # noqa: S105 - the OAuth token type, not a secret
    expires_in: int
    name: str
    role: str


@router.get("/api/auth/config", dependencies=[NotHosted])
def auth_config(request: Request) -> AuthConfig:
    """What a command line or MCP client needs to sign in to this studio."""
    auth = _auth(request)
    if not auth.settings.enabled or auth.client is None:
        return AuthConfig(sign_in="none")
    metadata = auth.client.metadata()
    return AuthConfig(
        sign_in="oidc",
        issuer=auth.settings.issuer,
        client_id=auth.settings.cli_client_id or auth.settings.client_id,
        scopes=list(auth.settings.scopes),
        device_authorization_endpoint=metadata.get("device_authorization_endpoint"),
        token_endpoint=metadata.get("token_endpoint"),
    )


@router.post("/auth/token", dependencies=[NotHosted])
def issue_token(request: Request, body: TokenRequest) -> TokenResponse:
    """Trade a provider ID token (from device sign-in) for a studio bearer token."""
    auth = _auth(request)
    if not auth.settings.enabled or auth.client is None:
        raise HTTPException(404, "Sign-in is off; no token is needed")
    log = request.app.state.audit
    try:
        claims = auth.client.verify(body.id_token, None)
    except SignInError as failure:
        log.record(
            actor="anonymous",
            actor_name="Not signed in",
            interface="cli",
            action="token",
            outcome="refused",
            detail={"reason": str(failure)},
        )
        raise HTTPException(401, str(failure)) from failure
    role = auth.settings.role_for(claims.values)
    if role is None:
        log.record(
            actor=claims.subject,
            actor_name=claims.name,
            interface="cli",
            action="token",
            outcome="refused",
            detail={"reason": "no role"},
        )
        raise HTTPException(403, "Your account has no role in this studio; ask an administrator")
    user = User(claims.subject, claims.name, claims.email, role)
    token, _ = auth.store.create(user, auth.settings.session_hours)
    log.record(
        actor=claims.subject,
        actor_name=claims.name,
        interface="cli",
        action="token",
        detail={"role": role.label},
    )
    return TokenResponse(
        access_token=token,
        expires_in=int(auth.settings.session_hours * 3600),
        name=claims.name,
        role=role.label,
    )
