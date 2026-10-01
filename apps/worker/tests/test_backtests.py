"""A queued backtest runs over stored history and stores its result; failures are recorded, not raised."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from ae_db.models import BacktestRun, HistoryCandle, Strategy, User
from ae_db.session import Database
from ae_marketdata.types import IST
from ae_worker.backtests import run_pending
from sqlalchemy import select, text

DAY = date(2026, 10, 6)  # a Tuesday
KEY = "NIFTY:2026-10-06:25000:CE"
CFG = {"kind": "time_based", "timing": {"entry": "09:20", "exit": "15:15", "days": ["TUE"]},
       "legs": [{"id": "L1", "action": "SELL", "option_type": "CE", "lots": 1}]}  # fmt: skip


def ts(hm: str) -> datetime:
    return datetime.combine(DAY, time.fromisoformat(hm), tzinfo=IST)


async def seed(db: Database, config: dict, name: str = "S") -> None:  # type: ignore[type-arg]
    async with db.system_session() as s:
        await s.execute(text("DELETE FROM history_candles"))
        u = User(auth_subject="sub", email="a@example.com")
        s.add(u)
        await s.flush()
        st = Strategy(user_id=u.id, name=name, config=config, kind="time_based")
        s.add(st)
        await s.flush()
        for i in range(375):
            t = ts("09:15") + timedelta(minutes=i)
            s.add(HistoryCandle(key="NIFTY", ts=t, open=25000, high=25001, low=24999, close=25000, volume=0))
        for hm, px in (("09:20", 100.0), ("15:15", 90.0)):
            s.add(HistoryCandle(key=KEY, ts=ts(hm), open=px, high=px, low=px, close=px, volume=0))
        s.add(BacktestRun(user_id=u.id, strategy_id=st.id, strategy_name=name, kind="time_based",
                          config_snapshot=config, start_date=DAY, end_date=DAY, slippage_pct=0.0))  # fmt: skip


async def test_pending_backtest_runs_and_stores_the_result(db: Database) -> None:
    await seed(db, CFG)
    assert await run_pending(db) == 1
    assert await run_pending(db) == 0  # nothing left
    async with db.system_session() as s:
        run = (await s.execute(select(BacktestRun))).scalar_one()
        await s.execute(text("DELETE FROM history_candles"))
    assert run.status == "done" and run.result is not None
    sm = run.result["summary"]
    assert (sm["trades"], sm["gross_pnl"], sm["days_replayed"]) == (1, (100 - 90) * 65, 1)
    assert run.result["daily"][0]["day"] == "2026-10-06"
    assert "today's lot size" in run.result["warnings"][0]


async def test_a_failing_backtest_is_recorded(db: Database) -> None:
    async with db.system_session() as s:
        u = User(auth_subject="sub", email="a@example.com")
        s.add(u)
        await s.flush()
        s.add(BacktestRun(user_id=u.id, strategy_name="bad", kind="time_based", config_snapshot={"kind": "nope"},
                          start_date=DAY, end_date=DAY))  # fmt: skip
    assert await run_pending(db) == 1
    async with db.system_session() as s:
        run = (await s.execute(select(BacktestRun))).scalar_one()
    assert run.status == "error" and run.error


def test_smc_backtests_replay_month_by_month() -> None:
    from datetime import date

    from ae_core.backtest import BacktestResult
    from ae_worker.backtests import merge, months

    assert months(date(2025, 1, 20), date(2025, 3, 3)) == [
        (date(2025, 1, 20), date(2025, 1, 31)),
        (date(2025, 2, 1), date(2025, 2, 28)),
        (date(2025, 3, 1), date(2025, 3, 3)),
    ]
    total = BacktestResult()
    for part in (
        BacktestResult(days_replayed=3, funnel={"armed": 1}),
        BacktestResult(days_replayed=2, funnel={"armed": 2}),
    ):
        merge(total, part)
    assert total.days_replayed == 5 and total.funnel == {"armed": 3}
