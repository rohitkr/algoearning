"""Sending notifications (ADR 0016): the engine queues them in the `notifications` table, this module applies each
user's settings and sends by email (SMTP) and Telegram (Bot API), links Telegram chats, and raises an alert when the
engine stops stepping running strategies."""

from __future__ import annotations

import asyncio
import os
import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

import httpx
import structlog
from ae_core.notifications import enabled_events
from ae_db.models import Notification, NotificationSettings, PlatformSetting, StrategyRun, User
from ae_db.session import Database
from sqlalchemy import select

log = structlog.get_logger("ae_worker.notify")
MAX_ATTEMPTS = 3
TELEGRAM_API = "https://api.telegram.org"


@dataclass
class EmailConfig:
    host: str
    port: int = 587
    user: str | None = None
    password: str | None = None
    sender: str = "AlgoEarning <no-reply@localhost>"
    starttls: bool = True

    @classmethod
    def from_env(cls) -> EmailConfig | None:
        host = os.environ.get("SMTP_HOST")
        if not host:
            return None
        return cls(
            host=host,
            port=int(os.environ.get("SMTP_PORT", "587")),
            user=os.environ.get("SMTP_USER") or None,
            password=os.environ.get("SMTP_PASSWORD") or None,
            sender=os.environ.get("SMTP_FROM", "AlgoEarning <no-reply@localhost>"),
            starttls=os.environ.get("SMTP_STARTTLS", "true").lower() != "false",
        )


class Sender:
    """Email and Telegram in one place; either may be unconfigured (then that channel is reported, not sent)."""

    def __init__(
        self,
        email: EmailConfig | None,
        telegram_token: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
        smtp_factory: Any = smtplib.SMTP,
    ) -> None:
        self.email, self.token = email, telegram_token
        self.transport, self.smtp_factory = transport, smtp_factory

    @classmethod
    def from_env(cls) -> Sender:
        return cls(EmailConfig.from_env(), os.environ.get("TELEGRAM_BOT_TOKEN") or None)

    def send_email(self, to: str, subject: str, body: str) -> None:
        if self.email is None:
            raise RuntimeError("email is not configured (SMTP_HOST)")
        cfg = self.email
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = cfg.sender, to, subject
        msg.set_content(body)
        with self.smtp_factory(cfg.host, cfg.port, timeout=20) as smtp:
            if cfg.starttls:
                smtp.starttls()
            if cfg.user:
                smtp.login(cfg.user, cfg.password or "")
            smtp.send_message(msg)

    async def email_async(self, to: str, subject: str, body: str) -> None:
        await asyncio.to_thread(self.send_email, to, subject, body)

    async def telegram(self, method: str, **params: Any) -> Any:
        if not self.token:
            raise RuntimeError("Telegram is not configured (TELEGRAM_BOT_TOKEN)")
        async with httpx.AsyncClient(transport=self.transport, timeout=30) as c:
            r = await c.post(f"{TELEGRAM_API}/bot{self.token}/{method}", json=params)
        body = r.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram: {body.get('description', r.status_code)}")
        return body["result"]

    async def send_telegram(self, chat_id: str, text: str) -> None:
        await self.telegram("sendMessage", chat_id=chat_id, text=text)


async def dispatch_pending(db: Database, sender: Sender, limit: int = 50) -> int:
    """Send queued notifications according to each user's settings. Returns how many were handled."""
    async with db.system_session() as s:
        pending = list(
            (
                await s.execute(
                    select(Notification)
                    .where(Notification.status == "pending")
                    .order_by(Notification.created_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )
        for n in pending:
            st = (
                await s.execute(select(NotificationSettings).where(NotificationSettings.user_id == n.user_id))
            ).scalar_one_or_none()
            user = await s.get(User, n.user_id)
            wants = enabled_events(st.events if st else None)
            email_on = st.email_enabled if st else True
            tg_on = bool(st and st.telegram_enabled and st.telegram_chat_id)
            if n.event not in wants:
                n.status = "skipped"
                continue
            if user is None or not (email_on or tg_on):
                n.status, n.error = "skipped", "no channel is switched on"
                continue
            errors, via = [], []
            if email_on and sender.email is not None:
                to = (st.email_address if st and st.email_address else user.email) if user else ""
                try:
                    await sender.email_async(to, f"[AlgoEarning] {n.title}", n.body)
                    via.append("email")
                except Exception as exc:
                    errors.append(f"email: {exc}")
            elif email_on and not tg_on:
                errors.append("email is not configured on this server")
            if tg_on and st is not None and st.telegram_chat_id:
                try:
                    await sender.send_telegram(st.telegram_chat_id, f"{n.title}\n{n.body}")
                    via.append("telegram")
                except Exception as exc:
                    errors.append(f"telegram: {exc}")
            n.attempts += 1
            n.sent_via = via
            if via:
                n.status, n.error = "sent", "; ".join(errors) or None
            elif n.attempts >= MAX_ATTEMPTS:
                n.status, n.error = "failed", "; ".join(errors)
            else:
                n.error = "; ".join(errors)
        return len(pending)


OFFSET_KEY = "telegram_offset"


async def link_telegram(db: Database, sender: Sender) -> int:
    """Read messages sent to the bot; `/start <code>` links that chat to the user who generated the code."""
    if not sender.token:
        return 0
    async with db.system_session() as s:
        row = (await s.execute(select(PlatformSetting).where(PlatformSetting.key == OFFSET_KEY))).scalar_one_or_none()
        offset = int(row.value) if row else 0
    updates = await sender.telegram("getUpdates", offset=offset, timeout=0, allowed_updates=["message"])
    linked = 0
    for u in updates:
        offset = max(offset, int(u["update_id"]) + 1)
        msg = u.get("message") or {}
        text = str(msg.get("text", "")).strip()
        chat = str((msg.get("chat") or {}).get("id", ""))
        if not chat or not text.startswith("/start"):
            continue
        code = text.removeprefix("/start").strip()
        async with db.system_session() as s:
            st = (
                (
                    await s.execute(select(NotificationSettings).where(NotificationSettings.telegram_link_code == code))
                ).scalar_one_or_none()
                if code
                else None
            )
            if st is None:
                reply = "That link code is not valid. Press Connect Telegram in AlgoEarning again."
            else:
                st.telegram_chat_id, st.telegram_enabled, st.telegram_link_code = chat, True, None
                reply = "Connected. You will get your AlgoEarning alerts here."
                linked += 1
        try:
            await sender.send_telegram(chat, reply)
        except Exception:
            log.warning("telegram reply failed")
    async with db.system_session() as s:
        row = (await s.execute(select(PlatformSetting).where(PlatformSetting.key == OFFSET_KEY))).scalar_one_or_none()
        if row is None:
            s.add(PlatformSetting(key=OFFSET_KEY, value=offset))
        else:
            row.value = offset
    return linked


async def check_engine(db: Database, now: datetime | None = None, stale: timedelta = timedelta(seconds=90)) -> int:
    """Running strategies the engine has not stepped for a while: tell their users (once an hour per run)."""
    now = now or datetime.now(UTC)
    n = 0
    async with db.system_session() as s:
        runs = (
            await s.execute(
                select(StrategyRun).where(
                    StrategyRun.status == "running",
                    StrategyRun.heartbeat_at.is_not(None),
                    StrategyRun.heartbeat_at < now - stale,
                )
            )
        ).scalars()
        for r in runs:
            recent = (
                await s.execute(
                    select(Notification.id).where(
                        Notification.run_id == r.id,
                        Notification.event == "engine_down",
                        Notification.created_at > now - timedelta(hours=1),
                    )
                )
            ).first()
            if recent:
                continue
            s.add(
                Notification(
                    user_id=r.user_id,
                    event="engine_down",
                    title=f"Engine not responding: {r.strategy_name}",
                    body=f"{r.strategy_name} has not been checked since {r.heartbeat_at:%H:%M:%S}. "
                    "Open positions are not being watched: check the app or your broker.",
                    run_id=r.id,
                )
            )
            n += 1
    return n
