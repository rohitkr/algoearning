"""Backtests: a user replays one of their strategies over stored history; the worker runs it (ADR 0017)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from ae_core.strategy import check, parse
from ae_db.models import BacktestRun
from ae_db.repositories import StrategyRepo
from ae_marketdata.history import coverage
from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import select

from ..audit import audit
from ..deps import CurrentUser, DbDep, UserSession
from ..entitlements import load_entitlements, require_feature
from ..errors import AppError, Conflict, NotFound
from ..schemas import ERROR_RESPONSES, BacktestDetail, BacktestIn, BacktestOut, HistoryCoverage
from ..settings import SettingsDep
from .strategies import _instruments

router = APIRouter(tags=["backtests"], responses=ERROR_RESPONSES)
MAX_DAYS = 800
MAX_PENDING = 3


def _out(r: BacktestRun) -> BacktestOut:
    summary = (r.result or {}).get("summary", {})
    return BacktestOut(
        id=r.id,
        strategy_id=r.strategy_id,
        strategy_name=r.strategy_name,
        kind=r.kind,
        underlying=str(r.config_snapshot.get("underlying", "")),
        start_date=r.start_date,
        end_date=r.end_date,
        multiplier=r.multiplier,
        slippage_pct=r.slippage_pct,
        status=r.status,  # type: ignore[arg-type]
        error=r.error,
        created_at=r.created_at,
        finished_at=r.finished_at,
        net_pnl=summary.get("net_pnl"),
        trades=summary.get("trades"),
    )


@router.get("/v1/backtests/coverage", response_model=list[HistoryCoverage])
async def history_coverage(_: CurrentUser, db: DbDep) -> list[HistoryCoverage]:
    """What history exists (per underlying), so the range picker and the results can be honest about it."""
    return [HistoryCoverage(**c.__dict__) for c in await coverage(db)]


@router.post("/v1/backtests", response_model=BacktestOut, status_code=status.HTTP_201_CREATED)
async def create_backtest(
    body: BacktestIn, user: CurrentUser, s: UserSession, request: Request, settings: SettingsDep
) -> BacktestOut:
    if body.start_date > body.end_date:
        raise AppError("the start date must not be after the end date")
    if (body.end_date - body.start_date).days > MAX_DAYS:
        raise AppError(f"a backtest covers at most {MAX_DAYS} days")
    ent = await load_entitlements(s, user.user_id, timedelta(days=settings.subscription_grace_days))
    require_feature(ent, "backtesting")
    strat = await StrategyRepo(s, user.user_id).get(body.strategy_id)
    if strat is None:
        raise NotFound("strategy not found")
    config = parse(strat.config)
    issues = check(config, await _instruments(s))
    if issues:
        raise Conflict(f"the strategy does not pass its checks: {issues[0].msg}", {"reason": "invalid"})
    waiting = (await s.execute(select(BacktestRun.id).where(BacktestRun.status.in_(("pending", "running"))))).all()
    if len(waiting) >= MAX_PENDING:
        raise Conflict("you already have backtests running: wait for one to finish", {"reason": "busy"})
    run = BacktestRun(
        user_id=user.user_id,
        strategy_id=strat.id,
        strategy_name=strat.name,
        kind=strat.kind,
        config_snapshot=dict(strat.config),
        schema_version=strat.schema_version,
        start_date=body.start_date,
        end_date=body.end_date,
        multiplier=body.multiplier,
        slippage_pct=body.slippage_pct,
    )
    s.add(run)
    await s.flush()
    await s.refresh(run)
    await audit(s, request, "backtest.create", user.user_id, "backtest_run", run.id, strategy=str(strat.id))
    return _out(run)


@router.get("/v1/backtests", response_model=list[BacktestOut])
async def list_backtests(user: CurrentUser, s: UserSession, limit: int = Query(30, ge=1, le=100)) -> list[BacktestOut]:
    q = select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(limit)
    return [_out(r) for r in (await s.execute(q)).scalars()]


async def _own(s: UserSession, backtest_id: uuid.UUID) -> BacktestRun:
    run = (await s.execute(select(BacktestRun).where(BacktestRun.id == backtest_id))).scalar_one_or_none()
    if run is None:  # another user's backtest looks exactly like a missing one
        raise NotFound("backtest not found")
    return run


@router.get("/v1/backtests/{backtest_id}", response_model=BacktestDetail)
async def get_backtest(backtest_id: uuid.UUID, user: CurrentUser, s: UserSession) -> BacktestDetail:
    run = await _own(s, backtest_id)
    return BacktestDetail(**_out(run).model_dump(), result=run.result)


@router.delete("/v1/backtests/{backtest_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_backtest(backtest_id: uuid.UUID, user: CurrentUser, s: UserSession) -> Response:
    run = await _own(s, backtest_id)
    await s.delete(run)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
