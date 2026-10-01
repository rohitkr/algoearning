"""Broker accounts (execution credentials) and the daily broker login.

Credentials are encrypted before they reach the database and never leave the server: responses carry only a masked
API key. Login: POST .../login returns the broker's login URL with a signed single-use `state`; the broker sends the
browser back to GET /v1/brokers/{broker}/callback, which exchanges the one-time token server-side, checks that the
account that logged in is the one registered, stores the session encrypted and redirects to the web app."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import structlog
from ae_brokers.base import BrokerAdapter, BrokerError, Credentials
from ae_core.secrets import SecretBox, mask
from ae_db.enums import Broker, BrokerAccountStatus
from ae_db.models import BrokerAccount, PlatformSetting
from ae_db.models import BrokerSession as SessionRow
from ae_db.repositories import BrokerAccountRepo
from fastapi import APIRouter, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import audit
from ..deps import CurrentUser, DbDep, UserSession
from ..entitlements import load_entitlements, require_within
from ..errors import AppError, Conflict, NotFound, Unavailable
from ..login_state import InvalidState, LoginStateSigner
from ..schemas import (
    ERROR_RESPONSES,
    BrokerAccountIn,
    BrokerAccountOut,
    BrokerAccountPatch,
    BrokerInfoOut,
    BrokerLoginOut,
    BrokerTestOut,
    ServerIpOut,
)
from ..settings import Settings, SettingsDep

router = APIRouter(prefix="/v1", tags=["brokers"], responses=ERROR_RESPONSES)
IP_CHANGE_WARNING = timedelta(days=3)
log = structlog.get_logger("ae_api.brokers")


def _ctx(account_id: uuid.UUID, field: str) -> str:
    return f"broker_account:{account_id}:{field}"


def _box(request: Request) -> SecretBox:
    box: SecretBox | None = getattr(request.app.state, "secretbox", None)
    if box is None:
        raise Unavailable("broker credentials cannot be stored: APP_ENCRYPTION_KEY is not configured")
    return box


def _adapter(request: Request, broker: str) -> BrokerAdapter:
    adapters: dict[str, BrokerAdapter] = request.app.state.brokers
    a = adapters.get(broker)
    if a is None:
        raise AppError(f"{broker} is not supported yet")
    return a


def _callback_url(settings: Settings, broker: str) -> str:
    return f"{settings.api_public_url.rstrip('/')}/v1/brokers/{broker}/callback"


def _creds(box: SecretBox, acc: BrokerAccount) -> Credentials:
    if acc.api_key_enc is None or acc.api_secret_enc is None:
        raise AppError("this broker account has no API credentials")
    return Credentials(
        box.decrypt(acc.api_key_enc, _ctx(acc.id, "api_key")),
        box.decrypt(acc.api_secret_enc, _ctx(acc.id, "api_secret")),
    )


async def _session(s: AsyncSession, account_id: uuid.UUID) -> SessionRow | None:
    return (await s.execute(select(SessionRow).where(SessionRow.broker_account_id == account_id))).scalar_one_or_none()


def _status(acc: BrokerAccount, sess: SessionRow | None, now: datetime) -> str:
    if acc.status == BrokerAccountStatus.ERROR:
        return "error"
    if sess is None:
        return "disconnected"
    return "connected" if sess.expires_at > now else "expired"


def _out(request: Request, acc: BrokerAccount, sess: SessionRow | None) -> BrokerAccountOut:
    box = _box(request)
    adapters: dict[str, BrokerAdapter] = request.app.state.brokers
    now = datetime.now(UTC)
    st = _status(acc, sess, now)
    return BrokerAccountOut(
        id=acc.id,
        broker=acc.broker.value,
        broker_name=adapters[acc.broker.value].info.name if acc.broker.value in adapters else acc.broker.value.title(),
        client_id=acc.client_id,
        label=acc.label,
        api_key_masked=mask(box.decrypt(acc.api_key_enc, _ctx(acc.id, "api_key"))) if acc.api_key_enc else "",
        static_ip=str(acc.static_ip) if acc.static_ip else None,
        status=st,  # type: ignore[arg-type]
        session_expires_at=sess.expires_at if sess and st == "connected" else None,
        last_login_at=acc.last_login_at,
        terminal_enabled=acc.terminal_enabled and st == "connected",
        engine_enabled=acc.engine_enabled and st == "connected",
        created_at=acc.created_at,
    )


async def _own(s: AsyncSession, user_id: uuid.UUID, account_id: uuid.UUID) -> BrokerAccount:
    acc = await BrokerAccountRepo(s, user_id).get(account_id)
    if acc is None:
        raise NotFound("broker account not found")
    return acc


# -- catalogue & accounts ------------------------------------------------------------------------------------
@router.get("/brokers", response_model=list[BrokerInfoOut])
async def broker_catalog(request: Request, settings: SettingsDep) -> list[BrokerInfoOut]:
    from ae_brokers.registry import catalog

    return [
        BrokerInfoOut(
            code=b.code,
            name=b.name,
            available=b.available,
            developer_console=b.developer_console,
            notes=b.notes,
            redirect_url=_callback_url(settings, b.code) if b.available else None,
        )
        for b in catalog(request.app.state.brokers)
    ]


@router.get("/brokers/server-ip", response_model=ServerIpOut)
async def server_ip(_: CurrentUser, db: DbDep) -> ServerIpOut:
    """Where orders come from: Zerodha refuses any IP the user's Kite app does not list (ADR 0019)."""
    async with db.system_session() as s:
        row = (await s.execute(select(PlatformSetting).where(PlatformSetting.key == "public_ip"))).scalar_one_or_none()
    value = row.value if row is not None and isinstance(row.value, dict) else {}
    out = ServerIpOut.model_validate({"ip": None, **value})
    out.changed_recently = bool(
        out.previous and out.changed_at and datetime.now(UTC) - out.changed_at < IP_CHANGE_WARNING
    )
    return out


@router.get("/broker-accounts", response_model=list[BrokerAccountOut])
async def list_accounts(user: CurrentUser, s: UserSession, request: Request) -> list[BrokerAccountOut]:
    rows = (
        (
            await s.execute(
                select(BrokerAccount).where(BrokerAccount.user_id == user.user_id).order_by(BrokerAccount.created_at)
            )
        )
        .scalars()
        .all()
    )
    sessions = {x.broker_account_id: x for x in (await s.execute(select(SessionRow))).scalars()}
    return [_out(request, a, sessions.get(a.id)) for a in rows]


@router.post("/broker-accounts", response_model=BrokerAccountOut, status_code=status.HTTP_201_CREATED)
async def add_account(
    body: BrokerAccountIn, user: CurrentUser, s: UserSession, request: Request, settings: SettingsDep
) -> BrokerAccountOut:
    box = _box(request)
    _adapter(request, body.broker)
    ent = await load_entitlements(s, user.user_id, timedelta(days=settings.subscription_grace_days))
    require_within(ent, "max_broker_accounts", ent.usage["max_broker_accounts"])
    acc_id = uuid.uuid4()
    acc = BrokerAccount(
        id=acc_id,
        user_id=user.user_id,
        broker=Broker(body.broker),
        client_id=body.client_id.strip().upper(),
        label=body.label,
        api_key_enc=box.encrypt(body.api_key.strip(), _ctx(acc_id, "api_key")),
        api_secret_enc=box.encrypt(body.api_secret.strip(), _ctx(acc_id, "api_secret")),
        key_version=box.current,
    )
    s.add(acc)
    try:
        await s.flush()
    except IntegrityError as exc:
        raise Conflict(f"{body.broker} account {acc.client_id} is already added") from exc
    await s.refresh(acc)
    await audit(
        s,
        request,
        "broker_account.add",
        user.user_id,
        "broker_account",
        acc.id,
        broker=body.broker,
        client_id=acc.client_id,
    )
    return _out(request, acc, None)


@router.patch("/broker-accounts/{account_id}", response_model=BrokerAccountOut)
async def update_account(
    account_id: uuid.UUID, body: BrokerAccountPatch, user: CurrentUser, s: UserSession, request: Request
) -> BrokerAccountOut:
    box = _box(request)
    acc = await _own(s, user.user_id, account_id)
    sess = await _session(s, acc.id)
    changes = body.model_dump(exclude_unset=True)
    connected = _status(acc, sess, datetime.now(UTC)) == "connected"
    if "label" in changes:
        acc.label = changes["label"]
    if "api_key" in changes or "api_secret" in changes:  # new credentials: the old session is void
        if changes.get("api_key"):
            acc.api_key_enc = box.encrypt(changes["api_key"].strip(), _ctx(acc.id, "api_key"))
        if changes.get("api_secret"):
            acc.api_secret_enc = box.encrypt(changes["api_secret"].strip(), _ctx(acc.id, "api_secret"))
        acc.key_version = box.current
        await s.execute(delete(SessionRow).where(SessionRow.broker_account_id == acc.id))
        sess, connected = None, False
        acc.terminal_enabled = acc.engine_enabled = False
        acc.status = BrokerAccountStatus.DISCONNECTED
    if changes.get("terminal_enabled") is True and not connected:
        raise Conflict("log in to the broker first", {"action": "login"})
    if changes.get("terminal_enabled") is False:  # terminal off = logged out, engine stops too
        await _logout(s, request, acc, sess)
        sess = None
    elif "terminal_enabled" in changes:
        acc.terminal_enabled = True
    if changes.get("engine_enabled") is True:
        if not connected or not acc.terminal_enabled:
            raise Conflict("connect the terminal before starting the trading engine", {"action": "terminal"})
        acc.engine_enabled = True
    elif changes.get("engine_enabled") is False:
        acc.engine_enabled = False
    await s.flush()
    await s.refresh(acc)
    await audit(
        s,
        request,
        "broker_account.update",
        user.user_id,
        "broker_account",
        acc.id,
        fields=sorted(k for k in changes if k not in ("api_key", "api_secret"))
        + (["credentials"] if {"api_key", "api_secret"} & changes.keys() else []),
    )
    return _out(request, acc, sess)


@router.delete("/broker-accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_account(account_id: uuid.UUID, user: CurrentUser, s: UserSession, request: Request) -> Response:
    acc = await _own(s, user.user_id, account_id)
    await _logout(s, request, acc, await _session(s, acc.id))
    await BrokerAccountRepo(s, user.user_id).delete(acc)  # cascades the (encrypted) session row
    await audit(
        s,
        request,
        "broker_account.remove",
        user.user_id,
        "broker_account",
        account_id,
        broker=acc.broker.value,
        client_id=acc.client_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _logout(s: AsyncSession, request: Request, acc: BrokerAccount, sess: SessionRow | None) -> None:
    if sess is not None and acc.broker.value in request.app.state.brokers:
        box = _box(request)
        try:
            token = box.decrypt(sess.access_token_enc, f"broker_session:{acc.id}:access_token")
            await _adapter(request, acc.broker.value).logout(_creds(box, acc), token)
        except (BrokerError, AppError) as exc:  # best effort: we forget the session anyway
            log.info("broker logout failed", account=str(acc.id), reason=str(exc))
    await s.execute(delete(SessionRow).where(SessionRow.broker_account_id == acc.id))
    acc.terminal_enabled = acc.engine_enabled = False
    acc.status = BrokerAccountStatus.DISCONNECTED


# -- daily login ------------------------------------------------------------------------------------------------
def _signer(request: Request) -> LoginStateSigner:
    signer: LoginStateSigner | None = getattr(request.app.state, "login_state", None)
    if signer is None:
        raise Unavailable("broker login needs APP_ENCRYPTION_KEY and Redis")
    return signer


@router.post("/broker-accounts/{account_id}/login", response_model=BrokerLoginOut)
async def start_login(account_id: uuid.UUID, user: CurrentUser, s: UserSession, request: Request) -> BrokerLoginOut:
    acc = await _own(s, user.user_id, account_id)
    adapter = _adapter(request, acc.broker.value)
    state = await _signer(request).issue(acc.id, user.user_id)
    await audit(s, request, "broker_account.login_started", user.user_id, "broker_account", acc.id)
    return BrokerLoginOut(login_url=adapter.login_url(_creds(_box(request), acc), state))


@router.get("/brokers/{broker}/callback", include_in_schema=False)
async def login_callback(broker: str, request: Request, db: DbDep, settings: SettingsDep) -> RedirectResponse:
    """Public: the broker redirects the user's browser here. Everything is proven by the signed state."""
    params: dict[str, Any] = dict(request.query_params)

    def back(**q: str) -> RedirectResponse:
        return RedirectResponse(f"{settings.web_url}/brokers?{urlencode(q)}", status_code=303)

    try:
        st = await _signer(request).consume(str(params.get("state", "")))
    except (InvalidState, ValueError, KeyError) as exc:
        log.warning("broker callback rejected", reason=str(exc))
        return back(error="invalid_state")
    box = _box(request)
    async with db.user_session(st.user_id) as s:
        acc = await BrokerAccountRepo(s, st.user_id).get(st.account_id)
        if acc is None or acc.broker.value != broker:
            return back(error="unknown_account")
        adapter = _adapter(request, broker)
        creds = _creds(box, acc)
        try:
            session = await adapter.exchange(creds, params)
        except BrokerError as exc:
            await audit(
                s, request, "broker_account.login_failed", st.user_id, "broker_account", acc.id, reason=exc.code
            )
            return back(error=exc.code, account=str(acc.id))
        if session.client_id.upper() != acc.client_id.upper():  # logged in with a different broker account
            try:
                await adapter.logout(creds, session.access_token)
            except BrokerError:
                pass
            await audit(
                s,
                request,
                "broker_account.login_wrong_account",
                st.user_id,
                "broker_account",
                acc.id,
                logged_in_as=session.client_id,
            )
            return back(error="wrong_account", account=str(acc.id), logged_in_as=session.client_id)
        await s.execute(delete(SessionRow).where(SessionRow.broker_account_id == acc.id))
        s.add(
            SessionRow(
                user_id=st.user_id,
                broker_account_id=acc.id,
                key_version=box.current,
                access_token_enc=box.encrypt(session.access_token, f"broker_session:{acc.id}:access_token"),
                expires_at=session.expires_at,
            )
        )
        acc.status, acc.last_login_at, acc.terminal_enabled = BrokerAccountStatus.CONNECTED, datetime.now(UTC), True
        await audit(
            s,
            request,
            "broker_account.login",
            st.user_id,
            "broker_account",
            acc.id,
            expires_at=session.expires_at.isoformat(),
        )
    return back(connected=str(st.account_id))


@router.post("/broker-accounts/{account_id}/test", response_model=BrokerTestOut)
async def test_connection(account_id: uuid.UUID, user: CurrentUser, s: UserSession, request: Request) -> BrokerTestOut:
    acc = await _own(s, user.user_id, account_id)
    sess = await _session(s, acc.id)
    if _status(acc, sess, datetime.now(UTC)) != "connected" or sess is None:
        return BrokerTestOut(ok=False, message="Not logged in to the broker today")
    box = _box(request)
    try:
        token = box.decrypt(sess.access_token_enc, f"broker_session:{acc.id}:access_token")
        prof = await _adapter(request, acc.broker.value).profile(_creds(box, acc), token)
    except BrokerError as exc:
        if exc.code == "session_expired":
            await s.execute(delete(SessionRow).where(SessionRow.broker_account_id == acc.id))
            acc.terminal_enabled = acc.engine_enabled = False
        return BrokerTestOut(ok=False, message=exc.message)
    return BrokerTestOut(ok=True, client_id=prof.get("user_id"), name=prof.get("user_name"))
