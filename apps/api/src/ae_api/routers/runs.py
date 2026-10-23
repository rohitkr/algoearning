"""Deploying strategies and following their runs (the engine executes them, ADR 0014), and the user's own risk
settings. Always scoped to the signed-in user (row-level security applies)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from ae_core.strategy import (
    AnyConfig,
    check,
    max_order_lots,
    parse,
    split_by_instrument,
    tip_source_ids,
    uses_tips,
)
from ae_core.trading.model import IST
from ae_db.enums import BrokerAccountStatus, RunStatus, StrategyStatus, TradingMode
from ae_db.models import (
    BacktestRun,
    BrokerAccount,
    BrokerSession,
    DataSyncJob,
    Instrument,
    Order,
    SignalRow,
    SignalSource,
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
from ..deps import CurrentUser, DbDep, UserSession
from ..entitlements import load_entitlements, require_feature, require_within
from ..errors import Conflict, NotFound, PlanLimitReached, Unavailable
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
        paused=r.paused,
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
        prices_at=datetime.fromisoformat(str(r.state["_prices_at"])) if r.state.get("_prices_at") else None,
        prices_stale=bool(r.state.get("_prices_stale")) and r.status == RunStatus.RUNNING,
        stop_reason=r.stop_reason,
        error=r.error,
    )


def _max_lots(config: AnyConfig) -> int:
    return max_order_lots(config)


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
    if uses_tips(config):
        if body.mode == "live":
            require_feature(ent, "signal_trading")
        for source_id in tip_source_ids(config):
            src = (await s.execute(select(SignalSource).where(SignalSource.id == source_id))).scalar_one_or_none()
            if src is None or src.status != "connected" or src.chat_id is None:
                raise Conflict("connect the Telegram signal source and pick its chat first", {"reason": "no_source"})
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
    # a run trades one instrument (one market feed): a strategy of plans on several instruments deploys as one run per
    # instrument, all of them started and stopped together from the page, each holding every plan of its instrument
    # (a Telegram tip traded as given likewise runs once per instrument it takes tips for)
    parts: list[tuple[str, dict[str, Any]]] = [(strat.name, dict(strat.config))]
    split = split_by_instrument(config)
    if len(split) > 1:
        parts = [(f"{strat.name} · {u}", c.model_dump(mode="json")) for u, c in split]
    runs: list[StrategyRun] = []
    for n, (name, snapshot) in enumerate(parts):
        require_within(ent, "max_running_strategies", ent.usage["max_running_strategies"] + n)
        run = StrategyRun(
            user_id=user.user_id,
            strategy_id=strat.id,
            broker_account_id=body.broker_account_id,
            mode=TradingMode(body.mode),
            status=RunStatus.PENDING,
            config_snapshot=snapshot,
            strategy_name=name,
            kind=strat.kind,
            schema_version=strat.schema_version,
            multiplier=body.multiplier,
            dry_run=body.mode == "live" and body.dry_run,
        )
        s.add(run)
        runs.append(run)
    await s.flush()
    for run in runs:
        await s.refresh(run)
        await audit(s, request, "run.deploy", user.user_id, "strategy_run", run.id, strategy=str(strat.id),
                    mode=body.mode, multiplier=body.multiplier, dry_run=run.dry_run)  # fmt: skip
    return run_out(runs[0])


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
            await s.execute(
                select(Trade)
                .where(Trade.run_id == run.id)
                .order_by(Trade.entry_time.desc(), Trade.tradingsymbol, Trade.id)
                .limit(200)
            )
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
    # legs entered together keep one order on every refresh: newest trade first, then by leg (L1, L2, ...)
    trades.sort(key=lambda t: (str((t.rules or {}).get("leg") or ""), t.tradingsymbol))
    trades.sort(key=lambda t: t.entry_time or datetime.min.replace(tzinfo=UTC), reverse=True)
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


@router.post("/v1/runs/{run_id}/pause", response_model=RunOut)
async def pause_run(run_id: uuid.UUID, user: CurrentUser, s: UserSession, request: Request) -> RunOut:
    """Pause: the strategy takes no new trade until resumed. Open positions keep their stop-losses, targets, limits and
    exit time (Stop squares them off)."""
    return await _set_paused(run_id, True, user, s, request)


@router.post("/v1/runs/{run_id}/resume", response_model=RunOut)
async def resume_run(run_id: uuid.UUID, user: CurrentUser, s: UserSession, request: Request) -> RunOut:
    return await _set_paused(run_id, False, user, s, request)


async def _set_paused(run_id: uuid.UUID, paused: bool, user: Any, s: AsyncSession, request: Request) -> RunOut:
    run = await _own_run(s, run_id)
    if run.status not in (RunStatus.PENDING, RunStatus.RUNNING):
        raise Conflict("the strategy is not running")
    run.paused = paused
    await s.flush()
    await audit(s, request, "run.pause" if paused else "run.resume", user.user_id, "strategy_run", run.id)
    return run_out(run, (await _open_counts(s, [run.id])).get(run.id, 0))


EXIT_NOW_KEY = "ae:exit_now:{}"  # the engine's EXIT_KEY


@router.post("/v1/runs/{run_id}/exit-now", response_model=RunOut)
async def exit_now(run_id: uuid.UUID, user: CurrentUser, s: UserSession, request: Request) -> RunOut:
    """Square off the run's open positions now, keeping the run running (Stop also stops it): the engine sends the
    exits on its next step, while the exchange is open. Live runs place real orders."""
    run = await _own_run(s, run_id)
    if run.status != RunStatus.RUNNING:
        raise Conflict("the strategy is not running")
    if not (await _open_counts(s, [run.id])).get(run.id, 0):
        raise Conflict("nothing is open", {"reason": "nothing_open"})
    inst = (
        await s.execute(select(Instrument).where(Instrument.code == run.config_snapshot.get("underlying", "NIFTY")))
    ).scalar_one_or_none()
    now = datetime.now(IST)
    if inst is None or now.weekday() >= 5 or not (inst.session_open <= f"{now:%H:%M}" < inst.session_close):
        raise Conflict("the market is closed: Exit now works while the exchange is open", {"reason": "closed"})
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise Unavailable("the trading engine cannot be reached")
    await redis.set(EXIT_NOW_KEY.format(run.id), str(user.user_id), ex=120)
    await audit(s, request, "run.exit_now", user.user_id, "strategy_run", run.id)
    return run_out(run, (await _open_counts(s, [run.id])).get(run.id, 0))


ENTER_NOW_KEY = "ae:enter_now:{}"  # the engine's FORCE_KEY: it takes the request on its next step


@router.post("/v1/runs/{run_id}/enter-now", response_model=RunOut)
async def enter_now(run_id: uuid.UUID, user: CurrentUser, s: UserSession, request: Request) -> RunOut:
    """Start today's trade now, outside the entry window (e.g. the entry time was missed): the engine enters it on its
    next step with the usual legs, lots and risk checks, while the exchange is open. Live runs place real orders."""
    run = await _own_run(s, run_id)
    if run.status != RunStatus.RUNNING:
        raise Conflict("the strategy is not running")
    inst = (
        await s.execute(select(Instrument).where(Instrument.code == run.config_snapshot.get("underlying", "NIFTY")))
    ).scalar_one_or_none()
    now = datetime.now(IST)
    if inst is None or now.weekday() >= 5 or not (inst.session_open <= f"{now:%H:%M}" < inst.session_close):
        raise Conflict("the market is closed: Enter now works while the exchange is open", {"reason": "closed"})
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise Unavailable("the trading engine cannot be reached")
    await redis.set(ENTER_NOW_KEY.format(run.id), str(user.user_id), ex=120)
    await audit(s, request, "run.enter_now", user.user_id, "strategy_run", run.id)
    return run_out(run, (await _open_counts(s, [run.id])).get(run.id, 0))


@router.post("/v1/runs/stop-all", response_model=list[RunOut])
async def stop_all(user: CurrentUser, s: UserSession, request: Request) -> list[RunOut]:
    runs = list((await s.execute(select(StrategyRun).where(StrategyRun.status.in_(ACTIVE)))).scalars())
    for r in runs:
        await _stop(s, r, "you stopped all runs")
    await audit(s, request, "run.stop_all", user.user_id, "strategy_run", None, runs=len(runs))
    return [run_out(r) for r in runs]


# -- risk settings -----------------------------------------------------------------------------------------------
@router.get("/v1/me/activity")
async def recent_activity(
    user: CurrentUser, s: UserSession, db: DbDep, since: datetime | None = None
) -> dict[str, Any]:
    """What happened since `since` (for the web app's sound alerts): Telegram tips received and trades executed
    (entries and exits) of the user's strategies, oldest first. Without `since`: nothing, only the server's time to
    start from."""
    now = datetime.now(UTC)
    if since is None:
        return {"now": now.isoformat(), "signals": [], "trades": [], "done": []}
    # a tab left open for days does not ring for all of it; and look back 2 minutes: an event is stamped when the
    # engine's transaction starts but only visible once it commits, so it can be older than the last poll (the page
    # drops the ones it has already played, by id)
    since = max(since, now - timedelta(hours=1)) - timedelta(minutes=2)
    sigs = (
        await s.execute(
            select(SignalRow).where(SignalRow.date > since).order_by(SignalRow.date)
        )
    ).scalars().all()  # fmt: skip
    evs = (
        await s.execute(
            select(TradeEvent, StrategyRun.strategy_name)
            .join(StrategyRun, StrategyRun.id == TradeEvent.run_id)
            .where(TradeEvent.ts > since, TradeEvent.event.in_(("entry", "exit")))
            .order_by(TradeEvent.ts)
            .limit(50)
        )
    ).all()  # fmt: skip
    backtests = (
        await s.execute(
            select(BacktestRun).where(BacktestRun.finished_at > since, BacktestRun.status.in_(("done", "error")))
        )
    ).scalars().all()  # fmt: skip
    async with db.system_session() as sys:  # history downloads are platform jobs: the ones this user asked for
        syncs = (
            await sys.execute(
                select(DataSyncJob).where(DataSyncJob.requested_by == user.user_id, DataSyncJob.finished_at > since)
            )
        ).scalars().all()  # fmt: skip
    done = [
        {"id": f"bt:{b.id}", "kind": "backtest", "name": b.strategy_name, "ok": b.status == "done",
         "ts": (b.finished_at or now).isoformat()}
        for b in backtests
    ] + [
        {"id": f"sync:{j.id}", "kind": "data_sync", "name": j.underlying, "ok": j.status in ("done", "stopped"),
         "ts": (j.finished_at or now).isoformat()}
        for j in syncs
    ]  # fmt: skip
    return {
        "now": now.isoformat(),
        "done": done,
        "signals": [
            {
                "id": f"{r.source_id}:{r.header_msg_id}",
                "tip": f"{r.action} {r.index} {r.strike} {r.option_type}",
                "date": r.date.isoformat(),
            }
            for r in sigs
        ],
        "trades": [
            {
                "id": e.id,
                "event": e.event,
                "strategy": name,
                "contract": e.detail.get("contract"),
                "side": e.detail.get("side"),
                "ts": e.ts.isoformat(),
            }
            for e, name in evs
        ],
    }


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
        live = fresh and not health.get("simulated")
        add("Price feed", live, "live prices" if live else "simulated prices: log in to the price feed in Monitor"
            if fresh else "no prices in the last 2 minutes")  # fmt: skip
        add(
            "Engine",
            bool(await redis.get("engine:leader")),
            "running" if await redis.get("engine:leader") else "not running: start make dev",
        )
    return PreflightOut(ok=all(c.ok for c in checks), checks=checks)
