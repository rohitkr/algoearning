"""Replaying a strategy over stored history (ADR 0017): used by the worker for saved backtests and by the API for
the builder's "test before saving", so both run exactly the same code."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import date, timedelta

from ae_core.backtest import BacktestResult, simulate
from ae_core.strategy import AnyConfig, PlansConfig, RulesConfig, SmcScalpConfig, prior_days_needed
from ae_core.trading.model import Tip
from ae_db.session import Database

from .history import load_history

SMC_SPREAD_PCT = 0.3  # modelled bid/ask spread for bought options (history has trades, not quotes): ADR 0018
WARMUP = timedelta(days=10)  # earlier sessions loaded for runners that read them (Runner.prior_days)


async def replay(
    db: Database, config: AnyConfig, start: date, end: date, *, multiplier: int, lot_size: int, strike_step: int,
    slippage_pct: float, tips: Sequence[Tip] = (),
) -> BacktestResult:  # fmt: skip
    """Run the simulator. The intraday SMC scalper (no position outlives its day) is replayed a month at a time, so a
    long range of option history never has to fit in memory at once."""
    if not isinstance(config, SmcScalpConfig):
        # rules reading earlier sessions (previous-day levels, indicator warm-up) need the days before the start
        sessions = prior_days_needed(config) if isinstance(config, RulesConfig | PlansConfig) else 0
        first = start - timedelta(days=7 + 2 * sessions) if sessions else start  # weekends and holidays included
        history = await load_history(db, config.underlying, first, end)
        return await asyncio.to_thread(
            simulate, config, history, start, end, multiplier=multiplier, lot_size=lot_size, strike_step=strike_step,
            slippage_pct=slippage_pct, tips=tips,
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


async def replay_config(
    db: Database, config: AnyConfig, start: date, end: date, *, multiplier: int, insts: Mapping[str, tuple[int, int]],
    slippage_pct: float, tips: Sequence[Tip] = (),
) -> BacktestResult:  # fmt: skip
    """`replay` for any config. A strategy of plans may trade several instruments: each instrument's plans are replayed
    on that instrument's own history and lot size, and the results are put together (every day row then says which
    instrument it is). `insts`: instrument -> (lot size, strike step)."""
    if not isinstance(config, PlansConfig) or len(config.underlyings()) == 1:
        lot, step = insts[config.underlying]
        return await replay(
            db, config, start, end, multiplier=multiplier, lot_size=lot, strike_step=step, slippage_pct=slippage_pct,
            tips=tips,
        )  # fmt: skip
    total = BacktestResult()
    for u in config.underlyings():
        lot, step = insts[u]
        part = await replay(
            db, config.for_underlying(u), start, end, multiplier=multiplier, lot_size=lot, strike_step=step,
            slippage_pct=slippage_pct, tips=tips,
        )  # fmt: skip
        for row in part.day_log:
            row["underlying"] = u
        merge(total, part)
    return total


def lot_size_note(insts: Mapping[str, tuple[int, int]], used: list[str]) -> str:
    sizes = ", ".join(f"{lot} for {u}" for u in used if (lot := insts[u][0]))
    return f"Quantities use today's lot size ({sizes}); older periods traded other sizes."


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
    total.day_log += part.day_log
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
