"""Live candles from 1-minute bars and ticks: alignment, closing, late bars and ticks, duplicates, session edges."""

from __future__ import annotations

from datetime import datetime, timedelta

from ae_marketdata.candles import CLOSE_GRACE, CandleBuilder
from ae_marketdata.types import IST, Bar


def at(h: int, m: int, s: int = 0, day: int = 5) -> datetime:
    return datetime(2026, 10, day, h, m, s, tzinfo=IST)


def bar(h: int, m: int, o: float, hi: float, lo: float, c: float, day: int = 5, v: int = 0) -> Bar:
    return Bar("NIFTY", at(h, m, day=day), o, hi, lo, c, v)


def flat(h: int, m: int, p: float, day: int = 5) -> Bar:
    return bar(h, m, p, p, p, p, day)


def test_periods_are_aligned_to_the_session_open() -> None:
    b = CandleBuilder(15)
    assert b.period_start(at(9, 15)) == at(9, 15)
    assert b.period_start(at(9, 29, 59)) == at(9, 15)
    assert b.period_start(at(9, 30)) == at(9, 30)
    assert b.period_start(at(15, 29)) == at(15, 15)
    assert CandleBuilder(3).period_start(at(9, 20)) == at(9, 18)
    # the last candle of the day is cut at the close
    assert CandleBuilder(60).period_end(at(15, 15)) == at(15, 30)


def test_seed_builds_closed_candles_and_the_forming_one() -> None:
    b = CandleBuilder(5)
    bars = [flat(9, 15 + i, 100 + i) for i in range(7)]  # 09:15..09:21
    b.seed([*reversed(bars), bars[0]])  # any order, duplicates ignored
    (c,) = b.candles()
    assert (c.ts, c.open, c.high, c.low, c.close) == (at(9, 15), 100, 104, 100, 104)
    f = b.forming
    assert f is not None and (f.ts, f.open, f.close) == (at(9, 20), 105, 106)


def test_a_candle_closes_when_its_last_minute_arrives() -> None:
    b = CandleBuilder(5)
    for i in range(4):
        u = b.on_bar(bar(9, 15 + i, 100, 101 + i, 99 - i, 100.5))
        assert not u.closed and u.forming is not None
    u = b.on_bar(bar(9, 19, 100, 102, 98, 101))
    (c,) = u.closed
    assert (c.open, c.high, c.low, c.close) == (100, 104, 96, 101)
    assert u.forming is None and b.forming is None


def test_ticks_move_the_forming_candle_until_the_bar_replaces_them() -> None:
    b = CandleBuilder(5)
    b.on_bar(bar(9, 15, 100, 101, 99, 100))
    u = b.on_tick(103, at(9, 16, 5))
    assert u.forming is not None and (u.forming.high, u.forming.close) == (103, 103)
    b.on_tick(97, at(9, 16, 30))
    f = b.forming
    assert f is not None and (f.high, f.low, f.close) == (103, 97, 97)
    # the minute's real bar is the truth (the ticks missed nothing here, but a bar always wins)
    u = b.on_bar(bar(9, 16, 100, 102, 98, 99))
    assert u.forming is not None and (u.forming.high, u.forming.low, u.forming.close) == (102, 98, 99)
    # a late tick for a minute whose bar is in changes nothing
    assert b.on_tick(150, at(9, 16, 59)).empty


def test_a_late_minute_keeps_its_tick_prices_until_its_bar_lands() -> None:
    b = CandleBuilder(5)
    b.on_tick(100, at(9, 15, 1))
    b.on_tick(110, at(9, 15, 40))
    b.on_tick(105, at(9, 16, 2))  # 09:15's bar has not arrived yet
    f = b.forming
    assert f is not None and (f.open, f.high, f.close) == (100, 110, 105)
    b.on_bar(bar(9, 15, 100, 111, 99, 108))
    f = b.forming
    assert f is not None and (f.high, f.low, f.close) == (111, 99, 105)


def test_the_clock_closes_a_candle_whose_last_minute_never_came_and_a_late_bar_revises_it() -> None:
    b = CandleBuilder(5)
    for i in range(4):
        b.on_bar(flat(9, 15 + i, 100))
    b.on_tick(104, at(9, 19, 10))
    assert b.close_due(at(9, 20) + CLOSE_GRACE - timedelta(seconds=1)).empty
    u = b.close_due(at(9, 20) + CLOSE_GRACE)
    (c,) = u.closed
    assert (c.high, c.close) == (104, 104)
    # the 09:19 bar finally lands with the true extremes
    u = b.on_bar(bar(9, 19, 100, 106, 99, 105))
    assert u.revised is not None and (u.revised.high, u.revised.low, u.revised.close) == (106, 99, 105)
    assert b.candles()[-1] == u.revised
    # the same bar again is not a change
    assert b.on_bar(bar(9, 19, 100, 106, 99, 105)).empty


def test_a_later_bar_closes_the_forming_candle_first() -> None:
    b = CandleBuilder(5)
    b.on_bar(flat(9, 15, 100))
    b.on_bar(flat(9, 16, 101))  # 09:17..09:19 never arrive
    u = b.on_bar(flat(9, 21, 103))
    (c,) = u.closed
    assert (c.ts, c.close) == (at(9, 15), 101)
    assert u.forming is not None and u.forming.ts == at(9, 20)


def test_older_bars_and_ticks_than_the_last_closed_candle_are_ignored() -> None:
    b = CandleBuilder(5)
    for i in range(10):
        b.on_bar(flat(9, 15 + i, 100 + i))
    assert len(b.candles()) == 2
    assert b.on_bar(flat(9, 16, 500)).empty
    assert b.on_tick(500, at(9, 22)).empty
    assert max(c.high for c in b.candles()) < 500


def test_outside_the_session_nothing_counts() -> None:
    b = CandleBuilder(5)
    assert b.on_tick(100, at(9, 7)).empty  # pre-open
    assert b.on_bar(flat(15, 30, 100)).empty  # after the close
    assert b.forming is None and b.candles() == []


def test_days_do_not_merge_and_the_last_candle_of_the_day_closes_at_the_close() -> None:
    b = CandleBuilder(15)
    b.on_bar(flat(15, 15, 100))
    u = b.on_bar(flat(15, 29, 101))
    assert [c.ts for c in u.closed] == [at(15, 15)]
    b.on_bar(flat(9, 15, 200, day=6))
    f = b.forming
    assert f is not None and f.ts == at(9, 15, day=6) and f.open == 200


def test_one_minute_candles_follow_bars_and_ticks() -> None:
    b = CandleBuilder(1)
    u = b.on_tick(100, at(9, 15, 3))
    assert u.forming is not None and u.forming.ts == at(9, 15)
    u = b.on_bar(bar(9, 15, 100, 101, 99, 100.5))
    assert [c.close for c in u.closed] == [100.5] and u.forming is None


def test_volume_is_summed() -> None:
    b = CandleBuilder(3)
    b.on_bar(bar(9, 15, 1, 1, 1, 1, v=10))
    b.on_bar(bar(9, 16, 1, 1, 1, 1, v=5))
    u = b.on_bar(bar(9, 17, 1, 1, 1, 1, v=1))
    assert u.closed[0].volume == 16
