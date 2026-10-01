"""Smart Money Concepts as objective rules (ADR 0018). Pure functions over completed candles, oldest first.

Nothing here looks ahead: a swing is known only once the `n` candles after it have closed, a higher-timeframe candle
only once its last minute has closed, and every check reads candles up to the one being decided on.

    swing high / low     fractal pivot: a high strictly above the n highs before it and at least the n after it
    structure            walking the candles: a close beyond the latest confirmed swing breaks structure;
                         with the trend it is a BOS, against it (or the first break after a flip) a CHoCH
    strong / weak        after an up-break, the lowest low of the breaking leg is the strong (protected) low and
                         the highest high since is the weak high; a close below the strong low = unclear structure
    dealing range        strong low .. weak high; equilibrium is its middle; premium above, discount below
    displacement         a body of at least k x ATR closing in the outer quarter of its range
    FVG                  three candles where candle 1's high is below candle 3's low (bullish), or the mirror
    order block          the last opposite-coloured candle before the displacement
    liquidity            previous day high/low, opening range high/low, confirmed swing highs/lows and equal
                         highs/lows (two swings within a tolerance); buy-side above price, sell-side below
    sweep                a wick beyond a pool that closes back inside within a few candles"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Literal

from .model import BarLike

Dir = Literal["up", "down"]
SESSION_START = (9, 15)


@dataclass(frozen=True)
class Bar:
    ts: datetime  # the first minute it covers
    open: float
    high: float
    low: float
    close: float

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open


def bars_of(src: Sequence[BarLike]) -> list[Bar]:
    return [Bar(b.ts, float(b.open), float(b.high), float(b.low), float(b.close)) for b in src]


# -- timeframes -------------------------------------------------------------------------------------------------
def bucket_start(ts: datetime, minutes: int) -> datetime:
    """The start of the `minutes` candle containing `ts`, aligned to 09:15."""
    mins = (ts.hour - SESSION_START[0]) * 60 + ts.minute - SESSION_START[1]
    return ts.replace(second=0, microsecond=0) - timedelta(minutes=mins % minutes)


def resample(bars: Sequence[BarLike], minutes: int) -> list[Bar]:
    """Completed `minutes` candles from 1-minute candles, aligned to 09:15 each day. A bucket is complete when its
    last minute is present or a later minute exists (a quiet minute has no candle)."""
    src = [b for b in bars_of(bars) if (b.ts.hour, b.ts.minute) >= SESSION_START]
    if minutes <= 1:
        return src
    groups: dict[datetime, list[Bar]] = {}
    for b in src:
        groups.setdefault(bucket_start(b.ts, minutes), []).append(b)
    out = []
    starts = list(groups)
    for n, start in enumerate(starts):
        g = groups[start]
        complete = n + 1 < len(starts) or g[-1].ts >= start + timedelta(minutes=minutes - 1)
        if complete:
            out.append(Bar(start, g[0].open, max(b.high for b in g), min(b.low for b in g), g[-1].close))
    return out


def atr(bars: Sequence[Bar], period: int = 14) -> list[float | None]:
    """Average true range over the last `period` candles (simple mean), None until there are enough."""
    out: list[float | None] = []
    trs: list[float] = []
    for i, b in enumerate(bars):
        prev = bars[i - 1].close if i else b.open
        trs.append(max(b.high - b.low, abs(b.high - prev), abs(b.low - prev)))
        out.append(sum(trs[-period:]) / period if len(trs) >= period else None)
    return out


# -- swings and structure ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Swing:
    i: int  # index of the swing candle
    price: float
    high: bool
    confirmed: int  # index of the candle that confirms it (i + n)
    ts: datetime


def swings(bars: Sequence[Bar], n: int) -> list[Swing]:
    out = []
    for i in range(n, len(bars) - n):
        b = bars[i]
        left, right = bars[i - n : i], bars[i + 1 : i + n + 1]
        if b.high > max(x.high for x in left) and b.high >= max(x.high for x in right):
            out.append(Swing(i, b.high, True, i + n, b.ts))
        if b.low < min(x.low for x in left) and b.low <= min(x.low for x in right):
            out.append(Swing(i, b.low, False, i + n, b.ts))
    return out


@dataclass(frozen=True)
class Break:
    i: int  # the candle whose close broke structure
    ts: datetime
    dir: Dir
    kind: Literal["BOS", "CHoCH"]
    level: float  # the swing that was broken
    swing_i: int
    origin: float  # the extreme the breaking leg started from: the strong low (up) / strong high (down)
    origin_i: int


@dataclass
class Structure:
    trend: Dir | None  # None: no break yet, or the protected extreme was lost (unclear)
    breaks: list[Break] = field(default_factory=list)
    swings: list[Swing] = field(default_factory=list)  # confirmed by the last candle
    strong: float | None = None  # strong low (up trend) / strong high (down trend)
    weak: float | None = None  # weak high (up trend) / weak low (down trend): the next liquidity target
    streak: int = 0  # consecutive breaks in the trend's direction (CHoCH + BOS = 2)

    @property
    def equilibrium(self) -> float | None:
        if self.strong is None or self.weak is None:
            return None
        return (self.strong + self.weak) / 2

    @property
    def last(self) -> Break | None:
        return self.breaks[-1] if self.breaks else None


def structure(bars: Sequence[Bar], n: int) -> Structure:
    sw = swings(bars, n)
    by_confirm: dict[int, list[Swing]] = {}
    for s in sw:
        by_confirm.setdefault(s.confirmed, []).append(s)
    st = Structure(trend=None, swings=[s for s in sw if s.confirmed < len(bars)])
    ref_h: Swing | None = None
    ref_l: Swing | None = None
    for i, b in enumerate(bars):
        for s in by_confirm.get(i, []):
            if s.high:
                ref_h = s
            else:
                ref_l = s
        if ref_h is not None and b.close > ref_h.price:
            seg = range(ref_h.i, i + 1)
            oi = min(seg, key=lambda k: bars[k].low)
            kind: Literal["BOS", "CHoCH"] = "BOS" if st.trend == "up" else "CHoCH"
            st.streak = st.streak + 1 if st.trend == "up" else 1
            st.breaks.append(Break(i, b.ts, "up", kind, ref_h.price, ref_h.i, bars[oi].low, oi))
            st.trend, st.strong, st.weak, ref_h = "up", bars[oi].low, b.high, None
            continue
        if ref_l is not None and b.close < ref_l.price:
            seg = range(ref_l.i, i + 1)
            oi = max(seg, key=lambda k: bars[k].high)
            kind = "BOS" if st.trend == "down" else "CHoCH"
            st.streak = st.streak + 1 if st.trend == "down" else 1
            st.breaks.append(Break(i, b.ts, "down", kind, ref_l.price, ref_l.i, bars[oi].high, oi))
            st.trend, st.strong, st.weak, ref_l = "down", bars[oi].high, b.low, None
            continue
        if st.trend == "up" and st.strong is not None and st.weak is not None:
            st.weak = max(st.weak, b.high)
            if b.close < st.strong:
                st.trend, st.streak = None, 0  # the protected low is lost: unclear until the next break
        elif st.trend == "down" and st.strong is not None and st.weak is not None:
            st.weak = min(st.weak, b.low)
            if b.close > st.strong:
                st.trend, st.streak = None, 0
    return st


# -- candles that matter ----------------------------------------------------------------------------------------
def displacement(b: Bar, atr_value: float | None, k: float) -> Dir | None:
    """A body of at least k x ATR closing in the outer quarter of the candle's range."""
    if atr_value is None or atr_value <= 0:
        return None
    rng = b.high - b.low
    if rng <= 0 or abs(b.close - b.open) < k * atr_value:
        return None
    if b.bullish and b.close >= b.high - rng / 4:
        return "up"
    if b.bearish and b.close <= b.low + rng / 4:
        return "down"
    return None


@dataclass(frozen=True)
class Zone:
    kind: Literal["FVG", "OB"]
    dir: Dir
    lo: float
    hi: float
    i: int  # the candle that completed it

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "dir": self.dir, "lo": round(self.lo, 2), "hi": round(self.hi, 2)}


def fvgs(bars: Sequence[Bar], start: int, end: int, d: Dir, min_size: float) -> list[Zone]:
    """Fair value gaps whose third candle is in start..end (inclusive)."""
    out = []
    for i in range(max(start, 2), min(end, len(bars) - 1) + 1):
        a, c = bars[i - 2], bars[i]
        if d == "up" and c.low - a.high >= max(min_size, 1e-9):
            out.append(Zone("FVG", "up", a.high, c.low, i))
        if d == "down" and a.low - c.high >= max(min_size, 1e-9):
            out.append(Zone("FVG", "down", c.high, a.low, i))
    return out


def order_block(bars: Sequence[Bar], disp_i: int, earliest: int, d: Dir, use_body: bool) -> Zone | None:
    """The last opposite-coloured candle before the displacement candle (not earlier than `earliest`)."""
    for i in range(disp_i - 1, max(earliest, 0) - 1, -1):
        b = bars[i]
        if (d == "up" and b.bearish) or (d == "down" and b.bullish):
            lo, hi = (min(b.open, b.close), max(b.open, b.close)) if use_body else (b.low, b.high)
            return Zone("OB", d, lo, hi, i)
    return None


# -- liquidity --------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Pool:
    name: str  # "previous day low", "opening range high", "equal lows", "swing high"
    price: float
    high: bool  # buy-side (above highs) when True, sell-side (below lows) when False
    since: datetime  # when it became known


def equal_levels(sw: Sequence[Swing], tol_pct: float) -> list[tuple[Swing, Swing]]:
    """Pairs of consecutive same-side swings within tol_pct of each other: equal highs / equal lows."""
    out = []
    for high in (True, False):
        side = [s for s in sw if s.high == high]
        for a, b in pairwise(side):
            if abs(a.price - b.price) <= a.price * tol_pct / 100:
                out.append((a, b))
    return out


@dataclass(frozen=True)
class Sweep:
    pool: Pool
    i: int  # the candle that went beyond the pool
    reclaim_i: int  # the candle that closed back inside
    extreme: float  # the furthest price reached beyond the pool


def find_sweep(bars: Sequence[Bar], pools: Sequence[Pool], first: int, last: int, d: Dir, min_pct: float,
               reclaim_bars: int) -> Sweep | None:  # fmt: skip
    """The latest sweep, in first..last, of a pool on the side opposite to a `d` trade (sell-side for longs).
    The pool must be known before the sweeping candle and still untaken until then."""
    want_high = d == "down"
    best: Sweep | None = None
    for p in pools:
        if p.high != want_high:
            continue
        for i in range(0, min(last, len(bars) - 1) + 1):
            b = bars[i]
            if b.ts <= p.since:
                continue
            touched = (b.high > p.price) if p.high else (b.low < p.price)
            if not touched:
                continue
            beyond = b.high > p.price * (1 + min_pct / 100) if p.high else b.low < p.price * (1 - min_pct / 100)
            if i < first or not beyond:
                break  # taken earlier, or only touched: the pool is gone
            reclaim = None
            for j in range(i, min(i + reclaim_bars, len(bars), last + 1)):
                if (bars[j].close < p.price) if p.high else (bars[j].close > p.price):
                    reclaim = j
                    break
            if reclaim is None:
                break  # accepted beyond the pool: a breakout, not a sweep
            seg = bars[i : reclaim + 1]
            extreme = max(x.high for x in seg) if p.high else min(x.low for x in seg)
            if best is None or reclaim > best.reclaim_i:
                best = Sweep(p, i, reclaim, extreme)
            break
    return best


def day_pools(prior: Sequence[Bar], today: Sequence[Bar], opening_minutes: int = 15) -> list[Pool]:
    """Previous day high/low and the opening range high/low (known once the opening range is over)."""
    out: list[Pool] = []
    if prior:
        last_day = prior[-1].ts.date()
        pd = [b for b in prior if b.ts.date() == last_day]
        since = pd[-1].ts
        out.append(Pool("previous day high", max(b.high for b in pd), True, since))
        out.append(Pool("previous day low", min(b.low for b in pd), False, since))
    if today:
        start = today[0].ts.replace(hour=SESSION_START[0], minute=SESSION_START[1])
        end = start + timedelta(minutes=opening_minutes)
        orb = [b for b in today if b.ts < end]
        if orb and any(b.ts >= end for b in today):
            since = end - timedelta(minutes=1)
            out.append(Pool("opening range high", max(b.high for b in orb), True, since))
            out.append(Pool("opening range low", min(b.low for b in orb), False, since))
    return out


def swing_pools(bars: Sequence[Bar], sw: Sequence[Swing], tol_pct: float, minutes: int) -> list[Pool]:
    """Confirmed swing highs/lows and equal highs/lows as pools, known from their confirming candle's close."""

    def known(s: Swing) -> datetime:
        return bars[s.confirmed].ts + timedelta(minutes=minutes - 1)

    out = [Pool("swing high" if s.high else "swing low", s.price, s.high, known(s)) for s in sw]
    for a, b in equal_levels(sw, tol_pct):
        price = max(a.price, b.price) if a.high else min(a.price, b.price)
        out.append(Pool("equal highs" if a.high else "equal lows", price, a.high, known(b)))
    return out
