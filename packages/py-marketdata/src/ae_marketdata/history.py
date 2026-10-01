"""Stored 1-minute history for backtesting (ADR 0017): loading it for the simulator, importing the DuckDB store of
algo-trading-claude, archiving each day's feed bars from Redis so history keeps growing, and reporting coverage."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import structlog
from ae_core.backtest import Candle, MemoryHistory
from ae_db.models import HistoryCandle
from ae_db.session import Database
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from .hub import Hub
from .types import IST, Bar

log = structlog.get_logger("ae_marketdata.history")
BATCH = 5000
# algo-trading-claude's Breeze stock codes -> our instrument codes
LEGACY_INDEX = {
    "NIFTY": "NIFTY",
    "CNXBAN": "BANKNIFTY",
    "NIFFIN": "FINNIFTY",
    "NIFSEL": "MIDCPNIFTY",
    "SENSEX": "SENSEX",
}
_HAS_OI = "SELECT 1 FROM information_schema.columns WHERE table_name = 'history_candles' AND column_name = 'oi'"
LEGACY_RIGHT = {"CALL": "CE", "PUT": "PE", "CE": "CE", "PE": "PE"}


def _ist(ts: datetime) -> datetime:
    return ts.replace(tzinfo=IST) if ts.tzinfo is None else ts.astimezone(IST)


@dataclass
class Coverage:
    underlying: str
    index_from: date | None
    index_to: date | None
    index_days: int
    option_from: date | None
    option_to: date | None
    option_days: int
    expiries: int


async def load_history(db: Database, underlying: str, start: date, end: date) -> MemoryHistory:
    """Everything the simulator needs for one underlying between two days (inclusive), in memory."""
    lo = datetime.combine(start, time(0), tzinfo=IST)
    hi = datetime.combine(end + timedelta(days=1), time(0), tzinfo=IST)
    h = MemoryHistory()
    hc = HistoryCandle
    async with db.system_session() as s:
        q = select(hc.ts, hc.open, hc.high, hc.low, hc.close).where(hc.key == underlying, hc.ts >= lo, hc.ts < hi)
        h.add_spot(underlying, (Candle(_ist(ts), o, hi_, lo_, c) for ts, o, hi_, lo_, c in (await s.execute(q)).all()))
        # open interest arrived with migration 0012: read it only where the column exists (a worker may run first)
        has_oi = bool((await s.execute(text(_HAS_OI))).first())
        cols = [hc.key, hc.ts, hc.open, hc.high, hc.low, hc.close, hc.volume, *([hc.oi] if has_oi else [])]
        oq = (
            select(*cols)
            .where(hc.key.like(f"{underlying}:%"), hc.ts >= lo, hc.ts < hi)
            .order_by(hc.key, hc.ts)
            .execution_options(yield_per=20000)
        )
        by_key: dict[str, list[Candle]] = {}
        async for row in await s.stream(oq):
            r: Any = row
            oi = r[7] if has_oi else None
            by_key.setdefault(r[0], []).append(Candle(_ist(r[1]), r[2], r[3], r[4], r[5], r[6] or 0, oi))
        for key, candles in by_key.items():
            h.add_option(key, candles)
        # expiries that exist in the data even when a contract has no bars in the range (positions carried in)
        rows = await s.execute(
            text("SELECT DISTINCT split_part(key, ':', 2) FROM history_candles WHERE key LIKE :p AND ts >= :lo"),
            {"p": f"{underlying}:%", "lo": lo},
        )
        for (e,) in rows:
            h._exp.setdefault(underlying, set()).add(date.fromisoformat(e))
    return h


async def coverage(db: Database) -> list[Coverage]:
    async with db.system_session() as s:
        idx = {
            r[0]: r
            for r in (
                await s.execute(
                    text(
                        "SELECT key, min(d), max(d), count(DISTINCT d) FROM "
                        "(SELECT key, (ts AT TIME ZONE 'Asia/Kolkata')::date d FROM history_candles) x "
                        "WHERE key NOT LIKE '%:%' GROUP BY key"
                    )
                )
            ).all()
        }
        opt = {
            r[0]: r
            for r in (
                await s.execute(
                    text(
                        "SELECT u, min(d), max(d), count(DISTINCT d), count(DISTINCT e) FROM ("
                        "SELECT split_part(key, ':', 1) u, split_part(key, ':', 2) e, "
                        "(ts AT TIME ZONE 'Asia/Kolkata')::date d FROM history_candles WHERE key LIKE '%:%') x "
                        "GROUP BY u"
                    )
                )
            ).all()
        }
    out = []
    for u in sorted(set(idx) | set(opt)):
        i, o = idx.get(u), opt.get(u)
        out.append(
            Coverage(
                u,
                i[1] if i else None,
                i[2] if i else None,
                i[3] if i else 0,
                o[1] if o else None,
                o[2] if o else None,
                o[3] if o else 0,
                o[4] if o else 0,
            )
        )
    return out


async def _insert(db: Database, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    async with db.system_session() as s:
        stmt = insert(HistoryCandle).values(rows).on_conflict_do_nothing(index_elements=["key", "ts"])
        await s.execute(stmt)
        return len(rows)  # rows offered: duplicates of stored minutes are skipped by the database


def _rows(kind: str, con: Any) -> Iterator[dict[str, Any]]:
    if kind == "index":
        q = (
            "SELECT instrument, ts, open, high, low, close, volume FROM market_candles "
            "WHERE timeframe = '1minute' ORDER BY instrument, ts"
        )
        for inst, ts, o, h, low, c, v in con.execute(q).fetchall():
            code = LEGACY_INDEX.get(inst)
            if code:
                yield {"key": code, "ts": _ist(ts), "open": o, "high": h, "low": low, "close": c, "volume": v or 0}
    else:
        q = (
            "SELECT underlying, expiry, strike, option_right, ts, open, high, low, close, volume "
            "FROM option_candles WHERE timeframe = '1minute'"
        )
        for u, e, k, r, ts, o, h, low, c, v in con.execute(q).fetchall():
            right = LEGACY_RIGHT.get(str(r).upper())
            if right and u in LEGACY_INDEX.values():
                key = f"{u}:{e:%Y-%m-%d}:{int(k)}:{right}"
                yield {"key": key, "ts": _ist(ts), "open": o, "high": h, "low": low, "close": c, "volume": v or 0}


async def import_duckdb(db: Database, path: str | Path) -> dict[str, int]:
    """One-off: copy algo-trading-claude's index and option candles into history_candles (existing rows are kept).
    Returns the rows offered per kind."""
    try:
        import duckdb  # only this command needs it: uv run --with duckdb python -m ae_worker import-history <file>
    except ImportError as exc:
        raise RuntimeError(
            "install duckdb for this: uv run --with duckdb python -m ae_worker import-history FILE"
        ) from exc
    con = duckdb.connect(str(path), read_only=True)
    out = {}
    try:
        for kind in ("index", "options"):
            batch: list[dict[str, Any]] = []
            n = 0
            for row in _rows(kind, con):
                batch.append(row)
                if len(batch) >= BATCH:
                    n += await _insert(db, batch)
                    batch = []
            n += await _insert(db, batch)
            out[kind] = n
            log.info("history imported", kind=kind, rows=n)
    finally:
        con.close()
    return out


async def archive_day(db: Database, hub: Hub, keys: Iterable[str], day: date) -> int:
    """Copy a day's completed bars from Redis into history (upsert-free: existing minutes are kept)."""
    n = 0
    for key in keys:
        bars: list[Bar] = await hub.bars(key, day)
        n += await _insert(
            db,
            [
                {
                    "key": b.key,
                    "ts": b.ts,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                }
                for b in bars
            ],
        )
    return n


async def archive_today(db: Database, hub: Hub, day: date | None = None) -> int:
    """Daily job: archive every index and every option contract the feed streamed today."""
    day = day or datetime.now(IST).date()
    pattern = f"md:bars:*:{day:%Y%m%d}"
    keys: list[str] = []
    async for k in hub.r.scan_iter(match=pattern, count=500):
        name = k.decode() if isinstance(k, bytes) else str(k)
        keys.append(name.split(":", 2)[2].rsplit(":", 1)[0])
    total = await archive_day(db, hub, keys, day)
    log.info("history archived", day=str(day), keys=len(keys), rows=total)
    return total


async def count_rows(db: Database) -> int:
    async with db.system_session() as s:
        return int((await s.execute(select(func.count()).select_from(HistoryCandle))).scalar_one())
