"""Clerk -> us: keep the users table in step with sign-ups, profile edits and deletions.

Each event is verified (Svix signature + timestamp), stored once under its event id (a replay or retry is
acknowledged without being applied twice) and processed in the same transaction."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from ae_db.models import WebhookEvent
from ae_db.repositories import UserRepo
from fastapi import APIRouter, Request
from sqlalchemy import select

from ..auth.clerk import identity_from_clerk_user
from ..auth.webhooks import InvalidSignature, verify_svix
from ..billing.razorpay import RazorpayError, webhook_signature_ok
from ..billing.service import settle
from ..deps import DbDep
from ..errors import AppError, Unavailable
from ..schemas import ERROR_RESPONSES
from ..settings import SettingsDep

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"], responses=ERROR_RESPONSES)
log = structlog.get_logger("ae_api.webhooks")


@router.post("/clerk")
async def clerk_webhook(request: Request, settings: SettingsDep, db: DbDep) -> dict[str, str]:
    if not settings.clerk_webhook_secret:
        raise Unavailable("Clerk webhook secret is not configured")
    body = await request.body()
    try:
        event_id = verify_svix(settings.clerk_webhook_secret, {k.lower(): v for k, v in request.headers.items()}, body)
    except InvalidSignature as exc:
        log.warning("rejected clerk webhook", reason=str(exc))
        raise AppError("invalid webhook signature") from exc
    event: dict[str, Any] = json.loads(body)
    etype, data = str(event.get("type", "")), event.get("data") or {}
    async with db.system_session() as s:
        seen = await s.execute(
            select(WebhookEvent.id).where(WebhookEvent.provider == "clerk", WebhookEvent.event_id == event_id)
        )
        if seen.first() is not None:
            return {"status": "duplicate"}
        row = WebhookEvent(provider="clerk", event_id=event_id, event_type=etype, payload=event)
        s.add(row)
        users = UserRepo(s)
        if etype in ("user.created", "user.updated"):
            ident = identity_from_clerk_user(data)
            if ident.email:
                await users.upsert_from_auth(ident.subject, ident.email, ident.name, ident.avatar_url)
        elif etype == "user.deleted" and data.get("id"):
            await users.mark_deleted(str(data["id"]))
        row.processed_at = datetime.now(UTC)
    log.info("clerk webhook", type=etype, event_id=event_id)
    return {"status": "ok"}


PAYMENT_EVENTS = ("payment.captured", "order.paid", "payment.failed", "payment.authorized")


@router.post("/razorpay")
async def razorpay_webhook(request: Request, settings: SettingsDep, db: DbDep) -> dict[str, str]:
    """Backup path for purchases: applies a payment even if the buyer closed the tab before /v1/billing/verify.
    Signed with the webhook secret; each event id is stored once, so Razorpay's retries are harmless."""
    client = getattr(request.app.state, "payments", None)
    if not settings.razorpay_webhook_secret or client is None:
        raise Unavailable("Razorpay webhooks are not configured")
    body = await request.body()
    if not webhook_signature_ok(
        settings.razorpay_webhook_secret, body, request.headers.get("x-razorpay-signature", "")
    ):
        log.warning("rejected razorpay webhook", reason="bad signature")
        raise AppError("invalid webhook signature")
    event: dict[str, Any] = json.loads(body)
    etype = str(event.get("event", ""))
    event_id = request.headers.get("x-razorpay-event-id") or f"{etype}:{event.get('created_at')}"
    payment = ((event.get("payload") or {}).get("payment") or {}).get("entity") or {}
    async with db.system_session() as s:
        seen = await s.execute(
            select(WebhookEvent.id).where(WebhookEvent.provider == "razorpay", WebhookEvent.event_id == event_id)
        )
        if seen.first() is not None:
            return {"status": "duplicate"}
        row = WebhookEvent(provider="razorpay", event_id=event_id, event_type=etype, payload=event)
        s.add(row)
        await s.flush()
        outcome = "ignored"
        if etype in PAYMENT_EVENTS and payment.get("order_id") and payment.get("id"):
            try:
                result = await settle(
                    s,
                    client,
                    str(payment["order_id"]),
                    str(payment["id"]),
                    timedelta(days=settings.subscription_grace_days),
                )
                outcome = result.outcome
            except RazorpayError as exc:  # Razorpay unreachable: 5xx makes Razorpay retry the webhook
                raise Unavailable(f"could not confirm the payment ({exc.status})") from exc
        row.processed_at = datetime.now(UTC)
    log.info("razorpay webhook", type=etype, event_id=event_id, outcome=outcome)
    return {"status": outcome}
