"""Clerk session tokens (JWT, RS256) verified locally against the instance's JWKS: no network call per
request once the keys are cached. Checks signature, expiry / not-before (small leeway), issuer (the Clerk
instance named by the publishable key) and `azp` (the page origin that minted the token must be ours).

A session token has no email, so the profile of a user we have never seen is read once from Clerk's
Backend API with the secret key; afterwards the users table (kept current by the webhook) is enough."""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx
import jwt
from starlette.requests import HTTPConnection

from .base import AuthError, Identity

LEEWAY_S = 5
CLERK_API = "https://api.clerk.com/v1"


def issuer_from_publishable_key(pk: str) -> str:
    """pk_test_<base64("closing-panda-7523.clerk.accounts.dev$")> -> https://closing-panda-7523.clerk.accounts.dev"""
    try:
        encoded = pk.split("_", 2)[2]
        host = base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode().rstrip("$")
    except (IndexError, ValueError, UnicodeDecodeError) as exc:
        raise ValueError("CLERK_PUBLISHABLE_KEY is not a valid Clerk publishable key") from exc
    if not host or "/" in host:
        raise ValueError("CLERK_PUBLISHABLE_KEY is not a valid Clerk publishable key")
    return f"https://{host}"


class SigningKeys(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


ProfileFetcher = Callable[[str], Awaitable[Identity]]


def clerk_profile_fetcher(secret_key: str, timeout: float = 5.0) -> ProfileFetcher:
    async def fetch(user_id: str) -> Identity:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(f"{CLERK_API}/users/{user_id}", headers={"Authorization": f"Bearer {secret_key}"})
        if r.status_code != 200:
            raise AuthError(f"could not read the Clerk user ({r.status_code})")
        return identity_from_clerk_user(r.json())

    return fetch


def identity_from_clerk_user(u: dict[str, Any]) -> Identity:
    primary = u.get("primary_email_address_id")
    emails = u.get("email_addresses") or []
    email = next((e.get("email_address") for e in emails if e.get("id") == primary), None)
    email = email or next((e.get("email_address") for e in emails), None)
    name = " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x) or u.get("username") or None
    return Identity(subject=u["id"], email=email, name=name, avatar_url=u.get("image_url"))


class ClerkAuthenticator:
    def __init__(
        self,
        publishable_key: str,
        authorized_parties: list[str],
        *,
        keys: SigningKeys | None = None,
        fetch_profile: ProfileFetcher | None = None,
    ) -> None:
        self.issuer = issuer_from_publishable_key(publishable_key)
        self.authorized_parties = {p.rstrip("/") for p in authorized_parties}
        self.keys = keys or jwt.PyJWKClient(f"{self.issuer}/.well-known/jwks.json", cache_keys=True, lifespan=3600)
        self.fetch_profile = fetch_profile

    @staticmethod
    def token_from(conn: HTTPConnection) -> str | None:
        header = conn.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            return header[7:].strip() or None
        return conn.cookies.get("__session")  # same-site browser requests carry Clerk's cookie

    def verify(self, token: str) -> dict[str, Any]:
        try:
            key = self.keys.get_signing_key_from_jwt(token).key
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self.issuer,
                leeway=LEEWAY_S,
                options={"require": ["exp", "iat", "iss", "sub"], "verify_aud": False},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid session token: {type(exc).__name__}") from exc
        azp = claims.get("azp")
        if azp and azp.rstrip("/") not in self.authorized_parties:
            raise AuthError("session token was issued for another origin")
        if claims.get("sts") == "pending":  # session not fully established (e.g. pending tasks)
            raise AuthError("session is not active yet")
        return claims

    async def authenticate(self, conn: HTTPConnection) -> Identity | None:
        token = self.token_from(conn)
        if not token:
            return None
        claims = self.verify(token)
        return Identity(subject=str(claims["sub"]), email=claims.get("email"))
