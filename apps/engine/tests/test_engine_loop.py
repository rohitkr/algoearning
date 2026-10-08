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


async def live_setup(db: Database, ids: dict[str, Any], box: Any, engine_enabled: bool = True) -> Any:
    from datetime import timedelta

    from ae_db.models import BrokerAccount, BrokerSession

    async with db.system_session() as s:
        acc = BrokerAccount(user_id=ids["user"], broker="zerodha", client_id="AB1234", status="connected",
                            engine_enabled=engine_enabled)  # fmt: skip
        s.add(acc)
        await s.flush()
        acc.api_key_enc = box.encrypt("api-key", f"broker_account:{acc.id}:api_key")
        token = box.encrypt("access-token", f"broker_session:{acc.id}:access_token")
        until = at("09:00") + timedelta(days=1)
        s.add(BrokerSession(user_id=ids["user"], broker_account_id=acc.id, expires_at=until, access_token_enc=token))
        return acc.id


def live_engine(db: Database, hub: Hub, clock: list[datetime], box: Any, kite: Any) -> Engine:
    from ae_brokers.fake_kite import book
    from ae_engine.live import LiveConfig

    async def no_sleep(_: float) -> None:
        return None

    return Engine(
        db, hub, now=lambda: clock[0], box=box, live=LiveConfig(fill_timeout_s=0),
        book=book(("NIFTY", DAY, 25000, "CE", "NIFTY2610625000CE")), kite_transport=kite.transport(), sleep=no_sleep,
    )  # fmt: skip


def a_box() -> Any:
    import base64

    from ae_core.secrets import SecretBox, new_master_key

    return SecretBox({1: base64.b64decode(new_master_key())}, 1)


async def settle() -> None:
    import asyncio

    for _ in range(20):
        await asyncio.sleep(0)


async def test_live_run_trades_on_zerodha_and_reconciles(env: Any) -> None:
    from ae_brokers.fake_kite import FakeKite

    db, hub, ids = env
    box = a_box()
    acc_id = await live_setup(db, ids, box)
    rid = await add_run(db, ids, mode="live", broker_account_id=acc_id)
    kite = FakeKite(prices={"NIFTY2610625000CE": 100.0})
    clock = [at("09:20")]
    eng = live_engine(db, hub, clock, box, kite)
    await prices(hub, clock[0], 25010, 100.0)
    await eng.tick()  # entry intent handed to the account
    await settle()
    assert [x[:3] for x in kite.log] == [("place", "SELL", "NIFTY2610625000CE")]
    assert (await run_row(db, rid)).state["_inflight"]
    await eng.tick()  # the fill is applied
    async with db.system_session() as s:
        t = (await s.execute(select(Trade).where(Trade.run_id == rid))).scalar_one()
        assert (t.mode.value, t.tradingsymbol, float(t.entry_price), t.status) == (
            "live",
            "NIFTY2610625000CE",
            100.0,
            "open",
        )
    assert (await run_row(db, rid)).state["_inflight"] == []
    assert kite.net == {"NIFTY2610625000CE": -130}

    # someone squares the position off in Kite: after two checks a minute apart it is closed here too
    kite.net = {}
    from datetime import timedelta

    for step in range(3):
        clock[0] = at("09:22") + timedelta(minutes=step)
        await prices(hub, clock[0], 25010, 99.0)
        await eng.tick()
    async with db.system_session() as s:
        t = (await s.execute(select(Trade).where(Trade.run_id == rid))).scalar_one()
        assert t.status == "closed" and t.exit_reason == "closed outside AlgoEarning"
    assert "position_closed_outside" in await events(db, rid)


async def test_live_entries_need_the_engine_switch_and_a_session(env: Any) -> None:
    from ae_brokers.fake_kite import FakeKite

    db, hub, ids = env
    box = a_box()
    acc_id = await live_setup(db, ids, box, engine_enabled=False)
    rid = await add_run(db, ids, mode="live", broker_account_id=acc_id)
    kite = FakeKite(prices={"NIFTY2610625000CE": 100.0})
    eng = live_engine(db, hub, [at("09:20")], box, kite)
    await prices(hub, at("09:20"), 25010, 100.0)
    await eng.tick()
    assert "order_not_placed" in await events(db, rid) and kite.log == []

    eng2 = live_engine(db, hub, [at("09:20")], None, kite)  # no key: cannot open the session
    async with db.system_session() as s:
        await s.execute(text("UPDATE broker_accounts SET engine_enabled = true"))
        await s.execute(text("UPDATE strategy_runs SET state = '{}'"))
    await eng2.tick()
    await settle()
    await eng2.tick()
    assert "order_failed" in await events(db, rid) and kite.log == []


async def test_dry_run_logs_orders_and_records_paper_trades(env: Any) -> None:
    from ae_brokers.fake_kite import FakeKite

    db, hub, ids = env
    rid = await add_run(db, ids, mode="live", dry_run=True)
    kite = FakeKite()
    eng = live_engine(db, hub, [at("09:20")], None, kite)
    await prices(hub, at("09:20"), 25010, 100.0)
    await eng.tick()
    await settle()
    await eng.tick()
    async with db.system_session() as s:
        t = (await s.execute(select(Trade).where(Trade.run_id == rid))).scalar_one()
        assert t.mode.value == "paper" and t.rules["dry_run"] is True
    assert "dry_run_order" in await events(db, rid) and kite.log == []


async def test_a_telegram_tip_starts_a_paper_trade(env: Any) -> None:
    """ADR 0025: the engine hands the user's tips of the day to a tip strategy; a bullish tip sells the put."""
    import uuid as _uuid

    from ae_db.models import SignalRow, SignalSource

    db, hub, ids = env
    pe = "NIFTY:2026-10-06:25000:PE"
    async with db.system_session() as s:
        src = SignalSource(id=_uuid.uuid4(), user_id=ids["user"], key_version=1, status="connected", chat_id=-1)
        s.add(src)
        await s.flush()
        s.add(SignalRow(user_id=ids["user"], source_id=src.id, header_msg_id=7, date=at("10:00"), index="NIFTY",
                        strike=25000, option_type="CE", action="BUY", direction="BULLISH", entry_low=150,
                        entry_high=154, stop_loss=135, targets=[169], targets_done=[], intraday=True,
                        status="OPEN", complete=True, message_ids=[7]))  # fmt: skip
        cfg = {"kind": "rules", "underlying": "NIFTY",
               "entry": {"mode": "tip", "at": "09:20", "until": "14:30", "source_id": str(src.id)},
               "legs": [{"id": "BULL", "action": "SELL", "option_type": "PE", "direction": "up"},
                        {"id": "BEAR", "action": "SELL", "option_type": "CE", "direction": "down"}]}  # fmt: skip
        run = StrategyRun(user_id=ids["user"], strategy_id=ids["strategy"], mode="paper", config_snapshot=cfg,
                          strategy_name="Tips", kind="rules")  # fmt: skip
        s.add(run)
        await s.flush()
        rid = run.id
    clock = [at("10:00")]
    eng = Engine(db, hub, now=lambda: clock[0])
    await hub.publish_tick(Tick("NIFTY", 25010, clock[0]))
    await hub.publish_tick(Tick(pe, 80.0, clock[0]))
    await hub.publish_tick(Tick(CE, 90.0, clock[0]))
    await eng.tick()
    clock[0] = at("10:00").replace(second=20)
    await hub.publish_tick(Tick("NIFTY", 25010, clock[0]))
    await hub.publish_tick(Tick(pe, 80.0, clock[0]))
    await eng.tick()
    async with db.system_session() as s:
        (t,) = (await s.execute(select(Trade).where(Trade.run_id == rid))).scalars()
        assert (t.side.value, t.option_type, float(t.strike)) == ("SELL", "PE", 25000)
    assert "tip_received" in await events(db, rid)
