"""Identity of this studio.

Everything platform-specific about naming lives here, so the rest of the
package can stay identical to its copy in the sibling studio.
"""

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version


@dataclass(frozen=True)
class Studio:
    name: str
    platform: str
    distribution: str
    package: str
    command: str
    config_env: str


STUDIO = Studio(
    name="Munki Web Studio",
    platform="Munki",
    distribution="munki-web-studio",
    package="munki_web_studio",
    command="mwsctl",
    config_env="MUNKI_WEB_STUDIO_CONFIG",
)

# Packages from other projects this studio must never import.
FORBIDDEN_IMPORTS = ("fleetmate", "fleetmate_web_admin", "cimian_web_studio")

try:
    __version__ = version(STUDIO.distribution)
except PackageNotFoundError:  # pragma: no cover - only when run from an uninstalled tree
    __version__ = "0.0.0"
