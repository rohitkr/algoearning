"""Signal sources (ADR 0025, phase A): a user's own Telegram login and the one chat it reads tips from.

The login runs here, step by step, as the web UI asks: start (phone, and the user's own API ID + hash or the
platform's app) -> code -> the 2-step password if the account has one -> pick a chat. Telegram's half-finished
session is kept encrypted on the row between steps; the code and the password are used once and never stored or
logged. Responses never carry a secret: the phone is masked, the API hash and the session are not sent at all.
Disconnect logs the session out at Telegram and wipes every secret; the source (and, from phase B, its history) stays
until it is deleted."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from ae_core.secrets import SecretBox, mask_phone
from ae_db.models import SignalSource
from ae_db.session import Database
from ae_telegram import (
    Chat,
    CodeExpired,
    CodeInvalid,
    Flood,
    PasswordInvalid,
    PasswordNeeded,
    PhoneInvalid,
    SessionInvalid,
    TelegramError,
    TelegramGateway,
)
from fastapi import APIRouter, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import audit
from ..deps import CurrentUser, UserSession, get_db
from ..entitlements import load_entitlements, require_within
from ..errors import AppError, Conflict, NotFound, TooManyRequests, Unavailable
from ..schemas import (
    ERROR_RESPONSES,
    SignalSetupOut,
    SignalSourceChatIn,
    SignalSourceCodeIn,
    SignalSourceLoginIn,
    SignalSourceOut,
    SignalSourcePasswordIn,
    TelegramChatOut,
)
from ..settings import Settings, SettingsDep

router = APIRouter(prefix="/v1", tags=["signal-sources"], responses=ERROR_RESPONSES)
log = structlog.get_logger("ae_api.signal_sources")


def _ctx(source_id: uuid.UUID, field: str) -> str:
    return f"signal_source:{source_id}:{field}"


def _box(request: Request) -> SecretBox:
    box: SecretBox | None = getattr(request.app.state, "secretbox", None)
    if box is None:
        raise Unavailable("Telegram logins cannot be stored: APP_ENCRYPTION_KEY is not configured")
    return box


def _gateway(request: Request) -> TelegramGateway:
    gw: TelegramGateway = request.app.state.telegram
    return gw


def _platform_app(settings: Settings) -> tuple[int, str] | None:
    if settings.telegram_api_id and settings.telegram_api_hash:
        return settings.telegram_api_id, settings.telegram_api_hash
    return None


def _app(box: SecretBox, src: SignalSource, settings: Settings) -> tuple[int, str]:
    """The Telegram app this source logs in with: the user's own, or the platform's."""
    if src.api_id is not None and src.api_hash_enc is not None:
        return src.api_id, box.decrypt(src.api_hash_enc, _ctx(src.id, "api_hash"))
    app = _platform_app(settings)
    if app is None:
        raise AppError("enter your Telegram API ID and hash: this server has no Telegram app of its own")
    return app


def _dec(box: SecretBox, src: SignalSource, field: str) -> str | None:
    raw: bytes | None = getattr(src, f"{field}_enc")
    return None if raw is None else box.decrypt(raw, _ctx(src.id, field))


def _set(box: SecretBox, src: SignalSource, field: str, value: str | None) -> None:
    setattr(src, f"{field}_enc", None if value is None else box.encrypt(value, _ctx(src.id, field)))


def _next_step(src: SignalSource) -> str | None:
    if src.status == "code_sent":
        return "code"
    if src.status == "password_needed":
        return "password"
    if src.status in ("needs_reconnect", "disconnected"):
        return "login"
    if src.status == "connected" and src.chat_id is None:
        return "chat"
    return None


def _out(box: SecretBox, src: SignalSource) -> SignalSourceOut:
    phone = _dec(box, src, "phone")
    return SignalSourceOut(
        id=src.id,
        label=src.label,
        status=src.status,  # type: ignore[arg-type]
        status_detail=src.status_detail,
        next_step=_next_step(src),  # type: ignore[arg-type]
        flood_until=src.flood_until,
        phone_masked=mask_phone(phone) if phone else None,
        api_id=src.api_id,
        platform_app=src.api_hash_enc is None,
        account_name=src.account_name,
        chat_id=src.chat_id,
        chat_title=src.chat_title,
        chat_kind=src.chat_kind,
        connected_at=src.connected_at,
        created_at=src.created_at,
    )


async def _own(s: AsyncSession, user_id: uuid.UUID, source_id: uuid.UUID) -> SignalSource:
    src = (
        await s.execute(select(SignalSource).where(SignalSource.id == source_id, SignalSource.user_id == user_id))
    ).scalar_one_or_none()
    if src is None:
        raise NotFound("signal source not found")
    return src


def _phone(raw: str) -> str:
    digits = "".join(ch for ch in raw if ch.isdigit())
    return ("+" if raw.strip().startswith("+") else "") + digits


async def _record(request: Request, source_id: uuid.UUID, **values: object) -> None:
    """Save a status in its own transaction: the request's is rolled back by the error raised next."""
    db: Database = get_db(request)
    async with db.system_session() as s:
        await s.execute(update(SignalSource).where(SignalSource.id == source_id).values(**values))


async def _telegram_failed(request: Request, src: SignalSource, exc: TelegramError) -> AppError:
    """Record what Telegram said on the source and turn it into the API error to raise."""
    if isinstance(exc, Flood):
        until = datetime.now(UTC) + timedelta(seconds=exc.seconds)
        await _record(request, src.id, status="flood_wait", flood_until=until, status_detail=str(exc))
        return TooManyRequests(str(exc), {"retry_after": exc.seconds})
    if isinstance(exc, SessionInvalid | CodeExpired):
        await _record(request, src.id, status="needs_reconnect", status_detail=str(exc), session_enc=None,
                      login_state_enc=None)  # fmt: skip
        return Conflict(str(exc), {"action": "login"})
    return AppError(str(exc))


# -- endpoints --------------------------------------------------------------------------------------------------------
@router.get("/signal-sources/setup", response_model=SignalSetupOut)
async def setup(user: CurrentUser, s: UserSession, settings: SettingsDep) -> SignalSetupOut:
    ent = await load_entitlements(s, user.user_id, timedelta(days=settings.subscription_grace_days))
    return SignalSetupOut(
        platform_app=_platform_app(settings) is not None,
        max_sources=ent.limit("max_signal_sources"),
        used=ent.usage.get("max_signal_sources", 0),
    )


@router.get("/signal-sources", response_model=list[SignalSourceOut])
async def list_sources(user: CurrentUser, s: UserSession, request: Request) -> list[SignalSourceOut]:
    box = _box(request)
    q = select(SignalSource).where(SignalSource.user_id == user.user_id).order_by(SignalSource.created_at)
    return [_out(box, x) for x in (await s.execute(q)).scalars()]


@router.post("/signal-sources", response_model=SignalSourceOut, status_code=status.HTTP_201_CREATED)
async def add_source(
    body: SignalSourceLoginIn, user: CurrentUser, s: UserSession, request: Request, settings: SettingsDep
) -> SignalSourceOut:
    """Start a new source: Telegram sends a login code to the user's Telegram app (or by SMS)."""
    box = _box(request)
    if not body.phone:
        raise AppError("enter the phone number of your Telegram account")
    if (body.api_id is None) != (body.api_hash is None):
        raise AppError("enter both the API ID and the API hash, or neither to use the platform's app")
    ent = await load_entitlements(s, user.user_id, timedelta(days=settings.subscription_grace_days))
    require_within(ent, "max_signal_sources", ent.usage.get("max_signal_sources", 0))
    src = SignalSource(id=uuid.uuid4(), user_id=user.user_id, label=body.label, api_id=body.api_id,
                       key_version=box.current, status="disconnected")  # fmt: skip
    if body.api_hash:
        _set(box, src, "api_hash", body.api_hash.lower())
    elif _platform_app(settings) is None:
        raise AppError("enter your Telegram API ID and hash: this server has no Telegram app of its own")
    _set(box, src, "phone", _phone(body.phone))
    s.add(src)
    await s.flush()
    await _start_login(s, request, settings, src)
    await audit(s, request, "signal_source.add", user.user_id, "signal_source", src.id,
                platform_app=body.api_hash is None)  # fmt: skip
    return _out(box, src)


@router.post("/signal-sources/{source_id}/login", response_model=SignalSourceOut)
async def restart_login(
    source_id: uuid.UUID,
    body: SignalSourceLoginIn,
    user: CurrentUser,
    s: UserSession,
    request: Request,
    settings: SettingsDep,
) -> SignalSourceOut:
    """Log in again (a new code, after a disconnect, an expired session or a wrong phone). Empty fields keep what
    is stored."""
    box = _box(request)
    src = await _own(s, user.user_id, source_id)
    if (body.api_id is None) != (body.api_hash is None):
        raise AppError("enter both the API ID and the API hash, or neither")
    if body.api_hash:
        src.api_id = body.api_id
        _set(box, src, "api_hash", body.api_hash.lower())
    if body.phone:
        _set(box, src, "phone", _phone(body.phone))
    if body.label is not None:
        src.label = body.label
    if src.phone_enc is None:
        raise AppError("enter the phone number of your Telegram account")
    await _start_login(s, request, settings, src)
    await audit(s, request, "signal_source.login", user.user_id, "signal_source", src.id)
    return _out(box, src)


async def _start_login(s: AsyncSession, request: Request, settings: Settings, src: SignalSource) -> None:
    box = _box(request)
    api_id, api_hash = _app(box, src, settings)
    phone = _dec(box, src, "phone") or ""
    try:
        started = await _gateway(request).send_code(api_id, api_hash, phone)
    except PhoneInvalid as exc:
        raise AppError(str(exc)) from exc
    except TelegramError as exc:
        raise await _telegram_failed(request, src, exc) from exc
    _set(box, src, "session", started.session)
    _set(box, src, "login_state", json.dumps({"code_hash": started.code_hash}))
    src.status, src.flood_until = "code_sent", None
    src.status_detail = "code sent to your Telegram app" if started.via == "app" else "code sent by SMS"
    await s.flush()


@router.post("/signal-sources/{source_id}/code", response_model=SignalSourceOut)
async def submit_code(
    source_id: uuid.UUID,
    body: SignalSourceCodeIn,
    user: CurrentUser,
    s: UserSession,
    request: Request,
    settings: SettingsDep,
) -> SignalSourceOut:
    box = _box(request)
    src = await _own(s, user.user_id, source_id)
    if src.status != "code_sent" or src.login_state_enc is None:
        raise Conflict("ask for a login code first", {"action": "login"})
    api_id, api_hash = _app(box, src, settings)
    state = json.loads(_dec(box, src, "login_state") or "{}")
    code = "".join(ch for ch in body.code if ch.isdigit())
    try:
        session, name = await _gateway(request).sign_in_code(
            api_id, api_hash, _dec(box, src, "session") or "", _dec(box, src, "phone") or "", code, state["code_hash"]
        )
    except PasswordNeeded as exc:
        _set(box, src, "session", exc.session)
        src.login_state_enc = None
        src.status, src.status_detail = "password_needed", "enter your 2-step verification password"
        await s.flush()
        return _out(box, src)
    except CodeInvalid as exc:
        raise AppError(str(exc)) from exc
    except TelegramError as exc:
        raise await _telegram_failed(request, src, exc) from exc
    await _connected(s, request, user.user_id, src, session, name)
    return _out(box, src)


@router.post("/signal-sources/{source_id}/password", response_model=SignalSourceOut)
async def submit_password(
    source_id: uuid.UUID,
    body: SignalSourcePasswordIn,
    user: CurrentUser,
    s: UserSession,
    request: Request,
    settings: SettingsDep,
) -> SignalSourceOut:
    box = _box(request)
    src = await _own(s, user.user_id, source_id)
    if src.status != "password_needed":
        raise Conflict("no password is asked for right now", {"action": "login"})
    api_id, api_hash = _app(box, src, settings)
    try:
        session, name = await _gateway(request).sign_in_password(
            api_id, api_hash, _dec(box, src, "session") or "", body.password
        )
    except PasswordInvalid as exc:
        raise AppError(str(exc)) from exc
    except TelegramError as exc:
        raise await _telegram_failed(request, src, exc) from exc
    await _connected(s, request, user.user_id, src, session, name)
    return _out(box, src)


async def _connected(
    s: AsyncSession, request: Request, user_id: uuid.UUID, src: SignalSource, session: str, name: str
) -> None:
    _set(_box(request), src, "session", session)
    src.login_state_enc = None
    src.status, src.status_detail, src.flood_until = "connected", None, None
    src.account_name, src.connected_at = name[:120], datetime.now(UTC)
    await s.flush()
    await audit(s, request, "signal_source.connected", user_id, "signal_source", src.id)


@router.get("/signal-sources/{source_id}/chats", response_model=list[TelegramChatOut])
async def list_chats(
    source_id: uuid.UUID, user: CurrentUser, s: UserSession, request: Request, settings: SettingsDep
) -> list[TelegramChatOut]:
    """The channels and groups of the logged-in account, to pick the tips chat from."""
    src = await _own(s, user.user_id, source_id)
    chats = await _chats(s, request, settings, src)
    return [TelegramChatOut(id=c.id, title=c.title, kind=c.kind, username=c.username) for c in chats]  # type: ignore[arg-type]


async def _chats(s: AsyncSession, request: Request, settings: Settings, src: SignalSource) -> list[Chat]:
    box = _box(request)
    if src.status != "connected" or src.session_enc is None:
        raise Conflict("log in to Telegram first", {"action": "login"})
    api_id, api_hash = _app(box, src, settings)
    try:
        return list(await _gateway(request).chats(api_id, api_hash, _dec(box, src, "session") or ""))
    except TelegramError as exc:
        raise await _telegram_failed(request, src, exc) from exc


@router.put("/signal-sources/{source_id}/chat", response_model=SignalSourceOut)
async def pick_chat(
    source_id: uuid.UUID,
    body: SignalSourceChatIn,
    user: CurrentUser,
    s: UserSession,
    request: Request,
    settings: SettingsDep,
) -> SignalSourceOut:
    """Read tips from this chat: one of the account's own channels or groups (checked with Telegram)."""
    src = await _own(s, user.user_id, source_id)
    chat = next((c for c in await _chats(s, request, settings, src) if c.id == body.chat_id), None)
    if chat is None:
        raise AppError("that chat is not one of this Telegram account's channels or groups")
    src.chat_id, src.chat_title, src.chat_kind = chat.id, chat.title[:200], chat.kind
    await s.flush()
    await audit(s, request, "signal_source.chat", user.user_id, "signal_source", src.id, chat_id=body.chat_id)
    return _out(_box(request), src)


@router.post("/signal-sources/{source_id}/disconnect", response_model=SignalSourceOut)
async def disconnect(
    source_id: uuid.UUID, user: CurrentUser, s: UserSession, request: Request, settings: SettingsDep
) -> SignalSourceOut:
    """Log the session out at Telegram and wipe every secret (phone, API hash, session). The chat choice stays."""
    src = await _own(s, user.user_id, source_id)
    await _logout(request, settings, src)
    await s.flush()
    await audit(s, request, "signal_source.disconnect", user.user_id, "signal_source", src.id)
    return _out(_box(request), src)


async def _logout(request: Request, settings: Settings, src: SignalSource) -> None:
    box = _box(request)
    session = _dec(box, src, "session")
    if session and src.status == "connected":
        try:
            api_id, api_hash = _app(box, src, settings)
            await _gateway(request).log_out(api_id, api_hash, session)
        except (TelegramError, AppError) as exc:  # already logged out / revoked / no app: the secrets go anyway
            log.info("telegram log out skipped", source=str(src.id), reason=type(exc).__name__)
    src.session_enc = src.login_state_enc = src.phone_enc = src.api_hash_enc = None
    src.api_id = None
    src.status, src.status_detail, src.flood_until = "disconnected", None, None


@router.delete("/signal-sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: uuid.UUID, user: CurrentUser, s: UserSession, request: Request, settings: SettingsDep
) -> Response:
    src = await _own(s, user.user_id, source_id)
    await _logout(request, settings, src)
    await s.delete(src)
    await s.flush()
    await audit(s, request, "signal_source.delete", user.user_id, "signal_source", source_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
