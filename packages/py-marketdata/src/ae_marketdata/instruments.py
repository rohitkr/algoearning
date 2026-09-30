"""Refresh every instrument's exchange facts (lot size, strike step, weekly or monthly expiries) from
Zerodha's public instrument list, so a SEBI lot-size change reaches strategies without a release (ADR 0011).
Run daily by the worker and on demand from Monitor.

Only facts the list can prove are written; an instrument missing from the list keeps its values and is reported.
Trading hours and is_active are admin settings and are never touched here."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone

import httpx
import structlog
from ae_brokers.instruments import derive_facts, fetch_dump
from ae_db.models import Instrument
from ae_db.repositories import InstrumentRepo
from ae_db.session import Database

log = structlog.get_logger("ae_marketdata.instruments")
IST = timezone(timedelta(hours=5, minutes=30))

Fetch = Callable[[str], Awaitable[list[dict[str, str]]]]


@dataclass
class RefreshResult:
    changed: dict[str, dict[str, tuple[object, object]]]  # code -> field -> (old, new)
    unchanged: list[str]
    missing: list[str]  # not in the list (or unusable): kept as they were


async def _kite_fetch(exchange: str) -> list[dict[str, str]]:
    async with httpx.AsyncClient() as client:
        return await fetch_dump(exchange, client)


async def refresh_instruments(db: Database, fetch: Fetch = _kite_fetch, today: date | None = None) -> RefreshResult:
    today = today or datetime.now(IST).date()
    async with db.system_session() as s:
        rows: list[Instrument] = await InstrumentRepo(s).list_all()
    dumps = {x: await fetch(x) for x in sorted({r.exchange for r in rows})}
    result = RefreshResult({}, [], [])
    now = datetime.now(UTC)
    async with db.system_session() as s:
        for row in await InstrumentRepo(s).list_all():
            facts = derive_facts(dumps[row.exchange], row.code, today)
            if facts is None:
                result.missing.append(row.code)
                continue
            new: dict[str, object] = {
                "lot_size": facts.lot_size,
                "strike_step": facts.strike_step,
                "weekly_expiry": facts.weekly_expiry,
            }
            diff: dict[str, tuple[object, object]] = {
                k: (getattr(row, k), value) for k, value in new.items() if getattr(row, k) != value
            }
            for k, value in new.items():
                setattr(row, k, value)
            row.expiries = [e.isoformat() for e in facts.expiries]  # rolls daily: not reported as a change
            row.source, row.refreshed_at = "kite", now
            if diff:
                result.changed[row.code] = diff
            else:
                result.unchanged.append(row.code)
    for code, diff in result.changed.items():
        log.warning("instrument changed", code=code, **{k: f"{a} -> {b}" for k, (a, b) in diff.items()})
    log.info("instruments refreshed", changed=sorted(result.changed), missing=result.missing)
    return result
