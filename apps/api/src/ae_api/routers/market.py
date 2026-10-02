"""Prices for the app (from the platform feed, ADR 0013), and the feed's admin side in Monitor."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import parse_qs, urlsplit

from ae_brokers.base import BrokerError, BrokerSession, Credentials
from ae_brokers.zerodha import next_token_expiry
from ae_core.secrets import SecretBox
from ae_db.models import Instrument
from ae_marketdata.hub import Hub
from ae_marketdata.session import (
    BREEZE_SESSION,
    KITE_ACCOUNT,
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


def _token(raw: str, param: str) -> str:
    """The token itself, also when the whole address the login landed on was pasted (…?param=<token>&…)."""
    raw = raw.strip()
    if "=" in raw:
        found = parse_qs(urlsplit(raw).query or raw.split("?", 1)[-1]).get(param)
        if found:
            return found[0].strip()
    return raw


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
                account = (await load_session(s, box, KITE_ACCOUNT))[0] if provider == "kite" and box else None
                logins.append(
                    FeedLogin(
                        provider=provider,
                        name=name,
                        login_url=url(key),
                        session_expires_at=expires,
                        account=account,
                        expected_account=settings.kite_feed_client_id.upper()
                        if provider == "kite" and settings.kite_feed_client_id
                        else None,
                    )
                )
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
    token = _token(body.session_token, "apisession")
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
    """Store the platform Kite app's session for the day: from a login's request token (Kite's redirect), or an
    access token another program already got from the same Kite app. The feed reconnects with it when it runs on
    Kite."""
    box = _box(request)
    if not (settings.kite_feed_api_key and settings.kite_feed_api_secret):
        raise AppError("the platform Kite app is not set up: KITE_FEED_API_KEY and KITE_FEED_API_SECRET")
    creds = Credentials(settings.kite_feed_api_key, settings.kite_feed_api_secret)
    adapter = request.app.state.brokers["zerodha"]
    if body.access_token is not None:
        # a ready-made session (e.g. from another program on the same Kite app): checked with Kite before keeping it
        access = body.access_token.strip()
        try:
            client_id = str((await adapter.profile(creds, access)).get("user_id") or "").upper()
        except BrokerError as exc:
            raise AppError(
                f"Kite refused the access token: {exc.message}. It must come from today's login to the same Kite "
                "app (KITE_FEED_API_KEY)."
            ) from exc
        session = BrokerSession(access, client_id, next_token_expiry(datetime.now(UTC)))
    else:
        try:
            session = await adapter.exchange(
                creds, {"status": "success", "request_token": _token(body.request_token or "", "request_token")}
            )
        except BrokerError as exc:
            raise AppError(
                f"Kite login failed: {exc.message}. A request token works once, for a few minutes: log in to "
                "Kite again and use the new one."
            ) from exc
    expected = (settings.kite_feed_client_id or "").strip().upper()
    if expected and session.client_id != expected:
        raise AppError(
            f"Kite logged in {session.client_id}, but the platform's Kite account is {expected} "
            "(KITE_FEED_CLIENT_ID): log out of Kite and log in with that account"
        )
    async with db.system_session() as s:
        expires = await save_session(s, box, KITE_SESSION, session.access_token, session.expires_at, admin.user_id)
        await save_session(s, box, KITE_ACCOUNT, session.client_id, session.expires_at, admin.user_id)
        await audit(
            s, request, "admin.market_data.kite_session", admin.user_id, "platform_secret", KITE_SESSION,
            actor="admin", expires_at=expires.isoformat(), kite_user=session.client_id,
            pasted="access_token" if body.access_token is not None else "request_token",
        )  # fmt: skip
    return await market_data_admin(admin, request, db, settings)
