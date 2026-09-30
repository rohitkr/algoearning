from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from starlette.requests import HTTPConnection


class AuthError(Exception):
    """The request carries credentials, but they are invalid (bad signature, expired, wrong audience...)."""


@dataclass(frozen=True)
class Identity:
    subject: str  # stable id from the provider, stored as users.auth_subject
    email: str | None = None  # None: look it up (a Clerk session token carries no email)
    name: str | None = None
    avatar_url: str | None = None


class Authenticator(Protocol):
    async def authenticate(self, conn: HTTPConnection) -> Identity | None:
        """Identity if this authenticator recognises the request's credentials, None if it carries none.
        Raises AuthError if credentials are present but invalid."""
        ...
