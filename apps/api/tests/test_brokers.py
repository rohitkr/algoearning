"""Broker accounts: encrypted credentials that never leave the server, plan limits, isolation, and the daily Zerodha
login round trip (signed single-use state, server-side token exchange, wrong-account protection)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from ae_api.main import create_app
from ae_api.settings import Settings
from ae_brokers.zerodha import IST, ZerodhaAdapter
from ae_core.secrets import new_master_key
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from conftest import TEST_REDIS_URL

A = {"X-Dev-User": "alice@example.com"}
B = {"X-Dev-User": "bob@example.com"}
SECRET, KEY = "kite-api-secret-DO-NOT-LEAK", "kiteapikey1234"
NEW = {"broker": "zerodha", "client_id": "ab1234", "label": "Main", "api_key": KEY, "api_secret": SECRET}


class FakeKite:
    def __init__(self) -> None:
        self.user_id = "AB1234"
        self.token_ok = True
        self.calls: list[tuple[str, str]] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path))
        if (req.method, req.url.path) == ("POST", "/session/token"):
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {"access_token": "ACCESS-TOKEN-XYZ", "user_id": self.user_id, "user_name": "R"},
                },
            )
        if req.url.path == "/session/token":
            return httpx.Response(200, json={"status": "success", "data": True})
        if req.url.path == "/user/profile":
            if not self.token_ok:
                return httpx.Response(
                    403,
                    json={
                        "status": "error",
                        "message": "Incorrect `api_key` or `access_token`.",
                        "error_type": "TokenException",
                    },
                )
            return httpx.Response(200, json={"status": "success", "data": {"user_id": self.user_id, "user_name": "R"}})
        return httpx.Response(404, json={"status": "error", "message": "nope"})


@pytest.fixture
def ctx(clean_db: str) -> Iterator[tuple[TestClient, FakeKite]]:
    import redis

    redis.Redis.from_url(TEST_REDIS_URL).flushdb()
    kite = FakeKite()
    app = create_app(
        Settings(
            app_env="test",
            dev_auth=True,
            database_url=clean_db,
            redis_url=TEST_REDIS_URL,
            app_encryption_key=new_master_key(),
            web_origin="http://localhost:3000",
        )
    )
    app.state.brokers = {"zerodha": ZerodhaAdapter(transport=httpx.MockTransport(kite.handler))}
    with TestClient(app, follow_redirects=False) as c:
        yield c, kite


def db_rows(url: str, sql: str) -> list[tuple[Any, ...]]:
    e = create_engine(url)
    with e.connect() as conn:
        rows = [tuple(r) for r in conn.execute(text(sql))]
    e.dispose()
    return rows


def add(c: TestClient, headers: dict[str, str] = A, **over: Any) -> Any:
    return c.post("/v1/broker-accounts", json={**NEW, **over}, headers=headers)


def login(c: TestClient, account_id: str, headers: dict[str, str] = A, **params: str) -> tuple[Any, str]:
    url = c.post(f"/v1/broker-accounts/{account_id}/login", headers=headers).json()["login_url"]
    state = parse_qs(parse_qs(urlparse(url).query)["redirect_params"][0])["state"][0]
    q = {"request_token": "rt-1", "action": "login", "status": "success", "state": state, **params}
    return c.get("/v1/brokers/zerodha/callback", params=q), state


def redirect_params(r: Any) -> dict[str, str]:
    assert r.status_code == 303, r.text
    loc = urlparse(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == "http://localhost:3000/brokers"
    return {k: v[0] for k, v in parse_qs(loc.query).items()}


def test_catalogue(ctx: tuple[TestClient, FakeKite]) -> None:
    c, _ = ctx
    cat = {b["code"]: b for b in c.get("/v1/brokers").json()}
    assert (
        cat["zerodha"]["available"]
        and cat["zerodha"]["redirect_url"] == "http://localhost:8000/v1/brokers/zerodha/callback"
    )
    assert cat["upstox"]["available"] is False and cat["upstox"]["redirect_url"] is None


def test_server_ip(ctx: tuple[TestClient, FakeKite], clean_db: str) -> None:
    c, _ = ctx
    assert c.get("/v1/brokers/server-ip", headers=A).json()["ip"] is None  # the worker has not checked yet
    engine = create_engine(clean_db)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO platform_settings (id, key, value) VALUES (gen_random_uuid(), 'public_ip', :v)"),
            {
                "v": json.dumps(
                    {
                        "ip": "49.36.10.99",
                        "previous": "49.36.10.20",
                        "changed_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                    }
                )
            },
        )
    got = c.get("/v1/brokers/server-ip", headers=A).json()
    assert (got["ip"], got["previous"], got["checked_at"]) == ("49.36.10.99", "49.36.10.20", None)
    assert got["changed_recently"] is True
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE platform_settings SET value = jsonb_set(value, '{changed_at}', to_jsonb(CAST(:t AS text)))"),
            {"t": (datetime.now(UTC) - timedelta(days=4)).isoformat()},
        )
    engine.dispose()
    assert c.get("/v1/brokers/server-ip", headers=A).json()["changed_recently"] is False
    assert c.get("/v1/brokers/server-ip").status_code == 401


def test_credentials_are_encrypted_and_never_returned(ctx: tuple[TestClient, FakeKite], clean_db: str) -> None:
    c, _ = ctx
    r = add(c)
    assert r.status_code == 201, r.text
    acc = r.json()
    assert acc["client_id"] == "AB1234" and acc["api_key_masked"] == "••••1234" and acc["status"] == "disconnected"
    for resp in (r, c.get("/v1/broker-accounts", headers=A)):
        assert SECRET not in resp.text and KEY not in resp.text and "api_secret" not in resp.text
    [(key_enc, secret_enc)] = db_rows(clean_db, "SELECT api_key_enc, api_secret_enc FROM broker_accounts")
    assert SECRET.encode() not in bytes(secret_enc) and KEY.encode() not in bytes(key_enc)
    assert all(SECRET not in json.dumps(d) for (d,) in db_rows(clean_db, "SELECT detail FROM audit_log"))


def test_plan_limit_duplicates_and_isolation(ctx: tuple[TestClient, FakeKite], clean_db: str) -> None:
    c, _ = ctx
    acc = add(c).json()
    second = add(c, client_id="CD5678")
    assert second.status_code == 403 and second.json()["error"]["details"]["feature"] == "max_broker_accounts"
    db_rows(clean_db, "SELECT 1")
    e = create_engine(clean_db)
    with e.begin() as conn:  # Alice upgrades: 2 broker accounts allowed
        conn.execute(
            text(
                "INSERT INTO subscriptions (user_id, plan_id, status, provider, current_period_end, "
                "cancel_at_period_end, kind) SELECT u.id, p.id, 'active', 'razorpay', :end, false, 'prepaid' "
                "FROM users u, plans p WHERE u.email='alice@example.com' AND p.code='pro'"
            ),
            {"end": datetime.now(UTC) + timedelta(days=30)},
        )
    e.dispose()
    assert add(c, client_id="ab1234").status_code == 409  # same broker account twice
    assert add(c, client_id="CD5678").status_code == 201
    for method, path in (
        ("get", "/v1/broker-accounts"),
        ("post", f"/v1/broker-accounts/{acc['id']}/login"),
        ("patch", f"/v1/broker-accounts/{acc['id']}"),
        ("delete", f"/v1/broker-accounts/{acc['id']}"),
    ):
        kw = {"json": {"label": "x"}} if method == "patch" else {}
        r = getattr(c, method)(path, headers=B, **kw)
        assert (r.json() == []) if method == "get" else r.status_code == 404, (method, r.text)


def test_daily_login_round_trip(ctx: tuple[TestClient, FakeKite], clean_db: str) -> None:
    c, _ = ctx
    acc = add(c).json()
    r, state = login(c, acc["id"])
    assert redirect_params(r) == {"connected": acc["id"]}
    [row] = c.get("/v1/broker-accounts", headers=A).json()
    assert row["status"] == "connected" and row["terminal_enabled"] is True
    expires = datetime.fromisoformat(row["session_expires_at"]).astimezone(IST)
    assert (expires.hour, expires.minute) == (6, 0)
    [(tok,)] = db_rows(clean_db, "SELECT access_token_enc FROM broker_sessions")
    assert b"ACCESS-TOKEN-XYZ" not in bytes(tok)
    assert c.post(f"/v1/broker-accounts/{acc['id']}/test", headers=A).json() == {
        "ok": True,
        "client_id": "AB1234",
        "name": "R",
        "message": None,
    }
    replay = c.get(
        "/v1/brokers/zerodha/callback", params={"request_token": "rt-1", "status": "success", "state": state}
    )
    assert redirect_params(replay) == {"error": "invalid_state"}  # single use
    forged = state[:-2] + ("AA" if not state.endswith("AA") else "BB")
    r = c.get("/v1/brokers/zerodha/callback", params={"request_token": "x", "status": "success", "state": forged})
    assert redirect_params(r) == {"error": "invalid_state"}


def test_logging_in_with_a_different_broker_account_is_refused(ctx: tuple[TestClient, FakeKite]) -> None:
    c, kite = ctx
    acc = add(c).json()
    kite.user_id = "ZZ9999"
    r, _ = login(c, acc["id"])
    assert redirect_params(r) == {"error": "wrong_account", "account": acc["id"], "logged_in_as": "ZZ9999"}
    assert ("DELETE", "/session/token") in kite.calls  # that session was invalidated
    assert c.get("/v1/broker-accounts", headers=A).json()[0]["status"] == "disconnected"


def test_cancelled_login(ctx: tuple[TestClient, FakeKite]) -> None:
    c, _ = ctx
    acc = add(c).json()
    r, _ = login(c, acc["id"], status="cancelled", request_token="")
    assert redirect_params(r)["error"] == "login_cancelled"


def test_switches_logout_and_credential_rotation(ctx: tuple[TestClient, FakeKite]) -> None:
    c, kite = ctx
    acc = add(c).json()
    path = f"/v1/broker-accounts/{acc['id']}"
    assert c.patch(path, json={"terminal_enabled": True}, headers=A).json()["error"]["details"] == {"action": "login"}
    assert c.patch(path, json={"engine_enabled": True}, headers=A).status_code == 409
    login(c, acc["id"])
    r = c.patch(path, json={"engine_enabled": True}, headers=A).json()
    assert r["engine_enabled"] is True and r["terminal_enabled"] is True
    r = c.patch(path, json={"terminal_enabled": False}, headers=A).json()  # terminal off = log out
    assert r["status"] == "disconnected" and r["engine_enabled"] is False and ("DELETE", "/session/token") in kite.calls
    login(c, acc["id"])
    r = c.patch(path, json={"api_secret": "rotated-secret-value"}, headers=A).json()
    assert r["status"] == "disconnected"  # new credentials void the session


def test_expired_broker_token_is_detected(ctx: tuple[TestClient, FakeKite]) -> None:
    c, kite = ctx
    acc = add(c).json()
    login(c, acc["id"])
    kite.token_ok = False
    r = c.post(f"/v1/broker-accounts/{acc['id']}/test", headers=A).json()
    assert r["ok"] is False and "access_token" in r["message"]
    assert c.get("/v1/broker-accounts", headers=A).json()[0]["status"] == "disconnected"


def test_remove_account(ctx: tuple[TestClient, FakeKite], clean_db: str) -> None:
    c, _ = ctx
    acc = add(c).json()
    login(c, acc["id"])
    assert c.delete(f"/v1/broker-accounts/{acc['id']}", headers=A).status_code == 204
    assert db_rows(clean_db, "SELECT count(*) FROM broker_accounts") == [(0,)]
    assert db_rows(clean_db, "SELECT count(*) FROM broker_sessions") == [(0,)]
    actions = [a for (a,) in db_rows(clean_db, "SELECT action FROM audit_log ORDER BY id")]
    assert actions[0] == "broker_account.add" and actions[-1] == "broker_account.remove"


def test_needs_an_encryption_key(clean_db: str) -> None:
    app = create_app(Settings(app_env="test", dev_auth=True, database_url=clean_db))
    with TestClient(app) as c:
        assert c.post("/v1/broker-accounts", json=NEW, headers=A).status_code == 503
