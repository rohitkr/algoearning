"""Replaying a strategy over stored history (ADR 0017): used by the worker for saved backtests and by the API for
the builder's "test before saving", so both run exactly the same code."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from ae_core.backtest import BacktestResult, simulate
from ae_core.strategy import AnyConfig, RulesConfig, SmcScalpConfig, prior_days_needed
from ae_db.session import Database

from .history import load_history

SMC_SPREAD_PCT = 0.3  # modelled bid/ask spread for bought options (history has trades, not quotes): ADR 0018
WARMUP = timedelta(days=10)  # earlier sessions loaded for runners that read them (Runner.prior_days)


async def replay(
    db: Database, config: AnyConfig, start: date, end: date, *, multiplier: int, lot_size: int, strike_step: int,
    slippage_pct: float,
) -> BacktestResult:  # fmt: skip
    """Run the simulator. The intraday SMC scalper (no position outlives its day) is replayed a month at a time, so a
    long range of option history never has to fit in memory at once."""
    if not isinstance(config, SmcScalpConfig):
        # rules reading earlier sessions (previous-day levels, indicator warm-up) need the days before the start
        sessions = prior_days_needed(config) if isinstance(config, RulesConfig) else 0
        first = start - timedelta(days=7 + 2 * sessions) if sessions else start  # weekends and holidays included
        history = await load_history(db, config.underlying, first, end)
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
