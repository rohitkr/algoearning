"""Performance maths over closed positions: pure, so the API, exports and (later) emails agree on every number.

A trade's P&L counts on the day it was closed. Drawdown is the largest fall of the cumulative P&L from its
running peak (the peak starts at 0, so a losing first day is a drawdown too)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ClosedTrade:
    day: date  # the day it was closed
    pnl: float


@dataclass(frozen=True)
class Day:
    day: date
    pnl: float
    trades: int
    cumulative: float


@dataclass(frozen=True)
class Summary:
    total_pnl: float
    trades: int
    wins: int
    losses: int
    win_rate: float | None  # 0..1; None without trades
    avg_win: float | None
    avg_loss: float | None  # negative
    profit_factor: float | None  # gross profit / gross loss; None without losses
    max_drawdown: float  # <= 0
    best_day: Day | None
    worst_day: Day | None
    trading_days: int


def daily(trades: Iterable[ClosedTrade]) -> list[Day]:
    pnl: dict[date, float] = defaultdict(float)
    count: dict[date, int] = defaultdict(int)
    for t in trades:
        pnl[t.day] += t.pnl
        count[t.day] += 1
    out, cum = [], 0.0
    for d in sorted(pnl):
        cum += pnl[d]
        out.append(Day(d, round(pnl[d], 2), count[d], round(cum, 2)))
    return out


def max_drawdown(days: Iterable[Day]) -> float:
    peak, worst = 0.0, 0.0
    for d in days:
        peak = max(peak, d.cumulative)
        worst = min(worst, d.cumulative - peak)
    return round(worst, 2)


def summarize(trades: Iterable[ClosedTrade]) -> Summary:
    ts = list(trades)
    wins = [t.pnl for t in ts if t.pnl > 0]
    losses = [t.pnl for t in ts if t.pnl < 0]
    days = daily(ts)
    gross_loss = -sum(losses)
    return Summary(
        total_pnl=round(sum(t.pnl for t in ts), 2),
        trades=len(ts),
        wins=len(wins),
        losses=len(losses),
        win_rate=round(len(wins) / len(ts), 4) if ts else None,
        avg_win=round(sum(wins) / len(wins), 2) if wins else None,
        avg_loss=round(sum(losses) / len(losses), 2) if losses else None,
        profit_factor=round(sum(wins) / gross_loss, 2) if gross_loss > 0 else None,
        max_drawdown=max_drawdown(days),
        best_day=max(days, key=lambda d: d.pnl) if days else None,
        worst_day=min(days, key=lambda d: d.pnl) if days else None,
        trading_days=len(days),
    )
