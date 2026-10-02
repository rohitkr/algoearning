"""Live candles of any timeframe for charts: the feed's completed 1-minute bars plus its ticks, aggregated as they
arrive. Pure and synchronous, so every edge case is testable without Redis.

    seed(bars)        the day's (and earlier days') completed 1-minute bars: closed candles + the one still forming
    on_bar(bar)       a completed 1-minute bar: folds it into its candle; the candle closes when its last minute is in
    on_tick(price)    a live price: moves the forming candle between bars (the 1-minute bar replaces it when it lands)
    close_due(now)    closes a candle whose time is over when its last minute never came (a quiet or missed minute)

Candles are aligned to the session open each day (09:15 -> 09:15, 09:20, ... for 5 minutes), like the exchange's
own charts and like ae_core.trading.smc.resample. Edge cases handled here, each returned as an Update so a reader
can redraw exactly what changed:

- a tick for a minute whose 1-minute bar already arrived is ignored (the bar is the truth);
- a 1-minute bar that arrives after its candle was closed by the clock revises that closed candle (`revised`);
- a bar or tick for a later candle closes the forming one first, so a missed final minute never blocks the chart;
- bars and ticks outside the session (pre-open, after the close) are ignored;
- a duplicate bar (the feed reconnecting) replaces the earlier copy instead of being counted twice."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta

from .types import IST, Bar

# a candle closes this long after its end when its last 1-minute bar has not arrived (the feed publishes a minute's
# bar a few seconds after the minute ends)
CLOSE_GRACE = timedelta(seconds=20)


@dataclass(frozen=True)
class Candle:
    ts: datetime  # start of the period (IST)
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def to_dict(self) -> dict[str, float | int]:
        return {
            "time": int(self.ts.timestamp()),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass(frozen=True)
class Update:
    """What changed: `closed` candles (oldest first) are final; `revised` is a closed candle corrected by a late
    1-minute bar; `forming` is the current candle (None when there is none)."""

    closed: tuple[Candle, ...] = ()
    revised: Candle | None = None
    forming: Candle | None = None
    forming_changed: bool = False

    @property
    def empty(self) -> bool:
        return not self.closed and self.revised is None and not self.forming_changed


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


@dataclass
class _Period:
    start: datetime
    bars: dict[datetime, Bar] = field(default_factory=dict)  # completed 1-minute bars by minute
    partial: list[float] | None = None  # [minute ts, o, h, l, c] from ticks after the last bar
    partial_minute: datetime | None = None

    def candle(self) -> Candle | None:
        mins = [self.bars[m] for m in sorted(self.bars)]
        if self.partial is not None and self.partial_minute is not None and self.partial_minute not in self.bars:
            o, h, low, c = self.partial[1:]
            mins.append(Bar("", self.partial_minute, o, h, low, c, 0))
        if not mins:
            return None
        return Candle(
            self.start,
            mins[0].open,
            max(b.high for b in mins),
            min(b.low for b in mins),
            mins[-1].close,
            float(sum(b.volume for b in mins)),
        )


class CandleBuilder:
    def __init__(self, minutes: int, session_open: str = "09:15", session_close: str = "15:30", keep: int = 2000):
        if minutes < 1:
            raise ValueError("minutes must be at least 1")
        self.minutes = minutes
        self.step = timedelta(minutes=minutes)
        self.open_t = _parse_hhmm(session_open)
        self.close_t = _parse_hhmm(session_close)
        self.closed: deque[Candle] = deque(maxlen=keep)
        self._cur: _Period | None = None
        self._last: _Period | None = None  # the most recently closed period, kept for late bars

    # -- time ----------------------------------------------------------------------------------------------------
    def in_session(self, ts: datetime) -> bool:
        t = ts.astimezone(IST).time()
        return self.open_t <= t < self.close_t

    def period_start(self, ts: datetime) -> datetime:
        ts = ts.astimezone(IST).replace(second=0, microsecond=0)
        anchor = ts.replace(hour=self.open_t.hour, minute=self.open_t.minute)
        mins = int((ts - anchor).total_seconds() // 60)
        return ts - timedelta(minutes=mins % self.minutes)

    def period_end(self, start: datetime) -> datetime:
        """When the period ends: its full length, or the session close for a shorter last candle of the day."""
        close = start.replace(hour=self.close_t.hour, minute=self.close_t.minute)
        return min(start + self.step, close)

    # -- input ---------------------------------------------------------------------------------------------------
    def seed(self, bars: Iterable[Bar]) -> None:
        """Start from stored 1-minute bars (any order, duplicates allowed). Replaces whatever was built before."""
        self.closed.clear()
        self._cur = self._last = None
        uniq: dict[datetime, Bar] = {}
        for b in bars:
            if self.in_session(b.ts):
                uniq[b.ts.astimezone(IST)] = b
        for ts in sorted(uniq):
            self.on_bar(uniq[ts])

    def on_bar(self, bar: Bar) -> Update:
        ts = bar.ts.astimezone(IST).replace(second=0, microsecond=0)
        if not self.in_session(ts):
            return Update()
        bar = replace(bar, ts=ts)
        start = self.period_start(ts)
        if self.closed and start <= self.closed[-1].ts:
            if self._last is not None and start == self._last.start:
                return self._revise(bar)
            return Update()  # older than the last closed candle: final
        closed = self._roll(start)
        cur = self._cur
        assert cur is not None
        cur.bars[ts] = bar
        if cur.partial_minute is not None and cur.partial_minute <= ts:
            cur.partial = cur.partial_minute = None
        if ts + timedelta(minutes=1) >= self.period_end(cur.start):
            closed.extend(self._close())
            return Update(tuple(closed), forming=None, forming_changed=True)
        return Update(tuple(closed), forming=cur.candle(), forming_changed=True)

    def on_tick(self, price: float, ts: datetime) -> Update:
        ts = ts.astimezone(IST)
        if not self.in_session(ts):
            return Update()
        minute = ts.replace(second=0, microsecond=0)
        start = self.period_start(minute)
        if self.closed and start <= self.closed[-1].ts:
            return Update()  # a late tick for a closed candle: its 1-minute bar settles it
        closed = self._roll(start)
        cur = self._cur
        assert cur is not None
        if cur.bars and minute <= max(cur.bars):
            # this minute's bar is in (it is the truth), or the tick is older than the last bar
            return Update(tuple(closed), forming=cur.candle(), forming_changed=bool(closed))
        if cur.partial is not None and cur.partial_minute == minute:
            p = cur.partial
            p[2], p[3], p[4] = max(p[2], price), min(p[3], price), price
        else:
            self._settle_partial(cur)  # the previous minute's bar is late: keep its prices meanwhile
            cur.partial_minute = minute
            cur.partial = [minute.timestamp(), price, price, price, price]
        return Update(tuple(closed), forming=cur.candle(), forming_changed=True)

    def close_due(self, now: datetime) -> Update:
        """Close the forming candle once its period (plus a grace for the last bar) is over."""
        if self._cur is None or now < self.period_end(self._cur.start) + CLOSE_GRACE:
            return Update()
        return Update(tuple(self._close()), forming=None, forming_changed=True)

    # -- output --------------------------------------------------------------------------------------------------
    @property
    def forming(self) -> Candle | None:
        return self._cur.candle() if self._cur is not None else None

    def candles(self) -> list[Candle]:
        """Closed candles, oldest first (the forming one is `forming`)."""
        return list(self.closed)

    # -- internals -----------------------------------------------------------------------------------------------
    def _roll(self, start: datetime) -> list[Candle]:
        """Make `start` the forming period, closing the current one if it is earlier."""
        closed: list[Candle] = []
        if self._cur is not None and start > self._cur.start:
            closed = self._close()
        if self._cur is None:
            self._cur = _Period(start)
        return closed

    @staticmethod
    def _settle_partial(p: _Period) -> None:
        """Fold tick prices of a minute whose bar has not arrived into the period as a stand-in bar (key "");
        the real bar replaces it if it lands later."""
        if p.partial is not None and p.partial_minute is not None and p.partial_minute not in p.bars:
            o, h, low, c = p.partial[1:]
            p.bars[p.partial_minute] = Bar("", p.partial_minute, o, h, low, c, 0)
        p.partial = p.partial_minute = None

    def _close(self) -> list[Candle]:
        cur, self._cur = self._cur, None
        if cur is None:
            return []
        self._settle_partial(cur)
        c = cur.candle()
        if c is None:
            return []
        self.closed.append(c)
        self._last = cur
        return [c]

    def _revise(self, bar: Bar) -> Update:
        last = self._last
        assert last is not None
        last.bars[bar.ts] = bar
        c = last.candle()
        if c is None or not self.closed or self.closed[-1].ts != last.start or self.closed[-1] == c:
            return Update()
        self.closed[-1] = c
        return Update(revised=c, forming=self.forming)
