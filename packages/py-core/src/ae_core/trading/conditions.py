"""Reading a rule-based strategy's conditions from the index candles (ADR 0022).

A condition is `left op right` on the latest COMPLETED candle of its timeframe (candles aligned to 09:15, built from
the 1-minute bars the feed and the history store hold). above/below hold for as long as they are true; a cross is
true only while the candle that crossed is the newest one, i.e. in the minute right after it completed. Earlier
sessions' bars warm the indicators up, so the first candle of a day already has an EMA or RSI.

Everything here is pure: the same bars give the same answer live, on paper and in a backtest."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..strategy import (
    Condition,
    ConditionGroup,
    IndicatorOperand,
    LevelOperand,
    NumberOperand,
    Operand,
    PriceOperand,
    RulesConfig,
)
from . import indicators as ind
from .model import BarLike
from .smc import Bar, bars_of, bucket_start

SESSION_OPEN = (9, 15)


@dataclass
class Reading:
    """One condition's outcome, with the values it compared (for the run's event log and backtest signals)."""

    ok: bool
    text: str


def candles_of(bars: Sequence[BarLike], tf: int, last_complete: bool) -> list[Bar]:
    """`tf`-minute candles from 1-minute bars, aligned to 09:15 each day. The newest candle counts only when its last
    minute is there (or `last_complete`, for earlier sessions that are over)."""
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
class Context:
    """The candles a step can see: earlier sessions and today's completed 1-minute bars. Resampled candles and
    indicator series are computed once per context and shared by every condition. `prior_cache` (kept by the
    runner across steps) holds the earlier sessions' candles, which do not change during a day."""

    prior: Sequence[BarLike]
    today: Sequence[BarLike]
    prior_cache: dict[tuple[object, ...], list[Bar]] = field(default_factory=dict)
    _candles: dict[int, list[Bar]] = field(default_factory=dict)
    _series: dict[tuple[object, ...], ind.Series] = field(default_factory=dict)

    @property
    def last_minute(self) -> datetime | None:
        return self.today[-1].ts if self.today else None

    def candles(self, tf: int) -> list[Bar]:
        if tf not in self._candles:
            key = (tf, len(self.prior), self.prior[-1].ts if self.prior else None)
            if key not in self.prior_cache:
                if len(self.prior_cache) > 32:
                    self.prior_cache.clear()
                self.prior_cache[key] = candles_of(self.prior, tf, last_complete=True)
            self._candles[tf] = [*self.prior_cache[key], *candles_of(self.today, tf, last_complete=False)]
        return self._candles[tf]

    def fresh(self, tf: int) -> bool:
        """Did the newest candle of `tf` complete with the newest 1-minute bar (a cross can only fire then)?"""
        c, last = self.candles(tf), self.last_minute
        return bool(c) and last is not None and c[-1].ts + timedelta(minutes=tf - 1) == last

    def series(self, tf: int, o: IndicatorOperand) -> ind.Series:
        key = (tf, o.name, o.period, o.line, o.multiplier, o.fast, o.slow, o.signal)
        if key not in self._series:
            self._series[key] = _compute(self.candles(tf), o)
        return self._series[key]

    def level(self, o: LevelOperand) -> float | None:
        today = [b for b in bars_of(self.today) if (b.ts.hour, b.ts.minute) >= SESSION_OPEN]
        if o.name.startswith("opening_range"):
            if not today:
                return None
            start = today[0].ts.replace(hour=SESSION_OPEN[0], minute=SESSION_OPEN[1])
            end = start + timedelta(minutes=o.minutes)
            if today[-1].ts < end - timedelta(minutes=1):
                return None  # the range is not complete yet
            rng = [b for b in today if b.ts < end]
            if len(rng) < max(1, int(o.minutes * 0.8)):
                return None  # too many missing minutes to trust the range
            return max(b.high for b in rng) if o.name.endswith("high") else min(b.low for b in rng)
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


def _compute(c: list[Bar], o: IndicatorOperand) -> ind.Series:
    closes = [b.close for b in c]
    if o.name == "ema":
        return ind.ema(closes, o.period)
    if o.name == "sma":
        return ind.sma(closes, o.period)
    if o.name == "rsi":
        return ind.rsi(closes, o.period)
    if o.name == "atr":
        return ind.atr(c, o.period)
    if o.name == "supertrend":
        return ind.supertrend(c, o.period, o.multiplier or 3.0)
    if o.name == "macd":
        line, sig, hist = ind.macd(closes, o.fast, o.slow, o.signal)
        return {"value": line, "signal": sig, "hist": hist}[o.line]
    if o.name == "bollinger":
        up, mid, lo = ind.bollinger(closes, o.period, o.multiplier or 2.0)
        return {"upper": up, "middle": mid, "lower": lo}[o.line]
    a, p, m = ind.adx(c, o.period)
    return {"value": a, "plus_di": p, "minus_di": m}[o.line]


def value(ctx: Context, tf: int, o: Operand, back: int = 0) -> float | None:
    """The operand on the newest completed candle (back=0) or the one before it (back=1)."""
    if isinstance(o, NumberOperand):
        return o.value
    if isinstance(o, LevelOperand):
        return ctx.level(o)
    c = ctx.candles(tf)
    i = len(c) - 1 - back
    if i < 0:
        return None
    if isinstance(o, PriceOperand):
        return float(getattr(c[i], o.field))
    s = ctx.series(tf, o)
    return s[i] if i < len(s) else None


def label(o: Operand) -> str:
    if isinstance(o, NumberOperand):
        return f"{o.value:g}"
    if isinstance(o, PriceOperand):
        return o.field
    if isinstance(o, LevelOperand):
        names = {"opening_range_high": f"{o.minutes}m range high", "opening_range_low": f"{o.minutes}m range low"}
        return names.get(o.name, o.name.replace("_", " "))
    if o.name == "macd":
        return "MACD" if o.line == "value" else f"MACD {o.line}"
    if o.name == "bollinger":
        return f"BB{o.period} {o.line}"
    if o.name == "adx":
        return {"value": f"ADX{o.period}", "plus_di": f"+DI{o.period}", "minus_di": f"-DI{o.period}"}[o.line]
    return f"{o.name.upper()}{o.period}"


def evaluate(ctx: Context, cond: Condition) -> Reading:
    tf = cond.timeframe
    a, b = value(ctx, tf, cond.left), value(ctx, tf, cond.right)
    words = cond.op.replace("_", " ")
    text = f"{tf}m {label(cond.left)} {words} {label(cond.right)}"
    if a is None or b is None:
        return Reading(False, f"{text}: not enough data yet")
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
    """Does the group hold, and the readings that decided it. An empty group always holds."""
    if not group.conditions:
        return True, []
    readings = [evaluate(ctx, c) for c in group.conditions]
    if group.match == "all":
        return all(r.ok for r in readings), [r.text for r in readings]
    hits = [r.text for r in readings if r.ok]
    return bool(hits), hits or [r.text for r in readings]


def warmup_days(cfg: RulesConfig) -> int:
    """Earlier sessions the indicators need (about 3x the longest period, on its timeframe), at least one for the
    previous day's levels; capped at 10."""
    need = 1
    for sig in cfg.signals:
        for g in (sig.when, sig.exit_when):
            for c in g.conditions if g else ():
                for o in (c.left, c.right):
                    if isinstance(o, IndicatorOperand):
                        bars = 3 * max(o.period, o.slow if o.name == "macd" else 0) + 10
                        need = max(need, -(-bars * c.timeframe // 375))
    return min(need, 10)
