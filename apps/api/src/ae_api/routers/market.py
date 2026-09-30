"""Prices for the app (from the platform feed, ADR 0013), and the feed's admin side in Monitor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from ae_core.secrets import SecretBox
from ae_db.models import Instrument
from ae_marketdata.hub import Hub
from ae_marketdata.session import breeze_login_url, breeze_session, save_breeze_session
from ae_marketdata.types import IST, InstrumentKey
from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from ..audit import audit
from ..deps import CurrentUser, DbDep
from ..errors import AppError, Unavailable
from ..schemas import (
    ERROR_RESPONSES,
    BreezeSessionIn,
    FeedInstrument,
    MarketDataAdmin,
    MarketSnapshot,
    Quote,
)
from ..settings import SettingsDep
from .admin import Admin

router = APIRouter(tags=["market"], responses=ERROR_RESPONSES)
STALE = timedelta(minutes=2)


def _hub(request: Request) -> Hub:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise Unavailable("market data needs REDIS_URL")
    return Hub(redis)


def _box(request: Request) -> SecretBox:
    box: SecretBox | None = getattr(request.app.state, "secretbox", None)
    if box is None:
        raise Unavailable("APP_ENCRYPTION_KEY is not configured")
    return box


def _dt(v: Any) -> datetime | None:
    return datetime.fromisoformat(v) if isinstance(v, str) else None


@router.get("/v1/market/snapshot", response_model=MarketSnapshot)
async def snapshot(
    _: CurrentUser,
    request: Request,
    db: DbDep,
    keys: Annotated[list[str] | None, Query(max_length=50, description="Instrument keys; default: every index")] = None,
) -> MarketSnapshot:
    hub = _hub(request)
    if keys:
        for k in keys:
            try:
                InstrumentKey.parse(k)
            except ValueError as exc:
                raise AppError(str(exc)) from exc
        await hub.want(keys)  # asking for a price keeps it streaming for a few minutes
    else:
        async with db.system_session() as s:
            keys = list(
                (
                    await s.execute(
                        select(Instrument.code).where(Instrument.is_active.is_(True)).order_by(Instrument.code)
                    )
                )
                .scalars()
                .all()
            )
    last = await hub.last(keys)
    health = await hub.health()
    now = datetime.now(UTC)
    fresh = any(now - t.ts < STALE for t in last.values())
    status: Literal["live", "simulated", "down"] = (
        "down" if not fresh else "simulated" if health.get("simulated") else "live"
    )
    return MarketSnapshot(
        quotes=[Quote(key=k, ltp=t.ltp, prev_close=t.prev_close, ts=t.ts) for k, t in last.items()],
        feed_status=status,
    )


@router.get("/v1/admin/market-data", response_model=MarketDataAdmin)
async def market_data_admin(_: Admin, request: Request, db: DbDep, settings: SettingsDep) -> MarketDataAdmin:
    hub = _hub(request)
    health = await hub.health()
    box = getattr(request.app.state, "secretbox", None)
    async with db.system_session() as s:
        codes = (
            (await s.execute(select(Instrument.code).where(Instrument.is_active.is_(True)).order_by(Instrument.code)))
            .scalars()
            .all()
        )
        expires = (await breeze_session(s, box))[1] if box is not None else None
    last = await hub.last(codes)
    today = datetime.now(IST).date()
    return MarketDataAdmin(
        source=health.get("source"),
        connected=bool(health.get("connected")),
        session=health.get("session"),
        session_expires_at=expires,
        login_url=breeze_login_url(settings.breeze_api_key) if settings.breeze_api_key else None,
        error=health.get("error"),
        wanted=int(health.get("wanted") or 0),
        subscribed=int(health.get("subscribed") or 0),
        api_calls_today=await hub.api_calls_today(),
        last_event=_dt(health.get("last_event")),
        updated_at=_dt(health.get("updated_at")),
        instruments=[
            FeedInstrument(
                code=c,
                ltp=last[c].ltp if c in last else None,
                ts=last[c].ts if c in last else None,
                bars_today=len(await hub.bars(c, today)),
            )
            for c in codes
        ],
    )


@router.put("/v1/admin/market-data/breeze-session", response_model=MarketDataAdmin)
async def set_breeze_session(
    body: BreezeSessionIn, admin: Admin, request: Request, db: DbDep, settings: SettingsDep
) -> MarketDataAdmin:
    """Store today's Breeze session token (from ICICI's login redirect); the feed reconnects with it."""
    box = _box(request)
    token = body.session_token.strip()
    async with db.system_session() as s:
        expires = await save_breeze_session(s, box, token, admin.user_id)
        await audit(
            s, request, "admin.market_data.breeze_session", admin.user_id, "platform_secret", "breeze_session",
            actor="admin", expires_at=expires.isoformat(),
        )  # fmt: skip
    return await market_data_admin(admin, request, db, settings)
