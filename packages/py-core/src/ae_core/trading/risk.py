"""Pre-trade checks, in the order they are applied. Exits are never blocked: closing risk is always allowed.

platform halt      an admin stopped all trading (Monitor)
user kill switch   the user stopped all their trading
daily loss/profit  the user's limits over all their runs today
open positions     the user's cap on simultaneously open legs
trades per day     the user's cap on entries per day
lots per order     the plan's max_lots_per_order
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Intent


@dataclass(frozen=True)
class RiskSettings:
    """A user's own limits (the risk settings page). None = no limit."""

    max_daily_loss: float | None = None
    max_daily_profit: float | None = None
    max_open_positions: int | None = None
    max_trades_per_day: int | None = None
    kill_switch: bool = False


@dataclass(frozen=True)
class RiskContext:
    settings: RiskSettings
    platform_halted: bool
    max_lots_per_order: int | None  # the plan's limit
    day_pnl: float  # realized + unrealized over the user's runs today
    open_positions: int
    entries_today: int


def breach(ctx: RiskContext) -> str | None:
    """A reason to square off every one of the user's runs now, or None."""
    s = ctx.settings
    if ctx.platform_halted:
        return "trading halted by the platform"
    if s.kill_switch:
        return "your kill switch is on"
    if s.max_daily_loss is not None and ctx.day_pnl <= -s.max_daily_loss:
        return f"your daily loss limit of ₹{s.max_daily_loss:,.0f} was reached"
    if s.max_daily_profit is not None and ctx.day_pnl >= s.max_daily_profit:
        return f"your daily profit target of ₹{s.max_daily_profit:,.0f} was reached"
    return None


def check_entry(intent: Intent, ctx: RiskContext) -> str | None:
    """Why this entry must not be placed, or None."""
    if intent.kind != "entry":
        return None
    reason = breach(ctx)
    if reason:
        return reason
    s = ctx.settings
    if s.max_open_positions is not None and ctx.open_positions >= s.max_open_positions:
        return f"you already have {ctx.open_positions} open positions (your limit is {s.max_open_positions})"
    if s.max_trades_per_day is not None and ctx.entries_today >= s.max_trades_per_day:
        return f"you reached your limit of {s.max_trades_per_day} entries today"
    if ctx.max_lots_per_order is not None and intent.lots > ctx.max_lots_per_order:
        return f"{intent.lots} lots is above your plan's {ctx.max_lots_per_order} lots per order"
    return None
