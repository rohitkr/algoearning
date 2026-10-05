"""The feed process: keep the source subscribed to what is wanted, publish what it sends.

Wanted = every active index (always streamed: the app header, strategies' signals) + whatever readers asked for
through Hub.want (option contracts the engine trades or watches). Sources that only stream prices get their
1-minute bars built here."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import datetime, time, timedelta

import structlog

from .hub import Hub
from .sources import Event, Source
from .types import IST, Bar, BarBuilder, InstrumentKey

log = structlog.get_logger("ae_marketdata.feed")

FILL_LAG = timedelta(minutes=3)  # a minute this old should be in Redis already; later ones are the live edge
FILL_EVERY = timedelta(minutes=10)  # between attempts for an index (a holiday, or a provider with no history)
FILL_SOON = timedelta(seconds=30)  # when the provider was not ready (no session yet)
SESSION_OPEN, SESSION_LAST = time(9, 15), time(15, 29)

# fetch(index code, first minute, last minute) -> its 1-minute bars, or None when no provider is ready yet
Fetch = Callable[[str, datetime, datetime], Awaitable[list[Bar] | None]]


class Feed:
    def __init__(
        self,
        hub: Hub,
        source: Source,
        always: Callable[[], Awaitable[set[str]]],
        before_sync: Callable[[], Awaitable[None]] | None = None,
        sync_every_s: float = 5.0,
        now: Callable[[], datetime] | None = None,
        fetch: Fetch | None = None,
    ) -> None:
        self.hub, self.source, self.always = hub, source, always
        self.fetch = fetch
        self._publish = asyncio.Lock()  # a gap fill rewrites a day's list: no live bar may land meanwhile
        self._fill_after: dict[str, datetime] = {}
        self.bars_filled = 0
        self.before_sync = before_sync
        self.sync_every_s = sync_every_s
        self.now = now or (lambda: datetime.now(IST))
        self.builder = BarBuilder()
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=100_000)
        self.bars_published = 0
        self.last_event: datetime | None = None

    async def handle(self, ev: Event) -> None:
        self.last_event = self.now()
        if isinstance(ev, Bar):
            async with self._publish:
                await self.hub.publish_bar(ev)
            self.bars_published += 1
            return
        await self.hub.publish_tick(ev)
        if not self.source.builds_bars:
            bar = self.builder.add(ev.key, ev.ltp, ev.ts)
            if bar is not None:
                await self.handle(bar)

    async def close_quiet_minutes(self) -> None:
        if not self.source.builds_bars:
            for bar in self.builder.close_until(self.now()):
                await self.handle(bar)

    async def fill_gaps(self) -> int:
        """Today's index bars the feed missed (started or logged in after the open, was down for a while): fetched
        from the provider's history and merged into Redis, so charts and strategies see the whole day. Looks at each
        index every FILL_EVERY; a single missing minute is left alone (it is the live edge)."""
        if self.fetch is None:
            return 0
        now = self.now()
        day = now.date()
        if now.weekday() >= 5 or now.time() < SESSION_OPEN:
            return 0
        first = datetime.combine(day, SESSION_OPEN, tzinfo=IST)
        last = min(now - FILL_LAG, datetime.combine(day, SESSION_LAST, tzinfo=IST)).replace(second=0, microsecond=0)
        if last < first:
            return 0
        total = 0
        for code in sorted(await self.always()):
            if now < self._fill_after.get(code, now):
                continue
            have = {b.ts.astimezone(IST).replace(second=0, microsecond=0) for b in await self.hub.bars(code, day)}
            missing = [first + timedelta(minutes=i) for i in range(int((last - first) / timedelta(minutes=1)) + 1)]
            missing = [t for t in missing if t not in have]
            if len(missing) < 2:
                continue
            try:
                got = await self.fetch(code, missing[0], missing[-1])
            except Exception as exc:
                log.warning("gap fill failed", key=code, error=str(exc))
                self._fill_after[code] = now + FILL_EVERY
                continue
            if got is None:
                self._fill_after[code] = now + FILL_SOON
                continue
            self._fill_after[code] = now + FILL_EVERY
            wanted = set(missing)
            async with self._publish:
                n = await self.hub.merge_bars([b for b in got if b.ts in wanted])
            self.bars_filled += n
            total += n
            log.info("gap filled", key=code, missing=len(missing), added=n)
        return total

    async def sync(self) -> set[str]:
        if self.before_sync:
            await self.before_sync()
        keys = await self.always() | await self.hub.wanted()
        parsed = set()
        for k in keys:
            with contextlib.suppress(ValueError):
                parsed.add(InstrumentKey.parse(k))
        await self.source.sync(parsed)
        await self.hub.set_health(
            source=self.source.name,
            simulated=self.source.name == "simulated",
            wanted=len(parsed),
            bars_published=self.bars_published,
            bars_filled=self.bars_filled,
            last_event=self.last_event.isoformat() if self.last_event else None,
            api_calls_today=await self.hub.api_calls_today(),
            updated_at=self.now().isoformat(),
            **self.source.status(),
        )
        return keys

    async def run(self, stop: asyncio.Event) -> None:
        await self.source.start(self.queue)

        async def consume() -> None:
            while True:
                ev = await self.queue.get()
                try:
                    await self.handle(ev)
                except Exception:
                    log.exception("publish failed")

        async def housekeeping() -> None:
            last_sync = 0.0
            loop = asyncio.get_running_loop()
            while True:
                if loop.time() - last_sync >= self.sync_every_s:
                    try:
                        await self.sync()
                        await self.fill_gaps()
                    except Exception:
                        log.exception("sync failed")
                    last_sync = loop.time()
                await self.close_quiet_minutes()
                await asyncio.sleep(1)

        tasks = [asyncio.create_task(consume()), asyncio.create_task(housekeeping())]
        log.info("feed running", source=self.source.name)
        await stop.wait()
        for t in tasks:
            t.cancel()
        await self.source.stop()
        await self.hub.set_health(connected=False, session="stopped")
