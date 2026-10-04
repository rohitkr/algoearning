"""Reading a rules strategy's conditions from the index candles (ADR 0022, phase 2).

A condition is `left op right` on the latest COMPLETED candle of its timeframe (candles aligned to 09:15, built from
the 1-minute bars the feed and the history store hold). above/below hold for as long as they are true; a cross is
true only while the candle that crossed is the newest one, i.e. at the first step after it completed. Levels come
from today's bars (opening range, open/high/low so far, the price at a time) or the previous session's.

Pure: the same bars give the same answer live, on paper and in a backtest."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..strategy import Condition, ConditionGroup, LevelOperand, NumberOperand, Operand, PriceOperand, RulesConfig
from .model import BarLike
from .smc import Bar, bars_of, bucket_start

SESSION_OPEN = (9, 15)


def candles_of(bars: Sequence[BarLike], tf: int, last_complete: bool) -> list[Bar]:
    """`tf`-minute candles from 1-minute bars, aligned to 09:15 each day. The newest candle counts only when its last
    minute is there (or `last_complete`, for earlier sessions, which are over)."""
    src = [b for b in bars_of(bars) if (b.ts.hour, b.ts.minute) >= SESSION_OPEN]
    if tf <= 1:
        return src
    groups: dict[datetime, list[Bar]] = {}
    for b in src:
        groups.setdefault(bucket_start(b.ts, tf), []).append(b)
    out = [Bar(t, g[0].open, max(b.high for b in g), min(b.low for b in g), g[-1].close) for t, g in groups.items()]
    if out and not last_complete and src[-1].ts < out[-1].ts + timedelta(minutes=tf - 1):
        out.pop()
    return out


@dataclass
class Reading:
    """One condition's outcome, with the values it compared (for the event log and backtest signals)."""

    ok: bool
    text: str


@dataclass
class Context:
    """What one step can see: earlier sessions' and today's completed 1-minute bars. Candles are built once per
    context and shared by every condition."""

    prior: Sequence[BarLike]
    today: Sequence[BarLike]
    _candles: dict[int, list[Bar]] = field(default_factory=dict)

    @property
    def last_minute(self) -> datetime | None:
        return self.today[-1].ts if self.today else None

    def candles(self, tf: int) -> list[Bar]:
        """Today's completed candles (conditions read the day's own candles; levels read earlier sessions)."""
        if tf not in self._candles:
            self._candles[tf] = candles_of(self.today, tf, last_complete=False)
        return self._candles[tf]

    def fresh(self, tf: int) -> bool:
        """Did the newest candle of `tf` complete with the newest 1-minute bar (a cross can only fire then)?"""
        c, last = self.candles(tf), self.last_minute
        return bool(c) and last is not None and c[-1].ts + timedelta(minutes=tf - 1) == last

    def level(self, o: LevelOperand) -> float | None:
        base = self._level(o)
        return None if base is None else base + o.offset

    def _level(self, o: LevelOperand) -> float | None:
        today = self.candles(1)
        if o.name.startswith("opening_range"):
            if not today:
                return None
            start = today[0].ts.replace(hour=SESSION_OPEN[0], minute=SESSION_OPEN[1])
            end = start + timedelta(minutes=o.minutes)
            if today[-1].ts < end - timedelta(minutes=1):
                return None  # the range is not complete yet
            rng = [b for b in today if b.ts < end]
            if len(rng) < max(1, int(o.minutes * 0.8)):
                return None  # too many minutes missing to trust it
            return max(b.high for b in rng) if o.name.endswith("high") else min(b.low for b in rng)
        if o.name == "price_at":
            if not o.at or not today:
                return None
            h, m = (int(x) for x in o.at.split(":"))
            want = today[0].ts.replace(hour=h, minute=m)
            bar = next((b for b in today if b.ts == want), None)
            return bar.close if bar else None
        if o.name.startswith("day_"):
            if not today:
                return None
            if o.name == "day_open":
                return today[0].open
            return max(b.high for b in today) if o.name == "day_high" else min(b.low for b in today)
        prior = bars_of(self.prior)
        if not prior:
            return None
        last_day = prior[-1].ts.date()
        prev = [b for b in prior if b.ts.date() == last_day]
        if o.name == "prev_high":
            return max(b.high for b in prev)
        if o.name == "prev_low":
            return min(b.low for b in prev)
        return prev[-1].close


def value(ctx: Context, tf: int, o: Operand, back: int = 0) -> float | None:
    """The operand on the newest completed candle (back=0) or the one before it (back=1)."""
    if isinstance(o, NumberOperand):
        return o.value
    if isinstance(o, LevelOperand):
        return ctx.level(o)
    c = ctx.candles(tf)
    i = len(c) - 1 - back
    return float(getattr(c[i], o.field)) if i >= 0 else None


def label(o: Operand) -> str:
    if isinstance(o, NumberOperand):
        return f"{o.value:g}"
    if isinstance(o, PriceOperand):
        return o.field
    names = {
        "opening_range_high": f"{o.minutes}m range high",
        "opening_range_low": f"{o.minutes}m range low",
        "price_at": f"price at {o.at}",
    }
    text = names.get(o.name, o.name.replace("_", " "))
    return f"{text} {o.offset:+g}" if o.offset else text


def evaluate(ctx: Context, cond: Condition) -> Reading:
    tf = cond.timeframe
    a, b = value(ctx, tf, cond.left), value(ctx, tf, cond.right)
    text = f"{tf}m {label(cond.left)} {cond.op.replace('_', ' ')} {label(cond.right)}"
    if a is None or b is None:
        return Reading(False, f"{text}: not known yet")
    shown = f"{text} ({a:.2f} vs {b:.2f})"
    if cond.op == "above":
        return Reading(a > b, shown)
    if cond.op == "below":
        return Reading(a < b, shown)
    if not ctx.fresh(tf):
        return Reading(False, shown)
    pa, pb = value(ctx, tf, cond.left, 1), value(ctx, tf, cond.right, 1)
    if pa is None or pb is None:
        return Reading(False, shown)
    if cond.op == "crosses_above":
        return Reading(pa <= pb and a > b, shown)
    return Reading(pa >= pb and a < b, shown)


def holds(ctx: Context, group: ConditionGroup) -> tuple[bool, list[str]]:
    """Does the group hold, and the readings that decided it."""
    readings = [evaluate(ctx, c) for c in group.conditions]
    if group.match == "all":
        return all(r.ok for r in readings), [r.text for r in readings]
    hits = [r.text for r in readings if r.ok]
    return bool(hits), hits or [r.text for r in readings]


def needs_prior_day(cfg: RulesConfig) -> bool:
    """Does any condition read the previous session's levels?"""
    groups = [cfg.entry.when, cfg.entry.when_mirrored, cfg.holding.exit_when, cfg.holding.exit_when_mirrored]
    return any(
        isinstance(o, LevelOperand) and o.name.startswith("prev_")
        for g in groups
        if g is not None
        for c in g.conditions
        for o in (c.left, c.right)
    )
