"""Live SMC charts: candles of any timeframe built from the platform feed, with Smart Money Concepts zones, pushed to
every chart on every open page.

One ChartFeed per (index, timeframe) however many charts show it: it is seeded once from stored history and the
feed's bars in Redis, then follows the feed's `md:bar` / `md:tick` channels through ae_marketdata.candles, and
recomputes the SMC overlay (ae_marketdata.smc_overlay) whenever a candle closes or a late bar revises one. Each chart
on a page is a Subscriber with its own queue; a chart that falls behind gets a fresh snapshot instead of a backlog.

Messages (JSON, one per server-sent event):

    snapshot  everything a chart needs: closed candles, the forming one, the overlay, the feed status
    candle    a candle changed: the forming one (closed=false) or one that just closed (closed=true)
    revise    an already closed candle was corrected by a late 1-minute bar (redraw the series)
    smc       the overlay, recomputed: replaces the previous one entirely
    status    the feed went live / simulated / down"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog
from ae_db.session import Database
from ae_marketdata.candles import CandleBuilder, Update
from ae_marketdata.history import recent_bars
from ae_marketdata.hub import BAR_CHANNEL, TICK_CHANNEL, Hub
from ae_marketdata.smc_overlay import Overlay, compute
from ae_marketdata.types import IST, Bar, Tick
from redis.asyncio import Redis

log = structlog.get_logger("ae_api.charts")

FeedStatus = Literal["live", "simulated", "down"]
STALE = timedelta(minutes=2)  # no price for this long: the feed is down (or the market is closed)
TIMEFRAMES = (1, 3, 5, 15)  # minutes
SESSIONS = {1: 2, 3: 4, 5: 8, 15: 20}  # trading days of history a chart starts with, per timeframe
SWING_LENGTH = 5  # candles on each side of a swing high / low
LINGER = timedelta(minutes=2)  # keep a feed nobody watches for a while (switching back and forth is instant)
QUEUE_MAX = 500


async def feed_status(hub: Hub, last: dict[str, Tick]) -> FeedStatus:
    """live / simulated when one of these prices is fresh, else down."""
    now = datetime.now(UTC)
    if not any(now - t.ts < STALE for t in last.values()):
        return "down"
    return "simulated" if (await hub.health()).get("simulated") else "live"


@dataclass(frozen=True)
class ChartInstrument:
    code: str
    name: str
    session_open: str
    session_close: str


class Subscriber:
    """One chart on one page."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAX)
        self.resync = False  # fell behind: the stream sends a snapshot instead of the dropped messages

    def send(self, msg: dict[str, Any]) -> None:
        if self.resync:
            return
        try:
            self.queue.put_nowait(msg)
        except asyncio.QueueFull:
            self.resync = True
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait({"type": "resync"})


@dataclass
class ChartFeed:
    inst: ChartInstrument
    minutes: int
    builder: CandleBuilder
    overlay: Overlay = field(default_factory=Overlay)
    subscribers: set[Subscriber] = field(default_factory=set)
    pending: list[Bar | Tick] | None = field(default_factory=list)  # events seen while seeding (None: seeded)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    status: FeedStatus = "down"
    last_price: float | None = None
    idle_since: datetime | None = None
    smc_dirty: bool = False
    smc_task: asyncio.Task[None] | None = None
    error: str | None = None

    @property
    def id(self) -> tuple[str, int]:
        return (self.inst.code, self.minutes)

    def snapshot(self) -> dict[str, Any]:
        forming = self.builder.forming
        return {
            "type": "snapshot",
            "key": self.inst.code,
            "name": self.inst.name,
            "timeframe": self.minutes,
            "candles": [c.to_dict() for c in self.builder.candles()],
            "forming": forming.to_dict() if forming else None,
            "smc": self.overlay.to_dict(),
            "status": self.status,
            "last_price": self.last_price,
            "error": self.error,
        }

    def broadcast(self, msg: dict[str, Any]) -> None:
        for s in list(self.subscribers):
            s.send(msg)


def update_messages(u: Update) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"type": "candle", "candle": c.to_dict(), "closed": True} for c in u.closed]
    if u.revised is not None:
        out.append({"type": "revise", "candle": u.revised.to_dict()})
    if u.forming_changed and u.forming is not None:
        out.append({"type": "candle", "candle": u.forming.to_dict(), "closed": False})
    return out


class ChartService:
    def __init__(
        self,
        redis: Redis,
        db: Database,
        *,
        now: Callable[[], datetime] | None = None,
        tick_s: float = 1.0,
    ) -> None:
        self.redis = redis
        self.hub = Hub(redis)
        self.db = db
        self.now = now or (lambda: datetime.now(IST))
        self.tick_s = tick_s
        self.feeds: dict[tuple[str, int], ChartFeed] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._listening = asyncio.Event()

    # -- subscribing ---------------------------------------------------------------------------------------------
    async def subscribe(self, inst: ChartInstrument, minutes: int) -> tuple[ChartFeed, Subscriber]:
        if minutes not in TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {TIMEFRAMES}")
        await self._start()
        feed = self.feeds.get((inst.code, minutes))
        if feed is None:
            builder = CandleBuilder(minutes, inst.session_open, inst.session_close)
            feed = self.feeds[(inst.code, minutes)] = ChartFeed(inst, minutes, builder)
            try:
                await self._seed(feed)
            except BaseException:
                del self.feeds[(inst.code, minutes)]
                feed.ready.set()
                raise
        else:
            await feed.ready.wait()
        sub = Subscriber()
        feed.subscribers.add(sub)
        feed.idle_since = None
        return feed, sub

    def unsubscribe(self, feed: ChartFeed, sub: Subscriber) -> None:
        feed.subscribers.discard(sub)
        if not feed.subscribers:
            feed.idle_since = self.now()

    async def _seed(self, feed: ChartFeed) -> None:
        """History first, then the events that arrived meanwhile (the listener was already buffering them)."""
        try:
            bars = await recent_bars(self.db, self.hub, feed.inst.code, SESSIONS[feed.minutes], self.now().date())
            feed.builder.seed(bars)
            feed.error = None
        except Exception as exc:  # a chart without history still shows live candles
            log.warning("chart history unavailable", key=feed.inst.code, error=str(exc))
            feed.error = "history unavailable"
        last = await self.hub.last([feed.inst.code])
        if feed.inst.code in last:
            feed.last_price = last[feed.inst.code].ltp
        feed.status = await feed_status(self.hub, last)
        pending, feed.pending = feed.pending or [], None
        for ev in pending:
            self._apply(feed, ev, broadcast=False)
        feed.builder.close_due(self.now())
        feed.overlay = await asyncio.to_thread(compute, feed.builder.candles(), SWING_LENGTH)
        feed.ready.set()

    # -- the feed ------------------------------------------------------------------------------------------------
    async def _start(self) -> None:
        if not self._tasks:
            self._tasks = [asyncio.create_task(self._listen()), asyncio.create_task(self._clock())]
        await self._listening.wait()

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks = []
        for f in self.feeds.values():
            if f.smc_task:
                f.smc_task.cancel()

    async def _listen(self) -> None:
        """Follow the feed's bars and ticks. On a lost connection, resubscribe and re-seed every chart, since
        bars may have been missed."""
        first = True
        while True:
            ps = self.redis.pubsub()
            try:
                await ps.subscribe(BAR_CHANNEL, TICK_CHANNEL)
                self._listening.set()
                if not first:
                    await self._reseed_all()
                first = False
                while True:
                    m = await ps.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if m is None:
                        continue
                    channel = m["channel"].decode() if isinstance(m["channel"], bytes) else m["channel"]
                    ev: Bar | Tick = Bar.from_json(m["data"]) if channel == BAR_CHANNEL else Tick.from_json(m["data"])
                    for feed in [f for f in self.feeds.values() if f.inst.code == ev.key]:
                        self._apply(feed, ev)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("chart feed connection lost", error=str(exc))
                await asyncio.sleep(2)
            finally:
                with contextlib.suppress(Exception):
                    await ps.aclose()  # type: ignore[no-untyped-call]

    async def _reseed_all(self) -> None:
        for feed in list(self.feeds.values()):
            feed.pending = []
            await self._seed(feed)
            feed.broadcast(feed.snapshot())

    def _apply(self, feed: ChartFeed, ev: Bar | Tick, broadcast: bool = True) -> None:
        if feed.pending is not None:
            feed.pending.append(ev)
            return
        if isinstance(ev, Bar):
            u = feed.builder.on_bar(ev)
        else:
            feed.last_price = ev.ltp
            u = feed.builder.on_tick(ev.ltp, ev.ts)
        self._after(feed, u, broadcast)

    def _after(self, feed: ChartFeed, u: Update, broadcast: bool = True) -> None:
        if u.empty:
            return
        if broadcast:
            for msg in update_messages(u):
                feed.broadcast(msg)
        if u.closed or u.revised is not None:
            self._recompute(feed)

    def _recompute(self, feed: ChartFeed) -> None:
        """Recompute the overlay off the event loop; one at a time per feed, the latest candles always win."""
        feed.smc_dirty = True
        if feed.smc_task is None or feed.smc_task.done():
            feed.smc_task = asyncio.create_task(self._smc(feed))

    async def _smc(self, feed: ChartFeed) -> None:
        while feed.smc_dirty:
            feed.smc_dirty = False
            try:
                feed.overlay = await asyncio.to_thread(compute, feed.builder.candles(), SWING_LENGTH)
            except Exception as exc:
                log.exception("smc overlay failed", key=feed.inst.code, minutes=feed.minutes, error=str(exc))
                continue
            feed.broadcast({"type": "smc", "smc": feed.overlay.to_dict()})

    async def _clock(self) -> None:
        """Every second: close candles whose time is over, refresh the feed status, forget unwatched feeds."""
        n = 0
        while True:
            await asyncio.sleep(self.tick_s)
            n += 1
            now = self.now()
            for key, feed in list(self.feeds.items()):
                if not feed.ready.is_set():
                    continue
                if feed.idle_since is not None and now - feed.idle_since > LINGER:
                    del self.feeds[key]
                    continue
                self._after(feed, feed.builder.close_due(now))
            if n % 5 == 0:
                await self._refresh_status()

    async def _refresh_status(self) -> None:
        codes = {f.inst.code for f in self.feeds.values()}
        if not codes:
            return
        try:
            last = await self.hub.last(codes)
            for feed in self.feeds.values():
                status = await feed_status(self.hub, {k: v for k, v in last.items() if k == feed.inst.code})
                if status != feed.status:
                    feed.status = status
                    feed.broadcast({"type": "status", "status": status})
        except Exception as exc:
            log.warning("chart status refresh failed", error=str(exc))
