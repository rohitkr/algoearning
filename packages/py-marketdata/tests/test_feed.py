"""The price feed: keys and bars, the Redis hub, the simulated and Breeze sources (Breeze through a fake SDK),
and the feed loop that ties them together."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from ae_marketdata.hub import Hub
from ae_marketdata.service import Feed
from ae_marketdata.sources import BreezeSource, FeedCode, SimulatedSource, breeze_expiry
from ae_marketdata.types import IST, Bar, BarBuilder, InstrumentKey, Tick
from redis.asyncio import Redis

from conftest import TEST_REDIS_URL

T0 = datetime(2026, 10, 1, 9, 15, 10, tzinfo=IST)
OPT = InstrumentKey("NIFTY", date(2026, 10, 6), 25000, "CE")


@pytest.fixture
async def hub() -> AsyncIterator[Hub]:
    r = Redis.from_url(TEST_REDIS_URL)
    try:
        await r.ping()
    except Exception:
        pytest.skip("Redis not reachable at TEST_REDIS_URL; run `make db-up`")
    await r.flushdb()
    yield Hub(r)
    await r.flushdb()
    await r.aclose()


def test_instrument_keys_round_trip() -> None:
    assert InstrumentKey.parse("NIFTY") == InstrumentKey("NIFTY")
    assert OPT.id == "NIFTY:2026-10-06:25000:CE" and InstrumentKey.parse(OPT.id) == OPT
    for bad in ("", "NIFTY:2026-10-06", "NIFTY:2026-10-06:25000:XX"):
        with pytest.raises(ValueError):
            InstrumentKey.parse(bad)


def test_bar_builder_emits_completed_minutes_only() -> None:
    b = BarBuilder()
    assert b.add("NIFTY", 100, T0) is None
    assert b.add("NIFTY", 103, T0 + timedelta(seconds=20)) is None
    assert b.add("NIFTY", 99, T0 + timedelta(seconds=40)) is None
    bar = b.add("NIFTY", 101, T0 + timedelta(seconds=60))  # first tick of 09:16 closes 09:15
    assert bar == Bar("NIFTY", T0.replace(second=0), 100, 103, 99, 99)
    assert b.close_until(T0 + timedelta(seconds=70)) == []  # 09:16 still open
    (quiet,) = b.close_until(T0 + timedelta(minutes=2))  # a quiet minute still closes
    assert quiet.ts.minute == 16 and quiet.close == 101


async def test_hub_round_trip_and_wanted_keys_expire(hub: Hub) -> None:
    bar = Bar("NIFTY", T0.replace(second=0), 1, 2, 0.5, 1.5, 10)
    await hub.publish_bar(bar)
    await hub.publish_tick(Tick("NIFTY", 25001.5, T0, 24990))
    assert await hub.bars("NIFTY", T0.date()) == [bar]
    assert (await hub.last(["NIFTY", "SENSEX"]))["NIFTY"].ltp == 25001.5
    await hub.want([OPT.id], ttl_s=60)
    await hub.want(["SENSEX"], ttl_s=1)
    assert await hub.wanted() == {OPT.id, "SENSEX"}
    assert await hub.wanted(now=__import__("time").time() + 5) == {OPT.id}
    assert await hub.count_api_call() == 1 and await hub.api_calls_today() == 1


def test_simulated_prices_are_sane() -> None:
    src = SimulatedSource(seed=1, now=lambda: T0)
    src.keys = {InstrumentKey("NIFTY"), OPT, InstrumentKey("NIFTY", date(2026, 10, 6), 24000, "CE")}
    ticks = {t.key: t for t in src.step()}
    spot = ticks["NIFTY"].ltp
    assert 24000 < spot < 26000 and ticks["NIFTY"].prev_close == 25000
    deep_itm = ticks["NIFTY:2026-10-06:24000:CE"].ltp
    assert deep_itm >= spot - 24000  # never below intrinsic value
    assert 0 < ticks[OPT.id].ltp < deep_itm


async def test_feed_publishes_simulated_ticks_and_builds_bars(hub: Hub) -> None:
    clock = [T0]
    src = SimulatedSource(seed=2, tick_s=0.01, now=lambda: clock[0])
    await hub.want([OPT.id])

    async def always() -> set[str]:
        return {"NIFTY"}

    feed = Feed(hub, src, always, now=lambda: clock[0])
    keys = await feed.sync()
    assert keys == {"NIFTY", OPT.id} and src.keys == {InstrumentKey("NIFTY"), OPT}
    for i in range(3):  # three ticks in 09:15, then one in 09:16
        clock[0] = T0 + timedelta(seconds=10 * i)
        for t in src.step():
            await feed.handle(t)
    clock[0] = T0 + timedelta(minutes=1)
    for t in src.step():
        await feed.handle(t)
    bars = await hub.bars("NIFTY", T0.date())
    assert [b.ts.minute for b in bars] == [15]
    assert set((await hub.last(["NIFTY", OPT.id])).keys()) == {"NIFTY", OPT.id}
    health = await hub.health()
    assert health["source"] == "simulated" and health["simulated"] is True and health["wanted"] == 2


class FakeBreeze:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.on_ticks: Any = None
        self.fail_session = False

    def generate_session(self, api_secret: str, session_token: str) -> None:
        if self.fail_session:
            raise Exception("Could not authenticate credentials. Please check token and keys.")
        self.calls.append(("session", {"token": session_token}))

    def ws_connect(self) -> None:
        self.calls.append(("ws_connect", {}))

    def ws_disconnect(self) -> None:
        self.calls.append(("ws_disconnect", {}))

    def subscribe_feeds(self, **kw: Any) -> None:
        self.calls.append(("subscribe", kw))

    def unsubscribe_feeds(self, **kw: Any) -> None:
        self.calls.append(("unsubscribe", kw))

    def get_stock_token_value(self, **kw: Any) -> tuple[str, bool]:
        return (f"4.1!{kw['stock_code']}{kw.get('strike_price', '')}", False)


async def test_breeze_subscribes_parses_and_reconnects_on_a_new_session(hub: Hub) -> None:
    fakes: list[FakeBreeze] = []

    def factory(api_key: str) -> FakeBreeze:
        fakes.append(FakeBreeze())
        return fakes[-1]

    token: list[str | None] = [None]
    codes = {"NIFTY": FeedCode("NSE", "NIFTY", "NFO"), "SENSEX": FeedCode("BSE", "BSESEN", "BFO")}
    src = BreezeSource(
        "key", "secret", lambda: token[0], lambda: codes, count_call=hub.count_api_call, sdk_factory=factory
    )
    q: asyncio.Queue[Any] = asyncio.Queue()
    await src.start(q)

    await src.sync({InstrumentKey("NIFTY")})
    assert fakes == [] and "log in" in (src.error or "") and src.status()["session"] == "login needed"

    token[0] = "tok1"
    await src.sync({InstrumentKey("NIFTY"), OPT})
    subs = [kw for name, kw in fakes[0].calls if name == "subscribe"]
    assert {"exchange_code": "NSE", "stock_code": "NIFTY", "product_type": "cash", "interval": "1minute"} in subs
    opt = [kw for kw in subs if kw.get("product_type") == "options"]
    assert opt[0]["expiry_date"] == breeze_expiry(OPT.expiry) == "06-Oct-2026"  # type: ignore[arg-type]
    assert opt[0]["right"] == "call" and opt[0]["strike_price"] == "25000"
    assert await hub.api_calls_today() == 1  # generate_session

    candle = {
        "interval": "1minute", "exchange_code": "NFO", "stock_code": "NIFTY", "expiry_date": "06-Oct-2026",
        "strike_price": "25000", "right_type": "Call", "low": "101", "high": "110", "open": "102",
        "close": "108.5", "volume": "650", "oi": "0", "datetime": "2026-10-01 09:15:00",
    }  # fmt: skip
    bar = src.parse(candle)
    assert bar == Bar(OPT.id, T0.replace(second=0, microsecond=0), 102, 110, 101, 108.5, 650)
    tick = src.parse({"symbol": "4.1!NIFTY", "last": 25012.35, "close": 24980.0})
    assert isinstance(tick, Tick) and tick.key == "NIFTY" and tick.prev_close == 24980.0
    assert src.parse({"symbol": "unknown", "last": 1}) is None

    fakes[0].on_ticks(candle)  # from the SDK's thread: lands on the queue
    await asyncio.sleep(0)
    assert (await asyncio.wait_for(q.get(), 1)) == bar

    await src.sync({InstrumentKey("NIFTY")})  # the option is no longer wanted
    assert any(n == "unsubscribe" and kw.get("product_type") == "options" for n, kw in fakes[0].calls)

    token[0] = "tok2"  # a new daily login: reconnect and resubscribe
    await src.sync({InstrumentKey("NIFTY")})
    assert ("ws_disconnect", {}) in fakes[0].calls and len(fakes) == 2 and src.status()["subscribed"] == 1


async def test_breeze_reports_a_rejected_session(hub: Hub) -> None:
    def factory(api_key: str) -> FakeBreeze:
        f = FakeBreeze()
        f.fail_session = True
        return f

    src = BreezeSource("key", "secret", lambda: "expired", lambda: {}, sdk_factory=factory)
    await src.start(asyncio.Queue())
    await src.sync({InstrumentKey("NIFTY")})
    assert "authenticate" in (src.error or "") and not src.status()["connected"]


async def test_breeze_session_is_stored_encrypted_for_the_day(db: Any) -> None:
    from ae_core.secrets import SecretBox, new_master_key
    from ae_marketdata.session import breeze_session, save_breeze_session
    from sqlalchemy import text

    box = SecretBox({1: __import__("base64").b64decode(new_master_key())}, 1)
    morning = datetime(2026, 10, 1, 8, 30, tzinfo=IST)
    async with db.system_session() as s:
        await s.execute(text("DELETE FROM platform_secrets"))
        assert await breeze_session(s, box, morning) == (None, None)
        expires = await save_breeze_session(s, box, " tok-123 ", None, morning)
        assert expires == datetime(2026, 10, 2, 0, 0, tzinfo=IST)
        raw = (await s.execute(text("SELECT value_enc FROM platform_secrets"))).scalar_one()
        assert b"tok-123" not in raw
        assert await breeze_session(s, box, morning + timedelta(hours=6)) == ("tok-123", expires)
        assert (await breeze_session(s, box, expires))[0] is None  # next day: log in again
        await s.execute(text("DELETE FROM platform_secrets"))
