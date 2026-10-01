"""Running users' backtests (ADR 0017): pick up pending ones, replay them over stored history, store the result.
CPU-bound, so the replay runs in a thread and never blocks the notification and feed jobs sharing the worker."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from ae_core.backtest import BacktestResult, simulate, summarize_result
from ae_core.strategy import AnyConfig, SmcScalpConfig, migrate, parse
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
        result = await replay(
            db, config, start, end, multiplier=mult, lot_size=lot, strike_step=step, slippage_pct=slip
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


SMC_SPREAD_PCT = 0.3  # modelled bid/ask spread for bought options (history has trades, not quotes): ADR 0018
WARMUP = timedelta(days=10)  # earlier sessions loaded for runners that read them (Runner.prior_days)


async def replay(
    db: Database, config: AnyConfig, start: date, end: date, *, multiplier: int, lot_size: int, strike_step: int,
    slippage_pct: float,
) -> BacktestResult:  # fmt: skip
    """Run the simulator. The intraday SMC scalper (no position outlives its day) is replayed a month at a time, so a
    long range of option history never has to fit in memory at once."""
    if not isinstance(config, SmcScalpConfig):
        history = await load_history(db, config.underlying, start, end)
        return await asyncio.to_thread(
            simulate, config, history, start, end, multiplier=multiplier, lot_size=lot_size, strike_step=strike_step,
            slippage_pct=slippage_pct,
        )  # fmt: skip
    total = BacktestResult()
    for a, b in months(start, end):
        history = await load_history(db, config.underlying, a - WARMUP, b)
        part = await asyncio.to_thread(
            simulate, config, history, a, b, multiplier=multiplier, lot_size=lot_size, strike_step=strike_step,
            slippage_pct=slippage_pct, spread_pct=SMC_SPREAD_PCT,
        )  # fmt: skip
        merge(total, part)
    total.warnings.insert(0, f"Option fills include a modelled bid/ask spread of {SMC_SPREAD_PCT}% of the premium.")
    return total


def months(start: date, end: date) -> list[tuple[date, date]]:
    out, a = [], start
    while a <= end:
        nxt = (a.replace(day=1) + timedelta(days=32)).replace(day=1)
        out.append((a, min(end, nxt - timedelta(days=1))))
        a = nxt
    return out


def merge(total: BacktestResult, part: BacktestResult) -> None:
    total.trades += part.trades
    total.signals += part.signals
    total.days_replayed += part.days_replayed
    total.days_without_data += part.days_without_data
    total.days_without_options += part.days_without_options
    total.gross_pnl = round(total.gross_pnl + part.gross_pnl, 2)
    total.charges = round(total.charges + part.charges, 2)
    for k, v in part.funnel.items():
        total.funnel[k] = total.funnel.get(k, 0) + v
    for w in part.warnings:
        if w not in total.warnings and "days have no option prices" not in w:
            total.warnings.append(w)
    if part.days_without_options and not any("days have no option prices" in w for w in total.warnings):
        total.warnings.insert(
            0, "Some days have no option prices in the stored history, so nothing could trade on them."
        )


# -- the SMC research report (ADR 0018) -------------------------------------------------------------------------
def subset(r: BacktestResult, lo: date, hi: date) -> BacktestResult:
    """The trades and signals of r whose entry day is in lo..hi."""
    out = BacktestResult()
    out.trades = [t for t in r.trades if lo <= t.entry_time.date() <= hi]
    out.signals = [x for x in r.signals if lo <= datetime.fromisoformat(x["time"]).date() <= hi]
    out.gross_pnl = round(sum(t.gross for t in out.trades), 2)
    out.charges = round(sum(t.charges for t in out.trades), 2)
    return out


def metrics(r: BacktestResult) -> dict[str, object]:
    full = summarize_result(r)
    sig = dict(full["signals"] or {})
    sig.pop("list", None)
    sig.pop("funnel", None)
    return {"positions": full["summary"], "signals": sig}


# Two parameter sets, both fixed before any profit or loss was looked at (only how often rules fired, ADR 0018):
# the strict defaults, and a balanced one with 3m setups and no premium/discount or room filter.
SMC_PROFILES: dict[str, dict[str, object]] = {
    "strict": {},
    "balanced": {"timeframes": {"setup": 3}, "rules": {"premium_discount": False, "min_room_r": 0}},
}


async def smc_report(db: Database, underlyings: list[str], rrs: list[int], start: date, end: date) -> dict[str, object]:
    """Every profile x underlying x R:R over the whole range, also split chronologically 60 / 40 into in-sample and
    out-of-sample halves."""
    split = start + (end - start) * 6 // 10
    out: dict[str, object] = {"from": str(start), "to": str(end), "split": str(split), "runs": []}
    runs: list[dict[str, object]] = []
    for u in underlyings:
        async with db.system_session() as s:
            inst = (await s.execute(select(Instrument).where(Instrument.code == u))).scalar_one()
            lot, step = inst.lot_size, inst.strike_step
        for (profile, base), rr in ((p, rr) for p in SMC_PROFILES.items() for rr in rrs):
            raw: dict[str, Any] = {"underlying": u, **base}
            raw["risk"] = {**dict(raw.get("risk") or {}), "rr": rr}
            cfg = SmcScalpConfig.model_validate(raw)
            r = await replay(db, cfg, start, end, multiplier=1, lot_size=lot, strike_step=step, slippage_pct=0.05)
            runs.append({
                "profile": profile, "underlying": u, "rr": f"1:{rr}", "lot_size": lot,
                "days_replayed": r.days_replayed, "days_without_options": r.days_without_options,
                "all": metrics(r),
                "in_sample": metrics(subset(r, start, split)),
                "out_of_sample": metrics(subset(r, split + timedelta(days=1), end)),
                "funnel": r.funnel, "warnings": r.warnings[:10],
                "signals": (summarize_result(r)["signals"] or {}).get("list", []),
            })  # fmt: skip
            log.info(
                "smc report run",
                profile=profile,
                underlying=u,
                rr=rr,
                signals=len(r.signals),
                net=round(r.gross_pnl - r.charges),
            )
    out["runs"] = runs
    return out
