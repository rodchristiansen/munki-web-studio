"""Roles and signed-in users."""

from dataclasses import dataclass
from enum import IntEnum


class Role(IntEnum):
    """Each role includes everything the roles below it can do."""

    VIEWER = 1
    EDITOR = 2
    PUBLISHER = 3
    ADMINISTRATOR = 4

    @property
    def label(self) -> str:
        return self.name.capitalize()

    @classmethod
    def parse(cls, text: str) -> Role:
        try:
            return cls[text.strip().upper()]
        except KeyError:
            raise ValueError(
                f"Unknown role {text!r}; use Viewer, Editor, Publisher, or Administrator"
            ) from None


@dataclass(frozen=True)
class User:
    subject: str
    name: str
    email: str | None
    role: Role


# Who acts when the studio runs without sign-in, on this machine only.
LOCAL_USER = User(subject="local", name="Local administrator", email=None, role=Role.ADMINISTRATOR)
