"""An in-process OpenID provider for tests: discovery, keys, and a token
endpoint that issues ID tokens for whoever the test says signed in."""

import json
import time
from typing import Any
from urllib.parse import parse_qs

import httpx
from joserfc import jwt
from joserfc.jwk import RSAKey

ISSUER = "https://id.test"


class FakeIdP:
    def __init__(self, client_id: str = "studio") -> None:
        self.client_id = client_id
        self.key = RSAKey.generate_key(2048, parameters={"kid": "test-key"})
        self.codes: dict[str, dict[str, Any]] = {}
        self.wrong_nonce = False
        self.wrong_audience = False

    def sign_in(self, code: str, nonce: str, **claims: Any) -> None:
        """Make `code` redeem for a token with these claims."""
        self.codes[code] = {"nonce": nonce, **claims}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                },
            )
        if path == "/jwks":
            return httpx.Response(200, json={"keys": [self.key.as_dict(private=False)]})
        if path == "/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            claims = self.codes.pop(form.get("code", ""), None)
            if claims is None or not form.get("code_verifier"):
                return httpx.Response(400, json={"error": "invalid_grant"})
            now = int(time.time())
            payload = {
                "iss": ISSUER,
                "aud": "someone-else" if self.wrong_audience else self.client_id,
                "iat": now,
                "exp": now + 300,
                **claims,
            }
            if self.wrong_nonce:
                payload["nonce"] = "not-the-nonce"
            token = jwt.encode({"alg": "RS256", "kid": "test-key"}, payload, self.key)
            return httpx.Response(
                200, content=json.dumps({"id_token": token, "token_type": "Bearer"})
            )
        return httpx.Response(404)

    def http(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))
