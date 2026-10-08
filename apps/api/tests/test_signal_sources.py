"""Signal sources (ADR 0025, phase A): the Telegram login through the API with a fake gateway: code, 2-step
password, picking a chat, flood waits, a revoked session, disconnect; secrets encrypted, never returned, never in the
audit log; plan limits; row-level isolation between users."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from ae_api.main import create_app
from ae_api.settings import Settings
from ae_core.secrets import new_master_key
from ae_telegram import Chat, CodeInvalid, Flood, LoginStarted, PasswordInvalid, PasswordNeeded, SessionInvalid
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from conftest import TEST_REDIS_URL

A = {"X-Dev-User": "alice@example.com"}
B = {"X-Dev-User": "bob@example.com"}
PHONE = "+91 98123 45210"
HASH = "0123456789abcdef0123456789abcdef"
SESSION = "1BVtsOK-THE-SECRET-TELEGRAM-SESSION"
CODE = "57931"
CHANNEL = Chat(-1001234567890, "Nifty Sensex VIP setups", "channel", "vipsetups")


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.password: str | None = None  # the account's 2-step password, if it has one
        self.fail: dict[str, Exception] = {}

    def _check(self, step: str) -> None:
        if step in self.fail:
            raise self.fail.pop(step)

    async def send_code(self, api_id: int, api_hash: str, phone: str) -> LoginStarted:
        self.calls.append(("send_code", (api_id, api_hash, phone)))
        self._check("send_code")
        return LoginStarted("half-" + SESSION, "CODEHASH", "app")

    async def sign_in_code(self, api_id: int, api_hash: str, session: str, phone: str, code: str,
                           code_hash: str) -> tuple[str, str]:  # fmt: skip
        self.calls.append(("sign_in_code", (session, phone, code, code_hash)))
        self._check("sign_in_code")
        if code != CODE:
            raise CodeInvalid("the code is not right")
        if self.password:
            raise PasswordNeeded("pw-" + SESSION)
        return SESSION, "Rohit (@rk)"

    async def sign_in_password(self, api_id: int, api_hash: str, session: str, password: str) -> tuple[str, str]:
        self.calls.append(("sign_in_password", session))
        if password != self.password:
            raise PasswordInvalid("the 2-step verification password is not right")
        return SESSION, "Rohit (@rk)"

    async def chats(self, api_id: int, api_hash: str, session: str) -> list[Chat]:
        self.calls.append(("chats", session))
        self._check("chats")
        return [CHANNEL, Chat(-77, "Traders", "group", None)]

    async def log_out(self, api_id: int, api_hash: str, session: str) -> None:
        self.calls.append(("log_out", session))


@pytest.fixture
def ctx(clean_db: str) -> Iterator[tuple[TestClient, FakeGateway, str]]:
    import redis

    redis.Redis.from_url(TEST_REDIS_URL).flushdb()
    app = create_app(
        Settings(
            app_env="test",
            dev_auth=True,
            database_url=clean_db,
            redis_url=TEST_REDIS_URL,
            app_encryption_key=new_master_key(),
            web_origin="http://localhost:3000",
            telegram_api_id=611335,
            telegram_api_hash="ffffffffffffffffffffffffffffffff",
        )
    )
    gw = FakeGateway()
    app.state.telegram = gw
    with TestClient(app) as c:
        yield c, gw, clean_db


def rows(url: str, sql: str) -> list[tuple[Any, ...]]:
    e = create_engine(url)
    with e.connect() as conn:
        out = [tuple(r) for r in conn.execute(text(sql))]
    e.dispose()
    return out


def connect(c: TestClient, headers: dict[str, str] = A, **body: Any) -> dict[str, Any]:
    r = c.post("/v1/signal-sources", json={"phone": PHONE, **body}, headers=headers)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    r = c.post(f"/v1/signal-sources/{sid}/code", json={"code": "57 931"}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def test_login_pick_a_chat_and_never_leak_a_secret(ctx: tuple[TestClient, FakeGateway, str]) -> None:
    c, gw, db = ctx
    assert c.get("/v1/signal-sources/setup", headers=A).json() == {"platform_app": True, "max_sources": 1, "used": 0}
    r = c.post("/v1/signal-sources", json={"phone": PHONE, "label": "VIP"}, headers=A)
    assert r.status_code == 201
    src = r.json()
    assert (src["status"], src["next_step"], src["phone_masked"], src["platform_app"]) == (
        "code_sent", "code", "+91 98•••••210", True)  # fmt: skip
    assert gw.calls[0] == ("send_code", (611335, "f" * 32, "+919812345210"))  # the platform's app

    assert c.post(f"/v1/signal-sources/{src['id']}/code", json={"code": "99999"}, headers=A).status_code == 400
    src = c.post(f"/v1/signal-sources/{src['id']}/code", json={"code": CODE}, headers=A).json()
    assert (src["status"], src["next_step"], src["account_name"]) == ("connected", "chat", "Rohit (@rk)")
    assert ("sign_in_code", ("half-" + SESSION, "+919812345210", CODE, "CODEHASH")) in gw.calls

    chats = c.get(f"/v1/signal-sources/{src['id']}/chats", headers=A).json()
    assert [x["title"] for x in chats] == ["Nifty Sensex VIP setups", "Traders"]
    assert c.put(f"/v1/signal-sources/{src['id']}/chat", json={"chat_id": 42}, headers=A).status_code == 400
    src = c.put(f"/v1/signal-sources/{src['id']}/chat", json={"chat_id": CHANNEL.id}, headers=A).json()
    assert (src["chat_title"], src["chat_kind"], src["next_step"]) == ("Nifty Sensex VIP setups", "channel", None)

    # nothing secret anywhere it could be read: responses, audit log; the database holds ciphertext only
    everything = str(c.get("/v1/signal-sources", headers=A).json()) + str(rows(db, "SELECT * FROM audit_log"))
    for secret in (SESSION, "9812345210", CODE, "CODEHASH", "f" * 32):
        assert secret not in everything
    stored = rows(db, "SELECT session_enc, phone_enc, login_state_enc FROM signal_sources")[0]
    assert SESSION.encode() not in bytes(stored[0]) and b"9812345210" not in bytes(stored[1])
    assert stored[2] is None  # the code request is gone once logged in


def test_two_step_password(ctx: tuple[TestClient, FakeGateway, str]) -> None:
    c, gw, _ = ctx
    gw.password = "hunter2"
    src = connect(c)
    assert (src["status"], src["next_step"]) == ("password_needed", "password")
    url = f"/v1/signal-sources/{src['id']}/password"
    assert c.post(url, json={"password": "wrong"}, headers=A).status_code == 400
    src = c.post(url, json={"password": "hunter2"}, headers=A).json()
    assert src["status"] == "connected"
    assert ("sign_in_password", "pw-" + SESSION) in gw.calls  # continued the half-finished session


def test_own_app_flood_wait_and_revoked_session(ctx: tuple[TestClient, FakeGateway, str]) -> None:
    c, gw, _ = ctx
    bad = c.post("/v1/signal-sources", json={"phone": PHONE, "api_id": 123}, headers=A)
    assert bad.status_code == 400  # an API ID needs its hash
    src = connect(c, api_id=123456, api_hash=HASH.upper())
    assert src["platform_app"] is False and src["api_id"] == 123456
    gw.fail["chats"] = SessionInvalid("the Telegram session is no longer valid: reconnect Telegram")
    r = c.get(f"/v1/signal-sources/{src['id']}/chats", headers=A)
    assert r.status_code == 409 and r.json()["error"]["details"] == {"action": "login"}
    src = c.get("/v1/signal-sources", headers=A).json()[0]
    assert (src["status"], src["next_step"]) == ("needs_reconnect", "login")  # kept, though the request failed

    gw.fail["send_code"] = Flood(300)
    r = c.post(f"/v1/signal-sources/{src['id']}/login", json={}, headers=A)
    assert r.status_code == 429 and r.json()["error"]["details"] == {"retry_after": 300}
    src = c.get("/v1/signal-sources", headers=A).json()[0]
    assert src["status"] == "flood_wait" and src["flood_until"] is not None
    src = c.post(f"/v1/signal-sources/{src['id']}/login", json={}, headers=A).json()  # later: the stored phone
    assert src["status"] == "code_sent" and gw.calls[-1] == ("send_code", (123456, HASH, "+919812345210"))


def test_disconnect_wipes_the_secrets_and_delete_removes_the_source(ctx: tuple[TestClient, FakeGateway, str]) -> None:
    c, gw, db = ctx
    src = connect(c)
    c.put(f"/v1/signal-sources/{src['id']}/chat", json={"chat_id": CHANNEL.id}, headers=A)
    src = c.post(f"/v1/signal-sources/{src['id']}/disconnect", headers=A).json()
    assert ("log_out", SESSION) in gw.calls
    assert (src["status"], src["phone_masked"], src["chat_title"]) == ("disconnected", None, "Nifty Sensex VIP setups")
    assert rows(db, "SELECT session_enc, phone_enc, api_hash_enc, login_state_enc FROM signal_sources") == [
        (None, None, None, None)
    ]
    assert c.post(f"/v1/signal-sources/{src['id']}/login", json={}, headers=A).status_code == 400  # phone needed
    assert c.delete(f"/v1/signal-sources/{src['id']}", headers=A).status_code == 204
    assert c.get("/v1/signal-sources", headers=A).json() == []


def test_plan_limit_and_isolation(ctx: tuple[TestClient, FakeGateway, str]) -> None:
    c, _, _ = ctx
    src = connect(c)
    r = c.post("/v1/signal-sources", json={"phone": PHONE}, headers=A)
    assert r.status_code == 403 and r.json()["error"]["details"]["feature"] == "max_signal_sources"
    assert c.get("/v1/signal-sources", headers=B).json() == []
    for method, path in (("get", "/chats"), ("post", "/disconnect"), ("delete", "")):
        assert getattr(c, method)(f"/v1/signal-sources/{src['id']}{path}", headers=B).status_code == 404
