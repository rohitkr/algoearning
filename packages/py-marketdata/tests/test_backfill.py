"""Breeze backfill: expiry calendars, which contracts and days to fetch, paging and the shared call budget."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pytest
from ae_marketdata.backfill import (
    BreezeHistory,
    BudgetExhausted,
    Fetch,
    contract_days,
    expiries,
    index_runs,
    plan,
    to_rows,
    weekdays,
)
from ae_marketdata.types import IST


def test_weekly_expiries_switch_weekday_and_move_back_over_holidays() -> None:
    days = [d for d in weekdays(date(2025, 8, 18), date(2025, 9, 12)) if d != date(2025, 8, 28)]  # a Thursday off
    ex = expiries("NIFTY", date(2025, 8, 18), date(2025, 9, 12), days)
    assert date(2025, 8, 21) in ex and date(2025, 8, 27) in ex  # Thursday, then Wednesday for the holiday
    assert date(2025, 9, 2) in ex and date(2025, 9, 9) in ex  # Tuesdays from September 2025


def test_monthly_expiries_are_the_last_weekday_of_the_month() -> None:
    days = weekdays(date(2025, 1, 1), date(2025, 10, 31))
    ex = [e for e in expiries("BANKNIFTY", date(2025, 1, 1), date(2025, 10, 31), days) if e <= date(2025, 10, 31)]
    assert ex[:2] == [date(2025, 1, 30), date(2025, 2, 27)]  # last Thursdays
    assert date(2025, 9, 30) in ex and date(2025, 10, 28) in ex  # last Tuesdays


def test_contract_days_cover_each_days_range_and_the_next_expiry_on_expiry_day() -> None:
    ranges = {date(2026, 9, 21): (24410.0, 24490.0), date(2026, 9, 22): (24500.0, 24520.0)}
    ex = [date(2026, 9, 22), date(2026, 9, 29)]
    want = contract_days("NIFTY", ranges, ex, 50, 1)
    assert want[(date(2026, 9, 22), 24350, "CE")] == [date(2026, 9, 21)]  # 24400 - 1 strike
    assert want[(date(2026, 9, 22), 24550, "PE")] == [date(2026, 9, 21), date(2026, 9, 22)]
    assert (date(2026, 9, 29), 24500, "CE") in want  # expiry day: the next expiry too


def test_plan_skips_stored_days_and_groups_consecutive_ones() -> None:
    days = [date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24)]
    key = "NIFTY:2026-09-29:24500:CE"
    wanted = {(date(2026, 9, 29), 24500, "CE"): days}
    (f,) = plan("NIFTY", wanted, days, stored={(key, date(2026, 9, 21))})
    assert (f.key, f.first, f.last, f.days, f.calls) == (key, date(2026, 9, 22), date(2026, 9, 24), 3, 2)
    split = plan("NIFTY", wanted, days, stored={(key, date(2026, 9, 22))})
    assert [(x.first, x.days) for x in split] == [(date(2026, 9, 21), 1), (date(2026, 9, 23), 2)]
    assert Fetch(key, date(2026, 9, 29), 24500, "CE", days[0], days[0], 1).calls == 1


def test_index_runs_bridge_weekends() -> None:
    runs = index_runs([date(2026, 9, 18), date(2026, 9, 21), date(2026, 9, 30)])
    assert runs == [(date(2026, 9, 18), date(2026, 9, 21), 2), (date(2026, 9, 30), date(2026, 9, 30), 1)]
    assert len(index_runs(weekdays(date(2026, 9, 1), date(2026, 9, 30)), max_days=10)) == 3


def test_rows_keep_session_minutes_volume_and_oi() -> None:
    raw = [
        {"datetime": "2026-09-21 09:15:00", "open": "1", "high": "2", "low": "0.5", "close": "1.5", "volume": "650",
         "open_interest": "120000"},
        {"datetime": "2026-09-21 09:14:00", "open": "1", "high": "1", "low": "1", "close": "1"},  # pre-open
        {"datetime": "bad"},
    ]  # fmt: skip
    (row,) = to_rows("K", raw)
    assert row["ts"] == datetime(2026, 9, 21, 9, 15, tzinfo=IST) and (row["volume"], row["oi"]) == (650, 120000)


class FakeSdk:
    """Pages of at most 1000 rows, newest first, like Breeze."""

    def __init__(self, n: int) -> None:
        start = datetime(2026, 9, 1, 9, 15)
        self.rows = [{"datetime": f"{start + timedelta(minutes=i):%Y-%m-%d %H:%M:%S}", "open": 1, "high": 1, "low": 1,
                      "close": 1} for i in range(n)]  # fmt: skip
        self.calls: list[dict[str, Any]] = []

    def get_historical_data_v2(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        end = datetime.strptime(kw["to_date"], "%Y-%m-%dT%H:%M:%S.000Z")
        page = [r for r in self.rows if datetime.fromisoformat(r["datetime"]) <= end][-1000:]
        return {"Status": 200, "Success": list(reversed(page)), "Error": None}


async def _nosleep(_: float) -> None:
    return None


async def test_paging_returns_everything_oldest_first() -> None:
    sdk, used = FakeSdk(2500), [0]

    def count(n: int) -> int:
        used[0] += n
        return used[0]

    c = BreezeHistory(sdk, count, lambda: used[0], sleep=_nosleep)
    rows = await c.candles({}, datetime(2026, 9, 1, 9, 15), datetime(2026, 9, 30, 15, 29))
    assert len(rows) == 2500 and rows[0]["datetime"] < rows[-1]["datetime"] and c.calls == 3 and used[0] == 3


async def test_the_budget_keeps_a_reserve_for_the_feed() -> None:
    c = BreezeHistory(FakeSdk(10), lambda n: None, lambda: 4600, reserve=500, sleep=_nosleep)
    with pytest.raises(BudgetExhausted):
        await c.candles({}, datetime(2026, 9, 1, 9, 15), datetime(2026, 9, 1, 15, 29))
