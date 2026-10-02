"""Where prices come from. A Source subscribes to instrument keys and puts Bars and Ticks on a queue; the Feed
(service.py) publishes them. Three sources, one at a time (`choose_source`):

    BreezeSource     ICICI Breeze streaming (the platform's licensed-to-us feed, ADR 0006): 1-minute OHLC candles
                     and last-price quotes over Breeze's websocket, so streaming costs no REST API calls. The default.
    KiteSource       the platform's own Kite account (kite_feed.py, ADR 0021): ticks over Kite's websocket
    SimulatedSource  random-walk prices for local development and demos, clearly labelled as simulated

Breeze details marked "verify" below come from the SDK source, not yet from a live session."""

from __future__ import annotations

import asyncio
import math
import random
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

import structlog

from .types import IST, Bar, InstrumentKey, Right, Tick

log = structlog.get_logger("ae_marketdata.sources")
Event = Bar | Tick


def choose_source(env: Mapping[str, str]) -> str:
    """breeze | kite | simulated, or what MARKET_DATA_SOURCE names. auto (the default): Breeze when its keys are set,
    else the platform Kite app when its key is set, else simulated prices."""
    choice = env.get("MARKET_DATA_SOURCE", "auto").lower() or "auto"
    if choice != "auto":
        return choice
    if env.get("BREEZE_API_KEY") and env.get("BREEZE_API_SECRET"):
        return "breeze"
    return "kite" if env.get("KITE_FEED_API_KEY") else "simulated"


class Source(Protocol):
    name: str
    builds_bars: bool  # False: the Feed turns this source's ticks into bars

    async def start(self, queue: asyncio.Queue[Event]) -> None: ...
    async def sync(self, keys: set[InstrumentKey]) -> None: ...
    async def stop(self) -> None: ...
    def status(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class FeedCode:
    """How Breeze names an underlying: the index on its cash exchange, options on the derivatives exchange."""

    spot_exchange: str  # NSE | BSE
    stock_code: str  # Breeze's code, e.g. NIFTY, CNXBAN, BSESEN
    deriv_exchange: str  # NFO | BFO


# -- simulated ---------------------------------------------------------------------------------------------------
SIM_SPOT = {"NIFTY": 25000.0, "BANKNIFTY": 55000.0, "FINNIFTY": 26000.0, "MIDCPNIFTY": 13000.0, "SENSEX": 82000.0}


class SimulatedSource:
    """Every second, each subscribed index moves a little (random walk) and each option is priced from it with a
    simple intrinsic + time-value model. Good enough to exercise strategies and screens, not for research."""

    name = "simulated"
    builds_bars = False

    def __init__(self, seed: int | None = None, tick_s: float = 1.0, now: Callable[[], datetime] | None = None):
        self.rng = random.Random(seed)  # noqa: S311 (simulated prices, not security)
        self.tick_s = tick_s
        self.now = now or (lambda: datetime.now(IST))
        self.spot: dict[str, float] = {}
        self.prev_close: dict[str, float] = {}
        self.keys: set[InstrumentKey] = set()
        self._task: asyncio.Task[None] | None = None

    def option_price(self, k: InstrumentKey, spot: float, today: date) -> float:
        assert k.expiry is not None and k.strike is not None
        intrinsic = max(0.0, spot - k.strike) if k.right == "CE" else max(0.0, k.strike - spot)
        days = max((k.expiry - today).days, 0) + 0.3
        vol = spot * 0.009 * math.sqrt(days)  # ~14% annualised
        time_value = vol * 0.4 * math.exp(-abs(spot - k.strike) / (1.5 * vol))
        return round(max(0.05, intrinsic + time_value), 2)

    def step(self) -> list[Tick]:
        now = self.now()
        for u in {k.underlying for k in self.keys}:
            if u not in self.spot:
                self.spot[u] = self.prev_close[u] = SIM_SPOT.get(u, 10000.0)
            self.spot[u] = round(self.spot[u] * (1 + self.rng.gauss(0, 0.00025)), 2)
        ticks = []
        for k in sorted(self.keys, key=lambda x: x.id):
            s = self.spot[k.underlying]
            if k.is_option:
                px = self.option_price(k, s, now.date())
                half = max(0.05, round(px * 0.002, 2))  # a tight two-sided quote, like a liquid weekly option
                ticks.append(Tick(k.id, px, now, bid=max(0.05, px - half), ask=px + half, volume=500_000, oi=2_000_000))
            else:
                ticks.append(Tick(k.id, s, now, self.prev_close[k.underlying]))
        return ticks

    async def start(self, queue: asyncio.Queue[Event]) -> None:
        async def loop() -> None:
            while True:
                for t in self.step():
                    queue.put_nowait(t)
                await asyncio.sleep(self.tick_s)

        self._task = asyncio.create_task(loop())

    async def sync(self, keys: set[InstrumentKey]) -> None:
        self.keys = set(keys)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    def status(self) -> dict[str, Any]:
        return {"connected": True, "session": "not needed", "subscribed": len(self.keys)}


# -- Breeze ------------------------------------------------------------------------------------------------------
def breeze_expiry(d: date) -> str:
    return d.strftime("%d-%b-%Y")  # the SDK's security master format, e.g. 06-Oct-2026 (verify)


def _num(v: Any) -> float:
    return float(v)


def _opt(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _opt_int(v: Any) -> int | None:
    f = _opt(v)
    return None if f is None else int(f)


class BreezeSource:
    """Breeze's websocket through the official SDK (it runs its own threads; callbacks hop onto the event loop).

    Each key gets two subscriptions: the 1-minute OHLC stream (bars) and exchange quotes (last price). A session
    token comes from the daily admin login (Monitor); without a valid one the source waits and says so."""

    name = "breeze"
    builds_bars = True

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        session_token: Callable[[], str | None],
        codes: Callable[[], Mapping[str, FeedCode]],
        count_call: Callable[[int], Any] | None = None,
        sdk_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.api_key, self.api_secret = api_key, api_secret
        self.session_token, self.codes = session_token, codes
        self.count_call = count_call
        self.sdk_factory = sdk_factory or _breeze_sdk
        self.sdk: Any = None
        self.subscribed: set[InstrumentKey] = set()
        self.token_to_key: dict[str, str] = {}
        self.code_to_underlying: dict[str, str] = {}
        self.connected_with: str | None = None  # the session token the socket was opened with
        self.last_message: datetime | None = None
        self.error: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[Event] | None = None
        self._lock = threading.Lock()

    async def start(self, queue: asyncio.Queue[Event]) -> None:
        self._loop, self._queue = asyncio.get_running_loop(), queue

    async def _connect(self, token: str) -> None:
        def connect() -> Any:
            sdk = self.sdk_factory(self.api_key)
            sdk.generate_session(api_secret=self.api_secret, session_token=token)
            sdk.on_ticks = self._on_ticks
            sdk.ws_connect()
            return sdk

        if self.count_call:
            await _maybe_await(self.count_call(1))  # generate_session makes one REST call
        self.sdk = await asyncio.to_thread(connect)
        self.connected_with, self.subscribed, self.error = token, set(), None
        log.info("breeze connected")

    async def _disconnect(self) -> None:
        sdk, self.sdk, self.connected_with, self.subscribed = self.sdk, None, None, set()
        if sdk is not None:
            try:
                await asyncio.to_thread(sdk.ws_disconnect)
            except Exception:  # noqa: S110 (closing a dead socket may fail; we are dropping it anyway)
                pass

    def _params(self, k: InstrumentKey) -> dict[str, str] | None:
        code = self.codes().get(k.underlying)
        if code is None:
            return None
        self.code_to_underlying[code.stock_code] = k.underlying
        if not k.is_option:
            return {"exchange_code": code.spot_exchange, "stock_code": code.stock_code, "product_type": "cash"}
        assert k.expiry is not None
        return {
            "exchange_code": code.deriv_exchange,
            "stock_code": code.stock_code,
            "product_type": "options",
            "expiry_date": breeze_expiry(k.expiry),
            "strike_price": str(k.strike),
            "right": "call" if k.right == "CE" else "put",
        }

    async def sync(self, keys: set[InstrumentKey]) -> None:
        token = self.session_token()
        if not token:
            if self.sdk is not None:
                await self._disconnect()
            self.error = "no Breeze session today: log in from Monitor > Market data"
            return
        try:
            if self.sdk is None or token != self.connected_with:
                await self._disconnect()
                await self._connect(token)
            add, drop = keys - self.subscribed, self.subscribed - keys
            for k in sorted(add, key=lambda x: x.id):
                p = self._params(k)
                if p is None:
                    continue
                await asyncio.to_thread(self._subscribe, k, p)
                self.subscribed.add(k)
            for k in sorted(drop, key=lambda x: x.id):
                p = self._params(k)
                if p is not None:
                    await asyncio.to_thread(self._unsubscribe, p)
                self.subscribed.discard(k)
        except Exception as exc:  # session rejected, network: report, drop the socket, retry on the next sync
            self.error = f"{type(exc).__name__}: {exc}"[:300]
            log.warning("breeze sync failed", error=self.error)
            await self._disconnect()

    def _subscribe(self, k: InstrumentKey, p: dict[str, str]) -> None:
        self.sdk.subscribe_feeds(**p, interval="1minute")
        self.sdk.subscribe_feeds(**p, get_exchange_quotes=True, get_market_depth=False)
        tokens = self.sdk.get_stock_token_value(**p, get_exchange_quotes=True, get_market_depth=False)
        quote_token = tokens[0] if isinstance(tokens, tuple | list) else tokens
        if quote_token:
            with self._lock:
                self.token_to_key[str(quote_token)] = k.id

    def _unsubscribe(self, p: dict[str, str]) -> None:
        self.sdk.unsubscribe_feeds(**p, interval="1minute")
        self.sdk.unsubscribe_feeds(**p, get_exchange_quotes=True, get_market_depth=False)

    # SDK threads -> event loop
    def _on_ticks(self, data: dict[str, Any]) -> None:
        try:
            ev = self.parse(data)
        except Exception as exc:
            log.warning("breeze message not understood", error=str(exc), keys=sorted(data)[:12])
            return
        if ev is not None and self._loop is not None and self._queue is not None:
            self.last_message = datetime.now(IST)
            self._loop.call_soon_threadsafe(self._queue.put_nowait, ev)

    def parse(self, d: Mapping[str, Any]) -> Event | None:
        if "interval" in d:  # OHLC candle
            if d.get("interval") not in ("1minute", "1MIN"):
                return None
            u = self.code_to_underlying.get(str(d.get("stock_code")))
            if u is None:
                return None
            if d.get("exchange_code") in ("NSE", "BSE"):
                key = InstrumentKey(u)
            else:
                right: Right = "CE" if str(d.get("right_type", "")).lower().startswith("c") else "PE"
                expiry = datetime.strptime(str(d["expiry_date"]), "%d-%b-%Y").date()
                key = InstrumentKey(u, expiry, round(float(d["strike_price"])), right)
            ts = datetime.strptime(str(d["datetime"]), "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)  # minute start (verify)
            return Bar(
                key.id,
                ts,
                _num(d["open"]),
                _num(d["high"]),
                _num(d["low"]),
                _num(d["close"]),
                int(float(d.get("volume") or 0)),
            )
        with self._lock:
            key_id = self.token_to_key.get(str(d.get("symbol")))
        last = d.get("last", d.get("last_trade_price"))
        if key_id is None or last in (None, ""):
            return None
        prev = d.get("close", d.get("previous_close"))
        return Tick(
            key_id,
            _num(last),
            datetime.now(IST),
            _num(prev) if prev not in (None, "", 0) else None,
            # best bid / offer, total traded quantity and open interest of the exchange quote (verify field names)
            bid=_opt(d.get("bPrice")),
            ask=_opt(d.get("sPrice")),
            volume=_opt_int(d.get("ttq")),
            oi=_opt_int(d.get("OI")),
        )

    async def stop(self) -> None:
        await self._disconnect()

    def status(self) -> dict[str, Any]:
        return {
            "connected": self.sdk is not None,
            "session": "active" if self.connected_with else "login needed",
            "subscribed": len(self.subscribed),
            "last_message": self.last_message.isoformat() if self.last_message else None,
            "error": self.error,
        }


def _breeze_sdk(api_key: str) -> Any:
    from breeze_connect import BreezeConnect  # imported lazily: tests and the simulated feed never need it

    return BreezeConnect(api_key=api_key)


async def _maybe_await(v: Any) -> Any:
    return await v if asyncio.iscoroutine(v) else v
