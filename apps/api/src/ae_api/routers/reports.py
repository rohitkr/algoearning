"""Performance reports and open positions, from the recorded trades (paper and live alike), per user under
row-level security. A trade counts on the day it was closed (IST)."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Literal

from ae_core.reports import ClosedTrade, Day, daily, summarize
from ae_db.enums import TradingMode
from ae_db.models import StrategyRun, Trade
from ae_db.pagination import clamp_limit, decode_cursor, encode_cursor
from ae_marketdata.types import IST
from fastapi import APIRouter, Query, Response
from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import CurrentUser, UserSession
from ..errors import AppError
from ..schemas import (
    ERROR_RESPONSES,
    OpenPosition,
    Page,
    ReportDay,
    ReportSummary,
    StrategyPerformance,
    TradeRow,
)

router = APIRouter(tags=["reports"], responses=ERROR_RESPONSES)

From = Annotated[date | None, Query(alias="from", description="First day (IST); default 30 days ago")]
To = Annotated[date | None, Query(alias="to", description="Last day (IST); default today")]
Mode = Annotated[Literal["paper", "live"] | None, Query(description="Only paper or only live trades")]


def _range(from_: date | None, to: date | None) -> tuple[date, date]:
    today = datetime.now(IST).date()
    to = to or today
    from_ = from_ or to - timedelta(days=29)
    if from_ > to:
        raise AppError("'from' must not be after 'to'")
    if (to - from_).days > 3660:
        raise AppError("the range is limited to 10 years")
    return from_, to


Closed = Select[Trade, str, uuid.UUID]


def _closed(from_: date, to: date, mode: str | None, strategy_id: uuid.UUID | None) -> Closed:
    start = datetime.combine(from_, time(0), tzinfo=IST)
    end = datetime.combine(to + timedelta(days=1), time(0), tzinfo=IST)
    q = (
        select(Trade, StrategyRun.strategy_name, StrategyRun.strategy_id)
        .outerjoin(StrategyRun, StrategyRun.id == Trade.run_id)
        .where(Trade.status == "closed", Trade.exit_time >= start, Trade.exit_time < end)
    )
    if mode:
        q = q.where(Trade.mode == TradingMode(mode))
    if strategy_id:
        q = q.where(StrategyRun.strategy_id == strategy_id)
    return q


def _day(t: Trade) -> date:
    assert t.exit_time is not None
    return t.exit_time.astimezone(IST).date()


def _row(t: Trade, name: str | None) -> dict[str, Any]:
    return {
        "id": t.id,
        "run_id": t.run_id,
        "strategy_name": name or "Manual",
        "mode": t.mode.value,
        "underlying": t.underlying,
        "tradingsymbol": t.tradingsymbol,
        "expiry": t.expiry,
        "strike": float(t.strike),
        "option_type": t.option_type,
        "side": t.side.value,
        "quantity": t.quantity,
        "entry_time": t.entry_time,
        "entry_price": float(t.entry_price),
        "exit_time": t.exit_time,
        "exit_price": float(t.exit_avg_price) if t.exit_avg_price is not None else None,
        "exit_reason": t.exit_reason,
        "pnl": float(t.realized_pnl if t.status == "closed" else t.unrealized_pnl),
    }


def _report_day(d: Day | None) -> ReportDay | None:
    return None if d is None else ReportDay(day=d.day, pnl=d.pnl, trades=d.trades, cumulative=d.cumulative)


async def _trades(s: AsyncSession, q: Closed) -> list[ClosedTrade]:
    return [ClosedTrade(_day(t), float(t.realized_pnl)) for t, _, _ in (await s.execute(q)).all()]


@router.get("/v1/reports/summary", response_model=ReportSummary)
async def summary(
    user: CurrentUser, s: UserSession, from_: From = None, to: To = None, mode: Mode = None,
    strategy_id: uuid.UUID | None = None,
) -> ReportSummary:  # fmt: skip
    f, t = _range(from_, to)
    r = summarize(await _trades(s, _closed(f, t, mode, strategy_id)))
    return ReportSummary(
        from_date=f,
        to_date=t,
        total_pnl=r.total_pnl,
        trades=r.trades,
        wins=r.wins,
        losses=r.losses,
        win_rate=r.win_rate,
        avg_win=r.avg_win,
        avg_loss=r.avg_loss,
        profit_factor=r.profit_factor,
        max_drawdown=r.max_drawdown,
        best_day=_report_day(r.best_day),
        worst_day=_report_day(r.worst_day),
        trading_days=r.trading_days,
    )


@router.get("/v1/reports/daily", response_model=list[ReportDay])
async def daily_pnl(
    user: CurrentUser, s: UserSession, from_: From = None, to: To = None, mode: Mode = None,
    strategy_id: uuid.UUID | None = None,
) -> list[ReportDay]:  # fmt: skip
    f, t = _range(from_, to)
    return [ReportDay(day=d.day, pnl=d.pnl, trades=d.trades, cumulative=d.cumulative)
            for d in daily(await _trades(s, _closed(f, t, mode, strategy_id)))]  # fmt: skip


@router.get("/v1/reports/strategies", response_model=list[StrategyPerformance])
async def by_strategy(
    user: CurrentUser, s: UserSession, from_: From = None, to: To = None, mode: Mode = None
) -> list[StrategyPerformance]:
    f, t = _range(from_, to)
    groups: dict[uuid.UUID | None, dict[str, Any]] = {}
    for tr, name, sid in (await s.execute(_closed(f, t, mode, None))).all():
        g = groups.setdefault(sid, {"name": name or "Manual", "runs": set(), "pnls": []})
        g["runs"].add(tr.run_id)
        g["pnls"].append(float(tr.realized_pnl))
    out = [
        StrategyPerformance(
            strategy_id=sid,
            strategy_name=g["name"],
            runs=len(g["runs"]),
            trades=len(g["pnls"]),
            pnl=round(sum(g["pnls"]), 2),
            win_rate=round(sum(1 for p in g["pnls"] if p > 0) / len(g["pnls"]), 4),
        )
        for sid, g in groups.items()
    ]
    return sorted(out, key=lambda x: x.pnl, reverse=True)


def _trade_page(
    from_: date, to: date, mode: str | None, strategy_id: uuid.UUID | None, cursor: str | None, n: int
) -> Closed:
    q = _closed(from_, to, mode, strategy_id).order_by(Trade.exit_time.desc(), Trade.id.desc()).limit(n + 1)
    if cursor:
        try:
            ts, id_ = decode_cursor(cursor)
        except ValueError as exc:
            raise AppError(str(exc)) from exc
        q = q.where(or_(Trade.exit_time < ts, and_(Trade.exit_time == ts, Trade.id < id_)))
    return q


@router.get("/v1/reports/trades", response_model=Page[TradeRow])
async def trades(
    user: CurrentUser, s: UserSession, from_: From = None, to: To = None, mode: Mode = None,
    strategy_id: uuid.UUID | None = None, cursor: str | None = None, limit: int = Query(50, ge=1, le=100),
) -> Page[TradeRow]:  # fmt: skip
    f, t = _range(from_, to)
    n = clamp_limit(limit)
    rows = (await s.execute(_trade_page(f, t, mode, strategy_id, cursor, n))).all()
    more = len(rows) > n
    rows = rows[:n]
    last = rows[-1][0] if rows else None
    return Page[TradeRow](
        items=[TradeRow(**_row(tr, name)) for tr, name, _ in rows],
        next_cursor=encode_cursor(last.exit_time, last.id) if more and last and last.exit_time else None,
    )


CSV_COLUMNS = ["exit_time", "strategy_name", "mode", "tradingsymbol", "side", "quantity", "entry_time",
               "entry_price", "exit_price", "exit_reason", "pnl"]  # fmt: skip


@router.get("/v1/reports/trades.csv", response_class=Response, responses={200: {"content": {"text/csv": {}}}})
async def trades_csv(
    user: CurrentUser, s: UserSession, from_: From = None, to: To = None, mode: Mode = None,
    strategy_id: uuid.UUID | None = None,
) -> Response:  # fmt: skip
    """Every closed trade in the range (oldest first), for spreadsheets and tax records."""
    f, t = _range(from_, to)
    q = _closed(f, t, mode, strategy_id).order_by(Trade.exit_time, Trade.id).limit(50_000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for tr, name, _ in (await s.execute(q)).all():
        r = _row(tr, name)
        for k in ("entry_time", "exit_time"):
            r[k] = r[k].astimezone(IST).strftime("%Y-%m-%d %H:%M:%S") if r[k] else ""
        # a leading =, +, - or @ would run as a formula in a spreadsheet
        w.writerow(["'" + v if isinstance(v, str) and v[:1] in "=+-@" else v for v in (r[c] for c in CSV_COLUMNS)])
    name = f"algoearning-trades-{f:%Y%m%d}-{t:%Y%m%d}.csv"
    return Response(
        buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


@router.get("/v1/positions/open", response_model=list[OpenPosition])
async def open_positions(user: CurrentUser, s: UserSession) -> list[OpenPosition]:
    q = (
        select(Trade, StrategyRun.strategy_name)
        .outerjoin(StrategyRun, StrategyRun.id == Trade.run_id)
        .where(Trade.status == "open")
        .order_by(Trade.entry_time.desc())
        .limit(200)
    )
    return [
        OpenPosition(
            **_row(t, name),
            last_ltp=float(t.last_ltp) if t.last_ltp is not None else None,
            current_sl=float(t.current_sl) if t.current_sl is not None else None,
            target=float(t.target) if t.target is not None else None,
        )
        for t, name in (await s.execute(q)).all()
    ]
