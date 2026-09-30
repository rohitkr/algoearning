"""Buying a prepaid plan period.

    1. POST /v1/billing/checkout   {plan_code}                         -> Razorpay order (amount from the plan)
    2. browser: Razorpay Checkout with that order_id
    3. POST /v1/billing/verify     {order_id, payment_id, signature}   -> plan time applied
The Razorpay webhook (/v1/webhooks/razorpay) applies the same payment if step 3 never arrives (tab closed)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from ae_core.billing import RunningPlan, next_period
from ae_core.entitlements import SubscriptionView, effective_subscription
from ae_db.enums import BillingOrderStatus
from ae_db.models import BillingOrder, Subscription
from ae_db.repositories import PlanRepo, UsageRepo, UserRepo
from fastapi import APIRouter, Request
from sqlalchemy import select

from ..audit import audit
from ..billing.razorpay import RazorpayClient, RazorpayError
from ..billing.service import settle
from ..deps import CurrentUser, DbDep, UserSession
from ..errors import AppError, NotFound, Unavailable
from ..schemas import ERROR_RESPONSES, CheckoutIn, CheckoutOut, PaymentOut, PurchaseOut, VerifyIn
from ..settings import SettingsDep

router = APIRouter(prefix="/v1/billing", tags=["billing"], responses=ERROR_RESPONSES)


class PaymentRejectedError(AppError):
    status, code = 409, "payment_rejected"


def provider(request: Request) -> RazorpayClient:
    client: RazorpayClient | None = getattr(request.app.state, "payments", None)
    if client is None:
        raise Unavailable("payments are not configured")
    return client


@router.post("/checkout", response_model=CheckoutOut)
async def checkout(
    body: CheckoutIn, user: CurrentUser, s: UserSession, request: Request, settings: SettingsDep
) -> CheckoutOut:
    client = provider(request)
    plan = await PlanRepo(s).by_code(body.plan_code)
    if plan is None or not plan.is_active:
        raise NotFound("plan not found")
    if plan.price_paise < 100:
        raise AppError("this plan cannot be bought")
    me = await UserRepo(s).get(user.user_id)
    subs = await UsageRepo(s, user.user_id).subscriptions()
    now = datetime.now(UTC)
    best = effective_subscription(
        [SubscriptionView(x.plan.code, x.plan.sort_order, x.status.value, x.current_period_end) for x in subs],
        now,
        timedelta(days=settings.subscription_grace_days),
    )
    running = next(
        (
            RunningPlan(x.plan.code, x.plan.price_paise, x.plan.interval.value, x.current_period_end)
            for x in subs
            if best
            and x.plan.code == best.plan_code
            and x.current_period_end
            and x.current_period_end == best.current_period_end
        ),
        None,
    )
    preview = next_period(now, plan.code, plan.price_paise, plan.interval.value, running)
    receipt = f"ae-{uuid.uuid4().hex[:16]}"
    try:
        order = await client.create_order(
            plan.price_paise, plan.currency, receipt, {"user_id": str(user.user_id), "plan_code": plan.code}
        )
    except RazorpayError as exc:
        raise Unavailable(f"could not start the payment ({exc.status})") from exc
    s.add(
        BillingOrder(
            user_id=user.user_id,
            plan_id=plan.id,
            provider="razorpay",
            provider_order_id=order["id"],
            amount_paise=plan.price_paise,
            currency=plan.currency,
            status=BillingOrderStatus.CREATED,
            detail={"receipt": receipt},
        )
    )
    await s.flush()
    await audit(
        s,
        request,
        "billing.checkout",
        user.user_id,
        "plan",
        plan.code,
        order_id=order["id"],
        amount_paise=plan.price_paise,
    )
    return CheckoutOut(
        provider="razorpay",
        key_id=client.key_id,
        order_id=order["id"],
        amount_paise=plan.price_paise,
        currency=plan.currency,
        plan_code=plan.code,
        plan_name=plan.name,
        customer_name=me.name if me else None,
        customer_email=user.email,
        period_end=preview.end,
        credit_paise=preview.credit_paise,
    )


@router.post("/verify", response_model=PurchaseOut)
async def verify(
    body: VerifyIn, user: CurrentUser, s: UserSession, db: DbDep, request: Request, settings: SettingsDep
) -> PurchaseOut:
    client = provider(request)
    mine = await s.execute(select(BillingOrder.id).where(BillingOrder.provider_order_id == body.order_id))
    if mine.first() is None:  # RLS: another user's order is simply not found
        raise NotFound("order not found")
    if not client.checkout_signature_ok(body.order_id, body.payment_id, body.signature):
        raise AppError("invalid payment signature")
    try:
        async with db.system_session() as sys_s:
            result = await settle(
                sys_s, client, body.order_id, body.payment_id, timedelta(days=settings.subscription_grace_days)
            )
            period_end = None
            if result.order is not None and result.order.subscription_id is not None:
                sub = await sys_s.get(Subscription, result.order.subscription_id)
                period_end = sub.current_period_end if sub else None
    except RazorpayError as exc:
        raise Unavailable(f"could not confirm the payment with Razorpay ({exc.status}); it will be retried") from exc
    if not result.ok or result.order is None:
        raise PaymentRejectedError(result.reason or "payment was not accepted")
    await audit(
        s,
        request,
        "billing.paid" if result.outcome == "paid" else "billing.verify_repeat",
        user.user_id,
        "plan",
        result.order.plan.code,
        order_id=body.order_id,
        payment_id=body.payment_id,
    )
    outcome: Literal["paid", "already_paid"] = "paid" if result.outcome == "paid" else "already_paid"
    return PurchaseOut(outcome=outcome, plan_code=result.order.plan.code, period_end=period_end)


@router.get("/payments", response_model=list[PaymentOut])
async def payments(user: CurrentUser, s: UserSession) -> list[PaymentOut]:
    rows = (
        await s.execute(
            select(BillingOrder)
            .where(BillingOrder.user_id == user.user_id, BillingOrder.status != BillingOrderStatus.CREATED)
            .order_by(BillingOrder.created_at.desc())
            .limit(100)
        )
    ).scalars()
    return [
        PaymentOut(
            order_id=o.provider_order_id,
            payment_id=o.provider_payment_id,
            plan_code=o.plan.code,
            plan_name=o.plan.name,
            amount_paise=o.amount_paise,
            currency=o.currency,
            status=o.status.value,
            paid_at=o.paid_at,
            created_at=o.created_at,
        )
        for o in rows
    ]
