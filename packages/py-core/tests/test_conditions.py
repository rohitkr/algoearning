"""Candles and levels built from 1-minute bars: finished candles only, the opening range, day levels without the
current candle, previous-session levels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from ae_core.strategy import Condition, Operand
from ae_core.trading import conditions
from ae_core.trading.model import IST

DAY = date(2026, 10, 6)


@dataclass
class B:
    ts: datetime
    open: float
    high: float
    low: float
    close: float


def bars(day: date, closes: list[float]) -> list[B]:
    t0 = datetime.combine(day, time(9, 15), tzinfo=IST)
    return [B(t0 + timedelta(minutes=i), c, c + 1, c - 1, c) for i, c in enumerate(closes)]


def at(hm: str) -> datetime:
    return datetime.combine(DAY, time.fromisoformat(hm), tzinfo=IST)


def test_only_finished_candles_and_their_ohlc() -> None:
    b = bars(DAY, [100, 101, 102, 103, 104, 105, 106])  # 09:15..09:21
    cs = conditions.candles(b, 5, at("09:22"))
    assert [(c.ts, c.open, c.high, c.low, c.close) for c in cs] == [(at("09:15"), 100, 105, 99, 104)]
    assert len(conditions.candles(b, 5, at("09:25"))) == 2  # 09:20-09:25 now over
    assert conditions.candles([], 5, at("09:25")) == []


def test_levels() -> None:
    b = bars(DAY, [100, 110, 90, 95, 120, 100])
    prior = bars(DAY - timedelta(days=1), [50, 60, 40, 55])
    now = at("09:21")
    assert conditions.level("opening_high", 3, b, prior, now, now) == 111
    assert conditions.level("opening_low", 3, b, prior, now, now) == 89
    assert conditions.level("opening_high", 30, b, prior, now, now) is None  # not over yet
    assert conditions.level("day_open", 0, b, prior, at("09:18"), now) == 100
    assert conditions.level("day_high", 0, b, prior, at("09:18"), now) == 111  # bars before 09:18 only
    assert conditions.level("day_low", 0, b, prior, at("09:20"), now) == 89
    assert conditions.level("day_high", 0, b, prior, at("09:15"), now) is None
    assert [conditions.level(n, 0, b, prior, now, now) for n in ("prev_high", "prev_low", "prev_close")] == [61, 39, 55]
    assert conditions.level("prev_high", 0, b, [], now, now) is None


def test_cross_needs_a_fresh_candle_and_state_does_not() -> None:
    b = bars(DAY, [100] * 5 + [100] * 4 + [110])  # the 09:20 candle closes at 110 (09:15 range high is 101)
    ctx = conditions.Context(at("09:25"), b, [], frozenset({5}))
    lvl = Operand(kind="level", level="opening_high", minutes=5)
    cross = Condition(op="crosses_above", right=lvl)
    state = Condition(op="above", right=lvl)
    assert conditions.holds(cross, ctx) and conditions.holds(state, ctx)
    stale = conditions.Context(at("09:25"), b, [], frozenset())
    assert not conditions.holds(cross, stale) and conditions.holds(state, stale)
    assert not conditions.holds(cross, conditions.Context(at("09:50"), b, [], frozenset({5})))  # data too old
