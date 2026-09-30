"""Zerodha Kite Connect execution (orders, order book, positions, margins) for one logged-in account, plus the
contract book that maps our contracts to Kite's tradingsymbols. Ported from algo-trading-claude zerodha/broker.py
(KiteBroker), async over Kite's REST API. Stateless apart from the account's credentials."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from .base import BrokerError
from .instruments import DUMP_URL
from .zerodha import API

TERMINAL = {"COMPLETE", "REJECTED", "CANCELLED"}


@dataclass(frozen=True)
class KiteContract:
    tradingsymbol: str
    exchange: str  # NFO | BFO
    lot_size: int
    tick_size: float


@dataclass(frozen=True)
class OrderRequest:
    tradingsymbol: str
    side: str  # BUY | SELL
    quantity: int
    exchange: str = "NFO"
    product: str = "MIS"  # MIS intraday, NRML carried
    order_type: str = "LIMIT"
    price: float | None = None
    tag: str = ""  # up to 20 chars; our idempotency / ownership marker

    def params(self) -> dict[str, Any]:
        p: dict[str, Any] = {
            "exchange": self.exchange,
            "tradingsymbol": self.tradingsymbol,
            "transaction_type": self.side,
            "quantity": self.quantity,
            "product": self.product,
            "order_type": self.order_type,
            "validity": "DAY",
        }
        if self.order_type == "LIMIT":
            p["price"] = self.price
        if self.tag:
            p["tag"] = self.tag[:20]
        return p


@dataclass(frozen=True)
class OrderStatus:
    order_id: str
    status: str  # Kite: OPEN, COMPLETE, REJECTED, CANCELLED, TRIGGER PENDING, ...
    filled_quantity: int = 0
    average_price: float = 0.0
    message: str = ""

    @property
    def done(self) -> bool:
        return self.status in TERMINAL


class ContractBook:
    """Our contract (underlying, expiry, strike, right) -> Kite's tradingsymbol, lot and tick, from the day's public
    instrument list. Build once a day with `load()`."""

    def __init__(self, by_key: dict[str, KiteContract] | None = None) -> None:
        self.by_key = by_key or {}
        self.day: date | None = None

    @staticmethod
    def key(underlying: str, expiry: date, strike: float, right: str) -> str:
        return f"{underlying}:{expiry:%Y-%m-%d}:{int(strike)}:{right}"

    def add_rows(self, rows: list[dict[str, str]]) -> None:
        for r in rows:
            if r.get("instrument_type") not in ("CE", "PE") or not r.get("expiry"):
                continue
            k = self.key(r["name"], date.fromisoformat(r["expiry"]), float(r["strike"]), r["instrument_type"])
            self.by_key[k] = KiteContract(
                r["tradingsymbol"], r["exchange"], int(float(r["lot_size"])), float(r.get("tick_size") or 0.05)
            )

    async def load(self, exchanges: list[str], client: httpx.AsyncClient, today: date) -> None:
        from .instruments import parse_dump

        self.by_key = {}
        for x in exchanges:
            r = await client.get(DUMP_URL.format(exchange=x), timeout=60)
            r.raise_for_status()
            self.add_rows(parse_dump(r.text))
        self.day = today

    def get(self, key: str) -> KiteContract:
        c = self.by_key.get(key)
        if c is None:
            raise BrokerError("unknown_contract", f"{key} is not in today's Zerodha instrument list")
        return c


class KiteClient:
    """One account's execution calls. Raises BrokerError: code session_expired (log in again), rejected, unreachable."""

    VARIETY = "regular"

    def __init__(self, api_key: str, access_token: str, *, transport: httpx.AsyncBaseTransport | None = None,
                 api: str = API) -> None:  # fmt: skip
        self._client = httpx.AsyncClient(
            base_url=api,
            timeout=15.0,
            transport=transport,
            headers={"X-Kite-Version": "3", "Authorization": f"token {api_key}:{access_token}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, path: str, **kw: Any) -> Any:
        try:
            r = await self._client.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise BrokerError("unreachable", f"Zerodha is unreachable ({type(exc).__name__})") from exc
        body = r.json() if r.content else {}
        if r.status_code >= 400 or body.get("status") == "error":
            kind = body.get("error_type") or "error"
            code = "session_expired" if kind == "TokenException" else "rejected"
            raise BrokerError(code, str(body.get("message") or f"Zerodha error {r.status_code}"), r.status_code)
        return body.get("data")

    async def place_order(self, req: OrderRequest) -> str:
        data = await self._call("POST", f"/orders/{self.VARIETY}", data=req.params())
        return str(data["order_id"])

    async def modify_order(self, order_id: str, *, price: float) -> None:
        await self._call("PUT", f"/orders/{self.VARIETY}/{order_id}", data={"price": price, "order_type": "LIMIT"})

    async def cancel_order(self, order_id: str) -> None:
        await self._call("DELETE", f"/orders/{self.VARIETY}/{order_id}")

    async def order_status(self, order_id: str) -> OrderStatus:
        history = await self._call("GET", f"/orders/{order_id}")
        last = history[-1]
        return OrderStatus(
            order_id,
            str(last["status"]),
            int(last.get("filled_quantity") or 0),
            float(last.get("average_price") or 0.0),
            str(last.get("status_message") or ""),
        )

    async def orders(self) -> list[dict[str, Any]]:
        """Today's order book (every order, any status)."""
        return list(await self._call("GET", "/orders") or [])

    async def positions(self, exchanges: tuple[str, ...] = ("NFO", "BFO")) -> dict[str, int]:
        """Net quantity per tradingsymbol (+long / -short), day and carried together."""
        out: dict[str, int] = {}
        for p in (await self._call("GET", "/portfolio/positions") or {}).get("net", []):
            if p.get("exchange") in exchanges:
                out[p["tradingsymbol"]] = out.get(p["tradingsymbol"], 0) + int(p.get("quantity") or 0)
        return out

    async def basket_margin(self, reqs: list[OrderRequest]) -> float:
        """Margin for all legs together, after hedge benefit ('final')."""
        orders = [dict(r.params(), variety=self.VARIETY, price=r.price or 0, trigger_price=0) for r in reqs]
        data = await self._call(
            "POST", "/margins/basket", params={"consider_positions": "true", "mode": "compact"}, json=orders
        )
        return float(data["final"]["total"])

    async def available_margin(self) -> float:
        data = await self._call("GET", "/user/margins/equity")
        return float(data["net"])
