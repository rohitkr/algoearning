"""Entry and exit conditions on the index (ADR 0023): finished candles built from 1-minute bars, ready-made levels
(opening range, today's open / high / low so far, previous session), indicators (ADR 0024: computed on the
condition's candles, earlier sessions first so they are warmed up at the open), and the comparison of two values.
Pure functions of the bars they are given, so live, paper and backtest see the same thing."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..strategy import Condition, ConditionGroup, Operand
from . import indicators as ind
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


def prior_candles(prior: Sequence[BarLike], size: int) -> list[Candle]:
    """Earlier sessions' candles of `size` minutes, each session counted from its own first bar (all are over)."""
    days: dict[object, list[BarLike]] = {}
    for b in prior:
        days.setdefault(b.ts.date(), []).append(b)
    out: list[Candle] = []
    for day_bars in days.values():
        out += candles(day_bars, size, day_bars[-1].ts + timedelta(days=1))
    return out


def indicator_series(cs: Sequence[Candle], o: Operand) -> ind.Series:
    """One value per candle for an indicator operand (None until warmed up)."""
    closes = [c.close for c in cs]
    name, n = o.indicator, o.period
    if name == "ema":
        return ind.ema(closes, n)
    if name == "sma":
        return ind.sma(closes, n)
    if name == "rsi":
        return ind.rsi(closes, n)
    if name == "atr":
        return ind.atr(cs, n)
    if name == "supertrend":
        return ind.supertrend(cs, n, o.multiplier or 3.0)
    if name == "macd":
        line, sig, hist = ind.macd(closes, o.fast, n, o.smoothing)
        return {"signal": sig, "hist": hist}.get(o.line, line)
    if name == "bollinger":
        up, mid, lo = ind.bollinger(closes, n, o.multiplier or 2.0)
        return {"upper": up, "lower": lo}.get(o.line, mid)
    if name == "adx":
        a, p, m = ind.adx(cs, n)
        return {"plus_di": p, "minus_di": m}.get(o.line, a)
    return [None] * len(cs)


@dataclass
class Context:
    now: datetime
    bars: Sequence[BarLike]
    prior: Sequence[BarLike]
    fresh: frozenset[int]  # candle sizes that finished a new candle since the last step
    # kept by the runner across steps: earlier sessions' candles (they do not change during the day)
    cache: dict[Any, Any] = field(default_factory=dict)
    _series: dict[Any, dict[datetime, float | None]] = field(default_factory=dict)

    def value(self, o: Operand, c: Candle, formed_at: datetime, size: int = 1) -> float | None:
        if o.kind == "price":
            return c.close
        if o.kind == "number":
            return o.value
        if o.kind == "indicator":
            return self.indicator(o, size).get(c.ts)
        return level(o.level or "", o.minutes, self.bars, self.prior, c.ts, formed_at)

    def indicator(self, o: Operand, size: int) -> dict[datetime, float | None]:
        """The indicator's value by candle start, earlier sessions included."""
        key = (size, o.indicator, o.period, o.line, o.multiplier, o.fast, o.smoothing)
        if key not in self._series:
            pk = ("prior", size, len(self.prior), self.prior[-1].ts if self.prior else None)
            if pk not in self.cache:
                if len(self.cache) > 64:
                    self.cache.clear()
                self.cache[pk] = prior_candles(self.prior, size)
            cs = [*self.cache[pk], *candles(self.bars, size, self.now)]
            self._series[key] = dict(zip((c.ts for c in cs), indicator_series(cs, o), strict=True))
        return self._series[key]


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
    size = cond.candle
    lt, rt = ctx.value(cond.left, cur, cur.ts, size), ctx.value(cond.right, cur, cur.ts, size)
    if lt is None or rt is None:
        return False
    if cond.op == "above":
        return lt > rt
    if cond.op == "below":
        return lt < rt
    if cond.candle not in ctx.fresh or len(cs) < 2:
        return False
    prev = cs[-2]
    lp, rp = ctx.value(cond.left, prev, cur.ts, size), ctx.value(cond.right, prev, cur.ts, size)
    if lp is None or rp is None:
        return False
    return (lp <= rp and lt > rt) if cond.op == "crosses_above" else (lp >= rp and lt < rt)


def group_holds(g: ConditionGroup, ctx: Context) -> bool:
    results = (holds(c, ctx) for c in g.conditions)
    return all(results) if g.match == "all" else any(results)
