"""A user's notification settings (channels and events), Telegram linking, a test message and their history."""

from __future__ import annotations

import secrets

from ae_core.notifications import EVENTS, enabled_events
from ae_db.models import Notification, NotificationSettings
from fastapi import APIRouter, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import audit
from ..deps import CurrentUser, UserSession
from ..errors import AppError
from ..schemas import (
    ERROR_RESPONSES,
    NotificationEventInfo,
    NotificationOut,
    NotificationSettingsIn,
    NotificationSettingsOut,
    TelegramLink,
)
from ..settings import Settings, SettingsDep

router = APIRouter(prefix="/v1/me/notifications", tags=["notifications"], responses=ERROR_RESPONSES)


async def _row(s: AsyncSession, user_id: object) -> NotificationSettings | None:
    return (await s.execute(select(NotificationSettings))).scalar_one_or_none()


def _out(st: NotificationSettings | None, email: str, cfg: Settings) -> NotificationSettingsOut:
    return NotificationSettingsOut(
        email_enabled=st.email_enabled if st else True,
        email_address=st.email_address if st else None,
        account_email=email,
        telegram_enabled=bool(st and st.telegram_enabled and st.telegram_chat_id),
        telegram_connected=bool(st and st.telegram_chat_id),
        events=sorted(enabled_events(st.events if st else None)),
        catalog=[
            NotificationEventInfo(key=e.key, label=e.label, hint=e.hint, default=e.default) for e in EVENTS.values()
        ],
        email_available=bool(cfg.smtp_host),
        telegram_available=bool(cfg.telegram_bot_token),
    )


@router.get("", response_model=NotificationSettingsOut)
async def get_settings(user: CurrentUser, s: UserSession, cfg: SettingsDep) -> NotificationSettingsOut:
    return _out(await _row(s, user.user_id), user.email, cfg)


@router.put("", response_model=NotificationSettingsOut)
async def put_settings(
    body: NotificationSettingsIn, user: CurrentUser, s: UserSession, cfg: SettingsDep, request: Request
) -> NotificationSettingsOut:
    unknown = set(body.events) - EVENTS.keys()
    if unknown:
        raise AppError(f"unknown events: {sorted(unknown)}")
    st = await _row(s, user.user_id)
    if st is None:
        st = NotificationSettings(user_id=user.user_id)
        s.add(st)
    if body.telegram_enabled and not st.telegram_chat_id:
        raise AppError("connect Telegram first: press Connect Telegram and send /start to the bot")
    st.email_enabled, st.email_address = body.email_enabled, body.email_address or None
    st.telegram_enabled, st.events = body.telegram_enabled, sorted(set(body.events))
    await s.flush()
    await audit(s, request, "notifications.update", user.user_id, "notification_settings", user.user_id,
                email=body.email_enabled, telegram=body.telegram_enabled, events=len(body.events))  # fmt: skip
    return _out(st, user.email, cfg)


@router.post("/telegram/link", response_model=TelegramLink)
async def telegram_link(user: CurrentUser, s: UserSession, cfg: SettingsDep) -> TelegramLink:
    """A one-time code: sending `/start <code>` to the bot links that Telegram chat to this account."""
    if not cfg.telegram_bot_token:
        raise AppError("Telegram is not set up on this server (TELEGRAM_BOT_TOKEN)")
    st = await _row(s, user.user_id)
    if st is None:
        st = NotificationSettings(user_id=user.user_id)
        s.add(st)
    st.telegram_link_code = secrets.token_urlsafe(9)
    await s.flush()
    bot = cfg.telegram_bot_username
    return TelegramLink(
        url=f"https://t.me/{bot}?start={st.telegram_link_code}" if bot else None,
        code=st.telegram_link_code,
        bot_username=bot,
    )


@router.delete("/telegram", response_model=NotificationSettingsOut)
async def telegram_unlink(user: CurrentUser, s: UserSession, cfg: SettingsDep) -> NotificationSettingsOut:
    st = await _row(s, user.user_id)
    if st is not None:
        st.telegram_chat_id, st.telegram_enabled, st.telegram_link_code = None, False, None
        await s.flush()
    return _out(st, user.email, cfg)


@router.post("/test", response_model=NotificationOut, status_code=201)
async def send_test(user: CurrentUser, s: UserSession) -> NotificationOut:
    """Queue a test message through the user's current settings (the worker sends it within seconds)."""
    st = await _row(s, user.user_id)
    n = Notification(user_id=user.user_id, event="order_problem", title="Test notification",
                     body="If you can read this, alerts reach you on the channels you switched on.")  # fmt: skip
    if st is not None and st.events is not None and "order_problem" not in st.events:
        n.event = next(iter(st.events), "order_problem")
    s.add(n)
    await s.flush()
    await s.refresh(n)
    return NotificationOut.model_validate(n)


@router.get("/history", response_model=list[NotificationOut])
async def history(user: CurrentUser, s: UserSession, limit: int = Query(30, ge=1, le=100)) -> list[NotificationOut]:
    q = select(Notification).order_by(Notification.created_at.desc()).limit(limit)
    return [NotificationOut.model_validate(n) for n in (await s.execute(q)).scalars()]
