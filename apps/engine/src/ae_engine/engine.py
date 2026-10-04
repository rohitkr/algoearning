"""The multi-user engine: every second, every active run of every user takes one step (ADR 0014).

For each run: build its market view from the price feed (Redis), let its runner decide, check each entry against
the user's risk settings and plan, execute, and record positions (trades), fills (orders) and decisions
(trade_events). Runs are isolated: one run's error stops that run, never the others. The runner's state is saved on
the run after every step, so a restart continues where it left off.

Paper runs fill at once on the paper exchange. Live runs (ADR 0015) hand their intents to the broker account's
LiveAccount, which works in the background; outcomes are applied on a later step. In-flight intents are kept on the
run so a restart can find them in Zerodha's order book, failed exits are retried, and each live account's positions
are reconciled with Zerodha every minute."""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import structlog
from ae_brokers.base import BrokerError
from ae_brokers.kite import ContractBook, KiteClient
from ae_core.backtest import Candle
from ae_core.notifications import FROM_ENGINE, compose
from ae_core.secrets import SecretBox
from ae_core.strategy import holds_overnight, migrate, parse
from ae_core.trading.model import IST, Contract, Intent, Market, Position, Quote
from ae_core.trading.risk import RiskContext, RiskSettings, breach, check_entry
from ae_core.trading.runners import Runner, make_runner
from ae_db.entitlements import load_entitlements
from ae_db.enums import BrokerAccountStatus, OrderKind, RunStatus, Side, TradingMode
from ae_db.models import (
    BrokerAccount,
    BrokerSession,
    HistoryCandle,
    Instrument,
    Notification,
    Order,
    PlatformSetting,
    StrategyRun,
    Trade,
    TradeEvent,
    UserRiskSettings,
)
from ae_db.session import Database
from ae_marketdata.hub import Hub
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .live import Batch, LiveAccount, LiveConfig, Outcome, recover_by_tag
from .paper import PaperExchange

log = structlog.get_logger("ae_engine")
ACTIVE = (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.STOPPING)
STALE = timedelta(minutes=2)
HALT_KEY = "trading_halted"
RETRY_EXIT = timedelta(seconds=5)
RECONCILE_EVERY = timedelta(seconds=60)


def _product(rn: Runner | None) -> str:
    """NRML for a strategy that may hold overnight (MIS is squared off by the broker before the close), else MIS."""
    return "NRML" if rn is not None and holds_overnight(rn.cfg) else "MIS"


def _product_of(r: StrategyRun) -> str:
    try:
        return "NRML" if holds_overnight(parse(migrate(r.schema_version, r.config_snapshot))) else "MIS"
    except ValueError:
        return "MIS"


@dataclass
class Inst:
    code: str
    exchange: str
    lot_size: int
    strike_step: int
    expiries: list[date]
    freeze_qty: int = 1800


def _symbol(p: Position) -> str:
    c = p.contract
    return f"{c.underlying}{c.expiry:%y%b%d}{c.strike}{c.right}".upper()


def _kind(intent: Intent) -> OrderKind:
    if intent.kind == "entry":
        return OrderKind.ENTRY
    if intent.reason == "stop-loss" or intent.reason.startswith("stop-loss:"):
        return OrderKind.SL
    if intent.reason == "target" or intent.reason.startswith("target "):
        return OrderKind.TARGET
    return OrderKind.EXIT


def _d(v: float | None) -> Decimal | None:
    return None if v is None else Decimal(str(round(v, 2)))


def _intent_dict(i: Intent) -> dict[str, Any]:
    return {"kind": i.kind, "side": i.side, "contract": i.contract.key, "lots": i.lots, "qty": i.qty,
            "reason": i.reason, "leg": i.leg, "position_id": i.position_id, "group": i.group}  # fmt: skip


def _intent_from(d: dict[str, Any]) -> Intent:
    return Intent(d["kind"], d["side"], Contract.from_key(d["contract"]), d["lots"], d["qty"], d["reason"], d["leg"],
                  position_id=d["position_id"], group=d.get("group"))  # fmt: skip


class Engine:
    def __init__(
        self,
        db: Database,
        hub: Hub,
        now: Callable[[], datetime] | None = None,
        exchange: PaperExchange | None = None,
        grace: timedelta = timedelta(days=3),
        box: SecretBox | None = None,
        live: LiveConfig | None = None,
        book: ContractBook | None = None,
        kite_transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.db, self.hub = db, hub
        self.now = now or (lambda: datetime.now(IST))
        self.exchange = exchange or PaperExchange()
        self.grace = grace
        self.runners: dict[uuid.UUID, Runner] = {}
        # live
        self.box, self.live_cfg = box, live or LiveConfig()
        self.book, self._book_given = book or ContractBook(), book is not None
        self.kite_transport, self.sleep = kite_transport, sleep
        self.accounts: dict[uuid.UUID | None, LiveAccount] = {}  # None: the dry-run account (no broker)
        self.account_token: dict[uuid.UUID, bytes | None] = {}
        self.prices: dict[str, float] = {}  # latest feed prices, for live limit pricing between steps
        self.recover: set[uuid.UUID] = set()  # live runs whose in-flight orders must be looked up after a restart
        self.reconciled: dict[uuid.UUID, datetime] = {}
        self.mismatch: dict[tuple[uuid.UUID, str], int] = defaultdict(int)
        self.prior: dict[tuple[str, date, int], list[Any]] = {}  # earlier sessions' index bars, loaded once a day

    # -- one pass --------------------------------------------------------------------------------------------------
    async def tick(self) -> int:
        now = self.now()
        async with self.db.system_session() as s:
            runs = list((await s.execute(select(StrategyRun).where(StrategyRun.status.in_(ACTIVE)))).scalars())
            insts = {
                r.code: Inst(
                    r.code,
                    r.exchange,
                    r.lot_size,
                    r.strike_step,
                    sorted(map(date.fromisoformat, r.expiries)),
                    r.freeze_qty,
                )
                for r in (await s.execute(select(Instrument))).scalars()
            }
            halted = bool(
                (
                    await s.execute(select(PlatformSetting.value).where(PlatformSetting.key == HALT_KEY))
                ).scalar_one_or_none()
            )
        for rid in set(self.runners) - {r.id for r in runs}:
            del self.runners[rid]  # stopped elsewhere or deleted
        if any(r.mode == TradingMode.LIVE for r in runs):
            await self._ensure_book(now.date())
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
            for r in runs:
                m = markets.get(self._underlying(r))
                if m is not None and runners[r.id].prior_days and len(m.prior_spot_bars) == 0:
                    m.prior_spot_bars = await self._prior_bars(m.underlying, now.date(), runners[r.id].prior_days)
            # prices for every contract any of this user's runners holds or wants
            for r in runs:
                m = markets.get(self._underlying(r))
                if m is not None:
                    wanted |= runners[r.id].wanted(m)
            ticks = await self.hub.last(wanted)
            prices = {k: t.ltp for k, t in ticks.items()}
            quotes = {k: Quote(t.bid, t.ask, t.volume, t.oi) for k, t in ticks.items()}
            self.prices.update(prices)
            for m in markets.values():
                m.prices, m.quotes = prices, quotes

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
                    if r.mode == TradingMode.LIVE:
                        await self._step_live(s, r, runners[r.id], m, insts[m.underlying], ctx, stop_all, now)
                    else:
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
            live = [r for r in runs if r.mode == TradingMode.LIVE and not r.dry_run and r.status in ACTIVE]
            for acc_id in {r.broker_account_id for r in live}:
                acc_runs = [r for r in live if r.broker_account_id == acc_id]
                try:
                    await self._reconcile(s, acc_id, acc_runs, runners, markets, now)
                except Exception:
                    log.exception("reconciliation failed", broker_account_id=str(acc_id))
        return wanted | {self._underlying(r) for r in runs}

    async def _prior_bars(self, underlying: str, today: date, sessions: int) -> list[Any]:
        """The index's 1-minute bars of the last `sessions` trading days before today: from the history store
        (archived every evening), else from the feed's bars still in Redis. Loaded once a day."""
        key = (underlying, today, sessions)  # runs may need different numbers of sessions
        if key in self.prior:
            return self.prior[key]
        lo = datetime.combine(today - timedelta(days=7 + 2 * sessions), datetime.min.time(), tzinfo=IST)
        hi = datetime.combine(today, datetime.min.time(), tzinfo=IST)
        async with self.db.system_session() as s:
            q = select(HistoryCandle).where(
                HistoryCandle.key == underlying, HistoryCandle.ts >= lo, HistoryCandle.ts < hi
            )
            rows = [
                Candle(c.ts.astimezone(IST), c.open, c.high, c.low, c.close) for c in (await s.execute(q)).scalars()
            ]
        by_day: dict[date, list[Any]] = defaultdict(list)
        for c in sorted(rows, key=lambda c: c.ts):
            by_day[c.ts.date()].append(c)
        for back in range(1, 8):
            d = today - timedelta(days=back)
            if d not in by_day:
                bars = await self.hub.bars(underlying, d)
                if bars:
                    by_day[d] = list(bars)
        days = sorted(by_day)[-sessions:]
        for k in [k for k in self.prior if k[1] != today]:
            del self.prior[k]
        self.prior[key] = [b for d in days for b in by_day[d]]
        return self.prior[key]

    def _underlying(self, r: StrategyRun) -> str:
        return str(r.config_snapshot.get("underlying", "NIFTY"))

    def _runner(self, r: StrategyRun) -> Runner:
        rn = self.runners.get(r.id)
        if rn is None:
            cfg = parse(migrate(r.schema_version, r.config_snapshot))
            rn = self.runners[r.id] = make_runner(cfg, r.multiplier, r.state)
            if r.mode == TradingMode.LIVE and r.state.get("_inflight"):
                self.recover.add(r.id)
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
        await self._mark(s, rn, m)
        if r.status == RunStatus.STOPPING and not rn.open_positions():
            r.status, r.stopped_at = RunStatus.STOPPED, now
            self._event(s, r, "run_stopped", reason=r.stop_reason or "stopped")
            self.runners.pop(r.id, None)
        r.state = {**rn.state(), "_underlying": m.underlying}
        r.realized_pnl = Decimal(str(rn.realized))
        r.unrealized_pnl = Decimal(str(rn.unrealized(m)))
        r.heartbeat_at = now

    # -- live -------------------------------------------------------------------------------------------------------
    async def _ensure_book(self, today: date) -> None:
        """Zerodha's contract list (tradingsymbols, lots, ticks), loaded once a day."""
        if self._book_given or self.book.day == today:
            return
        try:
            async with httpx.AsyncClient() as c:
                await self.book.load(["NFO", "BFO"], c, today)
            log.info("zerodha contracts loaded", contracts=len(self.book.by_key))
        except Exception:
            log.exception("could not load Zerodha's contract list: live orders wait for it")

    async def _account(
        self, s: AsyncSession, r: StrategyRun, now: datetime
    ) -> tuple[LiveAccount, BrokerAccount | None]:
        price: Callable[[str], float | None] = self.prices.get
        if r.dry_run:
            acct = self.accounts.get(None)
            if acct is None:
                acct = self.accounts[None] = LiveAccount(None, self.book, self.live_cfg, price, self.sleep)
            return acct, None
        if r.broker_account_id is None:
            raise RuntimeError("a live run needs a broker account")
        acc = await s.get(BrokerAccount, r.broker_account_id)
        if acc is None:
            raise RuntimeError("the broker account was removed")
        sess = (
            await s.execute(select(BrokerSession).where(BrokerSession.broker_account_id == acc.id))
        ).scalar_one_or_none()
        blob = sess.access_token_enc if sess is not None and sess.expires_at > now else None
        cached = self.accounts.get(acc.id)
        if cached is not None and self.account_token.get(acc.id) == blob:
            return cached, acc
        client = None
        if blob is not None and self.box is not None and acc.api_key_enc is not None:
            api_key = self.box.decrypt(acc.api_key_enc, f"broker_account:{acc.id}:api_key")
            token = self.box.decrypt(blob, f"broker_session:{acc.id}:access_token")
            client = KiteClient(api_key, token, transport=self.kite_transport)
        if cached is not None and cached.client is not None:
            await cached.client.aclose()
        acct = LiveAccount(client, self.book, self.live_cfg, price, self.sleep)
        if client is None:
            acct.session_error = (
                "APP_ENCRYPTION_KEY is not set for the engine"
                if self.box is None
                else "log in to Zerodha to trade live today"
            )
        self.accounts[acc.id], self.account_token[acc.id] = acct, blob
        return acct, acc

    async def _step_live(
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
        acct, acc = await self._account(s, r, now)
        inflight = [_intent_from(d) for d in r.state.get("_inflight", [])]
        retry: dict[str, str] = dict(r.state.get("_retry", {}))
        if r.status == RunStatus.PENDING:
            r.status, r.started_at = RunStatus.RUNNING, now
            self._event(s, r, "run_started", mode="live", dry_run=r.dry_run, multiplier=r.multiplier)
        if r.id in self.recover:
            inflight = await self._recover(s, r, rn, m, inst, acct, inflight, now)
        for o in acct.drain(r.id):
            inflight = [i for i in inflight if not (i.position_id == o.intent.position_id and i.kind == o.intent.kind)]
            await self._apply(s, r, rn, m, inst, o, retry, acc, now)
        if r.status == RunStatus.STOPPING or stop_all:
            reason = r.stop_reason or stop_all or "stopped"
            if stop_all and r.status != RunStatus.STOPPING:
                r.status, r.stop_reason = RunStatus.STOPPING, stop_all
                self._event(s, r, "risk_limit", level="WARNING", reason=stop_all)
            intents = rn.exit_all(reason, m)
        else:
            intents = rn.step(m)
        for pid, due in list(retry.items()):
            pos = rn.position(pid)
            if pos is None or not pos.open:
                del retry[pid]
            elif datetime.fromisoformat(due) <= now:
                i = rn._exit(pos, "retrying the exit")
                if i is not None:
                    intents.append(i)
                    del retry[pid]
        batch = []
        for i in intents:
            refused = check_entry(i, ctx())
            if not refused and i.kind == "entry" and acc is not None and not acc.engine_enabled:
                refused = "the Trading Engine switch is off for this broker account"
            if refused:
                rn.reject(i, refused)
                continue
            batch.append(i)
        if batch:
            acct.submit(Batch(r.id, batch, _product(rn), r.dry_run, inst.freeze_qty))
            inflight += batch
        for note in rn.notes:
            self._event(s, r, note.pop("event"), **note)
        rn.notes.clear()
        await self._mark(s, rn, m)
        if r.status == RunStatus.STOPPING and not rn.open_positions() and not inflight:
            r.status, r.stopped_at = RunStatus.STOPPED, now
            self._event(s, r, "run_stopped", reason=r.stop_reason or "stopped")
            self.runners.pop(r.id, None)
        r.state = {**rn.state(), "_underlying": m.underlying, "_inflight": [_intent_dict(i) for i in inflight],
                   "_retry": retry}  # fmt: skip
        r.realized_pnl = Decimal(str(rn.realized))
        r.unrealized_pnl = Decimal(str(rn.unrealized(m)))
        r.heartbeat_at = now

    async def _apply(
        self,
        s: AsyncSession,
        r: StrategyRun,
        rn: Runner,
        m: Market,
        inst: Inst,
        o: Outcome,
        retry: dict[str, str],
        acc: BrokerAccount | None,
        now: datetime,
    ) -> None:
        i = o.intent
        if o.ok and o.price is not None:
            pos = rn.fill(i, o.price, now, m)
            if pos is not None:
                await self._record(s, r, pos, i, o.price, inst, now, o)
            if o.dry_run:
                self._event(s, r, "dry_run_order", message=o.message, contract=i.contract.label, side=i.side, qty=i.qty)
            return
        rn.reject(i, o.message)
        if "session expired" in o.message and acc is not None and acc.status != BrokerAccountStatus.EXPIRED:
            acc.status = BrokerAccountStatus.EXPIRED
            s.add(Notification(user_id=r.user_id, event="broker_login", title="Log in to Zerodha again",
                               body="Your Zerodha session expired. Entries are paused and exits keep retrying "
                                    "until you log in on the Broker page.", run_id=r.id))  # fmt: skip
        if i.kind == "entry":
            self._event(s, r, "order_failed", level="WARNING", contract=i.contract.label, side=i.side, reason=o.message)
            return
        first = i.position_id not in r.state.get("_retry", {})
        retry[i.position_id] = (now + RETRY_EXIT).isoformat()
        if first:  # one event per failing exit, not one every few seconds
            self._event(s, r, "exit_failed", level="ERROR", contract=i.contract.label, reason=o.message,
                        filled=o.filled, retrying=True)  # fmt: skip

    async def _recover(
        self,
        s: AsyncSession,
        r: StrategyRun,
        rn: Runner,
        m: Market,
        inst: Inst,
        acct: LiveAccount,
        inflight: list[Intent],
        now: datetime,
    ) -> list[Intent]:
        """After a restart: settle the orders that were in flight from Zerodha's order book (never send twice)."""
        if r.dry_run:
            for i in inflight:
                rn.reject(i, "the engine restarted before this dry-run order")
            self.recover.discard(r.id)
            return []
        if acct.client is None:
            return inflight  # wait for a session to look them up
        for i in inflight:
            filled, avg = await recover_by_tag(acct.client, i)
            if filled and filled == i.qty:
                await self._apply(
                    s, r, rn, m, inst, Outcome(i, True, avg, "filled before the restart", filled), {}, None, now
                )
            elif filled:
                self._event(s, r, "partial_fill_before_restart", level="ERROR", contract=i.contract.label,
                            filled=filled, qty=i.qty, action="check the position in Zerodha")  # fmt: skip
                await self._apply(s, r, rn, m, inst, Outcome(i, True, avg, "partly filled", filled), {}, None, now)
            else:
                rn.reject(i, "not placed before the engine restarted")
        self.recover.discard(r.id)
        return []

    async def _reconcile(
        self,
        s: AsyncSession,
        acc_id: uuid.UUID | None,
        runs: list[StrategyRun],
        runners: dict[uuid.UUID, Runner],
        markets: dict[str, Market],
        now: datetime,
    ) -> None:
        """Compare this account's open positions with Zerodha's, once a minute. A position Zerodha no longer has
        (closed in Kite, say) is closed here too after two checks in a row; anything else is reported."""
        acct = self.accounts.get(acc_id)
        if acc_id is None or acct is None or acct.client is None:
            return
        last = self.reconciled.get(acc_id)
        if last is not None and now - last < RECONCILE_EVERY:
            return
        if any(r.state.get("_inflight") for r in runs):
            return  # orders are moving: compare when things are still
        self.reconciled[acc_id] = now
        expected: dict[str, int] = defaultdict(int)
        owners: dict[str, list[tuple[StrategyRun, Position]]] = defaultdict(list)
        for r in runs:
            for p in runners[r.id].open_positions():
                try:
                    sym = self.book.get(p.contract.key).tradingsymbol
                except BrokerError:
                    continue
                expected[sym] += p.qty * p.sign
                owners[sym].append((r, p))
        try:
            actual = await acct.client.positions()
        except BrokerError as exc:
            log.warning("reconciliation skipped", error=exc.message)
            return
        for sym in set(expected) | {k for k, v in actual.items() if v}:
            want, have = expected.get(sym, 0), actual.get(sym, 0)
            key = (acc_id, sym)
            if want == have:
                self.mismatch.pop(key, None)
                continue
            self.mismatch[key] += 1
            if self.mismatch[key] < 2:
                continue
            if want and not have:
                for r, p in owners[sym]:
                    rn, m = runners[r.id], markets.get(self._underlying(r))
                    px = (m.price(p.contract) if m else None) or p.entry_price
                    intent = Intent("exit", "SELL" if p.side == "BUY" else "BUY", p.contract, p.lots, p.qty,
                                    "closed outside AlgoEarning", p.leg, position_id=p.id)  # fmt: skip
                    pos = rn.fill(intent, px, now, m) if m else None
                    if pos is not None and m is not None:
                        await self._record(
                            s, r, pos, intent, px, Inst(m.underlying, "", m.lot_size, m.strike_step, []), now
                        )
                    self._event(s, r, "position_closed_outside", level="WARNING", symbol=sym, qty=p.qty)
                    r.state = {**rn.state(), **{k: v for k, v in r.state.items() if k.startswith("_")}}
            elif self.mismatch[key] == 2:  # report once
                for r in runs[:1] if not owners[sym] else [owners[sym][0][0]]:
                    self._event(s, r, "position_mismatch", level="WARNING", symbol=sym, expected=want, at_zerodha=have,
                                note="not changed automatically: check Zerodha")  # fmt: skip

    async def _mark(self, s: AsyncSession, rn: Runner, m: Market) -> None:
        open_trades = {p.id: p for p in rn.open_positions()}
        if not open_trades:
            return
        ids = [uuid.UUID(k) for k in open_trades]
        rows: list[Trade] = list((await s.execute(select(Trade).where(Trade.id.in_(ids)))).scalars())
        for t in rows:
            p = open_trades[str(t.id)]
            ltp = m.price(p.contract)
            t.last_ltp, t.unrealized_pnl, t.current_sl = _d(ltp), _d(p.pnl(ltp)) or Decimal(0), _d(p.sl)

    async def _record(
        self,
        s: AsyncSession,
        r: StrategyRun,
        p: Position,
        intent: Intent,
        price: float,
        inst: Inst,
        now: datetime,
        outcome: Outcome | None = None,
    ) -> None:
        pid = uuid.UUID(p.id)
        real = r.mode == TradingMode.LIVE and not r.dry_run
        symbol = _symbol(p)
        if r.mode == TradingMode.LIVE:
            try:
                symbol = self.book.get(p.contract.key).tradingsymbol
            except BrokerError:
                pass
        side = Side(intent.side)
        if intent.kind == "entry":
            s.add(
                Trade(
                    id=pid,
                    user_id=r.user_id,
                    run_id=r.id,
                    broker_account_id=r.broker_account_id,
                    mode=TradingMode.LIVE if real else TradingMode.PAPER,  # a dry run never counts as live
                    status="open",
                    trade_date=now.date(),
                    underlying=p.contract.underlying,
                    exchange=inst.exchange,
                    tradingsymbol=symbol,
                    expiry=p.contract.expiry,
                    strike=Decimal(p.contract.strike),
                    option_type=p.contract.right,
                    side=side,
                    product=_product(self.runners.get(r.id)) if r.id in self.runners else _product_of(r),
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
                    rules={
                        "leg": p.leg,
                        "group": p.group,
                        "sl_basis": p.sl_basis,
                        "reason": intent.reason,
                        "dry_run": r.dry_run,
                    },
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
                broker_order_id=",".join(outcome.order_ids)[:60] if outcome and outcome.order_ids else None,
                side=side,
                order_type="LIMIT" if real else "MARKET",
                quantity=p.qty,
                price=Decimal(str(price)),
                status="COMPLETE" if real or r.mode == TradingMode.PAPER else "DRY_RUN",
                filled_qty=p.qty,
                avg_price=Decimal(str(price)),
                status_message=("zerodha fill" if real else outcome.message if outcome else "dry run")
                if r.mode == TradingMode.LIVE
                else "paper fill",
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
        self._notify(s, r, event, detail)

    def _notify(self, s: AsyncSession, r: StrategyRun, event: str, detail: dict[str, Any]) -> None:
        """Queue what the user may want to hear about; the worker applies their settings and sends it."""
        kind, title = FROM_ENGINE.get(event, (None, ""))
        if event in ("entry", "exit"):
            reason = str(detail.get("reason", ""))
            if event == "exit" and reason in ("stop-loss", "target"):
                kind, title = "stop_loss", "Stop-loss hit" if reason == "stop-loss" else "Target hit"
            else:
                kind, title = (
                    ("trade_opened", "Position opened") if event == "entry" else ("trade_closed", "Position closed")
                )
        if kind is None:
            return
        if r.mode == TradingMode.LIVE and r.dry_run:
            title = f"[dry run] {title}"
        s.add(
            Notification(
                user_id=r.user_id,
                event=kind,
                title=f"{title}: {r.strategy_name}"[:200],
                body=compose(r.strategy_name, title, detail),
                run_id=r.id,
            )
        )
