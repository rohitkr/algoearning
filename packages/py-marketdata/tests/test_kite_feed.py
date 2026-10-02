"""The platform Kite account as price provider (ADR 0021): instrument tokens, KiteTicker's binary packets, the live
source over a fake websocket, and the historical API behind the backfill's client interface."""

from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import pytest
from ae_marketdata.backfill import to_rows
from ae_marketdata.kite_feed import KiteCode, KiteHistory, KiteSource, TokenBook, parse_message, parse_packet
from ae_marketdata.types import IST, InstrumentKey, Tick

CODES = {
    "NIFTY": KiteCode("NSE", "NIFTY 50", "NIFTY", "NFO"),
    "SENSEX": KiteCode("BSE", "SENSEX", "SENSEX", "BFO"),
}
NIFTY_T, SENSEX_T, OPT_T = 256265, 265, 12345602  # index tokens end in segment 9; NFO options in 2
OPT = InstrumentKey("NIFTY", date(2026, 10, 6), 25000, "CE")
HEADER = (
    "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,"
    "segment,exchange"
)
DUMPS = {
    "NSE": [
        f"{NIFTY_T},1001,NIFTY 50,NIFTY 50,0,,0,0,0,EQ,INDICES,NSE",
        "738561,2885,RELIANCE,RELIANCE,0,,0,0.05,1,EQ,NSE,NSE",
    ],
    "BSE": [f"{SENSEX_T},1,SENSEX,SENSEX,0,,0,0,0,EQ,INDICES,BSE"],
    "NFO": [
        f"{OPT_T},48225,NIFTY25O0625000CE,NIFTY,0,2026-10-06,25000.0,0.05,75,CE,NFO-OPT,NFO",
        "12345603,48226,NIFTY25O0625000PE,NIFTY,0,2026-10-06,25000.0,0.05,75,PE,NFO-OPT,NFO",
        "9999,1,NIFTY25OCTFUT,NIFTY,0,2026-10-28,0,0.05,75,FUT,NFO-FUT,NFO",
    ],
    "BFO": [],
}
T0 = datetime(2026, 10, 1, 9, 20, 5, tzinfo=IST)


def dump_transport() -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        ex = req.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, text="\n".join([HEADER, *DUMPS[ex]]))

    return httpx.MockTransport(handler)


def index_packet(token: int, ltp: float, close: float, ts: datetime | None = None) -> bytes:
    p = lambda v: round(v * 100)  # noqa: E731
    b = struct.pack(">7I", token, p(ltp), p(ltp + 10), p(ltp - 10), p(ltp - 5), p(close), 0)
    return b + struct.pack(">I", int(ts.timestamp())) if ts else b


def option_packet(token: int, ltp: float, bid: float, ask: float, volume: int, oi: int, ts: datetime) -> bytes:
    p = lambda v: round(v * 100)  # noqa: E731
    head = struct.pack(">11I", token, p(ltp), 75, p(ltp), volume, 1000, 1200, p(ltp), p(ltp), p(ltp), p(ltp - 20))
    tail = struct.pack(">5I", int(ts.timestamp()), oi, oi, oi, int(ts.timestamp()))
    bids = struct.pack(">IIHH", 150, p(bid), 3, 0) + bytes(48)
    asks = struct.pack(">IIHH", 225, p(ask), 2, 0) + bytes(48)
    return head + tail + bids + asks


def message(*packets: bytes) -> bytes:
    return struct.pack(">H", len(packets)) + b"".join(struct.pack(">H", len(p)) + p for p in packets)


def test_packets_index_and_option() -> None:
    q = parse_packet(index_packet(NIFTY_T, 25012.35, 24990.1))
    assert q is not None and (q.token, q.ltp, q.close, q.exchange_ts) == (NIFTY_T, 25012.35, 24990.1, None)
    f = parse_packet(index_packet(SENSEX_T, 82000.5, 81900, T0))
    assert f is not None and f.exchange_ts == T0
    o = parse_packet(option_packet(OPT_T, 101.5, 101.45, 101.6, 5_000_000, 2_400_000, T0))
    assert o is not None and (o.ltp, o.bid, o.ask, o.volume, o.oi, o.close) == (
        101.5,
        101.45,
        101.6,
        5_000_000,
        2_400_000,
        81.5,
    )
    assert parse_packet(struct.pack(">II", OPT_T, 9050)).ltp == 90.5  # type: ignore[union-attr]  # ltp mode
    assert parse_message(b"\x00") == []  # heartbeat
    assert [p.token for p in parse_message(message(index_packet(NIFTY_T, 100, 100), struct.pack(">II", OPT_T, 1)))] == [
        NIFTY_T,
        OPT_T,
    ]


async def test_token_book_maps_indices_and_listed_options() -> None:
    book = TokenBook()
    async with httpx.AsyncClient(transport=dump_transport()) as c:
        await book.load(CODES, c, T0.date())
    assert book.by_key == {"NIFTY": NIFTY_T, "SENSEX": SENSEX_T, OPT.id: OPT_T, "NIFTY:2026-10-06:25000:PE": 12345603}
    assert book.by_token[OPT_T] == OPT.id and book.day == T0.date()


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[Any] = []
        self.inbox: asyncio.Queue[bytes | str | None] = asyncio.Queue()

    async def send(self, m: str) -> None:
        self.sent.append(json.loads(m))

    def __aiter__(self) -> FakeSocket:
        return self

    async def __anext__(self) -> bytes | str:
        m = await self.inbox.get()
        if m is None:
            raise StopAsyncIteration
        return m


class Rejected(Exception):
    def __init__(self) -> None:
        super().__init__("server rejected WebSocket connection: HTTP 403")
        self.response = type("R", (), {"status_code": 403})()


class FakeKite:
    """Stands in for websockets.connect: one FakeSocket per connection, or a 403 for refused tokens."""

    def __init__(self, refuse: set[str] | None = None) -> None:
        self.urls: list[str] = []
        self.sockets: list[FakeSocket] = []
        self.refuse = refuse or set()

    def __call__(self, url: str) -> Any:
        self.urls.append(url)
        kite = self

        class Ctx:
            async def __aenter__(self) -> FakeSocket:
                if any(f"access_token={t}" in url for t in kite.refuse):
                    raise Rejected()
                kite.sockets.append(FakeSocket())
                return kite.sockets[-1]

            async def __aexit__(self, *a: object) -> None:
                return None

        return Ctx()


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.fixture
async def source() -> AsyncIterator[tuple[KiteSource, FakeKite, list[str | None], asyncio.Queue[Any]]]:
    kite = FakeKite(refuse={"bad"})
    token: list[str | None] = [None]
    src = KiteSource(
        "key 1",
        lambda: token[0],
        lambda: CODES,
        connect=kite,
        http=lambda: httpx.AsyncClient(transport=dump_transport()),
        now=lambda: T0,
        retry_s=0.01,
    )
    q: asyncio.Queue[Any] = asyncio.Queue()
    await src.start(q)
    yield src, kite, token, q
    await src.stop()


async def test_kite_waits_for_a_session_then_streams(source: Any) -> None:
    src, kite, token, q = source
    await src.sync({InstrumentKey("NIFTY")})
    assert src.status()["session"] == "login needed" and "log in" in src.status()["error"] and kite.urls == []

    token[0] = "tok-1"
    await src.sync({InstrumentKey("NIFTY"), OPT})
    await settle()
    assert kite.urls == ["wss://ws.kite.trade?api_key=key+1&access_token=tok-1"]
    ws = kite.sockets[0]
    assert ws.sent == [{"a": "subscribe", "v": [NIFTY_T, OPT_T]}, {"a": "mode", "v": ["full", [NIFTY_T, OPT_T]]}]
    assert src.status() | {"last_message": None} == {
        "connected": True, "session": "active", "subscribed": 2, "last_message": None, "error": None,
    }  # fmt: skip

    ws.inbox.put_nowait(b"\x00")  # heartbeat
    ws.inbox.put_nowait(
        message(index_packet(NIFTY_T, 25012.35, 24990.1, T0), option_packet(OPT_T, 101.5, 101.45, 101.6, 900, 50, T0))
    )
    ws.inbox.put_nowait(message(index_packet(SENSEX_T, 82000, 81900, T0)))  # not wanted: ignored
    await settle()
    assert [q.get_nowait() for _ in range(q.qsize())] == [
        Tick("NIFTY", 25012.35, T0, 24990.1),
        Tick(OPT.id, 101.5, T0, 81.5, bid=101.45, ask=101.6, volume=900, oi=50),
    ]

    await src.sync({InstrumentKey("NIFTY"), InstrumentKey("SENSEX")})  # option no longer wanted, Sensex is
    assert ws.sent[2:] == [
        {"a": "subscribe", "v": [SENSEX_T]},
        {"a": "mode", "v": ["full", [SENSEX_T]]},
        {"a": "unsubscribe", "v": [OPT_T]},
    ]

    await src.sync({InstrumentKey("NIFTY"), InstrumentKey("NIFTY", date(2026, 9, 1), 24000, "CE")})
    assert "not in Kite's instrument list: NIFTY:2026-09-01:24000:CE" in src.status()["error"]  # expired

    ws.inbox.put_nowait(json.dumps({"type": "error", "data": "too many instruments"}))
    await settle()
    assert src.status()["error"] == "Kite: too many instruments"


async def test_kite_reconnects_after_a_drop_and_on_a_new_session(source: Any) -> None:
    src, kite, token, _ = source
    token[0] = "tok-1"
    await src.sync({InstrumentKey("NIFTY")})
    await settle()
    kite.sockets[0].inbox.put_nowait(None)  # Kite closes the socket
    await asyncio.sleep(0.05)
    assert len(kite.sockets) == 2 and kite.sockets[1].sent[0] == {"a": "subscribe", "v": [NIFTY_T]}

    token[0] = "tok-2"  # the next day's login
    await src.sync({InstrumentKey("NIFTY")})
    await settle()
    assert kite.urls[-1].endswith("access_token=tok-2") and src.status()["connected"]


async def test_kite_does_not_retry_a_rejected_session(source: Any) -> None:
    src, kite, token, _ = source
    token[0] = "bad"
    await src.sync({InstrumentKey("NIFTY")})
    await asyncio.sleep(0.05)
    assert len(kite.urls) == 1 and not src.status()["connected"]
    await src.sync({InstrumentKey("NIFTY")})
    await asyncio.sleep(0.05)
    assert len(kite.urls) == 1 and "rejected" in src.status()["error"]
    assert src.status()["session"] == "login needed"


async def test_kite_history_pages_by_60_days_and_skips_expired_contracts() -> None:
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.url.path.endswith(f"/{OPT_T}/minute"):
            return httpx.Response(403, json={"status": "error", "message": "Invalid token"})
        day = req.url.params["from"][:10]
        candles = [[f"{day}T09:15:00+0530", 1, 2, 0.5, 1.5, 10], [f"{day}T15:31:00+0530", 1, 1, 1, 1, 0]]
        return httpx.Response(200, json={"status": "success", "data": {"candles": candles}})

    book = TokenBook()
    book.add_rows([dict(zip(HEADER.split(","), r.split(","), strict=True)) for r in DUMPS["NSE"] + DUMPS["NFO"]], CODES)
    client = httpx.AsyncClient(base_url="https://api.kite.trade", transport=httpx.MockTransport(handler))

    async def no_sleep(_: float) -> None:
        return None

    h = KiteHistory("key", "tok", "NIFTY", book, client=client, sleep=no_sleep)
    params = {"interval": "1minute", "stock_code": "NIFTY", "exchange_code": "NSE", "product_type": "cash"}
    raw = await h.candles(params, datetime(2026, 6, 1, 9, 15), datetime(2026, 9, 30, 15, 29))
    assert [r.url.params["from"] for r in calls] == [
        "2026-06-01 09:15:00",
        "2026-07-31 09:15:00",
        "2026-09-29 09:15:00",
    ]
    assert calls[0].headers["Authorization"] == "token key:tok" and calls[0].url.params["oi"] == "1"
    assert h.calls == 3 and raw[0]["datetime"] == "2026-06-01T09:15:00+05:30"
    rows = to_rows("NIFTY", raw)  # the backfill's own conversion: session minutes only
    assert len(rows) == 3 and rows[0]["ts"] == datetime(2026, 6, 1, 9, 15, tzinfo=IST) and rows[0]["close"] == 1.5

    expired = {
        "product_type": "options",
        "expiry_date": "2026-09-01T07:00:00.000Z",
        "right": "call",
        "strike_price": "24000",
    }
    assert await h.candles(expired, datetime(2026, 8, 25, 9, 15), datetime(2026, 9, 1, 15, 29)) == []
    listed = expired | {"expiry_date": "2026-10-06T07:00:00.000Z", "strike_price": "25000"}
    with pytest.raises(RuntimeError, match="log in again"):
        await h.candles(listed, datetime(2026, 10, 1, 9, 15), datetime(2026, 10, 1, 15, 29))
    await h.aclose()


async def test_kite_session_is_stored_encrypted(db: Any) -> None:
    from ae_core.secrets import SecretBox, new_master_key
    from ae_marketdata.session import KITE_SESSION, load_session, save_session
    from sqlalchemy import text

    box = SecretBox({1: __import__("base64").b64decode(new_master_key())}, 1)
    expires = datetime(2026, 10, 2, 6, 0, tzinfo=IST)
    async with db.system_session() as s:
        await s.execute(text("DELETE FROM platform_secrets"))
        await save_session(s, box, KITE_SESSION, " acc-9 ", expires, None)
        assert b"acc-9" not in (await s.execute(text("SELECT value_enc FROM platform_secrets"))).scalar_one()
        assert await load_session(s, box, KITE_SESSION, expires - timedelta(hours=1)) == ("acc-9", expires)
        assert (await load_session(s, box, KITE_SESSION, expires))[0] is None
        await s.execute(text("DELETE FROM platform_secrets"))


def test_breeze_stays_the_default_provider() -> None:
    from ae_marketdata.sources import choose_source

    breeze, kite = {"BREEZE_API_KEY": "b", "BREEZE_API_SECRET": "s"}, {"KITE_FEED_API_KEY": "k"}
    assert choose_source({}) == "simulated"
    assert choose_source(breeze) == "breeze" and choose_source(breeze | kite) == "breeze"
    assert choose_source(kite) == "kite" and choose_source({"BREEZE_API_KEY": "b"} | kite) == "kite"
    assert choose_source(breeze | kite | {"MARKET_DATA_SOURCE": "Kite"}) == "kite"
    assert choose_source(kite | {"MARKET_DATA_SOURCE": "breeze"}) == "breeze"


async def test_kite_reloads_an_instrument_list_loaded_before_zerodha_publishes_the_day_s() -> None:
    clock = [datetime(2026, 10, 1, 7, 50, tzinfo=IST)]
    loads: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        loads.append(req.url.path)
        return dump_transport().handle_request(req)

    transport = httpx.MockTransport(handler)
    src = KiteSource(
        "k", lambda: None, lambda: CODES, http=lambda: httpx.AsyncClient(transport=transport), now=lambda: clock[0]
    )
    assert await src._ensure_book() and len(loads) == 4
    clock[0] = clock[0].replace(hour=8, minute=20)
    assert await src._ensure_book() and len(loads) == 4  # same list until 08:30
    clock[0] = clock[0].replace(hour=8, minute=31)
    assert await src._ensure_book() and len(loads) == 8  # the day's list
    clock[0] = clock[0].replace(hour=14)
    assert await src._ensure_book() and len(loads) == 8
