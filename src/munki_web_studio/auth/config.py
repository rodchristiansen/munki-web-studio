"""Sign-in settings from the [auth] table of the config file.

[auth]
mode = "oidc"                       # or "none" for a local, single-user studio
issuer = "https://login.example.org/tenant/v2.0"
client_id = "studio"
client_secret_env = "STUDIO_OIDC_SECRET"   # the secret itself stays out of the file
public_url = "https://studio.example.org"  # where the provider sends people back
role_claim = "roles"                # claim holding role or group values
default_role = ""                   # role for people with no mapped value; empty refuses them

[auth.roles]
Viewer = ["studio-viewers"]
Editor = ["studio-editors"]
Publisher = ["studio-publishers"]
Administrator = ["studio-admins"]
"""

from dataclasses import dataclass, field
from typing import Any, cast

from munki_web_studio.auth.model import Role


class AuthConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AuthSettings:
    mode: str = "none"
    issuer: str = ""
    client_id: str = ""
    client_secret_env: str = ""
    cli_client_id: str = ""
    public_url: str = ""
    scopes: tuple[str, ...] = ("openid", "profile", "email")
    role_claim: str = "roles"
    default_role: Role | None = None
    role_values: dict[str, Role] = field(default_factory=lambda: {})
    session_hours: float = 12

    @property
    def enabled(self) -> bool:
        return self.mode == "oidc"

    @property
    def audiences(self) -> list[str]:
        """ID tokens for the browser app or the command line's client are accepted."""
        return [a for a in (self.client_id, self.cli_client_id) if a]

    def role_for(self, values: list[str]) -> Role | None:
        """The highest role any of a person's claim values maps to."""
        roles = [self.role_values[v] for v in values if v in self.role_values]
        if roles:
            return max(roles)
        return self.default_role


def parse_auth(raw: Any) -> AuthSettings:
    if raw is None:
        return AuthSettings()
    if not isinstance(raw, dict):
        raise AuthConfigError("[auth] must be a table")
    table = cast(dict[str, Any], raw)
    mode = table.get("mode", "none")
    if mode not in ("none", "oidc"):
        raise AuthConfigError('auth.mode must be "oidc" or "none"')
    if mode == "none":
        return AuthSettings()
    missing = [k for k in ("issuer", "client_id", "public_url") if not table.get(k)]
    if missing:
        raise AuthConfigError(f"auth needs {', '.join(missing)} when mode is oidc")
    public_url = str(table["public_url"]).rstrip("/")
    if (
        not public_url.startswith("https://")
        and "://localhost" not in public_url
        and "://127.0.0.1" not in public_url
    ):
        raise AuthConfigError("auth.public_url must use https outside local testing")
    role_values: dict[str, Role] = {}
    roles = table.get("roles", {})
    if not isinstance(roles, dict):
        raise AuthConfigError("[auth.roles] must map role names to lists of claim values")
    for name, values in cast(dict[str, Any], roles).items():
        role = Role.parse(name)
        if not isinstance(values, list):
            raise AuthConfigError(f"auth.roles.{name} must be a list")
        for value in cast(list[Any], values):
            role_values[str(value)] = max(role, role_values.get(str(value), role))
    default = table.get("default_role") or None
    return AuthSettings(
        mode="oidc",
        issuer=str(table["issuer"]).rstrip("/"),
        client_id=str(table["client_id"]),
        client_secret_env=str(table.get("client_secret_env", "")),
        cli_client_id=str(table.get("cli_client_id", "")),
        public_url=public_url,
        scopes=tuple(table.get("scopes", ("openid", "profile", "email"))),
        role_claim=str(table.get("role_claim", "roles")),
        default_role=Role.parse(default) if default else None,
        role_values=role_values,
        session_hours=float(table.get("session_hours", 12)),
    )
