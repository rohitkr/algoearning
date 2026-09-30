"""The feed process: keep the source subscribed to what is wanted, publish what it sends.

Wanted = every active index (always streamed: the app header, strategies' signals) + whatever readers asked for
through Hub.want (option contracts the engine trades or watches). Sources that only stream prices get their
1-minute bars built here."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import datetime

import structlog

from .hub import Hub
from .sources import Event, Source
from .types import IST, Bar, BarBuilder, InstrumentKey

log = structlog.get_logger("ae_marketdata.feed")


class Feed:
    def __init__(
        self,
        hub: Hub,
        source: Source,
        always: Callable[[], Awaitable[set[str]]],
        before_sync: Callable[[], Awaitable[None]] | None = None,
        sync_every_s: float = 5.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.hub, self.source, self.always = hub, source, always
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
