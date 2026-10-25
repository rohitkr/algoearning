"""The legs a strategy would trade right now, from the feed's live prices (the strategy and run pages' preview)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime

import pytest
from ae_api.main import create_app
from ae_api.settings import Settings
from ae_core.trading.model import IST
from ae_marketdata.hub import Hub
from ae_marketdata.types import Tick
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import create_engine, text

from conftest import TEST_REDIS_URL

A = {"X-Dev-User": "alice@example.com"}


@pytest.fixture
def api(clean_db: str) -> Iterator[TestClient]:
    app = create_app(Settings(app_env="test", dev_auth=True, database_url=clean_db, redis_url=TEST_REDIS_URL))
    with TestClient(app) as c:
        yield c


def publish(ticks: dict[str, float]) -> None:
    async def go() -> None:
        r = Redis.from_url(TEST_REDIS_URL)
        hub, now = Hub(r), datetime.now(IST)
        for k, px in ticks.items():
            await hub.publish_tick(Tick(k, px, now))
        await r.aclose()

    asyncio.run(go())


def test_an_iron_fly_previews_its_strikes_and_prices(api: TestClient, clean_db: str) -> None:
    from datetime import date, timedelta

    expiry = (date.today() + timedelta(days=9)).isoformat()
    later = (date.today() + timedelta(days=16)).isoformat()
    e = create_engine(clean_db)
    with e.begin() as c:
        c.execute(text("UPDATE instruments SET expiries = CAST(:x AS jsonb) WHERE code='NIFTY'"),
                  {"x": f'["{expiry}", "{later}"]'})  # fmt: skip
    e.dispose()
    publish({"NIFTY": 25010.0})
    prices = {f"NIFTY:{expiry}:{k}:{r}": max(1.0, 150 - abs(k - 25000) / 4) for k in range(24000, 26050, 50)
              for r in ("CE", "PE")}  # fmt: skip
    publish(prices)
    legs = [
        {"id": "L1", "action": "SELL", "option_type": "CE"},
        {"id": "L2", "action": "SELL", "option_type": "PE"},
        {"id": "L3", "action": "BUY", "option_type": "CE", "strike": {"mode": "premium", "premium": 60}},
        {"id": "L4", "action": "BUY", "option_type": "PE", "strike": {"mode": "premium", "premium": 60}},
    ]
    config = {"kind": "rules", "underlying": "NIFTY", "entry": {"at": "15:00"},
              "holding": {"mode": "intraday", "exit": "15:15"}, "legs": legs}  # fmt: skip
    r = api.post("/v1/strategies/preview-legs", json={"config": config, "multiplier": 2}, headers=A)
    assert r.status_code == 200, r.text
    (one,) = r.json()
    got = {x["leg"]: (x["action"], x["option_type"], x["strike"], x["price"], x["lots"]) for x in one["legs"]}
    assert got["L1"] == ("SELL", "CE", 25000, 150.0, 2) and got["L2"] == ("SELL", "PE", 25000, 150.0, 2)
    assert got["L3"][2] == 25350 and got["L4"][2] == 24650  # ~₹60 hedges: 150 - 360/4 = 60
    assert one["spot"] == 25010.0 and one["reason"] is None


def test_a_strike_moved_by_hand_is_priced_and_the_entry_window_is_said(api: TestClient, clean_db: str) -> None:
    from datetime import date, timedelta

    expiry = (date.today() + timedelta(days=9)).isoformat()
    e = create_engine(clean_db)
    with e.begin() as c:
        c.execute(
            text("UPDATE instruments SET expiries = CAST(:x AS jsonb) WHERE code='NIFTY'"), {"x": f'["{expiry}"]'}
        )
    e.dispose()
    publish({"NIFTY": 25010.0, f"NIFTY:{expiry}:25000:CE": 150.0, f"NIFTY:{expiry}:25100:CE": 110.0})
    config = {"kind": "rules", "underlying": "NIFTY", "entry": {"at": "09:15"},
              "holding": {"mode": "intraday", "exit": "15:29"},
              "legs": [{"id": "L1", "action": "SELL", "option_type": "CE"}]}  # fmt: skip
    r = api.post("/v1/strategies/preview-legs", json={"config": config, "strikes": {"": {"L1": 25100}}}, headers=A)
    (one,) = r.json()
    (leg,) = one["legs"]
    assert (leg["strike"], leg["algo_strike"], leg["price"]) == (25100, 25000, 110.0)
    assert leg["key"] == f"NIFTY:{expiry}:25100:CE" and one["entry_note"] and one["strike_step"] == 50
