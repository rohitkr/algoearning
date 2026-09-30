"""The `state` carried through a broker login redirect: proves the callback belongs to a login WE started, for
THIS user and THIS broker account, within the last 10 minutes, and only once.

    token = base64url(json{a: account, u: user, n: nonce, e: expiry}) + "." + base64url(HMAC-SHA256)
The HMAC key is derived from the master key (HKDF, its own purpose label), and the nonce is registered in Redis
on issue and consumed with GETDEL on use, so a copied or replayed callback URL is rejected."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from redis.asyncio import Redis

TTL_S = 600
PREFIX = "broker-login-state:"


class InvalidState(Exception):
    pass


@dataclass(frozen=True)
class LoginState:
    account_id: uuid.UUID
    user_id: uuid.UUID


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class LoginStateSigner:
    def __init__(self, master_key: bytes, redis: Redis) -> None:
        self._key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"ae broker login state v1").derive(
            master_key
        )
        self.redis = redis

    def _sig(self, body: bytes) -> str:
        return _b64(hmac.new(self._key, body, hashlib.sha256).digest())

    async def issue(self, account_id: uuid.UUID, user_id: uuid.UUID) -> str:
        nonce = secrets.token_urlsafe(16)
        body = json.dumps(
            {"a": str(account_id), "u": str(user_id), "n": nonce, "e": int(time.time()) + TTL_S}, separators=(",", ":")
        ).encode()
        await self.redis.set(PREFIX + nonce, "1", ex=TTL_S)
        return f"{_b64(body)}.{self._sig(body)}"

    async def consume(self, token: str) -> LoginState:
        try:
            raw_body, sig = token.split(".", 1)
            body = _unb64(raw_body)
        except ValueError as exc:
            raise InvalidState("malformed") from exc
        if not hmac.compare_digest(self._sig(body), sig):
            raise InvalidState("bad signature")
        data = json.loads(body)
        if int(data["e"]) < time.time():
            raise InvalidState("expired")
        if not await self.redis.getdel(PREFIX + str(data["n"])):
            raise InvalidState("already used")
        return LoginState(uuid.UUID(data["a"]), uuid.UUID(data["u"]))
