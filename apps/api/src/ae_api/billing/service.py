"""Settling a prepaid purchase, idempotently.

settle() is the ONE path that turns money into plan time. The browser's checkout callback, the Razorpay webhook
and the reconciliation command all call it, in any order, any number of times: the order row is locked
(SELECT ... FOR UPDATE) and a paid order is never applied again. A payment counts only if Razorpay itself
(fetched server-side, not the browser's word) says it belongs to this order, is for exactly the order's amount
and currency, and is captured (an authorized-only payment is captured first)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog
from ae_core.billing import RunningPlan, next_period
from ae_core.entitlements import SubscriptionView, effective_subscription
from ae_db.enums import BillingKind, BillingOrderStatus, SubscriptionStatus
from ae_db.models import BillingOrder, Payment, Subscription
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .razorpay import RazorpayClient

log = structlog.get_logger("ae_api.billing")


@dataclass(frozen=True)
class Settled:
    outcome: Literal["paid", "already_paid", "rejected", "not_captured", "unknown_order"]
    order: BillingOrder | None = None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome in ("paid", "already_paid")


async def settle(
    s: AsyncSession,
    client: RazorpayClient,
    provider_order_id: str,
    payment_id: str,
    grace: timedelta,
    now: datetime | None = None,
) -> Settled:
    """Must run in a system session (it writes the user's subscription and payment)."""
    order = (
        await s.execute(
            select(BillingOrder)
            .where(BillingOrder.provider_order_id == provider_order_id)
            .with_for_update(of=BillingOrder)
        )
    ).scalar_one_or_none()
    if order is None:
        return Settled("unknown_order", reason="unknown order")
    if order.status == BillingOrderStatus.PAID:
        return Settled("already_paid", order)

    payment = await client.fetch_payment(payment_id)
    problem = _mismatch(order, payment)
    if problem:  # recorded (the transaction commits), never applied
        order.detail = {**order.detail, "rejected_payment": payment_id, "reason": problem}
        log.warning("payment rejected", order=provider_order_id, payment=payment_id, reason=problem)
        return Settled("rejected", order, problem)
    if payment.get("status") == "authorized":
        payment = await client.capture(payment_id, order.amount_paise, order.currency)
    if payment.get("status") != "captured":
        if payment.get("status") == "failed":
            order.status = BillingOrderStatus.FAILED
            order.detail = {
                **order.detail,
                "failed_payment": payment_id,
                "reason": payment.get("error_description") or "payment failed",
            }
        return Settled("not_captured", order, f"payment is {payment.get('status')}")

    now = now or datetime.now(UTC)
    sub, detail = await _extend_plan(s, order, now, grace)
    s.add(
        Payment(
            user_id=order.user_id,
            subscription_id=sub.id,
            provider="razorpay",
            provider_payment_id=payment_id,
            amount_paise=int(payment["amount"]),
            currency=str(payment["currency"]),
            status="captured",
            raw={
                k: payment.get(k)
                for k in ("id", "order_id", "method", "amount", "currency", "status", "created_at", "email", "contact")
            },
        )
    )
    order.status, order.provider_payment_id, order.paid_at = BillingOrderStatus.PAID, payment_id, now
    order.subscription_id, order.detail = sub.id, {**order.detail, **detail}
    await s.flush()
    log.info(
        "plan purchased",
        user_id=str(order.user_id),
        plan=order.plan.code,
        order=provider_order_id,
        until=sub.current_period_end.isoformat() if sub.current_period_end else None,
    )
    return Settled("paid", order)


def _mismatch(order: BillingOrder, payment: dict[str, Any]) -> str | None:
    if payment.get("order_id") != order.provider_order_id:
        return "payment belongs to a different order"
    if int(payment.get("amount") or -1) != order.amount_paise or payment.get("currency") != order.currency:
        return "payment amount does not match the order"
    return None


async def _extend_plan(
    s: AsyncSession, order: BillingOrder, now: datetime, grace: timedelta
) -> tuple[Subscription, dict[str, Any]]:
    subs = list(
        (
            await s.execute(
                select(Subscription).where(Subscription.user_id == order.user_id).with_for_update(of=Subscription)
            )
        ).scalars()
    )
    views = {
        id(x): SubscriptionView(x.plan.code, x.plan.sort_order, x.status.value, x.current_period_end) for x in subs
    }
    best = effective_subscription(views.values(), now, grace)
    current = next((x for x in subs if views[id(x)] is best), None) if best else None
    running = (
        RunningPlan(
            current.plan.code, current.plan.price_paise, current.plan.interval.value, current.current_period_end
        )
        if current is not None and current.current_period_end is not None
        else None
    )
    plan = order.plan
    p = next_period(now, plan.code, order.amount_paise, plan.interval.value, running)
    detail: dict[str, Any] = {"period_start": p.start.isoformat(), "period_end": p.end.isoformat()}
    if p.extends_current and current is not None:
        current.current_period_end = p.end
        current.status = SubscriptionStatus.ACTIVE
        return current, detail
    if current is not None:  # switching plans: the old one ends now, its value is credited
        current.status, current.current_period_end = SubscriptionStatus.EXPIRED, now
        detail |= {
            "previous_plan": current.plan.code,
            "credit_paise": p.credit_paise,
            "credit_days": round(p.credit_seconds / 86400, 2),
        }
    sub = Subscription(
        user_id=order.user_id,
        plan_id=plan.id,
        status=SubscriptionStatus.ACTIVE,
        provider="razorpay",
        kind=BillingKind.PREPAID,
        current_period_start=p.start,
        current_period_end=p.end,
        cancel_at_period_end=False,
    )
    s.add(sub)
    await s.flush()
    return sub, detail
