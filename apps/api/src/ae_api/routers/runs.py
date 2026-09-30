"""Deploying strategies and following their runs (the engine executes them, ADR 0014), and the user's own risk
settings. Always scoped to the signed-in user (row-level security applies)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from ae_core.strategy import AnyConfig, TimeBasedConfig, check, parse
from ae_db.enums import BrokerAccountStatus, RunStatus, StrategyStatus, TradingMode
from ae_db.models import (
    BrokerAccount,
    BrokerSession,
    Order,
    StrategyRun,
    Trade,
    TradeEvent,
    UserOverride,
    UserRiskSettings,
)
from ae_db.repositories import BrokerAccountRepo, StrategyRepo
from fastapi import APIRouter, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import audit
from ..deps import CurrentUser, UserSession
from ..entitlements import load_entitlements, require_feature, require_within
from ..errors import Conflict, NotFound, PlanLimitReached
from ..schemas import (
    ERROR_RESPONSES,
    DeployIn,
    LiveBroker,
    LiveStatus,
    OrderOut,
    PositionOut,
    PreflightCheck,
    PreflightOut,
    RiskSettingsIO,
    RunDetail,
    RunEvent,
    RunOut,
)
from ..settings import SettingsDep
from .strategies import _instruments

router = APIRouter(tags=["runs"], responses=ERROR_RESPONSES)
ACTIVE = (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.STOPPING)


def _f(v: object) -> float | None:
    return None if v is None else float(v)  # type: ignore[arg-type]


async def _open_counts(s: AsyncSession, run_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not run_ids:
        return {}
    q = (
        select(Trade.run_id, func.count())
        .where(Trade.run_id.in_(run_ids), Trade.status == "open")
        .group_by(Trade.run_id)
    )
    return {rid: int(n) for rid, n in (await s.execute(q)).all() if rid is not None}


def run_out(r: StrategyRun, open_positions: int = 0) -> RunOut:
    return RunOut(
        dry_run=r.dry_run,
        id=r.id,
        strategy_id=r.strategy_id,
        strategy_name=r.strategy_name,
        kind=r.kind,
        underlying=str(r.config_snapshot.get("underlying", "")),
        mode=r.mode.value,
        status=r.status.value,
        multiplier=r.multiplier,
        broker_account_id=r.broker_account_id,
        realized_pnl=float(r.realized_pnl),
        unrealized_pnl=float(r.unrealized_pnl),
        open_positions=open_positions,
        created_at=r.created_at,
        started_at=r.started_at,
        stopped_at=r.stopped_at,
        heartbeat_at=r.heartbeat_at,
        engine_stale=r.status == RunStatus.RUNNING
        and (r.heartbeat_at is None or datetime.now(UTC) - r.heartbeat_at > timedelta(seconds=30)),
        stop_reason=r.stop_reason,
        error=r.error,
    )


def _max_lots(config: AnyConfig) -> int:
    if isinstance(config, TimeBasedConfig):
        return max(leg.lots for leg in config.legs)
    return config.lots


@router.post("/v1/strategies/{strategy_id}/deploy", response_model=RunOut, status_code=status.HTTP_201_CREATED)
async def deploy(
    strategy_id: uuid.UUID, body: DeployIn, user: CurrentUser, s: UserSession, request: Request, settings: SettingsDep
) -> RunOut:
    strat = await StrategyRepo(s, user.user_id).get(strategy_id)
    if strat is None:
        raise NotFound("strategy not found")
    if strat.status != StrategyStatus.READY:
        raise Conflict("mark the strategy ready before deploying it", {"reason": "not_ready"})
    config = parse(strat.config)
    issues = check(config, await _instruments(s))
    if issues:
        raise Conflict(f"the strategy no longer passes its checks: {issues[0].msg}", {"reason": "invalid"})
    active = (
        await s.execute(
            select(StrategyRun.id).where(StrategyRun.strategy_id == strat.id, StrategyRun.status.in_(ACTIVE))
        )
    ).first()
    if active:
        raise Conflict("this strategy is already running: stop it first", {"reason": "already_running"})
    ent = await load_entitlements(s, user.user_id, timedelta(days=settings.subscription_grace_days))
    if body.mode == "paper":
        require_feature(ent, "paper_trading")
    else:
        require_feature(ent, "live_trading")
        if not body.dry_run:
            status = await live_status(user, s, settings)
            if not status.unlocked:
                raise Conflict("real orders are not unlocked for your account yet", {"reason": "live_locked"})
            broker = next((b for b in status.brokers if b.id == body.broker_account_id), None)
            if broker is None:
                raise Conflict("choose the broker account to trade on", {"reason": "no_broker"})
            if not broker.connected:
                raise Conflict("log in to your broker for today first", {"reason": "broker_not_connected"})
            if not broker.engine_enabled:
                raise Conflict("switch on the Trading Engine for this broker account", {"reason": "engine_off"})
            if (body.confirm or "").strip() != strat.name.strip():
                raise Conflict("type the strategy's name to confirm real orders", {"reason": "confirm"})
    require_within(ent, "max_running_strategies", ent.usage["max_running_strategies"])
    lots, cap = _max_lots(config) * body.multiplier, ent.limit("max_lots_per_order")
    if cap is not None and lots > cap:
        raise PlanLimitReached(
            f"{lots} lots per order is above your {ent.plan_name} plan's {cap}",
            {"feature": "max_lots_per_order", "limit": cap, "used": lots, "plan": ent.plan_code},
        )
    if (
        body.broker_account_id is not None
        and await BrokerAccountRepo(s, user.user_id).get(body.broker_account_id) is None
    ):
        raise NotFound("broker account not found")
    run = StrategyRun(
        user_id=user.user_id,
        strategy_id=strat.id,
        broker_account_id=body.broker_account_id,
        mode=TradingMode(body.mode),
        status=RunStatus.PENDING,
        config_snapshot=dict(strat.config),
        strategy_name=strat.name,
        kind=strat.kind,
        schema_version=strat.schema_version,
        multiplier=body.multiplier,
        dry_run=body.mode == "live" and body.dry_run,
    )
    s.add(run)
    await s.flush()
    await s.refresh(run)
    await audit(s, request, "run.deploy", user.user_id, "strategy_run", run.id, strategy=str(strat.id), mode=body.mode,
                multiplier=body.multiplier, dry_run=run.dry_run)  # fmt: skip
    return run_out(run)


@router.get("/v1/runs", response_model=list[RunOut])
async def list_runs(
    user: CurrentUser, s: UserSession, active: bool = True, limit: int = Query(50, ge=1, le=200)
) -> list[RunOut]:
    q = select(StrategyRun).order_by(StrategyRun.created_at.desc()).limit(limit)
    q = q.where(StrategyRun.status.in_(ACTIVE) if active else StrategyRun.status.not_in(ACTIVE))
    runs = list((await s.execute(q)).scalars())
    counts = await _open_counts(s, [r.id for r in runs])
    return [run_out(r, counts.get(r.id, 0)) for r in runs]


async def _own_run(s: AsyncSession, run_id: uuid.UUID) -> StrategyRun:
    run = (await s.execute(select(StrategyRun).where(StrategyRun.id == run_id))).scalar_one_or_none()
    if run is None:  # RLS: another user's run looks exactly like a missing one
        raise NotFound("run not found")
    return run


@router.get("/v1/runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: uuid.UUID, user: CurrentUser, s: UserSession) -> RunDetail:
    run = await _own_run(s, run_id)
    trades = list(
        (
            await s.execute(select(Trade).where(Trade.run_id == run.id).order_by(Trade.entry_time.desc()).limit(200))
        ).scalars()
    )
    orders = list(
        (
            await s.execute(
                select(Order)
                .where(Order.trade_id.in_([t.id for t in trades]))
                .order_by(Order.created_at.desc())
                .limit(200)
            )
        ).scalars()
    )
    eq = select(TradeEvent).where(TradeEvent.run_id == run.id).order_by(TradeEvent.id.desc()).limit(200)
    events: list[TradeEvent] = list((await s.execute(eq)).scalars())
    return RunDetail(
        run=run_out(run, len([t for t in trades if t.status == "open"])),
        positions=[
            PositionOut(
                id=t.id,
                leg=(t.rules or {}).get("leg"),
                tradingsymbol=t.tradingsymbol,
                underlying=t.underlying,
                expiry=t.expiry,
                strike=float(t.strike),
                option_type=t.option_type,
                side=t.side.value,
                lots=t.lots,
                quantity=t.quantity,
                status=t.status,
                entry_price=float(t.entry_price),
                entry_time=t.entry_time,
                last_ltp=_f(t.last_ltp),
                current_sl=_f(t.current_sl),
                target=_f(t.target),
                exit_price=_f(t.exit_avg_price),
                exit_time=t.exit_time,
                exit_reason=t.exit_reason,
                pnl=float(t.realized_pnl if t.status == "closed" else t.unrealized_pnl),
            )
            for t in trades
        ],
        orders=[
            OrderOut(
                id=o.id,
                trade_id=o.trade_id,
                kind=o.kind.value,
                side=o.side.value,
                quantity=o.quantity,
                avg_price=_f(o.avg_price),
                status=o.status,
                created_at=o.created_at,
            )
            for o in orders
        ],
        events=[RunEvent(id=e.id, ts=e.ts, event=e.event, level=e.level, detail=e.detail) for e in events],
    )


async def _stop(s: AsyncSession, run: StrategyRun, reason: str) -> None:
    if run.status == RunStatus.PENDING:  # never started: nothing to square off
        run.status, run.stopped_at, run.stop_reason = RunStatus.STOPPED, datetime.now(UTC), reason
    elif run.status == RunStatus.RUNNING:
        run.status, run.stop_reason = RunStatus.STOPPING, reason
    await s.flush()


@router.post("/v1/runs/{run_id}/stop", response_model=RunOut)
async def stop_run(run_id: uuid.UUID, user: CurrentUser, s: UserSession, request: Request) -> RunOut:
    """Square off the run's open positions and stop it (the engine does it within a second or two)."""
    run = await _own_run(s, run_id)
    if run.status not in ACTIVE:
        raise Conflict("the run has already stopped")
    await _stop(s, run, "stopped by you")
    await audit(s, request, "run.stop", user.user_id, "strategy_run", run.id)
    return run_out(run, (await _open_counts(s, [run.id])).get(run.id, 0))


@router.post("/v1/runs/stop-all", response_model=list[RunOut])
async def stop_all(user: CurrentUser, s: UserSession, request: Request) -> list[RunOut]:
    runs = list((await s.execute(select(StrategyRun).where(StrategyRun.status.in_(ACTIVE)))).scalars())
    for r in runs:
        await _stop(s, r, "you stopped all runs")
    await audit(s, request, "run.stop_all", user.user_id, "strategy_run", None, runs=len(runs))
    return [run_out(r) for r in runs]


# -- risk settings -----------------------------------------------------------------------------------------------
@router.get("/v1/me/risk", response_model=RiskSettingsIO)
async def get_risk(user: CurrentUser, s: UserSession) -> RiskSettingsIO:
    rs = (await s.execute(select(UserRiskSettings))).scalar_one_or_none()
    if rs is None:
        return RiskSettingsIO()
    return RiskSettingsIO(
        max_daily_loss=_f(rs.max_daily_loss),
        max_daily_profit=_f(rs.max_daily_profit),
        max_open_positions=rs.max_open_positions,
        max_trades_per_day=rs.max_trades_per_day,
        kill_switch=rs.kill_switch,
    )


@router.put("/v1/me/risk", response_model=RiskSettingsIO)
async def put_risk(body: RiskSettingsIO, user: CurrentUser, s: UserSession, request: Request) -> RiskSettingsIO:
    rs = (await s.execute(select(UserRiskSettings))).scalar_one_or_none()
    if rs is None:
        rs = UserRiskSettings(user_id=user.user_id)
        s.add(rs)
    for k, v in body.model_dump().items():
        setattr(rs, k, v)
    await s.flush()
    await audit(s, request, "risk.update", user.user_id, "user_risk_settings", user.user_id, **body.model_dump())
    return body


@router.get("/v1/me/live", response_model=LiveStatus)
async def live_status(user: CurrentUser, s: UserSession, settings: SettingsDep) -> LiveStatus:
    ent = await load_entitlements(s, user.user_id, timedelta(days=settings.subscription_grace_days))
    unlocked = bool((await s.execute(select(UserOverride.live_unlocked))).scalar_one_or_none())
    now = datetime.now(UTC)
    sessions = {
        b: e for b, e in (await s.execute(select(BrokerSession.broker_account_id, BrokerSession.expires_at))).all()
    }
    brokers = [
        LiveBroker(
            id=a.id,
            client_id=a.client_id,
            label=a.label,
            connected=a.status == BrokerAccountStatus.CONNECTED and sessions.get(a.id, now) > now,
            engine_enabled=a.engine_enabled,
        )
        for a in (await s.execute(select(BrokerAccount).order_by(BrokerAccount.created_at))).scalars()
    ]
    plan = ent.allows("live_trading")
    reasons = []
    if not plan:
        reasons.append(f"your {ent.plan_name} plan does not include live trading")
    if not unlocked:
        reasons.append(
            "real orders are not unlocked for your account (an admin turns this on: "
            "Monitor > Users > your user > Real orders)"
        )
    if not brokers:
        reasons.append("add your broker account")
    elif not any(b.connected and b.engine_enabled for b in brokers):
        reasons.append("log in to your broker today and switch on its Trading Engine")
    return LiveStatus(
        plan_allows=plan,
        unlocked=unlocked,
        brokers=brokers,
        can_dry_run=plan,
        can_go_live=plan and unlocked and any(b.connected and b.engine_enabled for b in brokers),
        reasons=reasons,
    )


@router.post("/v1/me/live/preflight", response_model=PreflightOut)
async def preflight(broker_account_id: uuid.UUID, user: CurrentUser, s: UserSession, request: Request) -> PreflightOut:
    """Before real orders: can we reach the broker with today's session, read the funds, and are prices and the engine
    alive? Nothing is ordered."""
    from ae_brokers.base import BrokerError
    from ae_brokers.kite import KiteClient
    from ae_marketdata.hub import Hub

    checks: list[PreflightCheck] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append(PreflightCheck(name=name, ok=ok, detail=detail))

    acc = await BrokerAccountRepo(s, user.user_id).get(broker_account_id)
    if acc is None:
        raise NotFound("broker account not found")
    sess = (
        await s.execute(select(BrokerSession).where(BrokerSession.broker_account_id == acc.id))
    ).scalar_one_or_none()
    box = getattr(request.app.state, "secretbox", None)
    if sess is None or sess.expires_at <= datetime.now(UTC) or box is None or acc.api_key_enc is None:
        add("Zerodha session", False, "log in to Zerodha on the Broker page today")
    else:
        client = KiteClient(
            box.decrypt(acc.api_key_enc, f"broker_account:{acc.id}:api_key"),
            box.decrypt(sess.access_token_enc, f"broker_session:{acc.id}:access_token"),
        )
        try:
            funds = await client.available_margin()
            add("Zerodha session", True, "logged in")
            add("Funds", funds > 0, f"₹{funds:,.0f} available")
        except BrokerError as exc:
            add("Zerodha session", False, exc.message)
        finally:
            await client.aclose()
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        add("Price feed", False, "Redis is not available")
        add("Engine", False, "Redis is not available")
    else:
        tick = (await Hub(redis).last(["NIFTY"])).get("NIFTY")
        fresh = tick is not None and datetime.now(UTC) - tick.ts < timedelta(minutes=2)
        health = await Hub(redis).health()
        add("Price feed", fresh and not health.get("simulated"),
            "live prices" if fresh and not health.get("simulated") else "simulated prices: log in to Breeze in Monitor"
            if fresh else "no prices in the last 2 minutes")  # fmt: skip
        add(
            "Engine",
            bool(await redis.get("engine:leader")),
            "running" if await redis.get("engine:leader") else "not running: start make dev",
        )
    return PreflightOut(ok=all(c.ok for c in checks), checks=checks)
