from __future__ import annotations

from starlette.requests import HTTPConnection

from .base import AuthError, Identity


class DevHeaderAuthenticator:
    """`X-Dev-User: you@example.com`: local development and tests only (Settings refuses it elsewhere)."""

    async def authenticate(self, conn: HTTPConnection) -> Identity | None:
        email = conn.headers.get("x-dev-user", "").strip().lower()
        if not email:
            return None
        if "@" not in email or len(email) > 320:
            raise AuthError("invalid dev user")
        return Identity(subject=f"dev|{email}", email=email)
