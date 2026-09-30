"""Running users' backtests (ADR 0017): pick up pending ones, replay them over stored history, store the result.
CPU-bound, so the replay runs in a thread and never blocks the notification and feed jobs sharing the worker."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from ae_core.backtest import simulate, summarize_result
from ae_core.strategy import migrate, parse
from ae_db.models import BacktestRun, Instrument
from ae_db.session import Database
from ae_marketdata.history import load_history
from sqlalchemy import select

log = structlog.get_logger("ae_worker.backtests")


async def run_pending(db: Database) -> int:
    """Take one pending backtest and run it. Returns 1 if one was handled."""
    async with db.system_session() as s:
        run = (
            await s.execute(
                select(BacktestRun)
                .where(BacktestRun.status == "pending")
                .order_by(BacktestRun.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if run is None:
            return 0
        run.status, run.started_at = "running", datetime.now(UTC)
        rid = run.id
    try:
        async with db.system_session() as s:
            run = await s.get(BacktestRun, rid)
            assert run is not None
            config = parse(migrate(run.schema_version, run.config_snapshot))
            inst = (await s.execute(select(Instrument).where(Instrument.code == config.underlying))).scalar_one()
            start, end, mult, slip = run.start_date, run.end_date, run.multiplier, run.slippage_pct
            lot, step = inst.lot_size, inst.strike_step
        history = await load_history(db, config.underlying, start, end)
        result = await asyncio.to_thread(
            simulate, config, history, start, end, multiplier=mult, lot_size=lot, strike_step=step, slippage_pct=slip
        )
        out = summarize_result(result)
        out["warnings"] = [
            f"Quantities use today's lot size ({lot} for {config.underlying}); older periods traded other sizes.",
            *out["warnings"],
        ]
        async with db.system_session() as s:
            run = await s.get(BacktestRun, rid)
            assert run is not None
            run.status, run.result, run.finished_at = "done", out, datetime.now(UTC)
        log.info("backtest done", run_id=str(rid), trades=out["summary"]["trades"], net=out["summary"]["net_pnl"])
    except Exception as exc:
        log.exception("backtest failed", run_id=str(rid))
        async with db.system_session() as s:
            run = await s.get(BacktestRun, rid)
            if run is not None:
                run.status, run.error, run.finished_at = (
                    "error",
                    f"{type(exc).__name__}: {exc}"[:500],
                    datetime.now(UTC),
                )
    return 1
