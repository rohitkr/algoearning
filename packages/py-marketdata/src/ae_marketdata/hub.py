"""Redis as the meeting point between the one platform feed and everyone reading prices (ADR 0013).

    md:last                 hash   key -> latest Tick (JSON)
    md:bars:<key>:<date>    list   the day's completed 1-minute bars, oldest first (kept 4 days)
    md:bar / md:tick        pubsub every new bar / tick, for readers that want to wake up at once
    md:subs                 zset   key -> expiry (epoch seconds): what readers need streamed
    md:health               hash   the feed's own status, for Monitor

Readers (the engine, the API) register the keys they need with `want()` and renew them while they need them; the
feed subscribes to exactly the keys that are wanted and not expired, so nothing streams that nobody reads."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Any

from redis.asyncio import Redis

from .types import IST, Bar, Tick

LAST, SUBS, HEALTH = "md:last", "md:subs", "md:health"
BAR_CHANNEL, TICK_CHANNEL = "md:bar", "md:tick"
BARS_DAYS = 4
BARS_TTL_S = BARS_DAYS * 24 * 3600
WANT_TTL_S = 180


def bars_key(key: str, day: date) -> str:
    return f"md:bars:{key}:{day:%Y%m%d}"


class Hub:
    def __init__(self, redis: Redis) -> None:
        self.r = redis

    # -- written by the feed ---------------------------------------------------------------------------------
    async def publish_bar(self, bar: Bar) -> None:
        k = bars_key(bar.key, bar.ts.astimezone(IST).date())
        payload = bar.to_json()
        async with self.r.pipeline(transaction=False) as p:
            p.rpush(k, payload)
            p.expire(k, BARS_TTL_S)
            p.publish(BAR_CHANNEL, payload)
            await p.execute()

    async def merge_bars(self, bars: Sequence[Bar]) -> int:
        """Add bars the feed missed (it started late, or lost its connection): minutes already stored are kept, the
        list stays sorted, and each added bar is published like a live one so open charts redraw. Only the feed
        process calls this, and not while it publishes a bar. Returns how many were added."""
        by_list: dict[str, list[Bar]] = {}
        for b in bars:
            by_list.setdefault(bars_key(b.key, b.ts.astimezone(IST).date()), []).append(b)
        added = 0
        for k, new in by_list.items():
            have = {Bar.from_json(x).ts: x for x in await self.r.lrange(k, 0, -1)}
            fresh = {b.ts: b for b in new if b.ts not in have}
            if not fresh:
                continue
            merged = {**have, **{ts: b.to_json() for ts, b in fresh.items()}}
            async with self.r.pipeline(transaction=True) as p:
                p.delete(k)
                p.rpush(k, *(merged[ts] for ts in sorted(merged)))
                p.expire(k, BARS_TTL_S)
                for ts in sorted(fresh):
                    p.publish(BAR_CHANNEL, fresh[ts].to_json())
                await p.execute()
            added += len(fresh)
        return added

    async def publish_tick(self, tick: Tick) -> None:
        payload = tick.to_json()
        async with self.r.pipeline(transaction=False) as p:
            p.hset(LAST, tick.key, payload)
            p.publish(TICK_CHANNEL, payload)
            await p.execute()

    async def set_health(self, **fields: Any) -> None:
        await self.r.hset(HEALTH, mapping={k: json.dumps(v) for k, v in fields.items()})

    async def count_api_call(self, n: int = 1) -> int:
        k = f"md:calls:{datetime.now(IST):%Y%m%d}"
        used = int(await self.r.incrby(k, n))
        await self.r.expire(k, 3 * 24 * 3600)
        return used

    async def wanted(self, now: float | None = None) -> set[str]:
        now = time.time() if now is None else now
        await self.r.zremrangebyscore(SUBS, "-inf", now)
        members = await self.r.zrange(SUBS, 0, -1)
        return {m.decode() if isinstance(m, bytes) else str(m) for m in members}

    # -- used by readers -------------------------------------------------------------------------------------
    async def want(self, keys: Iterable[str], ttl_s: int = WANT_TTL_S) -> None:
        """Ask the feed to stream these keys for the next `ttl_s` seconds (renew to keep them)."""
        until = time.time() + ttl_s
        mapping = {k: until for k in keys}
        if mapping:
            await self.r.zadd(SUBS, mapping, gt=True)  # never shorten another reader's interest

    async def last(self, keys: Iterable[str]) -> dict[str, Tick]:
        keys = list(keys)
        if not keys:
            return {}
        raw = await self.r.hmget(LAST, keys)
        return {k: Tick.from_json(v) for k, v in zip(keys, raw, strict=True) if v is not None}

    async def bars(self, key: str, day: date) -> list[Bar]:
        return [Bar.from_json(x) for x in await self.r.lrange(bars_key(key, day), 0, -1)]

    async def health(self) -> dict[str, Any]:
        raw = await self.r.hgetall(HEALTH)
        return {(k.decode() if isinstance(k, bytes) else k): json.loads(v) for k, v in raw.items()}

    async def api_calls_today(self) -> int:
        return int(await self.r.get(f"md:calls:{datetime.now(IST):%Y%m%d}") or 0)
