"""Prices for the app and the feed's admin side (the feed itself is tested in py-marketdata)."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from ae_api.main import create_app
from ae_api.settings import Settings
from ae_brokers.zerodha import ZerodhaAdapter
from ae_core.secrets import new_master_key
from ae_marketdata.hub import Hub
from ae_marketdata.types import Tick
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import create_engine, text

from conftest import TEST_REDIS_URL

A = {"X-Dev-User": "alice@example.com"}
ADMIN = {"X-Dev-User": "admin@example.com"}


def kite_session(req: httpx.Request) -> httpx.Response:
    """Kite's token exchange for the platform app: rt-ok logs in AB1234, rt-other ZZ9999, anything else is refused."""
    form = parse_qs(req.content.decode())
    users = {"rt-ok": "ab1234", "rt-other": "zz9999"}
    rt = form["request_token"][0]
    if req.url.path != "/session/token" or rt not in users:
        return httpx.Response(
            403, json={"status": "error", "error_type": "TokenException", "message": "Token is invalid"}
        )
    want = hashlib.sha256(f"kitekey{rt}kitesecret".encode()).hexdigest()
    assert form["checksum"] == [want] and form["api_key"] == ["kitekey"]
    return httpx.Response(200, json={"status": "success", "data": {"access_token": "acc-1", "user_id": users[rt]}})


def run(coro):  # type: ignore[no-untyped-def]
    async def go():  # type: ignore[no-untyped-def]
        r = Redis.from_url(TEST_REDIS_URL)
        try:
            return await coro(Hub(r))
        finally:
            await r.aclose()

    return asyncio.run(go())


@pytest.fixture
def api(clean_db: str) -> Iterator[TestClient]:
    import redis

    redis.Redis.from_url(TEST_REDIS_URL).flushdb()
    settings = Settings(
        app_env="test",
        dev_auth=True,
        database_url=clean_db,
        redis_url=TEST_REDIS_URL,
        app_encryption_key=new_master_key(),
        breeze_api_key="my key/1",
        kite_feed_api_key="kitekey",
        kite_feed_api_secret="kitesecret",
        kite_feed_client_id="ab1234",
    )
    app = create_app(settings)
    app.state.brokers = {"zerodha": ZerodhaAdapter(transport=httpx.MockTransport(kite_session))}
    with TestClient(app) as c:
        c.get("/v1/me", headers=A)
        c.get("/v1/me", headers=ADMIN)
        e = create_engine(clean_db)
        with e.begin() as conn:
            conn.execute(text("UPDATE users SET role = 'admin' WHERE email = 'admin@example.com'"))
            conn.execute(text("DELETE FROM platform_secrets"))
        e.dispose()
        yield c
    redis.Redis.from_url(TEST_REDIS_URL).flushdb()


def test_snapshot_reports_prices_and_whether_the_feed_is_live(api: TestClient) -> None:
    assert api.get("/v1/market/snapshot").status_code == 401
    r = api.get("/v1/market/snapshot", headers=A).json()
    assert r == {"quotes": [], "feed_status": "down"}

    now = datetime.now(UTC)

    async def feed(hub: Hub) -> None:
        await hub.publish_tick(Tick("NIFTY", 25010.5, now, 24990.0))
        await hub.publish_tick(Tick("SENSEX", 82000.0, now - timedelta(minutes=10)))
        await hub.set_health(source="simulated", simulated=True)

    run(feed)
    r = api.get("/v1/market/snapshot", headers=A).json()
    assert r["feed_status"] == "simulated"
    assert {q["key"]: q["ltp"] for q in r["quotes"]} == {"NIFTY": 25010.5, "SENSEX": 82000.0}

    opt = "NIFTY:2026-10-06:25000:CE"
    r = api.get(f"/v1/market/snapshot?keys={opt}", headers=A)
    assert r.status_code == 200 and r.json()["quotes"] == []

    async def wanted(hub: Hub) -> set[str]:
        return await hub.wanted()

    assert opt in run(wanted)  # asking for a contract starts streaming it
    assert api.get("/v1/market/snapshot?keys=NIFTY:bad", headers=A).status_code == 400


def test_admin_market_data_and_breeze_session(api: TestClient) -> None:
    assert api.get("/v1/admin/market-data", headers=A).status_code == 403
    d = api.get("/v1/admin/market-data", headers=ADMIN).json()
    assert d["source"] is None
    assert d["logins"] == [
        {"provider": "breeze", "name": "ICICI Breeze", "session_expires_at": None, "account": None,
         "expected_account": None, "login_url": "https://api.icicidirect.com/apiuser/login?api_key=my%20key%2F1"},
        {"provider": "kite", "name": "Kite (platform account)", "session_expires_at": None, "account": None,
         "expected_account": "AB1234", "login_url": "https://kite.zerodha.com/connect/login?v=3&api_key=kitekey"},
    ]  # fmt: skip
    assert {i["code"] for i in d["instruments"]} >= {"NIFTY", "SENSEX"}

    landed = "https://app.algoearning.com/?apisession=12345678"
    r = api.put("/v1/admin/market-data/breeze-session", json={"session_token": landed}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["logins"][0]["session_expires_at"] is not None
    assert r.json()["logins"][1]["session_expires_at"] is None
    assert api.put("/v1/admin/market-data/breeze-session", json={"session_token": "1"}, headers=A).status_code == 403
    log = api.get("/v1/admin/audit?action=admin.market_data", headers=ADMIN).json()["items"]
    assert len(log) == 1 and "12345678" not in str(log)  # the token itself is never logged


def test_admin_kite_session_for_the_platform_feed(api: TestClient) -> None:
    body = {"request_token": "rt-ok"}
    assert api.put("/v1/admin/market-data/kite-session", json=body, headers=A).status_code == 403
    r = api.put("/v1/admin/market-data/kite-session", json={"request_token": "rt-bad"}, headers=ADMIN)
    assert r.status_code == 400 and "Kite login failed" in r.json()["error"]["message"]

    r = api.put("/v1/admin/market-data/kite-session", json={"request_token": "rt-other"}, headers=ADMIN)
    assert r.status_code == 400 and "ZZ9999" in r.json()["error"]["message"]  # not the platform's account
    assert r.json()["error"]["message"].count("AB1234") == 1

    # the whole address the login landed on works too (here: the old local app's, which nothing answered)
    landed = "http://127.0.0.1:5678/kite/callback?action=login&type=login&status=success&request_token=rt-ok"
    r = api.put("/v1/admin/market-data/kite-session", json={"request_token": landed}, headers=ADMIN)
    assert r.status_code == 200
    kite = r.json()["logins"][1]
    assert kite["account"] == "AB1234"
    expires = datetime.fromisoformat(kite["session_expires_at"])
    assert kite["provider"] == "kite" and expires.hour == 0 and expires.minute == 30  # 06:00 IST, in UTC
    log = api.get("/v1/admin/audit?action=admin.market_data.kite_session", headers=ADMIN).json()["items"]
    assert len(log) == 1 and "acc-1" not in str(log) and "AB1234" in str(log)  # which Kite account, never the token

    async def stored(_: Hub) -> str | None:
        from ae_core.secrets import SecretBox, load_master_key
        from ae_db.session import Database
        from ae_marketdata.session import KITE_SESSION, load_session

        settings = api.app.state.settings  # type: ignore[attr-defined]
        db = Database(settings.database_url)
        try:
            async with db.system_session() as s:
                box = SecretBox({1: load_master_key(settings.app_encryption_key)}, 1)
                return (await load_session(s, box, KITE_SESSION))[0]
        finally:
            await db.dispose()

    assert run(stored) == "acc-1"  # what the feed reads when it runs on Kite
