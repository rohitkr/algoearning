"""Entry and exit conditions on the index (ADR 0023): finished candles built from 1-minute bars, ready-made levels
(opening range, today's open / high / low so far, previous session), and the comparison of two values. Pure functions
of the bars they are given, so live, paper and backtest see the same thing."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..strategy import Condition, ConditionGroup, Operand
from .model import BarLike


@dataclass(frozen=True)
class Candle:
    ts: datetime  # start
    open: float
    high: float
    low: float
    close: float


def candles(bars: Sequence[BarLike], size: int, now: datetime) -> list[Candle]:
    """Today's finished candles of `size` minutes, counted from the day's first bar (the session open)."""
    if not bars:
        return []
    origin = bars[0].ts
    span = timedelta(minutes=size)
    buckets: dict[int, list[BarLike]] = {}
    for b in bars:
        buckets.setdefault(int((b.ts - origin) / span), []).append(b)
    out = []
    for idx in sorted(buckets):
        if origin + span * (idx + 1) > now:
            continue  # still forming
        g = buckets[idx]
        out.append(Candle(origin + span * idx, g[0].open, max(b.high for b in g), min(b.low for b in g), g[-1].close))
    return out


def level(
    name: str, minutes: int, bars: Sequence[BarLike], prior: Sequence[BarLike], asof: datetime, formed_at: datetime
) -> float | None:
    """A level as known when the candle starting at `asof` opened. The opening range is known once its `minutes`
    are over (by `formed_at`); day levels use only bars before `asof`; previous-session levels need `prior`."""
    if name.startswith("prev_"):
        if not prior:
            return None
        last = [b for b in prior if b.ts.date() == prior[-1].ts.date()]
        return {
            "prev_high": max(b.high for b in last),
            "prev_low": min(b.low for b in last),
            "prev_close": last[-1].close,
        }[name]
    if not bars:
        return None
    if name.startswith("opening_"):
        end = bars[0].ts + timedelta(minutes=minutes)
        if formed_at < end:
            return None
        rng = [b for b in bars if b.ts < end]
        return max(b.high for b in rng) if name == "opening_high" else min(b.low for b in rng)
    before = [b for b in bars if b.ts < asof]
    if not before:
        return None
    if name == "day_open":
        return before[0].open
    return max(b.high for b in before) if name == "day_high" else min(b.low for b in before)


@dataclass
class Context:
    now: datetime
    bars: Sequence[BarLike]
    prior: Sequence[BarLike]
    fresh: frozenset[int]  # candle sizes that finished a new candle since the last step

    def value(self, o: Operand, c: Candle, formed_at: datetime) -> float | None:
        if o.kind == "price":
            return c.close
        if o.kind == "number":
            return o.value
        return level(o.level or "", o.minutes, self.bars, self.prior, c.ts, formed_at)


def latest(bars: Sequence[BarLike], size: int, now: datetime) -> Candle | None:
    cs = candles(bars, size, now)
    return cs[-1] if cs else None


def holds(cond: Condition, ctx: Context) -> bool:
    cs = candles(ctx.bars, cond.candle, ctx.now)
    if not cs:
        return False
    cur = cs[-1]
    if ctx.now - (cur.ts + timedelta(minutes=cond.candle)) > timedelta(minutes=cond.candle + 1):
        return False  # no recent candle (feed gap, engine just started): no signal from stale data
    lt, rt = ctx.value(cond.left, cur, cur.ts), ctx.value(cond.right, cur, cur.ts)
    if lt is None or rt is None:
        return False
    if cond.op == "above":
        return lt > rt
    if cond.op == "below":
        return lt < rt
    if cond.candle not in ctx.fresh or len(cs) < 2:
        return False
    prev = cs[-2]
    lp, rp = ctx.value(cond.left, prev, cur.ts), ctx.value(cond.right, prev, cur.ts)
    if lp is None or rp is None:
        return False
    return (lp <= rp and lt > rt) if cond.op == "crosses_above" else (lp >= rp and lt < rt)


def group_holds(g: ConditionGroup, ctx: Context) -> bool:
    results = (holds(c, ctx) for c in g.conditions)
    return all(results) if g.match == "all" else any(results)
