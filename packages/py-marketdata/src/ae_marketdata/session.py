"""The platform's daily Breeze session: an admin logs in to ICICI once a trading day (Monitor > Market data); the
session token is stored encrypted in platform_secrets and the feed picks it up within seconds."""

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
BREEZE_LOGIN_URL = "https://api.icicidirect.com/apiuser/login?api_key={api_key}"


def breeze_login_url(api_key: str) -> str:
    return BREEZE_LOGIN_URL.format(api_key=quote(api_key, safe=""))


def session_expiry(now: datetime) -> datetime:
    """Breeze session tokens are good for the day they are issued (IST)."""
    local = now.astimezone(IST)
    return datetime.combine(local.date() + timedelta(days=1), time(0, 0), tzinfo=IST)


def _context(name: str) -> str:
    return f"platform_secret:{name}"


async def save_breeze_session(
    s: AsyncSession, box: SecretBox, token: str, by: uuid.UUID | None, now: datetime | None = None
) -> datetime:
    now = now or datetime.now(UTC)
    row = (await s.execute(select(PlatformSecret).where(PlatformSecret.name == BREEZE_SESSION))).scalar_one_or_none()
    if row is None:
        row = PlatformSecret(name=BREEZE_SESSION)
        s.add(row)
    row.value_enc = box.encrypt(token.strip(), _context(BREEZE_SESSION))
    row.expires_at = session_expiry(now)
    row.updated_by = by
    await s.flush()
    return row.expires_at


async def breeze_session(
    s: AsyncSession, box: SecretBox, now: datetime | None = None
) -> tuple[str | None, datetime | None]:
    """(token, expires_at); token is None when there is none or it has expired."""
    now = now or datetime.now(UTC)
    row = (await s.execute(select(PlatformSecret).where(PlatformSecret.name == BREEZE_SESSION))).scalar_one_or_none()
    if row is None:
        return None, None
    if row.expires_at is not None and row.expires_at <= now:
        return None, row.expires_at
    return box.decrypt(row.value_enc, _context(BREEZE_SESSION)), row.expires_at
