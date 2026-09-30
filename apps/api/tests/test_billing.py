"""Prepaid purchases end to end against a fake Razorpay (in-memory HTTP): checkout, signature + server-side payment
checks, idempotent settlement, renewals, plan switches with credit, and the webhook backup path."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from ae_api.billing.razorpay import RazorpayClient
from ae_api.main import create_app
from ae_api.settings import Settings
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

KEY_ID, KEY_SECRET, WEBHOOK_SECRET = "rzp_test_fake", "fake_secret", "whsec_rzp_fake"
A = {"X-Dev-User": "alice@example.com"}
B = {"X-Dev-User": "bob@example.com"}


class FakeRazorpay:
    def __init__(self) -> None:
        self.orders: dict[str, dict[str, Any]] = {}
        self.payments: dict[str, dict[str, Any]] = {}
        self.captures: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        if request.method == "POST" and path == "/orders":
            body = json.loads(request.content)
            oid = f"order_{len(self.orders) + 1:04d}"
            self.orders[oid] = {"id": oid, **body}
            return httpx.Response(200, json=self.orders[oid])
        if path.startswith("/payments/"):
            pid = path.split("/")[2]
            if pid not in self.payments:
                return httpx.Response(400, json={"error": {"description": "The id provided does not exist"}})
            if path.endswith("/capture"):
                self.captures.append(pid)
                self.payments[pid]["status"] = "captured"
            return httpx.Response(200, json=self.payments[pid])
        return httpx.Response(404, json={"error": {"description": "not found"}})

    def pay(self, order_id: str, *, status: str = "captured", amount: int | None = None, currency: str = "INR") -> str:
        pid = f"pay_{len(self.payments) + 1:04d}"
        order = self.orders[order_id]
        self.payments[pid] = {
            "id": pid,
            "order_id": order_id,
            "amount": amount if amount is not None else order["amount"],
            "currency": currency,
            "status": status,
            "method": "upi",
        }
        return pid


def sig(order_id: str, payment_id: str, secret: str = KEY_SECRET) -> str:
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


@pytest.fixture
def ctx(clean_db: str) -> Iterator[tuple[TestClient, FakeRazorpay]]:
    fake = FakeRazorpay()
    app = create_app(
        Settings(
            app_env="test",
            dev_auth=True,
            database_url=clean_db,
            razorpay_key_id=KEY_ID,
            razorpay_key_secret=KEY_SECRET,
            razorpay_webhook_secret=WEBHOOK_SECRET,
        )
    )
    app.state.payments = RazorpayClient(KEY_ID, KEY_SECRET, transport=httpx.MockTransport(fake.handler))
    with TestClient(app) as c:
        yield c, fake


def buy(c: TestClient, fake: FakeRazorpay, plan: str = "pro", headers: dict[str, str] = A, **pay_kw: Any) -> Any:
    co = c.post("/v1/billing/checkout", json={"plan_code": plan}, headers=headers)
    assert co.status_code == 200, co.text
    oid = co.json()["order_id"]
    pid = fake.pay(oid, **pay_kw)
    return c.post(
        "/v1/billing/verify", json={"order_id": oid, "payment_id": pid, "signature": sig(oid, pid)}, headers=headers
    )


def plan_of(c: TestClient, headers: dict[str, str] = A) -> tuple[str, datetime | None]:
    e = c.get("/v1/me/entitlements", headers=headers).json()
    end = e["current_period_end"]
    return e["plan_code"], datetime.fromisoformat(end) if end else None


def days_from_now(d: datetime | None) -> float:
    assert d is not None
    return (d - datetime.now(UTC)).total_seconds() / 86400


def db_rows(url: str, sql: str) -> list[tuple[Any, ...]]:
    e = create_engine(url)
    with e.connect() as conn:
        rows = [tuple(r) for r in conn.execute(text(sql))]
    e.dispose()
    return rows


def test_checkout_prices_come_from_the_server(ctx: tuple[TestClient, FakeRazorpay]) -> None:
    c, fake = ctx
    r = c.post("/v1/billing/checkout", json={"plan_code": "pro"}, headers=A)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["amount_paise"] == 99_900 and body["key_id"] == KEY_ID and body["customer_email"] == "alice@example.com"
    assert fake.orders[body["order_id"]]["amount"] == 99_900
    assert 29.9 < days_from_now(datetime.fromisoformat(body["period_end"])) < 30.1
    assert c.post("/v1/billing/checkout", json={"plan_code": "free"}, headers=A).status_code == 400
    assert c.post("/v1/billing/checkout", json={"plan_code": "nope"}, headers=A).status_code == 404
    assert c.post("/v1/billing/checkout", json={"plan_code": "pro", "amount_paise": 1}, headers=A).status_code == 422


def test_payments_not_configured(clean_db: str) -> None:
    app = create_app(Settings(app_env="test", dev_auth=True, database_url=clean_db))
    with TestClient(app) as c:
        assert c.post("/v1/billing/checkout", json={"plan_code": "pro"}, headers=A).status_code == 503


def test_a_paid_order_grants_the_plan_exactly_once(ctx: tuple[TestClient, FakeRazorpay], clean_db: str) -> None:
    c, fake = ctx
    r = buy(c, fake)
    assert r.status_code == 200 and r.json()["outcome"] == "paid", r.text
    plan, end = plan_of(c)
    assert plan == "pro" and 29.9 < days_from_now(end) < 30.1
    oid = next(iter(fake.orders))
    pid = next(iter(fake.payments))
    again = c.post(
        "/v1/billing/verify", json={"order_id": oid, "payment_id": pid, "signature": sig(oid, pid)}, headers=A
    )
    assert again.json()["outcome"] == "already_paid"
    assert plan_of(c) == (plan, end)  # no second extension
    assert db_rows(clean_db, "SELECT count(*) FROM payments") == [(1,)]
    hist = c.get("/v1/billing/payments", headers=A).json()
    assert [(h["plan_code"], h["status"], h["amount_paise"]) for h in hist] == [("pro", "paid", 99_900)]


def test_forged_signature_is_rejected(ctx: tuple[TestClient, FakeRazorpay]) -> None:
    c, fake = ctx
    oid = c.post("/v1/billing/checkout", json={"plan_code": "pro"}, headers=A).json()["order_id"]
    pid = fake.pay(oid)
    r = c.post(
        "/v1/billing/verify", json={"order_id": oid, "payment_id": pid, "signature": sig(oid, pid, "wrong")}, headers=A
    )
    assert r.status_code == 400 and plan_of(c)[0] == "free"


def test_wrong_amount_is_rejected_and_recorded(ctx: tuple[TestClient, FakeRazorpay], clean_db: str) -> None:
    c, fake = ctx
    r = buy(c, fake, amount=100)  # ₹1 paid for a ₹999 plan
    assert r.status_code == 409 and r.json()["error"]["code"] == "payment_rejected"
    assert plan_of(c)[0] == "free"
    assert db_rows(clean_db, "SELECT status, detail->>'reason' FROM billing_orders") == [
        ("created", "payment amount does not match the order")
    ]


def test_authorized_payment_is_captured_then_applied(ctx: tuple[TestClient, FakeRazorpay]) -> None:
    c, fake = ctx
    assert buy(c, fake, status="authorized").json()["outcome"] == "paid"
    assert len(fake.captures) == 1 and plan_of(c)[0] == "pro"


def test_failed_payment_marks_the_order_failed(ctx: tuple[TestClient, FakeRazorpay], clean_db: str) -> None:
    c, fake = ctx
    assert buy(c, fake, status="failed").status_code == 409
    assert db_rows(clean_db, "SELECT status FROM billing_orders") == [("failed",)]
    assert plan_of(c)[0] == "free"


def test_cannot_settle_another_users_order(ctx: tuple[TestClient, FakeRazorpay]) -> None:
    c, fake = ctx
    oid = c.post("/v1/billing/checkout", json={"plan_code": "pro"}, headers=A).json()["order_id"]
    pid = fake.pay(oid)
    r = c.post("/v1/billing/verify", json={"order_id": oid, "payment_id": pid, "signature": sig(oid, pid)}, headers=B)
    assert r.status_code == 404 and plan_of(c, B)[0] == "free" and plan_of(c, A)[0] == "free"


def test_renewing_extends_and_switching_credits(ctx: tuple[TestClient, FakeRazorpay], clean_db: str) -> None:
    c, fake = ctx
    buy(c, fake, "pro")
    buy(c, fake, "pro")
    plan, end = plan_of(c)
    assert plan == "pro" and 59.9 < days_from_now(end) < 60.1  # 30 + 30
    r = buy(c, fake, "pro_plus")
    assert r.json()["outcome"] == "paid"
    plan, end = plan_of(c)
    credit_days = 30 * (99_900 * 2 * 0.999) / 249_900  # ~2 Pro months of credit
    assert plan == "pro_plus" and days_from_now(end) == pytest.approx(30 + credit_days, abs=0.5)
    statuses = db_rows(
        clean_db, "SELECT p.code, s.status FROM subscriptions s JOIN plans p ON p.id = s.plan_id ORDER BY s.created_at"
    )
    assert statuses == [("pro", "expired"), ("pro_plus", "active")]


def webhook(c: TestClient, event: dict[str, Any], event_id: str, secret: str = WEBHOOK_SECRET) -> Any:
    body = json.dumps(event).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return c.post(
        "/v1/webhooks/razorpay",
        content=body,
        headers={"x-razorpay-signature": signature, "x-razorpay-event-id": event_id},
    )


def test_webhook_applies_a_payment_when_the_browser_never_returned(ctx: tuple[TestClient, FakeRazorpay]) -> None:
    c, fake = ctx
    oid = c.post("/v1/billing/checkout", json={"plan_code": "pro"}, headers=A).json()["order_id"]
    pid = fake.pay(oid)
    event = {"event": "payment.captured", "payload": {"payment": {"entity": fake.payments[pid]}}}
    assert webhook(c, event, "evt_1", secret="forged").status_code == 400
    assert plan_of(c)[0] == "free"
    assert webhook(c, event, "evt_1").json() == {"status": "paid"}
    assert webhook(c, event, "evt_1").json() == {"status": "duplicate"}  # Razorpay retry
    assert webhook(c, {**event, "event": "order.paid"}, "evt_2").json() == {"status": "already_paid"}
    later = c.post(
        "/v1/billing/verify", json={"order_id": oid, "payment_id": pid, "signature": sig(oid, pid)}, headers=A
    )
    assert later.json()["outcome"] == "already_paid" and plan_of(c)[0] == "pro"
