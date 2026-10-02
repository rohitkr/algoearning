"""Live SMC charts: the options and snapshot endpoints, and the chart service following the feed through Redis."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from ae_api.charts import ChartInstrument, ChartService, Subscriber
from ae_api.main import create_app
from ae_api.settings import Settings
from ae_db.models import HistoryCandle
from ae_marketdata.hub import Hub
from ae_marketdata.types import IST, Bar, Tick
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import delete

from conftest import TEST_REDIS_URL

A = {"X-Dev-User": "alice@example.com"}
DAY = datetime(2026, 10, 5, tzinfo=IST)  # a Monday
NIFTY = ChartInstrument("NIFTY", "Nifty 50", "09:15", "15:30")


def at(h: int, m: int, s: int = 0, day: datetime = DAY) -> datetime:
    return day.replace(hour=h, minute=m, second=s)


def bar(ts: datetime, p: float) -> Bar:
    return Bar("NIFTY", ts, p, p + 2, p - 2, p + 1)


@pytest.fixture
def api(clean_db: str) -> Iterator[TestClient]:
    import redis

    redis.Redis.from_url(TEST_REDIS_URL).flushdb()
    settings = Settings(app_env="test", dev_auth=True, database_url=clean_db, redis_url=TEST_REDIS_URL)
    with TestClient(create_app(settings)) as c:
        yield c
    redis.Redis.from_url(TEST_REDIS_URL).flushdb()


def test_options_list_the_chartable_indices(api: TestClient) -> None:
    assert api.get("/v1/charts/options").status_code == 401
    r = api.get("/v1/charts/options", headers=A).json()
    assert [i["code"] for i in r["instruments"]] == ["NIFTY", "SENSEX", "BANKNIFTY"]
    assert r["timeframes"] == [1, 3, 5, 15]


def test_snapshot_validates_and_answers_without_any_prices(api: TestClient) -> None:
    assert api.get("/v1/charts/snapshot?key=NIFTY", headers={}).status_code == 401
    assert api.get("/v1/charts/snapshot?key=NIFTY&timeframe=7", headers=A).status_code == 400
    r = api.get("/v1/charts/snapshot?key=FINNIFTY", headers=A)
    assert r.status_code == 400 and r.json()["error"]["details"]["reason"] == "unknown_instrument"
    assert api.get("/v1/charts/stream?key=NIFTY&timeframe=2", headers=A).status_code == 400
    s = api.get("/v1/charts/snapshot?key=nifty&timeframe=15", headers=A).json()
    assert (s["key"], s["timeframe"], s["status"], s["candles"], s["forming"]) == ("NIFTY", 15, "down", [], None)
    assert s["smc"]["boxes"] == []


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    r = Redis.from_url(TEST_REDIS_URL, socket_timeout=2)
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


async def next_msg(sub: Subscriber, want: str) -> dict[str, Any]:
    while True:
        msg = await asyncio.wait_for(sub.queue.get(), timeout=5)
        if msg["type"] == want:
            return msg


async def test_service_seeds_from_history_and_follows_bars_and_ticks(db: Any, redis: Redis) -> None:
    hub = Hub(redis)
    prev = DAY - timedelta(days=3)  # Friday, from the history store
    async with db.system_session() as s:
        s.add_all(
            HistoryCandle(key="NIFTY", ts=at(9, 15 + i, day=prev), open=1, high=2, low=0.5, close=1.5, volume=0)
            for i in range(10)
        )
    for i in range(47):  # today 09:15..10:01 in Redis
        await hub.publish_bar(bar(at(9, 15) + timedelta(minutes=i), 25000 + i))
    now = at(10, 2, 10)
    svc = ChartService(redis, db, now=lambda: now, tick_s=0.05)
    try:
        feed, sub = await svc.subscribe(NIFTY, 5)
        snap = feed.snapshot()
        times = [c["time"] for c in snap["candles"]]
        assert times[0] == int(at(9, 15, day=prev).timestamp())  # history first
        assert times[-1] == int(at(9, 55).timestamp()) and len(times) == 2 + 9
        assert snap["forming"]["time"] == int(at(10, 0).timestamp()) and snap["forming"]["close"] == 25047

        # a second chart on the same index and timeframe shares the feed
        feed2, sub2 = await svc.subscribe(NIFTY, 5)
        assert feed2 is feed and len(feed.subscribers) == 2
        svc.unsubscribe(feed2, sub2)

        await hub.publish_tick(Tick("NIFTY", 25100.0, at(10, 2, 20)))
        msg = await next_msg(sub, "candle")
        assert msg["closed"] is False and msg["candle"]["high"] == 25100 and msg["candle"]["close"] == 25100

        for m in (2, 3, 4):
            await hub.publish_bar(bar(at(10, m), 25050))
        msg = await next_msg(sub, "candle")
        while not msg["closed"]:
            msg = await next_msg(sub, "candle")
        assert msg["candle"]["time"] == int(at(10, 0).timestamp()) and msg["candle"]["close"] == 25051
        smc = await next_msg(sub, "smc")
        assert smc["smc"]["candles"] == 12

        # a late bar for the candle just closed revises it
        await hub.publish_bar(Bar("NIFTY", at(10, 1), 25046, 25300, 25040, 25047))
        rev = await next_msg(sub, "revise")
        assert rev["candle"]["high"] == 25300
        assert feed.builder.candles()[-1].high == 25300

        svc.unsubscribe(feed, sub)
        assert feed.idle_since == now
    finally:
        await svc.close()
        async with db.system_session() as s:
            await s.execute(delete(HistoryCandle).where(HistoryCandle.key == "NIFTY"))


async def test_a_chart_that_falls_behind_is_resynced_not_flooded() -> None:
    sub = Subscriber()
    for i in range(sub.queue.maxsize + 10):
        sub.send({"type": "candle", "n": i})
    assert sub.resync and sub.queue.qsize() == 1
    assert sub.queue.get_nowait() == {"type": "resync"}
