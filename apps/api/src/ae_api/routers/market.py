"""Prices for the app (from the platform feed, ADR 0013), and the feed's admin side in Monitor."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from ae_brokers.base import BrokerError, Credentials
from ae_core.secrets import SecretBox
from ae_db.models import Instrument
from ae_marketdata.hub import Hub
from ae_marketdata.session import (
    BREEZE_SESSION,
    KITE_SESSION,
    breeze_login_url,
    kite_login_url,
    load_session,
    save_breeze_session,
    save_session,
)
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
    FeedLogin,
    KiteSessionIn,
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
        providers: list[tuple[Literal["breeze", "kite"], str, str | None, Callable[[str], str], str]] = [
            ("breeze", "ICICI Breeze", settings.breeze_api_key, breeze_login_url, BREEZE_SESSION),
            ("kite", "Kite (platform account)", settings.kite_feed_api_key, kite_login_url, KITE_SESSION),
        ]
        logins = []
        for provider, name, key, url, secret in providers:
            if key:
                expires = (await load_session(s, box, secret))[1] if box is not None else None
                logins.append(FeedLogin(provider=provider, name=name, login_url=url(key), session_expires_at=expires))
    last = await hub.last(codes)
    today = datetime.now(IST).date()
    return MarketDataAdmin(
        source=health.get("source"),
        connected=bool(health.get("connected")),
        session=health.get("session"),
        logins=logins,
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


@router.put("/v1/admin/market-data/kite-session", response_model=MarketDataAdmin)
async def set_kite_session(
    body: KiteSessionIn, admin: Admin, request: Request, db: DbDep, settings: SettingsDep
) -> MarketDataAdmin:
    """Turn the platform Kite login's request token (from Kite's redirect) into the day's session; the feed
    reconnects with it when it runs on Kite."""
    box = _box(request)
    if not (settings.kite_feed_api_key and settings.kite_feed_api_secret):
        raise AppError("the platform Kite app is not set up: KITE_FEED_API_KEY and KITE_FEED_API_SECRET")
    creds = Credentials(settings.kite_feed_api_key, settings.kite_feed_api_secret)
    try:
        session = await request.app.state.brokers["zerodha"].exchange(
            creds, {"status": "success", "request_token": body.request_token.strip()}
        )
    except BrokerError as exc:
        raise AppError(f"Kite login failed: {exc.message}") from exc
    async with db.system_session() as s:
        expires = await save_session(s, box, KITE_SESSION, session.access_token, session.expires_at, admin.user_id)
        await audit(
            s, request, "admin.market_data.kite_session", admin.user_id, "platform_secret", KITE_SESSION,
            actor="admin", expires_at=expires.isoformat(), kite_user=session.client_id,
        )  # fmt: skip
    return await market_data_admin(admin, request, db, settings)
