"""Read-only Telegram access for signal sources (ADR 0025): log in as the user (a bot cannot read a channel it does
not administer), list their channels and groups, read a chat's history, listen to it, log out. Nothing here can send,
forward, react or mark anything read: every Telethon call goes through a client that refuses any other method.

The only code that may construct a Telethon client is `_new_client` in `client.py` (a test checks)."""

from .client import (
    Chat,
    CodeExpired,
    CodeInvalid,
    Flood,
    LoginStarted,
    Message,
    PasswordInvalid,
    PasswordNeeded,
    PhoneInvalid,
    SessionInvalid,
    TelegramError,
    TelegramGateway,
    TelethonGateway,
)

__all__ = [
    "Chat",
    "CodeExpired",
    "CodeInvalid",
    "Flood",
    "LoginStarted",
    "Message",
    "PasswordInvalid",
    "PasswordNeeded",
    "PhoneInvalid",
    "SessionInvalid",
    "TelegramError",
    "TelegramGateway",
    "TelethonGateway",
]
