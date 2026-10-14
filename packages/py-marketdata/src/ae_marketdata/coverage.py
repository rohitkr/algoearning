"""What history is stored and what is missing, per day, for Monitor > Data sync.

The same plan the download follows (backfill.py): for every trading day the index candles, and the option contracts
a strategy could have traded (the nearest expiry, strikes around the day's index range). Each day gets a status from
what is stored, and the totals say what a download would fetch and how many calls it costs."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from ae_db.session import Database

from .backfill import (
    BARS_PER_DAY,
    CAS_FROM,
    ROWS_PER_CALL,
    _index_days,
    checked_days,
    contract_days,
    expiries,
    index_runs,
    known_holidays,
    option_counts,
    plan,
    stored_days,
    tail_days,
    weekdays,
)

FULL = int(BARS_PER_DAY * 0.9)  # a contract (or index) day with at least this many candles is complete
TRADING_DAY = 60  # an index day with fewer candles than this is not a trading day we have data for


@dataclass
class DayCoverage:
    day: date
    status: str  # ok | partial | missing | closed
    index_bars: int = 0
    options_expected: int = 0
    options_complete: int = 0
    options_partial: int = 0
    options_missing: int = 0
    closing_missing: int | None = None  # contracts without a 15:30+ candle (None before the closing session began)


@dataclass
class Plan:
    index_days_missing: int = 0
    index_calls: int = 0
    option_fetches: int = 0
    option_calls: int = 0
    closing_fetches: int = 0
    closing_calls: int = 0


@dataclass
class Coverage:
    underlying: str
    start: date
    end: date
    first_day: date | None = None  # the first and last day with index candles
    last_day: date | None = None
    trading_days: int = 0
    days: list[DayCoverage] = field(default_factory=list)
    plan: Plan = field(default_factory=Plan)


def day_status(weekday_open: bool, index_bars: int, expected: int, complete: int, partial: int, missing: int) -> str:
    """closed: a weekend or a known holiday. missing: no index candles, or no complete contract at all. partial: some
    candles but not all of them. ok: everything the plan wants is there."""
    if not weekday_open:
        return "closed"
    if index_bars < TRADING_DAY:
        return "missing"
    if expected and complete == 0:
        return "missing"
    if index_bars < FULL or partial or missing:
        return "partial"
    return "ok"


async def coverage(
    db: Database, underlying: str, start: date, end: date, strike_step: int, buffer: int = 4
) -> Coverage:
    out = Coverage(underlying, start, end)
    index = await _index_days(db, underlying, start, end)
    holidays = await known_holidays(db, underlying)
    have = sorted(d for d, (_, _, n) in index.items() if n >= TRADING_DAY)
    if have:
        out.first_day, out.last_day = have[0], have[-1]
    out.trading_days = len(have)
    open_days = [d for d in weekdays(start, end) if d not in holidays]
    # the index days a download would fetch, grouped into requests
    missing_index = [d for d in open_days if index.get(d, (0, 0, 0))[2] < FULL]
    out.plan.index_days_missing = len(missing_index)
    out.plan.index_calls = sum(math.ceil(n * BARS_PER_DAY / ROWS_PER_CALL) for _, _, n in index_runs(missing_index))

    counts: dict[tuple[str, date], int] = {}
    wanted: dict[tuple[date, int, str], list[date]] = {}
    tail: set[tuple[str, date]] = set()
    checked: set[tuple[str, date]] = set()
    if have:
        ex = expiries(underlying, start, end, have)
        wanted = contract_days(underlying, {d: index[d][:2] for d in have}, ex, strike_step, buffer)
        counts = await option_counts(db, underlying, start, end)
        checked = await checked_days(db, underlying)
        tail = await tail_days(db, underlying, max(start, CAS_FROM), end) | await checked_days(
            db, underlying, tail=True
        )
        fetches = plan(underlying, wanted, have, stored_days(counts, FULL) | checked)
        out.plan.option_fetches, out.plan.option_calls = len(fetches), sum(f.calls for f in fetches)

    per_day: dict[date, list[tuple[str, int]]] = {}
    for (e, k, r), ds in wanted.items():
        key = f"{underlying}:{e:%Y-%m-%d}:{k}:{r}"
        for d in ds:
            # a contract-day already asked for holds all the provider has, however few candles that is
            per_day.setdefault(d, []).append((key, FULL if (key, d) in checked else counts.get((key, d), 0)))
    closing = 0
    for d in sorted(set(weekdays(start, end)) | set(index)):
        items = per_day.get(d, [])
        complete = sum(n >= FULL for _, n in items)
        partial = sum(0 < n < FULL for _, n in items)
        missing = sum(n == 0 for _, n in items)
        no_tail = sum((key, d) not in tail for key, _ in items) if d >= CAS_FROM and items else None
        closing += no_tail or 0
        bars = index.get(d, (0, 0, 0))[2]
        closed = d.weekday() >= 5 or d in holidays
        out.days.append(
            DayCoverage(
                d,
                day_status(not closed, bars, len(items), complete, partial, missing),
                bars,
                len(items),
                complete,
                partial,
                missing,
                no_tail,
            )
        )
    out.plan.closing_fetches = out.plan.closing_calls = closing
    return out
