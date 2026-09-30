"""The multi-user engine: every second, every active run of every user takes one step (ADR 0014).

For each run: build its market view from the price feed (Redis), let its runner decide, check each entry against
the user's risk settings and plan, fill on the paper exchange, and record positions (trades), fills (orders) and
decisions (trade_events). Runs are isolated: one run's error stops that run, never the others. The runner's state
is saved on the run after every step, so a restart continues where it left off.

Live orders are not wired yet: a live run is refused until broker execution ships (ADR 0014)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from ae_core.strategy import migrate, parse
from ae_core.trading.model import IST, Intent, Market, Position
from ae_core.trading.risk import RiskContext, RiskSettings, breach, check_entry
from ae_core.trading.runners import Runner, make_runner
from ae_db.entitlements import load_entitlements
from ae_db.enums import OrderKind, RunStatus, Side, TradingMode
from ae_db.models import Instrument, Order, PlatformSetting, StrategyRun, Trade, TradeEvent, UserRiskSettings
from ae_db.session import Database
from ae_marketdata.hub import Hub
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .paper import PaperExchange

log = structlog.get_logger("ae_engine")
ACTIVE = (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.STOPPING)
STALE = timedelta(minutes=2)
HALT_KEY = "trading_halted"


@dataclass
class Inst:
    code: str
    exchange: str
    lot_size: int
    strike_step: int
    expiries: list[date]


def _symbol(p: Position) -> str:
    c = p.contract
    return f"{c.underlying}{c.expiry:%y%b%d}{c.strike}{c.right}".upper()


def _kind(intent: Intent) -> OrderKind:
    if intent.kind == "entry":
        return OrderKind.ENTRY
    return {"stop-loss": OrderKind.SL, "target": OrderKind.TARGET}.get(intent.reason, OrderKind.EXIT)


def _d(v: float | None) -> Decimal | None:
    return None if v is None else Decimal(str(round(v, 2)))


class Engine:
    def __init__(
        self,
        db: Database,
        hub: Hub,
        now: Callable[[], datetime] | None = None,
        exchange: PaperExchange | None = None,
        grace: timedelta = timedelta(days=3),
    ) -> None:
        self.db, self.hub = db, hub
        self.now = now or (lambda: datetime.now(IST))
        self.exchange = exchange or PaperExchange()
        self.grace = grace
        self.runners: dict[uuid.UUID, Runner] = {}

    # -- one pass --------------------------------------------------------------------------------------------------
    async def tick(self) -> int:
        now = self.now()
        async with self.db.system_session() as s:
            runs = list((await s.execute(select(StrategyRun).where(StrategyRun.status.in_(ACTIVE)))).scalars())
            insts = {
                r.code: Inst(r.code, r.exchange, r.lot_size, r.strike_step, sorted(map(date.fromisoformat, r.expiries)))
                for r in (await s.execute(select(Instrument))).scalars()
            }
            halted = bool(
                (
                    await s.execute(select(PlatformSetting.value).where(PlatformSetting.key == HALT_KEY))
                ).scalar_one_or_none()
            )
        for rid in set(self.runners) - {r.id for r in runs}:
            del self.runners[rid]  # stopped elsewhere or deleted
        by_user: dict[uuid.UUID, list[StrategyRun]] = defaultdict(list)
        for r in runs:
            by_user[r.user_id].append(r)
        wanted: set[str] = set()
        for user_id, user_runs in by_user.items():
            try:
                wanted |= await self._user(user_id, [r.id for r in user_runs], insts, halted, now)
            except Exception:  # nothing was saved: reload these runs from the database next time
                log.exception("user pass failed", user_id=str(user_id))
                for r in user_runs:
                    self.runners.pop(r.id, None)
        if wanted:
            await self.hub.want(wanted)
        return len(runs)

    async def _markets(self, codes: set[str], insts: dict[str, Inst], now: datetime) -> dict[str, Market]:
        last = await self.hub.last(codes)
        out = {}
        for u in codes:
            i = insts.get(u)
            if i is None:
                continue
            t = last.get(u)
            spot = t.ltp if t is not None and now - t.ts < STALE else None
            out[u] = Market(now, u, spot, await self.hub.bars(u, now.date()), {}, i.expiries, i.lot_size, i.strike_step)
        return out

    async def _user(
        self, user_id: uuid.UUID, run_ids: list[uuid.UUID], insts: dict[str, Inst], halted: bool, now: datetime
    ) -> set[str]:
        wanted: set[str] = set()
        async with self.db.system_session() as s:
            q = select(StrategyRun).where(StrategyRun.id.in_(run_ids)).with_for_update(skip_locked=True)
            runs = list((await s.execute(q)).scalars())
            rs = (
                await s.execute(select(UserRiskSettings).where(UserRiskSettings.user_id == user_id))
            ).scalar_one_or_none()
            settings = RiskSettings(
                max_daily_loss=float(rs.max_daily_loss) if rs and rs.max_daily_loss is not None else None,
                max_daily_profit=float(rs.max_daily_profit) if rs and rs.max_daily_profit is not None else None,
                max_open_positions=rs.max_open_positions if rs else None,
                max_trades_per_day=rs.max_trades_per_day if rs else None,
                kill_switch=bool(rs and rs.kill_switch),
            )
            ent = await load_entitlements(s, user_id, self.grace)
            runners = {r.id: self._runner(r) for r in runs}
            markets = await self._markets({self._underlying(r) for r in runs}, insts, now)
            # prices for every contract any of this user's runners holds or wants
            for r in runs:
                m = markets.get(self._underlying(r))
                if m is not None:
                    wanted |= runners[r.id].wanted(m)
            prices = {k: t.ltp for k, t in (await self.hub.last(wanted)).items()}
            for m in markets.values():
                m.prices = prices

            def day_pnl() -> float:
                total = 0.0
                for r in runs:
                    rn, m = runners[r.id], markets.get(self._underlying(r))
                    today = [p for p in rn.positions if p.entry_time.date() == now.date() or p.open]
                    total += sum(p.pnl(m.price(p.contract) if m else None) for p in today)
                return total

            def ctx() -> RiskContext:
                return RiskContext(
                    settings=settings,
                    platform_halted=halted,
                    max_lots_per_order=ent.limit("max_lots_per_order"),
                    day_pnl=day_pnl(),
                    open_positions=sum(len(runners[r.id].open_positions()) for r in runs),
                    entries_today=sum(
                        1 for r in runs for p in runners[r.id].positions if p.entry_time.date() == now.date()
                    ),
                )

            stop_all = breach(ctx())
            for r in runs:
                m = markets.get(self._underlying(r))
                try:
                    if m is None:
                        raise RuntimeError(f"{self._underlying(r)} is not an available instrument")
                    await self._step(s, r, runners[r.id], m, insts[m.underlying], ctx, stop_all, now)
                except Exception as exc:
                    log.exception("run failed", run_id=str(r.id))
                    r.status, r.error, r.stopped_at = RunStatus.ERROR, f"{type(exc).__name__}: {exc}"[:500], now
                    s.add(
                        TradeEvent(
                            user_id=r.user_id, run_id=r.id, event="run_error", level="ERROR", detail={"error": r.error}
                        )
                    )
                    self.runners.pop(r.id, None)
        return wanted | {self._underlying(r) for r in runs}

    def _underlying(self, r: StrategyRun) -> str:
        return str(r.config_snapshot.get("underlying", "NIFTY"))

    def _runner(self, r: StrategyRun) -> Runner:
        rn = self.runners.get(r.id)
        if rn is None:
            cfg = parse(migrate(r.schema_version, r.config_snapshot))
            rn = self.runners[r.id] = make_runner(cfg, r.multiplier, r.state)
        return rn

    async def _step(
        self,
        s: AsyncSession,
        r: StrategyRun,
        rn: Runner,
        m: Market,
        inst: Inst,
        ctx: Callable[[], RiskContext],
        stop_all: str | None,
        now: datetime,
    ) -> None:
        if r.mode == TradingMode.LIVE:
            raise RuntimeError("live trading is not available yet: deploy on paper")
        if r.status == RunStatus.PENDING:
            r.status, r.started_at = RunStatus.RUNNING, now
            self._event(s, r, "run_started", mode=r.mode.value, multiplier=r.multiplier)
        if r.status == RunStatus.STOPPING or stop_all:
            reason = r.stop_reason or stop_all or "stopped"
            if stop_all and r.status != RunStatus.STOPPING:
                r.status, r.stop_reason = RunStatus.STOPPING, stop_all
                self._event(s, r, "risk_limit", level="WARNING", reason=stop_all)
            intents = rn.exit_all(reason, m)
        else:
            intents = rn.step(m)
        for intent in intents:
            refused = check_entry(intent, ctx())
            if refused:
                rn.reject(intent, refused)
                continue
            price = self.exchange.fill(intent, m)
            if price is None:
                rn.reject(intent, "no price for the contract yet")
                continue
            pos = rn.fill(intent, price, now, m)
            if pos is not None:
                await self._record(s, r, pos, intent, price, inst, now)
        for note in rn.notes:
            self._event(s, r, note.pop("event"), **note)
        rn.notes.clear()
        # mark to market
        open_trades = {p.id: p for p in rn.open_positions()}
        if open_trades:
            ids = [uuid.UUID(k) for k in open_trades]
            rows: list[Trade] = list((await s.execute(select(Trade).where(Trade.id.in_(ids)))).scalars())
            for t in rows:
                p = open_trades[str(t.id)]
                ltp = m.price(p.contract)
                t.last_ltp, t.unrealized_pnl, t.current_sl = _d(ltp), _d(p.pnl(ltp)) or Decimal(0), _d(p.sl)
        if r.status == RunStatus.STOPPING and not rn.open_positions():
            r.status, r.stopped_at = RunStatus.STOPPED, now
            self._event(s, r, "run_stopped", reason=r.stop_reason or "stopped")
            self.runners.pop(r.id, None)
        r.state = {**rn.state(), "_underlying": m.underlying}
        r.realized_pnl = Decimal(str(rn.realized))
        r.unrealized_pnl = Decimal(str(rn.unrealized(m)))
        r.heartbeat_at = now

    async def _record(
        self, s: AsyncSession, r: StrategyRun, p: Position, intent: Intent, price: float, inst: Inst, now: datetime
    ) -> None:
        pid = uuid.UUID(p.id)
        side = Side(intent.side)
        if intent.kind == "entry":
            s.add(
                Trade(
                    id=pid,
                    user_id=r.user_id,
                    run_id=r.id,
                    broker_account_id=r.broker_account_id,
                    mode=r.mode,
                    status="open",
                    trade_date=now.date(),
                    underlying=p.contract.underlying,
                    exchange=inst.exchange,
                    tradingsymbol=_symbol(p),
                    expiry=p.contract.expiry,
                    strike=Decimal(p.contract.strike),
                    option_type=p.contract.right,
                    side=side,
                    product="NRML" if r.kind == "range_breakout" else "MIS",
                    lots=p.lots,
                    lot_size=inst.lot_size,
                    quantity=p.qty,
                    entry_price=Decimal(str(price)),
                    entry_avg_price=Decimal(str(price)),
                    initial_sl=_d(p.sl),
                    current_sl=_d(p.sl),
                    target=_d(p.target),
                    filled_qty=p.qty,
                    open_qty=p.qty,
                    rules={"leg": p.leg, "group": p.group, "sl_basis": p.sl_basis, "reason": intent.reason},
                    entry_time=now,
                    last_ltp=Decimal(str(price)),
                )
            )
            await s.flush()
        else:
            t = await s.get(Trade, pid)
            if t is not None:
                t.status, t.exit_reason = "closed", intent.reason[:40]
                t.exit_avg_price, t.exit_time = Decimal(str(price)), now
                t.exited_qty, t.open_qty = p.qty, 0
                t.realized_pnl, t.unrealized_pnl = Decimal(str(p.pnl())), Decimal(0)
        s.add(
            Order(
                user_id=r.user_id,
                trade_id=pid,
                broker_account_id=r.broker_account_id,
                kind=_kind(intent),
                tag=uuid.uuid4().hex[:20],
                side=side,
                order_type="MARKET",
                quantity=p.qty,
                price=Decimal(str(price)),
                status="COMPLETE",
                filled_qty=p.qty,
                avg_price=Decimal(str(price)),
                status_message="paper fill",
            )
        )
        detail: dict[str, Any] = {"contract": p.contract.label, "side": intent.side, "lots": p.lots, "qty": p.qty,
                                  "price": price, "reason": intent.reason}  # fmt: skip
        if intent.kind == "exit":
            detail["pnl"] = p.pnl()
        self._event(s, r, "entry" if intent.kind == "entry" else "exit", trade_id=pid, **detail)

    def _event(
        self,
        s: AsyncSession,
        r: StrategyRun,
        event: str,
        level: str = "INFO",
        trade_id: uuid.UUID | None = None,
        **detail: Any,
    ) -> None:
        s.add(
            TradeEvent(user_id=r.user_id, run_id=r.id, trade_id=trade_id, event=event[:60], level=level, detail=detail)
        )
