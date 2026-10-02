"""The platform's daily price-feed session: an admin logs in to the provider (ICICI Breeze, or the platform's own
Kite account) once a trading day from Monitor > Market data; the token is stored encrypted in platform_secrets and
the feed picks it up within seconds."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time, timedelta
from urllib.parse import quote

from ae_core.secrets import SecretBox
from ae_db.models import PlatformSecret
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .types import IST

BREEZE_SESSION = "breeze_session"
KITE_SESSION = "kite_feed_session"  # the platform's Kite access token (not any user's broker account)
KITE_ACCOUNT = "kite_feed_account"  # the Zerodha client ID that session belongs to (shown in Monitor)
KITE_LOGIN_URL = "https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
BREEZE_LOGIN_URL = "https://api.icicidirect.com/apiuser/login?api_key={api_key}"


def breeze_login_url(api_key: str) -> str:
    return BREEZE_LOGIN_URL.format(api_key=quote(api_key, safe=""))


def kite_login_url(api_key: str) -> str:
    return KITE_LOGIN_URL.format(api_key=quote(api_key, safe=""))


def session_expiry(now: datetime) -> datetime:
    """Breeze session tokens are good for the day they are issued (IST)."""
    local = now.astimezone(IST)
    return datetime.combine(local.date() + timedelta(days=1), time(0, 0), tzinfo=IST)


def _context(name: str) -> str:
    return f"platform_secret:{name}"


async def save_session(
    s: AsyncSession, box: SecretBox, name: str, token: str, expires_at: datetime, by: uuid.UUID | None
) -> datetime:
    row = (await s.execute(select(PlatformSecret).where(PlatformSecret.name == name))).scalar_one_or_none()
    if row is None:
        row = PlatformSecret(name=name)
        s.add(row)
    row.value_enc = box.encrypt(token.strip(), _context(name))
    row.expires_at = expires_at
    row.updated_by = by
    await s.flush()
    return expires_at


async def load_session(
    s: AsyncSession, box: SecretBox, name: str, now: datetime | None = None
) -> tuple[str | None, datetime | None]:
    """(token, expires_at); token is None when there is none or it has expired."""
    now = now or datetime.now(UTC)
    row = (await s.execute(select(PlatformSecret).where(PlatformSecret.name == name))).scalar_one_or_none()
    if row is None:
        return None, None
    if row.expires_at is not None and row.expires_at <= now:
        return None, row.expires_at
    return box.decrypt(row.value_enc, _context(name)), row.expires_at


async def save_breeze_session(
    s: AsyncSession, box: SecretBox, token: str, by: uuid.UUID | None, now: datetime | None = None
) -> datetime:
    return await save_session(s, box, BREEZE_SESSION, token, session_expiry(now or datetime.now(UTC)), by)


async def breeze_session(
    s: AsyncSession, box: SecretBox, now: datetime | None = None
) -> tuple[str | None, datetime | None]:
    return await load_session(s, box, BREEZE_SESSION, now)
