"""Bearer-token checks for the MCP server.

MCP clients send the studio bearer token from `<command> login` (or a token an
administrator issues). The token names a server-side session, so signing out or
expiry ends MCP access too. Scopes carry the session's role and every role below
it, so a tool can require one.
"""

from collections.abc import Callable

from fastmcp.server.auth import AccessToken, TokenVerifier

from munki_web_studio.auth.model import Role
from munki_web_studio.auth.sessions import SessionStore


class StudioTokenVerifier(TokenVerifier):
    def __init__(self, store: Callable[[], SessionStore | None]) -> None:
        super().__init__()
        self._store = store

    async def verify_token(self, token: str) -> AccessToken | None:
        store = self._store()
        session = store.get(token) if store else None
        if session is None:
            return None
        role = session.user.role
        return AccessToken(
            token=token,
            client_id="studio",
            scopes=[r.label.lower() for r in Role if r <= role],
            subject=session.user.subject,
            claims={"name": session.user.name, "role": role.label},
        )
