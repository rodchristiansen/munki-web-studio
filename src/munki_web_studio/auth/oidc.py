"""A small OpenID Connect client: discovery, authorization code with PKCE,
token exchange, and ID token verification against the provider's keys."""

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlencode

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from munki_web_studio.auth.config import AuthSettings


class SignInError(Exception):
    pass


@dataclass(frozen=True)
class Claims:
    subject: str
    name: str
    email: str | None
    values: list[str]


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    return verifier, challenge.decode()


class OIDCClient:
    def __init__(
        self, settings: AuthSettings, secret: str | None, http: httpx.Client | None = None
    ) -> None:
        self.settings = settings
        self.secret = secret
        self.http = http or httpx.Client(timeout=10)
        self._metadata: dict[str, Any] | None = None
        self._keys: KeySet | None = None
        self._keys_at = 0.0

    @property
    def redirect_uri(self) -> str:
        return f"{self.settings.public_url}/auth/callback"

    def metadata(self) -> dict[str, Any]:
        if self._metadata is None:
            url = f"{self.settings.issuer}/.well-known/openid-configuration"
            response = self.http.get(url)
            response.raise_for_status()
            data = cast(dict[str, Any], response.json())
            if data.get("issuer", "").rstrip("/") != self.settings.issuer:
                raise SignInError("The provider's issuer does not match the configured issuer")
            self._metadata = data
        return self._metadata

    def keys(self, refresh: bool = False) -> KeySet:
        if self._keys is None or refresh or time.time() - self._keys_at > 3600:
            response = self.http.get(self.metadata()["jwks_uri"])
            response.raise_for_status()
            self._keys = KeySet.import_key_set(response.json())
            self._keys_at = time.time()
        return self._keys

    def authorize_url(self, state: str, nonce: str, challenge: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self.settings.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.settings.scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.metadata()['authorization_endpoint']}?{urlencode(params)}"

    def device_endpoint(self) -> str | None:
        endpoint = self.metadata().get("device_authorization_endpoint")
        return str(endpoint) if endpoint else None

    def exchange(self, code: str, verifier: str, nonce: str) -> Claims:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.settings.client_id,
            "code_verifier": verifier,
        }
        if self.secret:
            form["client_secret"] = self.secret
        response = self.http.post(self.metadata()["token_endpoint"], data=form)
        if response.status_code != 200:
            raise SignInError("The provider refused the sign-in code")
        id_token = cast(dict[str, Any], response.json()).get("id_token")
        if not isinstance(id_token, str):
            raise SignInError("The provider returned no ID token")
        return self.verify(id_token, nonce)

    def verify(self, id_token: str, nonce: str | None) -> Claims:
        """Check signature, issuer, audience and expiry; the nonce too when one was sent."""
        try:
            try:
                token = jwt.decode(id_token, self.keys(), algorithms=["RS256", "ES256", "PS256"])
            except JoseError:
                token = jwt.decode(
                    id_token, self.keys(refresh=True), algorithms=["RS256", "ES256", "PS256"]
                )
            checks: dict[str, Any] = {
                "iss": {"essential": True, "value": self.settings.issuer},
                "aud": {"essential": True, "values": self.settings.audiences},
                "sub": {"essential": True},
                "exp": {"essential": True},
            }
            if nonce is not None:
                checks["nonce"] = {"essential": True, "value": nonce}
            registry = jwt.JWTClaimsRegistry(**checks)
            registry.validate(token.claims)
        except JoseError as error:
            raise SignInError(f"The ID token is not valid: {error}") from error
        claims = token.claims
        raw_values = claims.get(self.settings.role_claim, [])
        values = (
            [str(v) for v in cast(list[Any], raw_values)]
            if isinstance(raw_values, list)
            else [str(raw_values)]
        )
        return Claims(
            subject=str(claims["sub"]),
            name=str(claims.get("name") or claims.get("preferred_username") or claims["sub"]),
            email=str(claims["email"]) if claims.get("email") else None,
            values=values,
        )
