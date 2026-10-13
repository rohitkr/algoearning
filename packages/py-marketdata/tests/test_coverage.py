"""Monitor > Data sync's picture of what history is stored and what is missing, day by day."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from ae_db.models import HistoryCandle
from ae_db.session import Database
from ae_marketdata.backfill import contract_days, expiries, remember_holidays
from ae_marketdata.coverage import coverage, day_status
from ae_marketdata.types import IST
from sqlalchemy import text

MON, TUE, WED, SAT = date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 10)


def ts(day: date, hm: str = "09:15", plus: int = 0) -> datetime:
    return datetime.combine(day, time.fromisoformat(hm), tzinfo=IST) + timedelta(minutes=plus)


async def seed_index(db: Database, days: list[date]) -> None:
    async with db.system_session() as s:
        await s.execute(text("DELETE FROM history_candles"))
        for d in days:
            for i in range(375):
                s.add(HistoryCandle(key="NIFTY", ts=ts(d, plus=i), open=25000, high=25040, low=24960, close=25000,
                                    volume=0))  # fmt: skip


async def seed_contract(db: Database, key: str, day: date, bars: int, from_hm: str = "09:15") -> None:
    async with db.system_session() as s:
        for i in range(bars):
            s.add(HistoryCandle(key=key, ts=ts(day, from_hm, i), open=10, high=11, low=9, close=10, volume=1))


def wanted_keys(day: date) -> list[str]:
    ex = expiries("NIFTY", MON, WED, [MON, TUE, WED])
    wanted = contract_days("NIFTY", {d: (24960.0, 25040.0) for d in (MON, TUE, WED)}, ex, 50, 4)
    return [f"NIFTY:{e:%Y-%m-%d}:{k}:{r}" for (e, k, r), ds in sorted(wanted.items()) if day in ds]


def test_a_days_status_follows_what_is_stored() -> None:
    assert day_status(False, 0, 0, 0, 0, 0) == "closed"
    assert day_status(True, 0, 0, 0, 0, 0) == "missing"  # no index candles on a market day
    assert day_status(True, 375, 22, 0, 0, 22) == "missing"  # candles for the index, none for any contract
    assert day_status(True, 375, 22, 20, 2, 0) == "partial"
    assert day_status(True, 300, 0, 0, 0, 0) == "partial"  # a short index day
    assert day_status(True, 375, 22, 22, 0, 0) == "ok"


async def test_missing_partial_and_complete_days_are_told_apart(db: Database) -> None:
    await seed_index(db, [MON, TUE, WED])
    cov = await coverage(db, "NIFTY", MON, SAT, 50)
    by = {d.day: d for d in cov.days}
    assert [by[d].status for d in (MON, TUE, WED)] == ["missing"] * 3  # index only, no option candles yet
    assert SAT not in by and cov.trading_days == 3  # weekends are not listed
    assert cov.plan.option_fetches > 0 and cov.plan.option_calls > 0

    for key in wanted_keys(MON):  # Monday: every contract stored in full
        await seed_contract(db, key, MON, 375)
    keys = wanted_keys(TUE)
    await seed_contract(db, keys[0], TUE, 50)  # Tuesday: one contract with only part of the session
    for key in keys[1:]:
        await seed_contract(db, key, TUE, 375)
    cov = await coverage(db, "NIFTY", MON, SAT, 50)
    by = {d.day: d for d in cov.days}
    assert by[MON].status == "ok" and by[MON].options_missing == 0 and by[MON].options_partial == 0
    assert by[TUE].status == "partial" and by[TUE].options_partial == 1 and by[TUE].options_missing == 0
    assert by[WED].status == "missing"
    before = cov.plan.option_fetches
    await seed_contract(db, keys[0], TUE, 375 - 50, "10:05")  # the rest of that contract arrives
    cov = await coverage(db, "NIFTY", MON, SAT, 50)
    assert {d.day: d.status for d in cov.days}[TUE] == "ok" and cov.plan.option_fetches == before - 1


async def test_a_known_holiday_is_closed_not_missing(db: Database) -> None:
    await seed_index(db, [MON, WED])  # nothing on Tuesday
    assert {d.day: d.status for d in (await coverage(db, "NIFTY", MON, WED, 50)).days}[TUE] == "missing"
    await remember_holidays(db, "NIFTY", [TUE])
    cov = await coverage(db, "NIFTY", MON, WED, 50)
    assert {d.day: d.status for d in cov.days}[TUE] == "closed" and cov.plan.index_days_missing == 0
