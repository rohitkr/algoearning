"""Clerk sign-in: session-token verification, first-sight profile lookup, cookie tokens, account deletion,
and the signed users webhook (replay-safe)."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import jwt
import pytest
from ae_api.auth.base import Identity
from ae_api.auth.clerk import ClerkAuthenticator, issuer_from_publishable_key
from ae_api.auth.webhooks import InvalidSignature, sign_svix, verify_svix
from ae_api.main import create_app
from ae_api.settings import Settings
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

HOST = "test-instance.clerk.accounts.dev"
PK = "pk_test_" + base64.b64encode(f"{HOST}$".encode()).decode().rstrip("=")
ISSUER = f"https://{HOST}"
ORIGIN = "http://localhost:3000"
WHSEC = "whsec_" + base64.b64encode(b"super-secret-webhook-key-32bytes").decode()
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@dataclass
class _K:
    key: Any


class FakeJwks:
    def get_signing_key_from_jwt(self, token: str) -> _K:
        return _K(KEY.public_key())


def token(sub: str = "user_abc", *, key: Any = KEY, **over: Any) -> str:
    now = int(time.time())
    claims = {"sub": sub, "iss": ISSUER, "iat": now, "nbf": now, "exp": now + 60, "azp": ORIGIN, "sid": "sess_1"}
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


def bearer(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture
def ctx(clean_db: str) -> Iterator[tuple[TestClient, list[str]]]:
    fetched: list[str] = []

    async def fetch(user_id: str) -> Identity:
        fetched.append(user_id)
        return Identity(subject=user_id, email="Rohit@Example.com", name="Rohit Kumar", avatar_url="https://img/x.png")

    app = create_app(
        Settings(
            app_env="test",
            database_url=clean_db,
            clerk_publishable_key=PK,
            clerk_webhook_secret=WHSEC,
            web_origin=ORIGIN,
        )
    )
    app.state.authenticators = [ClerkAuthenticator(PK, [ORIGIN], keys=FakeJwks())]
    app.state.fetch_profile = fetch
    with TestClient(app) as c:
        yield c, fetched


def test_issuer_comes_from_the_publishable_key() -> None:
    assert issuer_from_publishable_key(PK) == ISSUER
    real = "pk_test_Y2xvc2luZy1wYW5kYS03NTIzLmNsZXJrLmFjY291bnRzLmRldiQ"
    assert issuer_from_publishable_key(real) == "https://closing-panda-7523.clerk.accounts.dev"
    with pytest.raises(ValueError):
        issuer_from_publishable_key("pk_test_!!!")


def test_valid_token_signs_in_and_creates_the_user_once(ctx: tuple[TestClient, list[str]]) -> None:
    c, fetched = ctx
    r = c.get("/v1/me", headers=bearer(token()))
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "rohit@example.com" and r.json()["name"] == "Rohit Kumar"
    assert c.get("/v1/me", headers=bearer(token())).json()["id"] == r.json()["id"]
    assert fetched == ["user_abc"]  # Clerk profile read only on first sight


def test_session_cookie_is_accepted(ctx: tuple[TestClient, list[str]]) -> None:
    c, _ = ctx
    c.cookies.set("__session", token())
    assert c.get("/v1/me").status_code == 200


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(lambda: token(exp=int(time.time()) - 60), id="expired"),
        pytest.param(lambda: token(nbf=int(time.time()) + 600), id="not-yet-valid"),
        pytest.param(lambda: token(iss="https://evil.clerk.accounts.dev"), id="wrong-issuer"),
        pytest.param(lambda: token(azp="https://evil.example"), id="wrong-origin"),
        pytest.param(lambda: token(key=OTHER_KEY), id="forged-signature"),
        pytest.param(lambda: token(sub=None), id="no-subject"),
        pytest.param(lambda: token(sts="pending"), id="pending-session"),
        pytest.param(
            lambda: jwt.encode(
                {"sub": "user_abc", "iss": ISSUER, "exp": int(time.time()) + 60, "iat": int(time.time())},
                None,
                algorithm="none",
            ),
            id="alg-none",
        ),
        pytest.param(lambda: "not.a.jwt", id="garbage"),
    ],
)
def test_invalid_tokens_are_rejected(ctx: tuple[TestClient, list[str]], bad: Any) -> None:
    c, fetched = ctx
    r = c.get("/v1/me", headers=bearer(bad()))
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    assert fetched == []


def test_dev_header_is_ignored_when_dev_auth_is_off(ctx: tuple[TestClient, list[str]]) -> None:
    c, _ = ctx
    assert c.get("/v1/me", headers={"X-Dev-User": "x@example.com"}).status_code == 401


def webhook(
    c: TestClient, event: dict[str, Any], msg_id: str = "msg_1", ts: int | None = None, secret: str = WHSEC
) -> Any:
    body = json.dumps(event).encode()
    ts = ts or int(time.time())
    headers = {
        "svix-id": msg_id,
        "svix-timestamp": str(ts),
        "svix-signature": sign_svix(secret, msg_id, ts, body),
        "content-type": "application/json",
    }
    return c.post("/v1/webhooks/clerk", content=body, headers=headers)


CLERK_USER = {
    "id": "user_web",
    "primary_email_address_id": "e1",
    "first_name": "Web",
    "last_name": "Hook",
    "image_url": "https://img/w.png",
    "email_addresses": [
        {"id": "e0", "email_address": "old@example.com"},
        {"id": "e1", "email_address": "Primary@Example.com"},
    ],
}


def users(url: str) -> list[tuple[str, str, str]]:
    e = create_engine(url)
    with e.connect() as conn:
        rows = [tuple(r) for r in conn.execute(text("SELECT auth_subject, email, status FROM users ORDER BY email"))]
    e.dispose()
    return rows  # type: ignore[return-value]


def test_webhook_syncs_users_and_ignores_replays(ctx: tuple[TestClient, list[str]], clean_db: str) -> None:
    c, _ = ctx
    r = webhook(c, {"type": "user.created", "data": CLERK_USER})
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert users(clean_db) == [("user_web", "primary@example.com", "active")]
    assert webhook(c, {"type": "user.created", "data": CLERK_USER}).json() == {"status": "duplicate"}
    webhook(c, {"type": "user.updated", "data": {**CLERK_USER, "first_name": "Renamed"}}, msg_id="msg_2")
    webhook(c, {"type": "user.deleted", "data": {"id": "user_web", "deleted": True}}, msg_id="msg_3")
    assert users(clean_db) == [("user_web", "primary@example.com", "deleted")]
    r = c.get("/v1/me", headers=bearer(token("user_web")))  # a deleted account cannot sign in
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"


def test_closed_alpha_refuses_new_accounts_but_lets_existing_ones_in(clean_db: str) -> None:
    app = create_app(
        Settings(
            app_env="production",
            database_url=clean_db,
            clerk_publishable_key=PK,
            clerk_webhook_secret=WHSEC,
            web_origin=ORIGIN,
        )
    )
    app.state.authenticators = [ClerkAuthenticator(PK, [ORIGIN], keys=FakeJwks())]
    with TestClient(app) as c:
        r = c.get("/v1/me", headers=bearer(token("user_new")))
        assert r.status_code == 403
        assert r.json()["error"]["message"] == "Internal Alpha Test Environment. Closed to the public."
        assert r.json()["error"]["details"] == {"reason": "registration_closed"}
        assert webhook(c, {"type": "user.created", "data": CLERK_USER}).json() == {"status": "ok"}
        assert users(clean_db) == []  # a Clerk sign-up does not create an account either
    e = create_engine(clean_db)
    with e.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (auth_subject, email, role, status)"
                " VALUES ('user_web', 'primary@example.com', 'user', 'active')"
            )
        )
    e.dispose()
    with TestClient(app) as c:
        assert c.get("/v1/me", headers=bearer(token("user_web"))).status_code == 200
        webhook(c, {"type": "user.updated", "data": {**CLERK_USER, "first_name": "Renamed"}}, msg_id="msg_9")
    assert users(clean_db) == [("user_web", "primary@example.com", "active")]


def test_webhook_rejects_forged_and_stale_events(ctx: tuple[TestClient, list[str]], clean_db: str) -> None:
    c, _ = ctx
    other = "whsec_" + base64.b64encode(b"a-different-secret-entirely-000").decode()
    assert webhook(c, {"type": "user.created", "data": CLERK_USER}, secret=other).status_code == 400
    assert webhook(c, {"type": "user.created", "data": CLERK_USER}, ts=int(time.time()) - 3600).status_code == 400
    assert c.post("/v1/webhooks/clerk", content=b"{}").status_code == 400
    assert users(clean_db) == []


def test_webhook_needs_a_configured_secret(clean_db: str) -> None:
    app = create_app(Settings(app_env="test", database_url=clean_db))
    with TestClient(app) as c:
        assert c.post("/v1/webhooks/clerk", content=b"{}").status_code == 503


def test_svix_verification_unit() -> None:
    body = b'{"a":1}'
    now = 1_700_000_000
    sig = sign_svix(WHSEC, "m1", now, body)
    hdr = {"svix-id": "m1", "svix-timestamp": str(now), "svix-signature": f"v1,bogus {sig}"}
    assert verify_svix(WHSEC, hdr, body, now=now) == "m1"  # any listed signature may match
    with pytest.raises(InvalidSignature):
        verify_svix(WHSEC, hdr, body + b" ", now=now)  # body tampered
