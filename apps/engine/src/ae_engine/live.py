"""Live execution on Zerodha for the engine (ADR 0015). Ported from algo-trading-claude zerodha/orders.py and
zerodha/executor.py, made async so one user's order waiting never holds up another user's strategies.

Per broker account, a LiveAccount runs each run's batches of intents one after another, in the background:

    entries   one basket-margin check for the batch (hedge benefit included) against available funds + buffer;
              legs in the runner's order (hedges first); every leg is sliced at the exchange freeze quantity and
              placed as a marketable limit (LTP +/- buffer, rounded to tick), re-priced if it does not fill, and
              cancelled after the last re-price. If a leg fails, the legs of the batch already filled are
              unwound (shorts first) so a short is never left without its hedge.
    exits     same order path; what an exit could not close stays pending and is retried by the engine.
    dry run   no broker call at all: the order is logged and "filled" at the feed's price, so a user can watch
              a live strategy's orders for a day before real money.

Every order carries a tag derived from the position it belongs to, so after a restart the engine can find what
was filled in Zerodha's order book instead of sending an order twice."""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import structlog
from ae_brokers.base import BrokerError
from ae_brokers.kite import ContractBook, KiteClient, KiteContract, OrderRequest
from ae_core.trading.model import Intent

log = structlog.get_logger("ae_engine.live")


@dataclass(frozen=True)
class LiveConfig:
    limit_buffer_pct: float = 2.0  # LIMIT = LTP +/- this %: marketable, but never a runaway market order
    fill_timeout_s: float = 20.0  # wait this long for a fill before re-pricing
    poll_s: float = 1.0
    max_reprices: int = 3
    margin_buffer_pct: float = 10.0  # need available >= required x (1 + buffer)


@dataclass(frozen=True)
class Fill:
    order_id: str
    quantity: int
    average_price: float


class OrderFailed(RuntimeError):
    def __init__(self, msg: str, fills: list[Fill]) -> None:
        super().__init__(msg)
        self.fills = fills


def round_to_tick(price: float, tick: float, side: str) -> float:
    """BUY rounds up, SELL rounds down, so the limit stays marketable."""
    steps = price / tick
    steps = math.ceil(steps - 1e-9) if side == "BUY" else math.floor(steps + 1e-9)
    return round(max(steps, 1) * tick, 2)


def slice_quantity(qty: int, lot: int, freeze: int) -> list[int]:
    if qty % lot:
        raise ValueError(f"quantity {qty} is not a multiple of the lot size {lot}")
    per = max(lot, freeze // lot * lot)
    out = [per] * (qty // per)
    if qty % per:
        out.append(qty % per)
    return out


def tag_for(position_id: str) -> str:
    """Kite allows 20 characters: enough of the position's id to find its orders again."""
    return "ae" + uuid.UUID(position_id).hex[:18]


def avg_price(fills: list[Fill]) -> float:
    q = sum(f.quantity for f in fills)
    return round(sum(f.quantity * f.average_price for f in fills) / q, 2) if q else 0.0


PriceFn = Callable[[str], float | None]


@dataclass
class OrderManager:
    client: KiteClient
    cfg: LiveConfig
    price: PriceFn  # our contract key -> the feed's last price
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    clock: Callable[[], float] = time.monotonic

    def limit_price(self, key: str, c: KiteContract, side: str) -> float:
        ltp = self.price(key)
        if ltp is None:
            raise OrderFailed(f"no price for {c.tradingsymbol}", [])
        k = 1 + self.cfg.limit_buffer_pct / 100 if side == "BUY" else 1 - self.cfg.limit_buffer_pct / 100
        return round_to_tick(ltp * k, c.tick_size, side)

    async def execute(self, key: str, c: KiteContract, side: str, qty: int, product: str, tag: str,
                      freeze: int) -> list[Fill]:  # fmt: skip
        fills: list[Fill] = []
        for q in slice_quantity(qty, c.lot_size, freeze):
            try:
                fills.append(await self._one(key, c, side, q, product, tag))
            except OrderFailed as exc:
                raise OrderFailed(str(exc), fills + exc.fills) from None
        return fills

    async def _one(self, key: str, c: KiteContract, side: str, qty: int, product: str, tag: str) -> Fill:
        req = OrderRequest(
            c.tradingsymbol, side, qty, c.exchange, product, "LIMIT", self.limit_price(key, c, side), tag
        )
        try:
            oid = await self.client.place_order(req)
        except BrokerError as exc:
            if exc.code == "session_expired":
                raise
            raise OrderFailed(f"{side} {c.tradingsymbol} refused: {exc.message}", []) from exc
        log.info("order placed", side=side, symbol=c.tradingsymbol, qty=qty, price=req.price, order_id=oid)
        for attempt in range(self.cfg.max_reprices + 1):
            st = await self._wait(oid)
            if st.status == "COMPLETE":
                return Fill(oid, qty, st.average_price)
            if st.done:
                partial = [Fill(oid, st.filled_quantity, st.average_price)] if st.filled_quantity else []
                raise OrderFailed(f"{side} {c.tradingsymbol} {st.status.lower()}: {st.message}", partial)
            if attempt < self.cfg.max_reprices:
                await self.client.modify_order(oid, price=self.limit_price(key, c, side))
        await self.client.cancel_order(oid)
        st = await self.client.order_status(oid)
        partial = [Fill(oid, st.filled_quantity, st.average_price)] if st.filled_quantity else []
        raise OrderFailed(f"{side} {c.tradingsymbol} not filled after {self.cfg.max_reprices} re-prices", partial)

    async def _wait(self, oid: str):  # type: ignore[no-untyped-def]
        deadline = self.clock() + self.cfg.fill_timeout_s
        while True:
            st = await self.client.order_status(oid)
            if st.done or self.clock() >= deadline:
                return st
            await self.sleep(self.cfg.poll_s)


@dataclass
class Outcome:
    """What happened to one intent. ok: fully done at `price`. Not ok: `filled` units happened anyway (they are
    already unwound for entries; for exits they are gone at the broker and only the rest is still open)."""

    intent: Intent
    ok: bool
    price: float | None
    message: str
    filled: int = 0
    dry_run: bool = False
    order_ids: list[str] = field(default_factory=list)


@dataclass
class Batch:
    run_id: uuid.UUID
    intents: list[Intent]
    product: str
    dry_run: bool
    freeze: int


class LiveAccount:
    """Execution for one broker account. `submit` returns at once; outcomes are collected with `drain`."""

    def __init__(self, client: KiteClient | None, book: ContractBook, cfg: LiveConfig, price: PriceFn,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:  # fmt: skip
        self.client, self.book, self.cfg, self.price = client, book, cfg, price
        self.om = OrderManager(client, cfg, price, sleep) if client else None
        self.queues: dict[uuid.UUID, asyncio.Queue[Batch]] = {}
        self.workers: dict[uuid.UUID, asyncio.Task[None]] = {}
        self.outcomes: dict[uuid.UUID, list[Outcome]] = {}
        self.session_error: str | None = None

    def submit(self, batch: Batch) -> None:
        q = self.queues.setdefault(batch.run_id, asyncio.Queue())
        q.put_nowait(batch)
        w = self.workers.get(batch.run_id)
        if w is None or w.done():
            self.workers[batch.run_id] = asyncio.create_task(self._work(batch.run_id, q))

    def drain(self, run_id: uuid.UUID) -> list[Outcome]:
        return self.outcomes.pop(run_id, [])

    async def _work(self, run_id: uuid.UUID, q: asyncio.Queue[Batch]) -> None:
        while not q.empty():
            batch = q.get_nowait()
            try:
                out = await self.process(batch)
            except Exception as exc:  # never lose the outcome: every intent is reported, as failed
                log.exception("batch failed", run_id=str(run_id))
                out = [Outcome(i, False, None, f"{type(exc).__name__}: {exc}") for i in batch.intents]
            self.outcomes.setdefault(run_id, []).extend(out)
            q.task_done()

    async def process(self, b: Batch) -> list[Outcome]:
        if b.dry_run:
            return [self._dry(i) for i in b.intents]
        if self.om is None or self.client is None:
            reason = self.session_error or "log in to Zerodha to trade live today"
            return [Outcome(i, False, None, reason) for i in b.intents]
        entries = [i for i in b.intents if i.kind == "entry"]
        refused = await self._margin_refusal(entries, b.product) if entries else None
        out: list[Outcome] = []
        opened: list[tuple[Intent, list[Fill]]] = []
        failed: str | None = None
        for i in b.intents:
            if i.kind == "entry" and (refused or failed):
                out.append(Outcome(i, False, None, refused or f"not placed: {failed}"))
                continue
            try:
                c = self.book.get(i.contract.key)
                fills = await self.om.execute(
                    i.contract.key, c, i.side, i.qty, b.product, tag_for(i.position_id), b.freeze
                )
            except (OrderFailed, BrokerError, ValueError) as exc:
                part = exc.fills if isinstance(exc, OrderFailed) else []
                msg = exc.message if isinstance(exc, BrokerError) else str(exc)
                if isinstance(exc, BrokerError) and exc.code == "session_expired":
                    self.session_error = msg = "the Zerodha session expired: log in again"
                if i.kind == "entry":
                    failed = msg
                    if part:
                        opened.append((i, part))
                    out.append(Outcome(i, False, None, msg, order_ids=[f.order_id for f in part]))
                else:
                    done = sum(f.quantity for f in part)
                    out.append(Outcome(i, False, avg_price(part) if part else None, msg, filled=done,
                                       order_ids=[f.order_id for f in part]))  # fmt: skip
                continue
            if i.kind == "entry":
                opened.append((i, fills))
            out.append(Outcome(i, True, avg_price(fills), "filled", sum(f.quantity for f in fills),
                               order_ids=[f.order_id for f in fills]))  # fmt: skip
        if failed and opened:
            out = await self._unwind(opened, out, b, failed)
        return out

    async def _margin_refusal(self, entries: list[Intent], product: str) -> str | None:
        assert self.client is not None
        try:
            reqs = []
            for i in entries:
                c = self.book.get(i.contract.key)
                px = self.price(i.contract.key) or 0.0
                reqs.append(OrderRequest(c.tradingsymbol, i.side, i.qty, c.exchange, product, "LIMIT", px))
            need = await self.client.basket_margin(reqs) * (1 + self.cfg.margin_buffer_pct / 100)
            have = await self.client.available_margin()
        except BrokerError as exc:
            if exc.code == "session_expired":
                self.session_error = "the Zerodha session expired: log in again"
            return f"margin check failed: {exc.message}"
        if have < need:
            buf = self.cfg.margin_buffer_pct
            return f"not enough margin: need ₹{need:,.0f} (with {buf:g}% buffer), available ₹{have:,.0f}"
        return None

    async def _unwind(self, opened: list[tuple[Intent, list[Fill]]], out: list[Outcome], b: Batch,
                      failed: str) -> list[Outcome]:  # fmt: skip
        """A leg of the entry failed: close what the batch already opened, shorts before their hedges."""
        assert self.om is not None
        undone: set[str] = set()
        for i, fills in sorted(opened, key=lambda x: x[0].side == "BUY"):
            qty = sum(f.quantity for f in fills)
            try:
                c = self.book.get(i.contract.key)
                side = "BUY" if i.side == "SELL" else "SELL"
                await self.om.execute(i.contract.key, c, side, qty, b.product, tag_for(i.position_id), b.freeze)
                undone.add(i.position_id)
            except (OrderFailed, BrokerError, ValueError) as exc:
                log.critical("could not unwind: CHECK POSITIONS", symbol=i.contract.label, qty=qty, error=str(exc))
        # a leg that could not be unwound stays a filled entry: an open position the run keeps managing
        return [
            Outcome(o.intent, False, None, f"unwound because another leg failed: {failed}")
            if o.intent.position_id in undone
            else o
            for o in out
        ]

    def _dry(self, i: Intent) -> Outcome:
        px = self.price(i.contract.key)
        if px is None:
            return Outcome(i, False, None, "dry run: no price for the contract yet", dry_run=True)
        try:
            c = self.book.get(i.contract.key)
            symbol = c.tradingsymbol
        except BrokerError:
            symbol = i.contract.label
        return Outcome(i, True, px, f"dry run: would {i.side} {i.qty} {symbol} at about {px:g}", i.qty, dry_run=True)


async def recover_by_tag(client: KiteClient, intent: Intent) -> tuple[int, float]:
    """After a restart: how much of an in-flight intent Zerodha filled (from the day's order book, by tag)."""
    tag = tag_for(intent.position_id)
    fills = [
        Fill(str(o["order_id"]), int(o.get("filled_quantity") or 0), float(o.get("average_price") or 0))
        for o in await client.orders()
        if o.get("tag") == tag and o.get("transaction_type") == intent.side and int(o.get("filled_quantity") or 0)
    ]
    return sum(f.quantity for f in fills), avg_price(fills)
