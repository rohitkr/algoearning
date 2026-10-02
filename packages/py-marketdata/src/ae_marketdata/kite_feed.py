"""The platform's own Kite account as the price provider (ADR 0021), the alternative to Breeze: Kite's websocket
(KiteTicker) for live prices and Kite's historical API for backfills. Not any user's broker account: an admin logs in
to the platform's Kite Connect app once a trading day from Monitor > Market data.

Kite streams ticks only (no candles), so the Feed builds the 1-minute bars (builds_bars = False). Instruments are
addressed by Kite's numeric instrument token, looked up in Zerodha's public daily instrument lists.

Websocket protocol (https://kite.trade/docs/connect/v3/websocket/): wss://ws.kite.trade?api_key=..&access_token=..;
subscribe with {"a": "subscribe", "v": [tokens]} and {"a": "mode", "v": ["full", [tokens]]}. Binary messages carry
packets: 2 bytes packet count, then per packet 2 bytes length + the packet; a 1-byte message is a heartbeat. Prices
are in paise (divide by 100). Packet layouts by length:

    index      8: token, last price           28: + high, low, open, close, change     32: + exchange time
    tradable   8: token, last price           44: + last qty, avg, volume, buy qty, sell qty, open, high, low, close
                                             184: + last trade time, OI, OI high, OI low, exchange time, 5 + 5 depth"""

from __future__ import annotations

import asyncio
import contextlib
import json
import struct
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog
from ae_brokers.instruments import DUMP_URL, parse_dump

from .types import IST, InstrumentKey, Right, Tick

log = structlog.get_logger("ae_marketdata.kite")
WS_URL = "wss://ws.kite.trade"
API = "https://api.kite.trade"
SEGMENT_INDICES = 9
MAX_TOKENS = 3000  # Kite's limit per connection
LIST_READY = time(8, 30)  # IST: Zerodha has published the day's instrument list (a list loaded earlier is reloaded)


@dataclass(frozen=True)
class KiteCode:
    """How Kite names an underlying: the index's tradingsymbol on its cash exchange, options by `name` on the
    derivatives exchange."""

    exchange: str  # NSE | BSE
    symbol: str  # Kite's index tradingsymbol, e.g. "NIFTY 50", "NIFTY BANK", "SENSEX"
    name: str  # the options' `name` in the instrument list: our code (NIFTY, BANKNIFTY, SENSEX, ...)
    deriv_exchange: str  # NFO | BFO


# -- instrument tokens ------------------------------------------------------------------------------------------
class TokenBook:
    """Our instrument keys <-> Kite instrument tokens, from the day's instrument lists. Expired contracts are not in
    the lists, so they have no token (Kite has no data for them)."""

    def __init__(self) -> None:
        self.by_key: dict[str, int] = {}
        self.by_token: dict[int, str] = {}
        self.day: date | None = None

    def add_rows(self, rows: Iterable[Mapping[str, str]], codes: Mapping[str, KiteCode]) -> None:
        spots = {(c.exchange, c.symbol): u for u, c in codes.items()}
        options = {(c.deriv_exchange, c.name): u for u, c in codes.items()}
        for r in rows:
            try:
                token = int(r["instrument_token"])
            except (KeyError, ValueError):
                continue
            exchange = r.get("exchange", "")
            u = spots.get((exchange, r.get("tradingsymbol", "")))
            if u is not None:
                self._add(InstrumentKey(u), token)
                continue
            u = options.get((exchange, r.get("name", "")))
            if u is None or r.get("instrument_type") not in ("CE", "PE") or not r.get("expiry"):
                continue
            right: Right = "CE" if r["instrument_type"] == "CE" else "PE"
            try:
                key = InstrumentKey(u, date.fromisoformat(r["expiry"]), round(float(r["strike"])), right)
            except (KeyError, ValueError):
                continue
            self._add(key, token)

    def _add(self, key: InstrumentKey, token: int) -> None:
        self.by_key[key.id] = token
        self.by_token[token] = key.id

    async def load(self, codes: Mapping[str, KiteCode], client: httpx.AsyncClient, today: date) -> None:
        exchanges = sorted({c.exchange for c in codes.values()} | {c.deriv_exchange for c in codes.values()})
        rows: list[dict[str, str]] = []
        for ex in exchanges:
            r = await client.get(DUMP_URL.format(exchange=ex), timeout=60)
            r.raise_for_status()
            rows.extend(parse_dump(r.text))
        self.by_key, self.by_token = {}, {}
        self.add_rows(rows, codes)
        self.day = today
        log.info("kite instrument tokens loaded", tokens=len(self.by_key), exchanges=exchanges)


# -- packets ----------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Packet:
    token: int
    ltp: float
    close: float | None = None  # previous day's close
    volume: int | None = None
    oi: int | None = None
    bid: float | None = None
    ask: float | None = None
    exchange_ts: datetime | None = None


def _divisor(token: int) -> float:
    segment = token & 0xFF
    return 10_000_000.0 if segment == 3 else 10_000.0 if segment == 6 else 100.0  # currency segments differ


def _ints(b: bytes, n: int, start: int = 0) -> tuple[int, ...]:
    return struct.unpack_from(f">{n}I", b, start)


def _when(epoch: int) -> datetime | None:
    return datetime.fromtimestamp(epoch, IST) if epoch > 0 else None


def parse_packet(b: bytes) -> Packet | None:
    if len(b) < 8:
        return None
    token, raw_ltp = _ints(b, 2)
    d = _divisor(token)
    ltp = raw_ltp / d
    if token & 0xFF == SEGMENT_INDICES:
        if len(b) < 28:
            return Packet(token, ltp)
        close = _ints(b, 1, 20)[0] / d
        ts = _when(_ints(b, 1, 28)[0]) if len(b) >= 32 else None
        return Packet(token, ltp, close or None, exchange_ts=ts)
    if len(b) < 44:
        return Packet(token, ltp)
    _, _, volume, _, _, _, _, _, close = _ints(b, 9, 8)
    if len(b) < 184:
        return Packet(token, ltp, close / d or None, volume=volume)
    oi = _ints(b, 1, 48)[0]
    ts = _when(_ints(b, 1, 60)[0])
    bid_qty, bid = struct.unpack_from(">II", b, 64)  # best of 5 bids, then 5 offers at 64 + 60
    ask_qty, ask = struct.unpack_from(">II", b, 124)
    return Packet(
        token,
        ltp,
        close / d or None,
        volume=volume,
        oi=oi,
        bid=bid / d if bid_qty and bid else None,
        ask=ask / d if ask_qty and ask else None,
        exchange_ts=ts,
    )


def parse_message(msg: bytes) -> list[Packet]:
    """One binary websocket message -> its packets (none for a heartbeat)."""
    if len(msg) < 4:
        return []
    count = struct.unpack_from(">H", msg, 0)[0]
    out, i = [], 2
    for _ in range(count):
        if i + 2 > len(msg):
            break
        n = struct.unpack_from(">H", msg, i)[0]
        p = parse_packet(msg[i + 2 : i + 2 + n])
        if p is not None:
            out.append(p)
        i += 2 + n
    return out


# -- the live source --------------------------------------------------------------------------------------------
Connect = Callable[[str], Any]  # url -> an async context manager yielding a websocket (send, async iteration)


def _ws_connect(url: str) -> Any:
    from websockets.asyncio.client import connect

    return connect(url, ping_interval=None, max_size=2**22)  # Kite sends its own 1-byte heartbeats


class KiteSource:
    """The Source (sources.py) for Kite: one websocket in full mode for every wanted key. Without today's session
    it waits and says so; a rejected session is not retried until a new one is saved."""

    name = "kite"
    builds_bars = False

    def __init__(
        self,
        api_key: str,
        access_token: Callable[[], str | None],
        codes: Callable[[], Mapping[str, KiteCode]],
        *,
        connect: Connect = _ws_connect,
        http: Callable[[], httpx.AsyncClient] = httpx.AsyncClient,
        now: Callable[[], datetime] | None = None,
        retry_s: float = 2.0,
    ) -> None:
        self.api_key, self.access_token, self.codes = api_key, access_token, codes
        self.connect, self.http = connect, http
        self.now = now or (lambda: datetime.now(IST))
        self.retry_s = retry_s
        self.book = TokenBook()
        self.book_tried: datetime | None = None
        self.book_loaded: datetime | None = None
        self.wanted: dict[int, str] = {}  # token -> key id
        self.sent: set[int] = set()  # tokens subscribed on the open socket
        self.missing: list[str] = []  # wanted keys Kite has no token for
        self.ws: Any = None
        self.token: str | None = None  # the access token the socket runs with
        self.rejected: str | None = None  # an access token Kite refused
        self.last_message: datetime | None = None
        self.error: str | None = None
        self._queue: asyncio.Queue[Any] | None = None
        self._task: asyncio.Task[None] | None = None
        self._send_lock = asyncio.Lock()

    async def start(self, queue: asyncio.Queue[Any]) -> None:
        self._queue = queue

    async def _ensure_book(self) -> bool:
        now = self.now()
        today = now.date()
        loaded = self.book_loaded
        if self.book.day == today and not (loaded and loaded.time() < LIST_READY <= now.time()):
            return True
        if self.book_tried and self.now() - self.book_tried < timedelta(minutes=1):
            return bool(self.book.by_key)
        self.book_tried = self.now()
        try:
            async with self.http() as client:
                await self.book.load(self.codes(), client, today)
            self.book_loaded = now
        except Exception as exc:  # network: keep yesterday's tokens (index tokens never change) and retry
            self.error = f"Kite instrument list failed: {type(exc).__name__}: {exc}"[:300]
            log.warning("kite instrument list failed", error=self.error)
        return bool(self.book.by_key)

    async def sync(self, keys: set[InstrumentKey]) -> None:
        token = self.access_token()
        if not token or token == self.rejected:
            await self._close()
            self.error = (
                "Kite rejected today's session: log in again from Monitor > Market data"
                if token
                else "no Kite session today: log in from Monitor > Market data"
            )
            return
        if not await self._ensure_book():
            return
        wanted: dict[int, str] = {}
        missing = []
        for k in sorted(keys, key=lambda x: x.id):
            t = self.book.by_key.get(k.id)
            if t is None:
                missing.append(k.id)
            elif len(wanted) < MAX_TOKENS:
                wanted[t] = k.id
        self.wanted, self.missing = wanted, missing
        if self._task is None or self._task.done() or token != self.token:
            await self._close()
            self.token, self.error = token, None
            self._task = asyncio.create_task(self._run(token))
            return  # the connection subscribes everything wanted once it is open
        await self._push()

    async def _push(self) -> None:
        ws = self.ws
        if ws is None:
            return
        async with self._send_lock:
            add = sorted(set(self.wanted) - self.sent)
            drop = sorted(self.sent - set(self.wanted))
            if add:
                await ws.send(json.dumps({"a": "subscribe", "v": add}))
                await ws.send(json.dumps({"a": "mode", "v": ["full", add]}))
            if drop:
                await ws.send(json.dumps({"a": "unsubscribe", "v": drop}))
            self.sent = (self.sent | set(add)) - set(drop)

    async def _run(self, token: str) -> None:
        url = f"{WS_URL}?" + urlencode({"api_key": self.api_key, "access_token": token})
        failures = 0
        while self.token == token:
            try:
                async with self.connect(url) as ws:
                    self.ws, self.sent, self.error, failures = ws, set(), None, 0
                    log.info("kite connected")
                    await self._push()
                    async for msg in ws:
                        self._handle(msg)
                self.error = "Kite closed the connection"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in (401, 403):  # the access token is invalid or expired
                    self.rejected, self.error = token, "Kite rejected today's session: log in again from Monitor"
                    log.warning("kite session rejected", status=status)
                    self.ws, self.sent = None, set()
                    return
                self.error = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("kite connection lost", error=self.error)
            self.ws, self.sent = None, set()
            failures += 1
            await asyncio.sleep(min(self.retry_s * 2 ** (failures - 1), 30.0))

    def _handle(self, msg: bytes | str) -> None:
        if isinstance(msg, str):  # text: errors and order updates (we only care about errors)
            with contextlib.suppress(ValueError):
                d = json.loads(msg)
                if isinstance(d, dict) and d.get("type") == "error":
                    self.error = f"Kite: {d.get('data')}"[:300]
            return
        for ev in self.ticks(msg):
            self.last_message = self.now()
            if self._queue is not None:
                self._queue.put_nowait(ev)

    def ticks(self, msg: bytes) -> list[Tick]:
        out = []
        for p in parse_message(msg):
            key = self.wanted.get(p.token)
            if key is None or p.ltp <= 0:
                continue
            ts = p.exchange_ts or self.now()
            out.append(Tick(key, p.ltp, ts, p.close, bid=p.bid, ask=p.ask, volume=p.volume, oi=p.oi))
        return out

    async def _close(self) -> None:
        task, self._task, self.token = self._task, None, None
        self.ws, self.sent = None, set()
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def stop(self) -> None:
        await self._close()

    def status(self) -> dict[str, Any]:
        active = self.token is not None and self.token != self.rejected
        error = self.error
        if error is None and self.missing:
            shown = ", ".join(self.missing[:3]) + ("..." if len(self.missing) > 3 else "")
            error = f"not in Kite's instrument list: {shown}"
        return {
            "connected": self.ws is not None,
            "session": "active" if active else "login needed",
            "subscribed": len(self.sent),
            "last_message": self.last_message.isoformat() if self.last_message else None,
            "error": error,
        }


# -- history ----------------------------------------------------------------------------------------------------
MAX_MINUTE_DAYS = 60  # Kite returns at most 60 days of minute candles per request


@dataclass
class KiteHistory:
    """Kite's historical API behind the backfill's client interface (backfill.py), which speaks in Breeze's request
    parameters: they are turned into our instrument key, then Kite's token. A contract Kite has no token for (it has
    expired) returns no rows. Needs the platform Kite app's historical-data access."""

    api_key: str
    access_token: str
    underlying: str
    book: TokenBook
    client: httpx.AsyncClient = field(default_factory=lambda: httpx.AsyncClient(base_url=API, timeout=30.0))
    delay_s: float = 0.35  # Kite allows 3 historical requests a second
    calls: int = 0
    sleep: Callable[[float], Any] = field(default=asyncio.sleep)

    def key(self, params: Mapping[str, Any]) -> InstrumentKey:
        if params.get("product_type") != "options":
            return InstrumentKey(self.underlying)
        right: Right = "CE" if str(params.get("right", "")).lower().startswith("c") else "PE"
        expiry = date.fromisoformat(str(params["expiry_date"])[:10])
        return InstrumentKey(self.underlying, expiry, round(float(params["strike_price"])), right)

    async def _call(self, token: int, start: datetime, end: datetime) -> list[list[Any]]:
        err = ""
        for attempt in range(4):
            self.calls += 1
            try:
                r = await self.client.get(
                    f"/instruments/historical/{token}/minute",
                    params={"from": f"{start:%Y-%m-%d %H:%M:%S}", "to": f"{end:%Y-%m-%d %H:%M:%S}", "oi": 1},
                    headers={"X-Kite-Version": "3", "Authorization": f"token {self.api_key}:{self.access_token}"},
                )
            except httpx.HTTPError as exc:
                err = f"{type(exc).__name__}: {exc}"
            else:
                body = r.json() if r.content else {}
                if r.status_code == 200:
                    await self.sleep(self.delay_s)
                    return list((body.get("data") or {}).get("candles") or [])
                err = f"status={r.status_code} {body.get('message', '')}".strip()
                if r.status_code in (401, 403):
                    raise RuntimeError(f"Kite refused the history request: {err} (log in again from Monitor)")
                if r.status_code != 429 and r.status_code < 500:
                    raise RuntimeError(f"Kite history failed: {err}")
            log.warning("kite history call failed", attempt=attempt + 1, error=err)
            await self.sleep(2 * 2**attempt)
        raise RuntimeError(f"Kite history failed after 4 attempts: {err}")

    async def candles(self, params: dict[str, Any], start: datetime, end: datetime) -> list[dict[str, Any]]:
        """All 1-minute rows in [start, end] (naive IST), oldest first, shaped like Breeze's rows."""
        token = self.book.by_key.get(self.key(params).id)
        if token is None:
            return []
        rows: list[dict[str, Any]] = []
        a = start
        while a <= end:
            b = min(end, a + timedelta(days=MAX_MINUTE_DAYS) - timedelta(seconds=1))
            for c in await self._call(token, a, b):
                ts = datetime.strptime(str(c[0]), "%Y-%m-%dT%H:%M:%S%z").astimezone(IST)
                rows.append({"datetime": ts.isoformat(), "open": c[1], "high": c[2], "low": c[3], "close": c[4],
                             "volume": c[5], "open_interest": c[6] if len(c) > 6 else None})  # fmt: skip
            a = b + timedelta(seconds=1)
        return rows

    async def aclose(self) -> None:
        await self.client.aclose()
