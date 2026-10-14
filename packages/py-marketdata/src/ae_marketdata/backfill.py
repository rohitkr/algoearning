"""Backfilling 1-minute history from the price provider's REST API, Breeze or Kite (ADR 0018, 0021): the index first,
then the option contracts a scalper could have traded, into the same history_candles table the backtester reads.

    python -m ae_worker backfill NIFTY --from 2025-01-01 [--to 2026-09-30] [--dry-run] [--reserve 500]

Which contracts: for every trading day, the nearest expiry (and, on an expiry day, the next one too), every strike
within `buffer` strikes of that day's index range, calls and puts. Each contract is fetched only for the runs of
consecutive days it was in reach, and only for days not stored yet, so a rerun continues where the last one stopped.
A day with only a few candles counts as stored too; `--refill-partial` fetches those again (only a contract with nearly
the whole session counts as complete).

Budget: Breeze allows 5,000 REST calls a day, shared with the live feed. Calls are counted in the feed's counter
(Monitor shows it) and the backfill stops when fewer than `reserve` are left. Run it after market hours.

Breeze facts (from algo-trading-claude's downloader, verified there against the live API): historical_data_v2 returns
at most 1,000 rows, newest first; timestamps are sent with a Z suffix but read as IST wall-clock time; an option's
expiry is passed as YYYY-MM-DDT07:00:00.000Z. Expiry calendars below come from the same project."""

from __future__ import annotations

import asyncio
import math
import tomllib
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Protocol

import structlog
from ae_db.models import HistoryCandle, PlatformSetting
from ae_db.session import Database
from sqlalchemy import func, select, text

from .history import _insert
from .types import IST

log = structlog.get_logger("ae_marketdata.backfill")
ROWS_PER_CALL = 1000
BARS_PER_DAY = 375
DAILY_LIMIT = 5000
SESSION = (time(9, 15), time(15, 29))
# the closing auction session (CAS): options trade 15:30-15:40 from 3 Aug 2026; the index does not move then
CAS = (time(15, 30), time(15, 39))
CAS_FROM = date(2026, 8, 3)

# what a running download reports (a dict the UI shows) and how it is asked to stop
Progress = Callable[[dict[str, Any]], Awaitable[None]]
ShouldStop = Callable[[], Awaitable[bool]]


# -- expiry calendars -------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ExpiryRule:
    frequency: str  # weekly | monthly (the last such weekday of the month)
    weekday: int  # Monday = 0
    start: date = date(2000, 1, 1)
    until: date = date(2100, 1, 1)


MON, TUE, WED, THU, FRI = range(5)
EXPIRY_RULES: dict[str, tuple[ExpiryRule, ...]] = {
    "NIFTY": (ExpiryRule("weekly", THU, until=date(2025, 8, 31)), ExpiryRule("weekly", TUE, date(2025, 9, 1))),
    "BANKNIFTY": (
        ExpiryRule("monthly", THU, date(2024, 12, 1), date(2025, 8, 31)),
        ExpiryRule("monthly", TUE, date(2025, 9, 1)),
    ),
    "FINNIFTY": (
        ExpiryRule("monthly", THU, date(2024, 12, 1), date(2025, 8, 31)),
        ExpiryRule("monthly", TUE, date(2025, 9, 1)),
    ),
    "SENSEX": (
        ExpiryRule("weekly", TUE, date(2025, 1, 1), date(2025, 8, 31)),
        ExpiryRule("weekly", THU, date(2025, 9, 1)),
    ),
}


def expiries(underlying: str, start: date, end: date, trading_days: Sequence[date]) -> list[date]:
    """Expiry dates between start and end; an expiry on a holiday moves back to the previous trading day."""
    rules = EXPIRY_RULES.get(underlying)
    if not rules:
        raise ValueError(f"no expiry calendar for {underlying}")
    open_days = set(trading_days)
    known_until = max(open_days) if open_days else date.min
    closed = calendar_holidays()  # the days after the last stored one are judged by the holiday calendar
    out: set[date] = set()
    d = start
    while d <= end + timedelta(days=40):
        for r in rules:
            if not (r.start <= d <= r.until) or d.weekday() != r.weekday:
                continue
            if r.frequency == "monthly" and (d + timedelta(days=7)).month == d.month:
                continue  # not the last such weekday of the month
            e = d
            while e > d - timedelta(days=5) and (
                e in closed or e.weekday() >= 5 or (e <= known_until and e not in open_days)
            ):
                e -= timedelta(days=1)
            out.add(e)
        d += timedelta(days=1)
    return sorted(out)


# -- planning ---------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Fetch:
    key: str  # history key, e.g. NIFTY:2026-10-06:25000:CE
    expiry: date
    strike: int
    right: str
    first: date
    last: date
    days: int  # trading days in the run

    @property
    def calls(self) -> int:
        return max(1, math.ceil(self.days * BARS_PER_DAY / ROWS_PER_CALL))


def contract_days(
    underlying: str,
    ranges: dict[date, tuple[float, float]],
    expiry_list: Sequence[date],
    step: int,
    buffer: int,
) -> dict[tuple[date, int, str], list[date]]:
    """Every (expiry, strike, right) and the days it should be stored: the traded expiry of each day (the next one
    too on expiry day), strikes from `buffer` below that day's low to `buffer` above its high."""
    out: dict[tuple[date, int, str], list[date]] = {}
    for d in sorted(ranges):
        lo, hi = ranges[d]
        live = [e for e in expiry_list if e >= d]
        traded = live[:2] if live and live[0] == d else live[:1]
        first = math.floor(lo / step) * step - buffer * step
        last = math.ceil(hi / step) * step + buffer * step
        for e in traded:
            for k in range(first, last + 1, step):
                for r in ("CE", "PE"):
                    out.setdefault((e, k, r), []).append(d)
    return out


def plan(underlying: str, wanted: dict[tuple[date, int, str], list[date]], trading_days: Sequence[date],
         stored: set[tuple[str, date]]) -> list[Fetch]:  # fmt: skip
    """Fetches for the contract-days not stored yet, one per run of consecutive trading days."""
    index = {d: i for i, d in enumerate(sorted(trading_days))}
    out = []
    for (e, k, r), days in sorted(wanted.items()):
        key = f"{underlying}:{e:%Y-%m-%d}:{k}:{r}"
        todo = [d for d in days if (key, d) not in stored and d in index]
        run: list[date] = []
        for d in todo:
            if run and index[d] != index[run[-1]] + 1:
                out.append(Fetch(key, e, k, r, run[0], run[-1], len(run)))
                run = []
            run.append(d)
        if run:
            out.append(Fetch(key, e, k, r, run[0], run[-1], len(run)))
    return out


def index_runs(missing: Sequence[date], max_days: int = 10) -> list[tuple[date, date, int]]:
    """Missing index days grouped into runs of consecutive weekdays (at most `max_days` each, so every run is
    stored as soon as it is fetched): (first, last, days)."""
    out: list[tuple[date, date, int]] = []
    for d in sorted(missing):
        if out and (d - out[-1][1]).days <= 3 and out[-1][2] < max_days:
            a, _, n = out[-1]
            out[-1] = (a, d, n + 1)
        else:
            out.append((d, d, 1))
    return out


class HistoryClient(Protocol):
    """Where history comes from: BreezeHistory below, or KiteHistory (kite_feed.py). Requests use Breeze's
    parameters; rows come back shaped like Breeze's (datetime, open, high, low, close, volume, open_interest)."""

    calls: int

    async def candles(self, params: dict[str, Any], start: datetime, end: datetime) -> list[dict[str, Any]]: ...


# -- Breeze ----------------------------------------------------------------------------------------------------
class BudgetExhausted(RuntimeError):
    pass


def fmt_ts(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%S.000Z")  # read by Breeze as IST wall-clock time


@dataclass
class BreezeHistory:
    """historical_data_v2 with paging, retries and the shared daily call budget."""

    sdk: Any
    count_call: Callable[[int], Any]  # increments the shared counter, returns calls used today
    used_today: Callable[[], Any]
    reserve: int = 500
    delay_s: float = 0.65  # Breeze allows 100 calls a minute
    calls: int = 0
    sleep: Callable[[float], Any] = field(default=asyncio.sleep)

    async def _call(self, params: dict[str, Any], start: datetime, end: datetime) -> list[dict[str, Any]]:
        for attempt in range(4):
            used = int(await _maybe(self.used_today()))
            if used >= DAILY_LIMIT - self.reserve:
                raise BudgetExhausted(f"{used} Breeze calls used today; keeping {self.reserve} for the live feed")
            await _maybe(self.count_call(1))
            self.calls += 1
            try:
                resp = await asyncio.to_thread(
                    self.sdk.get_historical_data_v2, from_date=fmt_ts(start), to_date=fmt_ts(end), **params
                )
            except Exception as exc:  # network: retry
                err = f"{type(exc).__name__}: {exc}"
            else:
                status, success, error = resp.get("Status"), resp.get("Success"), resp.get("Error")
                if status == 200 and isinstance(success, list):
                    await self.sleep(self.delay_s)
                    return success
                if status == 200 and not error:
                    await self.sleep(self.delay_s)
                    return []
                err = f"status={status} error={error}"
                if error and any(k in str(error).lower() for k in ("session", "unauthor", "invalid user")):
                    raise RuntimeError(f"Breeze session rejected: {error} (log in again from Monitor)")
                if "no data" in str(error).lower():
                    return []
            log.warning("breeze history call failed", attempt=attempt + 1, error=err)
            await self.sleep(2 * 2**attempt)
        raise RuntimeError(f"Breeze history failed after 4 attempts: {err}")

    async def candles(self, params: dict[str, Any], start: datetime, end: datetime) -> list[dict[str, Any]]:
        """All 1-minute rows in [start, end], oldest first (pages come newest first)."""
        rows: list[dict[str, Any]] = []
        window_end = end
        while True:
            page = await self._call(params, start, window_end)
            rows.extend(page)
            if len(page) < ROWS_PER_CALL:
                break
            stamps = [t for t in (_parse(r.get("datetime")) for r in page) if t is not None]
            earliest = min(stamps) if stamps else None
            if earliest is None or earliest <= start or earliest >= window_end:
                break
            window_end = earliest - timedelta(seconds=1)
        return sorted(rows, key=lambda r: str(r.get("datetime") or ""))


def _parse(v: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None


async def _maybe(v: Any) -> Any:
    return await v if asyncio.iscoroutine(v) else v


def to_rows(key: str, raw: Iterable[dict[str, Any]], window: tuple[time, time] = SESSION) -> list[dict[str, Any]]:
    """Breeze rows -> history_candles rows (the window's minutes only: the main session by default)."""
    out = []
    for r in raw:
        ts = _parse(r.get("datetime"))
        if ts is None:
            continue
        ts = ts.replace(tzinfo=IST) if ts.tzinfo is None else ts.astimezone(IST)
        if not (window[0] <= ts.time() <= window[1]):
            continue
        try:
            o, h, lo, c = (float(r[k]) for k in ("open", "high", "low", "close"))
        except (KeyError, TypeError, ValueError):
            continue
        oi = r.get("open_interest")
        vol = int(float(r.get("volume") or 0))
        out.append({"key": key, "ts": ts, "open": o, "high": h, "low": lo, "close": c, "volume": vol,
                    "oi": int(float(oi)) if oi not in (None, "") else None})  # fmt: skip
    return out


async def _insert_all(db: Database, rows: list[dict[str, Any]], batch: int = 5000) -> int:
    """Postgres takes at most 65,535 parameters per statement: insert in batches."""
    n = 0
    for i in range(0, len(rows), batch):
        n += await _insert(db, rows[i : i + batch])
    return n


# -- the job ----------------------------------------------------------------------------------------------------
@dataclass
class Report:
    underlying: str
    index_days_missing: int = 0
    index_calls: int = 0
    option_fetches: int = 0
    option_calls: int = 0
    rows: int = 0
    calls_made: int = 0
    stopped: str | None = None
    notes: list[str] = field(default_factory=list)


async def _index_days(db: Database, underlying: str, start: date, end: date) -> dict[date, tuple[float, float, int]]:
    lo = datetime.combine(start, time(0), tzinfo=IST)
    hi = datetime.combine(end + timedelta(days=1), time(0), tzinfo=IST)
    async with db.system_session() as s:
        rows = await s.execute(
            text(
                "SELECT (ts AT TIME ZONE 'Asia/Kolkata')::date d, min(low), max(high), count(*) FROM history_candles "
                "WHERE key = :k AND ts >= :lo AND ts < :hi GROUP BY 1"
            ),
            {"k": underlying, "lo": lo, "hi": hi},
        )
        return {r[0]: (float(r[1]), float(r[2]), int(r[3])) for r in rows}


async def option_counts(db: Database, underlying: str, start: date, end: date) -> dict[tuple[str, date], int]:
    """Candles stored per (contract key, day)."""
    lo = datetime.combine(start, time(0), tzinfo=IST)
    hi = datetime.combine(end + timedelta(days=1), time(0), tzinfo=IST)
    day = text("(ts AT TIME ZONE 'Asia/Kolkata')::date")
    async with db.system_session() as s:
        rows = await s.execute(
            select(HistoryCandle.key, day, func.count())
            .where(HistoryCandle.key.like(f"{underlying}:%"), HistoryCandle.ts >= lo, HistoryCandle.ts < hi)
            .group_by(HistoryCandle.key, day)
        )
        return {(k, d): int(n) for k, d, n in rows}


def stored_days(counts: dict[tuple[str, date], int], min_bars: int = 1) -> set[tuple[str, date]]:
    """The contract-days that count as stored: with at least `min_bars` candles (1: any candle at all)."""
    return {kd for kd, n in counts.items() if n >= min_bars}


async def _stored_option_days(
    db: Database, underlying: str, start: date, end: date, min_bars: int = 1
) -> set[tuple[str, date]]:
    return stored_days(await option_counts(db, underlying, start, end), min_bars)


async def tail_days(db: Database, underlying: str, start: date, end: date) -> set[tuple[str, date]]:
    """Contract-days that already have a candle from 15:30 on (the closing auction session)."""
    lo = datetime.combine(start, time(0), tzinfo=IST)
    hi = datetime.combine(end + timedelta(days=1), time(0), tzinfo=IST)
    day = text("(ts AT TIME ZONE 'Asia/Kolkata')::date")
    async with db.system_session() as s:
        rows = await s.execute(
            select(HistoryCandle.key, day)
            .where(
                HistoryCandle.key.like(f"{underlying}:%"),
                HistoryCandle.ts >= lo,
                HistoryCandle.ts < hi,
                text("(ts AT TIME ZONE 'Asia/Kolkata')::time >= time '15:30'"),
            )
            .distinct()
        )
        return {(k, d) for k, d in rows}


def _checked_key(underlying: str, tail: bool) -> str:
    return f"checked{'_tail' if tail else ''}:{underlying}"


async def checked_days(db: Database, underlying: str, tail: bool = False) -> set[tuple[str, date]]:
    """Contract-days a download already asked the provider for. What came back (even nothing, or a few candles: an
    option that hardly traded) is all the provider holds, so they are not asked for again and count as complete."""
    async with db.system_session() as s:
        row = (
            await s.execute(select(PlatformSetting.value).where(PlatformSetting.key == _checked_key(underlying, tail)))
        ).scalar_one_or_none()
    stored: dict[str, list[str]] = dict(row or {})
    return {(k, date.fromisoformat(d)) for k, ds in stored.items() for d in ds}


async def remember_checked(
    db: Database, underlying: str, items: Iterable[tuple[str, date]], tail: bool = False
) -> None:
    new: dict[str, set[str]] = {}
    for key, d in items:
        new.setdefault(key, set()).add(d.isoformat())
    if not new:
        return
    async with db.system_session() as s:
        row = (
            await s.execute(select(PlatformSetting).where(PlatformSetting.key == _checked_key(underlying, tail)))
        ).scalar_one_or_none()
        merged: dict[str, set[str]] = {k: set(v) for k, v in dict(row.value if row else {}).items()}
        for k, ds in new.items():
            merged.setdefault(k, set()).update(ds)
        value = {k: sorted(v) for k, v in merged.items()}
        if row is None:
            s.add(PlatformSetting(key=_checked_key(underlying, tail), value=value))
        else:
            row.value = value


def calendar_holidays(calendar: str = "NSE") -> set[date]:
    """The exchange's trading holidays from holidays.toml (edit that file to add a year). All indices follow it."""
    with (Path(__file__).parent / "holidays.toml").open("rb") as f:
        return {date.fromisoformat(d) for d in tomllib.load(f)[calendar]["holidays"]}


def _holiday_key(underlying: str) -> str:
    return f"holidays:{underlying}"[:60]


async def known_holidays(db: Database, underlying: str) -> set[date]:
    """Weekdays this job found to have no index candles at all although their neighbours had some: market holidays.
    Remembered so that they are not asked for again and not reported as missing."""
    async with db.system_session() as s:
        row = (
            await s.execute(select(PlatformSetting.value).where(PlatformSetting.key == _holiday_key(underlying)))
        ).scalar_one_or_none()
    days: list[str] = list(row or [])
    return {date.fromisoformat(d) for d in days} | calendar_holidays()


async def remember_holidays(db: Database, underlying: str, days: Iterable[date]) -> None:
    new = {d.isoformat() for d in days}
    if not new:
        return
    async with db.system_session() as s:
        row = (
            await s.execute(select(PlatformSetting).where(PlatformSetting.key == _holiday_key(underlying)))
        ).scalar_one_or_none()
        if row is None:
            s.add(PlatformSetting(key=_holiday_key(underlying), value=sorted(new)))
        else:
            row.value = sorted(set(row.value or []) | new)


def weekdays(start: date, end: date) -> list[date]:
    every = (start + timedelta(days=i) for i in range((end - start).days + 1))
    return [d for d in every if d.weekday() < 5]


async def backfill(
    db: Database,
    underlying: str,
    start: date,
    end: date,
    *,
    feed_code: str,
    spot_exchange: str,
    deriv_exchange: str,
    strike_step: int,
    client: HistoryClient | None,
    buffer: int = 4,
    dry_run: bool = False,
    index_only: bool = False,
    refill_partial: bool = False,
    recheck: bool = False,
    progress: Progress | None = None,
    should_stop: ShouldStop | None = None,
) -> Report:
    """Index bars for missing weekdays, then options per the plan. With dry_run (or no client) nothing is fetched:
    the report says what would be. `progress` is told how far it is; `should_stop` is asked between requests."""
    rep = Report(underlying)
    days = await _index_days(db, underlying, start, end)
    holidays = await known_holidays(db, underlying)
    missing = [d for d in weekdays(start, end) if d not in holidays and days.get(d, (0, 0, 0))[2] < BARS_PER_DAY * 0.9]
    runs = index_runs(missing)
    rep.index_days_missing = len(missing)
    rep.index_calls = sum(math.ceil(n * BARS_PER_DAY / ROWS_PER_CALL) for _, _, n in runs)
    live = client is not None and not dry_run
    if live and client is not None:
        params = {
            "interval": "1minute",
            "stock_code": feed_code,
            "exchange_code": spot_exchange,
            "product_type": "cash",
        }
        try:
            for i, (a, b, _) in enumerate(runs, start=1):
                if should_stop is not None and await should_stop():
                    rep.stopped, rep.calls_made = "stopped by you", client.calls
                    return rep
                raw = await client.candles(params, datetime.combine(a, SESSION[0]), datetime.combine(b, SESSION[1]))
                rep.rows += await _insert_all(db, to_rows(underlying, raw))
                got = {r["ts"].date() for r in to_rows(underlying, raw)}
                if got:  # some days of this run came back and others did not: those were closed
                    await remember_holidays(db, underlying, [d for d in missing if a <= d <= b and d not in got])
                log.info("backfill index", underlying=underlying, days=f"{a}..{b}", calls=client.calls)
                if progress is not None:
                    await progress({"phase": "index", "done": i, "total": len(runs), "calls": client.calls,
                                    "rows": rep.rows})  # fmt: skip
        except BudgetExhausted as exc:
            rep.stopped, rep.calls_made = str(exc), client.calls
            return rep
        days = await _index_days(db, underlying, start, end)
    if index_only:
        return rep
    trading = sorted(d for d, (_, _, n) in days.items() if n >= 60)
    if not trading:
        rep.notes.append("no index history in the range yet: the option plan needs it (run without --dry-run first)")
        return rep
    if len(trading) < len(weekdays(start, end)) * 0.8:
        rep.notes.append(f"index history covers {len(trading)} days; options are planned only for those")
    ex = expiries(underlying, start, end, trading)
    wanted = contract_days(underlying, {d: days[d][:2] for d in trading}, ex, strike_step, buffer)
    # a contract-day with a few candles (a live feed that only watched it for a while, an interrupted download) counts
    # as stored unless asked to refill: then it must hold nearly the whole session, as the index days do
    stored = await _stored_option_days(db, underlying, start, end, int(BARS_PER_DAY * 0.9) if refill_partial else 1)
    if not recheck:
        stored |= await checked_days(db, underlying)  # asked before: the provider holds nothing more for them
    fetches = plan(underlying, wanted, trading, stored)
    rep.option_fetches, rep.option_calls = len(fetches), sum(f.calls for f in fetches)
    log.info("backfill plan", underlying=underlying, index_days_fetched=rep.index_days_missing,
             option_fetches=rep.option_fetches, option_calls=rep.option_calls)  # fmt: skip
    if not live or client is None:
        return rep
    if progress is not None:
        await progress({"phase": "options", "done": 0, "total": len(fetches), "calls": client.calls, "rows": rep.rows})
    try:
        for n, f in enumerate(fetches, start=1):
            if should_stop is not None and await should_stop():
                rep.stopped = "stopped by you"
                break
            params = {
                "interval": "1minute",
                "stock_code": feed_code,
                "exchange_code": deriv_exchange,
                "product_type": "options",
                "expiry_date": f"{f.expiry:%Y-%m-%d}T07:00:00.000Z",
                "right": "call" if f.right == "CE" else "put",
                "strike_price": str(f.strike),
            }
            raw = await client.candles(
                params, datetime.combine(f.first, SESSION[0]), datetime.combine(f.last, SESSION[1])
            )
            rep.rows += await _insert_all(db, to_rows(f.key, raw))
            await remember_checked(db, underlying, [(f.key, d) for d in trading if f.first <= d <= f.last])
            if n % 10 == 0 or n == len(fetches):
                log.info("backfill progress", underlying=underlying, done=n, of=len(fetches), calls=client.calls)
            if progress is not None and (n % 5 == 0 or n == len(fetches)):
                await progress({"phase": "options", "done": n, "total": len(fetches), "calls": client.calls,
                                "rows": rep.rows})  # fmt: skip
    except BudgetExhausted as exc:
        rep.stopped = str(exc)
    rep.calls_made = client.calls
    return rep


async def backfill_tail(
    db: Database,
    underlying: str,
    start: date,
    end: date,
    *,
    feed_code: str,
    deriv_exchange: str,
    strike_step: int,
    client: HistoryClient | None,
    buffer: int = 4,
    dry_run: bool = False,
    recheck: bool = False,
    progress: Progress | None = None,
    should_stop: ShouldStop | None = None,
) -> Report:
    """The closing auction session (15:30-15:39) of every wanted option contract-day from `CAS_FROM` that has no
    candle after 15:30 yet: one small call per contract-day. The index has no candles then (it does not move), so
    only options are fetched. Re-running fetches again the contract-days that had no trade in that window."""
    rep = Report(underlying)
    start = max(start, CAS_FROM)
    days = await _index_days(db, underlying, start, end)
    trading = sorted(d for d, (_, _, n) in days.items() if n >= 60)
    if not trading:
        rep.notes.append("no index history in the range: run the normal backfill first")
        return rep
    ex = expiries(underlying, start, end, trading)
    wanted = contract_days(underlying, {d: days[d][:2] for d in trading}, ex, strike_step, buffer)
    have = await tail_days(db, underlying, start, end)
    if not recheck:
        have |= await checked_days(db, underlying, tail=True)
    fetches = [
        Fetch(f"{underlying}:{e:%Y-%m-%d}:{k}:{r}", e, k, r, d, d, 1)
        for (e, k, r), ds in sorted(wanted.items())
        for d in ds
        if (f"{underlying}:{e:%Y-%m-%d}:{k}:{r}", d) not in have
    ]
    rep.option_fetches, rep.option_calls = len(fetches), len(fetches)
    rep.notes.append(f"{len(trading)} trading days from {trading[0]}, closing session only (15:30-15:39)")
    if dry_run or client is None:
        return rep
    empty = 0
    if progress is not None:
        await progress({"phase": "closing", "done": 0, "total": len(fetches), "calls": client.calls, "rows": 0})
    try:
        for n, f in enumerate(fetches, start=1):
            if should_stop is not None and await should_stop():
                rep.stopped = "stopped by you"
                break
            params = {
                "interval": "1minute",
                "stock_code": feed_code,
                "exchange_code": deriv_exchange,
                "product_type": "options",
                "expiry_date": f"{f.expiry:%Y-%m-%d}T07:00:00.000Z",
                "right": "call" if f.right == "CE" else "put",
                "strike_price": str(f.strike),
            }
            raw = await client.candles(params, datetime.combine(f.first, CAS[0]), datetime.combine(f.first, CAS[1]))
            got = to_rows(f.key, raw, CAS)
            empty += not got
            rep.rows += await _insert_all(db, got)
            await remember_checked(db, underlying, [(f.key, f.first)], tail=True)
            if n % 50 == 0 or n == len(fetches):
                log.info("closing-session progress", underlying=underlying, done=n, of=len(fetches), calls=client.calls)
            if progress is not None and (n % 5 == 0 or n == len(fetches)):
                await progress({"phase": "closing", "done": n, "total": len(fetches), "calls": client.calls,
                                "rows": rep.rows, "empty": empty})  # fmt: skip
    except BudgetExhausted as exc:
        rep.stopped = str(exc)
    rep.calls_made = client.calls
    if empty:
        rep.notes.append(
            f"{empty} of {n} contract-days came back with no 15:30-15:39 candles: the provider holds nothing for them "
            "in that window (no trade, or with Kite an expired contract)."
        )
    return rep
