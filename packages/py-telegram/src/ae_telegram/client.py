"""Telegram through Telethon (MTProto, the user's own account), read-only (ADR 0025).

`TelegramGateway` is what the API and the signals process use; `TelethonGateway` is the real one (tests use fakes).
Every step takes and returns a Telethon StringSession string: the caller stores it encrypted, nothing is kept here.
Telethon's errors become a few plain ones (wrong code, password needed, flood wait with its seconds, session no longer
valid), so callers never import Telethon.

The fence: `ReadOnlyClient` wraps the Telethon client and allows only the methods in ALLOWED (connecting, the login
steps, listing chats, reading messages, listening, logging out). Asking it for anything else (send_message,
forward_messages, send_read_acknowledge, ...) raises before a request is made."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

DEVICE = {"device_model": "AlgoEarning", "system_version": "signal reader", "app_version": "1.0"}

ALLOWED = frozenset(
    {
        "connect",
        "disconnect",
        "is_connected",
        "is_user_authorized",
        "send_code_request",  # login: Telegram sends the code to the user's own app
        "sign_in",
        "get_me",
        "iter_dialogs",
        "get_entity",
        "get_input_entity",
        "iter_messages",
        "add_event_handler",
        "run_until_disconnected",
        "log_out",
        "session",
    }
)


# -- errors ---------------------------------------------------------------------------------------------------------
class TelegramError(Exception):
    """Something Telegram said no to; `str()` is safe to show the user."""


class PhoneInvalid(TelegramError):
    pass


class CodeInvalid(TelegramError):
    pass


class CodeExpired(TelegramError):
    pass


class PasswordInvalid(TelegramError):
    pass


class PasswordNeeded(TelegramError):
    """The account has 2-step verification: the session so far must be kept for the password step."""

    def __init__(self, session: str) -> None:
        super().__init__("this account has a 2-step verification password")
        self.session = session


class SessionInvalid(TelegramError):
    """Logged out, revoked from Telegram's Devices list, expired or the account is gone: log in again."""


class Flood(TelegramError):
    def __init__(self, seconds: int) -> None:
        super().__init__(f"Telegram asks to wait {seconds} seconds before trying again")
        self.seconds = seconds


# -- data -----------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class LoginStarted:
    session: str  # the half-finished session: keep it (encrypted) for the code step
    code_hash: str
    via: str  # where the code went: "app" (the user's Telegram app) or "sms"


@dataclass(frozen=True)
class Chat:
    id: int
    title: str
    kind: str  # channel | group
    username: str | None


@dataclass(frozen=True)
class Message:
    id: int
    date: datetime
    text: str
    edit_date: datetime | None
    reply_to: int | None
    has_media: bool


class TelegramGateway(Protocol):
    async def send_code(self, api_id: int, api_hash: str, phone: str) -> LoginStarted: ...
    async def sign_in_code(
        self, api_id: int, api_hash: str, session: str, phone: str, code: str, code_hash: str
    ) -> tuple[str, str]:
        """(session, account name). Raises PasswordNeeded(session) when the account has 2-step verification."""
        ...

    async def sign_in_password(self, api_id: int, api_hash: str, session: str, password: str) -> tuple[str, str]: ...
    async def chats(self, api_id: int, api_hash: str, session: str) -> list[Chat]: ...
    async def log_out(self, api_id: int, api_hash: str, session: str) -> None: ...


# -- the fence ------------------------------------------------------------------------------------------------------
class ReadOnlyClient:
    """A Telethon client that refuses every method outside ALLOWED."""

    def __init__(self, client: Any) -> None:
        object.__setattr__(self, "_client", client)

    def __getattr__(self, name: str) -> Any:
        if name not in ALLOWED:
            raise PermissionError(f"Telegram access is read-only: {name} is not allowed")
        return getattr(object.__getattribute__(self, "_client"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise PermissionError("Telegram access is read-only")


def _new_client(api_id: int, api_hash: str, session: str) -> ReadOnlyClient:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    return ReadOnlyClient(TelegramClient(StringSession(session or None), api_id, api_hash, **DEVICE))


def _translate(exc: BaseException) -> TelegramError | None:
    from telethon import errors as e

    if isinstance(exc, e.FloodWaitError):
        return Flood(int(exc.seconds))
    if isinstance(exc, e.PhoneNumberInvalidError | e.PhoneNumberBannedError | e.PhoneNumberUnoccupiedError):
        return PhoneInvalid("Telegram does not accept this phone number")
    if isinstance(exc, e.PhoneCodeInvalidError | e.PhoneCodeEmptyError):
        return CodeInvalid("the code is not right")
    if isinstance(exc, e.PhoneCodeExpiredError):
        return CodeExpired("the code has expired: ask for a new one")
    if isinstance(exc, e.PasswordHashInvalidError):
        return PasswordInvalid("the 2-step verification password is not right")
    if isinstance(exc, e.ApiIdInvalidError):
        return TelegramError("Telegram does not accept this API ID and hash")
    invalid = (
        e.AuthKeyUnregisteredError,
        e.SessionRevokedError,
        e.SessionExpiredError,
        e.UserDeactivatedError,
        e.UserDeactivatedBanError,
        e.AuthKeyDuplicatedError,
    )
    if isinstance(exc, invalid):
        return SessionInvalid("the Telegram session is no longer valid: reconnect Telegram")
    if isinstance(exc, e.RPCError):
        return TelegramError(f"Telegram refused the request ({type(exc).__name__})")
    return None


def _name(me: Any) -> str:
    full = " ".join(x for x in (getattr(me, "first_name", None), getattr(me, "last_name", None)) if x)
    user = getattr(me, "username", None)
    return f"{full} (@{user})" if full and user else (full or (f"@{user}" if user else "Telegram user"))


def to_message(m: Any) -> Message:
    reply = getattr(getattr(m, "reply_to", None), "reply_to_msg_id", None)
    return Message(
        int(m.id), m.date, m.message or "", getattr(m, "edit_date", None), reply, getattr(m, "media", None) is not None
    )


class TelethonGateway:
    """The real gateway. Each call connects, does one thing and disconnects (the API holds no connections); the
    signals process keeps its own long-lived client through `open()`."""

    def __init__(self, factory: Callable[[int, str, str], ReadOnlyClient] = _new_client) -> None:
        self._factory = factory

    async def _run(self, api_id: int, api_hash: str, session: str, fn: Callable[[Any], Awaitable[Any]]) -> Any:
        client = self._factory(api_id, api_hash, session)
        try:
            await client.connect()
            return await fn(client)
        except TelegramError:
            raise
        except Exception as exc:
            translated = _translate(exc)
            if translated is None:
                raise
            raise translated from exc
        finally:
            await client.disconnect()

    async def send_code(self, api_id: int, api_hash: str, phone: str) -> LoginStarted:
        async def go(c: Any) -> LoginStarted:
            sent = await c.send_code_request(phone)
            via = "app" if "App" in type(getattr(sent, "type", None)).__name__ else "sms"
            return LoginStarted(c.session.save(), sent.phone_code_hash, via)

        result: LoginStarted = await self._run(api_id, api_hash, "", go)
        return result

    async def sign_in_code(
        self, api_id: int, api_hash: str, session: str, phone: str, code: str, code_hash: str
    ) -> tuple[str, str]:
        from telethon.errors import SessionPasswordNeededError

        async def go(c: Any) -> tuple[str, str]:
            try:
                me = await c.sign_in(phone=phone, code=code, phone_code_hash=code_hash)
            except SessionPasswordNeededError as exc:
                raise PasswordNeeded(c.session.save()) from exc
            return c.session.save(), _name(me)

        result: tuple[str, str] = await self._run(api_id, api_hash, session, go)
        return result

    async def sign_in_password(self, api_id: int, api_hash: str, session: str, password: str) -> tuple[str, str]:
        async def go(c: Any) -> tuple[str, str]:
            me = await c.sign_in(password=password)
            return c.session.save(), _name(me)

        result: tuple[str, str] = await self._run(api_id, api_hash, session, go)
        return result

    async def chats(self, api_id: int, api_hash: str, session: str) -> list[Chat]:
        async def go(c: Any) -> list[Chat]:
            if not await c.is_user_authorized():
                raise SessionInvalid("the Telegram session is no longer valid: reconnect Telegram")
            out = []
            async for d in c.iter_dialogs():
                if d.is_channel or d.is_group:
                    kind = "group" if d.is_group else "channel"
                    out.append(Chat(int(d.id), d.name or "", kind, getattr(d.entity, "username", None)))
            return out

        result: list[Chat] = await self._run(api_id, api_hash, session, go)
        return result

    async def log_out(self, api_id: int, api_hash: str, session: str) -> None:
        async def go(c: Any) -> None:
            if await c.is_user_authorized():
                await c.log_out()

        await self._run(api_id, api_hash, session, go)
