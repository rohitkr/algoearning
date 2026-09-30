"""The engine against a real database and Redis: a paper run enters and exits on the feed's prices, records trades,
orders and events, survives a restart, stops on request, obeys the kill switches, and refuses live mode."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import date, datetime, time
from typing import Any

import pytest
from ae_db.enums import RunStatus
from ae_db.models import PlatformSetting, Strategy, StrategyRun, Trade, TradeEvent, User, UserRiskSettings
from ae_db.session import Database
from ae_engine.engine import Engine
from ae_marketdata.hub import Hub
from ae_marketdata.types import IST, Tick
from redis.asyncio import Redis
from sqlalchemy import select, text

from conftest import TEST_REDIS_URL

DAY = date(2026, 10, 6)  # Tuesday, NIFTY expiry
CE = "NIFTY:2026-10-06:25000:CE"
CONFIG: dict[str, Any] = {
    "kind": "time_based",
    "underlying": "NIFTY",
    "timing": {"entry": "09:20", "exit": "15:15", "days": ["TUE"]},
    "legs": [{"id": "L1", "action": "SELL", "option_type": "CE", "lots": 2, "stop_loss": {"value": 30}}],
    "risk": {},
}


def at(hm: str) -> datetime:
    return datetime.combine(DAY, time.fromisoformat(hm), tzinfo=IST)


@pytest.fixture
async def env(db: Database) -> AsyncIterator[tuple[Database, Hub, dict[str, Any]]]:
    r = Redis.from_url(TEST_REDIS_URL)
    await r.flushdb()
    async with db.system_session() as s:
        old = (await s.execute(text("SELECT expiries FROM instruments WHERE code = 'NIFTY'"))).scalar_one()
        await s.execute(
            text("UPDATE instruments SET expiries = CAST(:e AS jsonb) WHERE code = 'NIFTY'"),
            {"e": '["2026-10-06", "2026-10-13", "2026-10-27"]'},
        )
        u = User(auth_subject="sub-a", email="a@example.com")
        s.add(u)
        await s.flush()
        st = Strategy(user_id=u.id, name="S", config=CONFIG, kind="time_based")
        s.add(st)
        await s.flush()
        ids = {"user": u.id, "strategy": st.id}
    yield db, Hub(r), ids
    async with db.system_session() as s:
        restore = text("UPDATE instruments SET expiries = CAST(:e AS jsonb) WHERE code = 'NIFTY'")
        await s.execute(restore, {"e": json.dumps(old)})
    await r.flushdb()
    await r.aclose()


async def add_run(db: Database, ids: dict[str, Any], **kw: Any) -> Any:
    async with db.system_session() as s:
        run = StrategyRun(user_id=ids["user"], strategy_id=ids["strategy"], mode=kw.pop("mode", "paper"),
                          config_snapshot=CONFIG, strategy_name="S", kind="time_based", **kw)  # fmt: skip
        s.add(run)
        await s.flush()
        return run.id


async def prices(hub: Hub, now: datetime, spot: float, ce: float | None) -> None:
    await hub.publish_tick(Tick("NIFTY", spot, now))
    if ce is not None:
        await hub.publish_tick(Tick(CE, ce, now))


async def run_row(db: Database, rid: Any) -> StrategyRun:
    async with db.system_session() as s:
        return (await s.execute(select(StrategyRun).where(StrategyRun.id == rid))).scalar_one()


async def events(db: Database, rid: Any) -> list[str]:
    async with db.system_session() as s:
        return list(
            (
                await s.execute(select(TradeEvent.event).where(TradeEvent.run_id == rid).order_by(TradeEvent.id))
            ).scalars()
        )


async def test_paper_run_enters_stops_out_and_records_everything(env: Any) -> None:
    db, hub, ids = env
    rid = await add_run(db, ids)
    clock = [at("09:19")]
    eng = Engine(db, hub, now=lambda: clock[0])

    await prices(hub, clock[0], 25010, None)
    await eng.tick()
    run = await run_row(db, rid)
    assert run.status == RunStatus.RUNNING and run.started_at is not None

    clock[0] = at("09:20")
    await prices(hub, clock[0], 25010, None)
    await eng.tick()  # asks for the contract's price
    assert CE in await hub.wanted()
    await prices(hub, clock[0], 25010, 100.0)
    await eng.tick()
    async with db.system_session() as s:
        (t,) = (await s.execute(select(Trade).where(Trade.run_id == rid))).scalars()
        assert (t.status, t.side.value, t.quantity, float(t.entry_price)) == ("open", "SELL", 130, 99.95)
        assert float(t.current_sl) == 129.94 and t.tradingsymbol == "NIFTY26OCT0625000CE"

    eng = Engine(db, hub, now=lambda: clock[0])  # a restart: the position comes back from the run row
    clock[0] = at("10:00")
    await prices(hub, clock[0], 25100, 131.0)
    await eng.tick()
    run = await run_row(db, rid)
    assert float(run.realized_pnl) == round((99.95 - 131.05) * 130, 2)
    assert await events(db, rid) == ["run_started", "entry", "exit"]
    async with db.system_session() as s:
        t = (await s.execute(select(Trade).where(Trade.run_id == rid))).scalar_one()
        assert t.status == "closed" and t.exit_reason == "stop-loss"


async def test_stop_squares_off_then_stops(env: Any) -> None:
    db, hub, ids = env
    rid = await add_run(db, ids)
    clock = [at("09:20")]
    eng = Engine(db, hub, now=lambda: clock[0])
    await prices(hub, clock[0], 25010, 100.0)
    await eng.tick()
    await eng.tick()
    async with db.system_session() as s:
        run = await s.get(StrategyRun, rid)
        run.status, run.stop_reason = RunStatus.STOPPING, "stopped by you"
    await eng.tick()
    run = await run_row(db, rid)
    assert run.status == RunStatus.STOPPED and run.stopped_at is not None
    assert (await events(db, rid))[-2:] == ["exit", "run_stopped"]


async def test_kill_switches_block_entries_and_square_off(env: Any) -> None:
    db, hub, ids = env
    rid = await add_run(db, ids)
    clock = [at("09:20")]
    eng = Engine(db, hub, now=lambda: clock[0])
    async with db.system_session() as s:
        s.add(UserRiskSettings(user_id=ids["user"], max_open_positions=0))
    await prices(hub, clock[0], 25010, 100.0)
    await eng.tick()
    await eng.tick()
    assert "order_not_placed" in await events(db, rid)
    async with db.system_session() as s:
        assert (await s.execute(select(Trade).where(Trade.run_id == rid))).first() is None
        await s.execute(text("DELETE FROM user_risk_settings"))
        s.add(PlatformSetting(key="trading_halted", value=True))
    await eng.tick()
    run = await run_row(db, rid)
    assert run.status == RunStatus.STOPPED and run.stop_reason == "trading halted by the platform"


async def test_live_runs_are_refused_and_other_runs_keep_going(env: Any) -> None:
    db, hub, ids = env
    live = await add_run(db, ids, mode="live")
    paper = await add_run(db, ids)
    eng = Engine(db, hub, now=lambda: at("09:20"))
    await prices(hub, at("09:20"), 25010, 100.0)
    await eng.tick()
    assert (await run_row(db, live)).status == RunStatus.ERROR
    assert "live trading is not available" in ((await run_row(db, live)).error or "")
    assert (await run_row(db, paper)).status == RunStatus.RUNNING
