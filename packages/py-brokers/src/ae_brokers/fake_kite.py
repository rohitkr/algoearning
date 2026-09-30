"""A fake Kite Connect REST API (httpx transport) for tests and demos: orders fill when marketable against `prices`,
PaperBroker, so the live order path runs end to end without Zerodha."""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import parse_qs

import httpx

from ae_brokers.kite import ContractBook, KiteContract


@dataclass
class FakeKite:
    token: str = "api-key:access-token"  # noqa: S105 (a fake credential for the fake API)
    funds: float = 1_000_000.0
    margin: float = 50_000.0
    prices: dict[str, float] = field(default_factory=dict)  # tradingsymbol -> price
    reject: set[str] = field(default_factory=set)
    never_fill: set[str] = field(default_factory=set)
    orders: dict[str, dict[str, Any]] = field(default_factory=dict)
    net: dict[str, int] = field(default_factory=dict)
    log: list[tuple[str, ...]] = field(default_factory=list)
    _ids: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    def _fill(self, oid: str) -> None:
        o = self.orders[oid]
        if o["status"] != "OPEN" or o["tradingsymbol"] in self.never_fill:
            return
        px = self.prices[o["tradingsymbol"]]
        buy = o["transaction_type"] == "BUY"
        if (o["price"] >= px) if buy else (o["price"] <= px):
            o.update(status="COMPLETE", filled_quantity=o["quantity"], average_price=px)
            self.net[o["tradingsymbol"]] = self.net.get(o["tradingsymbol"], 0) + (1 if buy else -1) * o["quantity"]

    def ok(self, data: Any) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": data})

    def handler(self, req: httpx.Request) -> httpx.Response:
        if req.headers.get("authorization") != f"token {self.token}":
            return httpx.Response(403, json={"status": "error", "error_type": "TokenException", "message": "expired"})
        path, m = req.url.path, req.method
        form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()} if m in ("POST", "PUT") else {}
        if m == "POST" and path == "/orders/regular":
            oid = f"K{next(self._ids)}"
            self.log.append(("place", form["transaction_type"], form["tradingsymbol"], form["quantity"]))
            if form["tradingsymbol"] in self.reject:
                self.orders[oid] = {**form, "order_id": oid, "status": "REJECTED", "filled_quantity": 0,
                                    "average_price": 0, "status_message": "RMS: blocked", "price": 0,
                                    "quantity": int(form["quantity"])}  # fmt: skip
            else:
                self.orders[oid] = {**form, "order_id": oid, "status": "OPEN", "filled_quantity": 0,
                                    "average_price": 0, "status_message": "", "price": float(form["price"]),
                                    "quantity": int(form["quantity"])}  # fmt: skip
                self._fill(oid)
            return self.ok({"order_id": oid})
        if m == "PUT" and path.startswith("/orders/regular/"):
            oid = path.rsplit("/", 1)[1]
            self.orders[oid]["price"] = float(form["price"])
            self.log.append(("modify", oid, form["price"]))
            self._fill(oid)
            return self.ok({"order_id": oid})
        if m == "DELETE" and path.startswith("/orders/regular/"):
            oid = path.rsplit("/", 1)[1]
            if self.orders[oid]["status"] == "OPEN":
                self.orders[oid]["status"] = "CANCELLED"
            self.log.append(("cancel", oid))
            return self.ok({"order_id": oid})
        if m == "GET" and path.startswith("/orders/"):
            return self.ok([self.orders[path.rsplit("/", 1)[1]]])
        if m == "GET" and path == "/orders":
            return self.ok(list(self.orders.values()))
        if m == "GET" and path == "/portfolio/positions":
            net = [{"exchange": "NFO", "tradingsymbol": k, "quantity": v} for k, v in self.net.items()]
            return self.ok({"net": net, "day": []})
        if m == "POST" and path == "/margins/basket":
            json.loads(req.content)
            return self.ok({"final": {"total": self.margin}})
        if m == "GET" and path == "/user/margins/equity":
            return self.ok({"net": self.funds})
        return httpx.Response(404, json={"status": "error", "message": f"{m} {path}"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def book(*contracts: tuple[str, date, int, str, str]) -> ContractBook:
    b = ContractBook()
    for u, e, k, r, sym in contracts:
        b.by_key[ContractBook.key(u, e, k, r)] = KiteContract(sym, "NFO", 65, 0.05)
    return b
