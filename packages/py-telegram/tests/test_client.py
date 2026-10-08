"""The read-only fence and the login steps, with a fake Telethon client (no network)."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from ae_telegram import CodeInvalid, Flood, PasswordNeeded, SessionInvalid, TelethonGateway
from ae_telegram.client import ALLOWED, ReadOnlyClient
from telethon import errors

ROOT = Path(__file__).resolve().parents[3]


class FakeTelethon:
    """Records calls; `script` maps a method to what it returns or raises."""

    def __init__(self, **script: Any) -> None:
        self.calls: list[str] = []
        self.script = script
        self.session = SimpleNamespace(save=lambda: "SESSION-AFTER")

    def __getattr__(self, name: str) -> Any:
        async def call(*args: Any, **kw: Any) -> Any:
            self.calls.append(name)
            out = self.script.get(name)
            if isinstance(out, BaseException):
                raise out
            return out(*args, **kw) if callable(out) else out

        return call

    def iter_dialogs(self) -> Any:
        async def gen() -> Any:
            for d in self.script.get("dialogs", []):
                yield d

        self.calls.append("iter_dialogs")
        return gen()


def gateway(fake: FakeTelethon) -> TelethonGateway:
    return TelethonGateway(lambda api_id, api_hash, session: ReadOnlyClient(fake))


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_the_client_refuses_anything_that_writes() -> None:
    c = ReadOnlyClient(FakeTelethon())
    for name in ("send_message", "forward_messages", "send_read_acknowledge", "send_reaction", "delete_messages",
                 "edit_message", "send_file", "pin_message", "__call__", "join_channel"):  # fmt: skip
        with pytest.raises(PermissionError):
            getattr(c, name)
    with pytest.raises(PermissionError):
        c.flood_sleep_threshold = 0
    assert {"send_message", "forward_messages", "send_read_acknowledge"}.isdisjoint(ALLOWED)


def test_only_the_gateway_builds_a_telethon_client() -> None:
    """Nothing in the code base constructs TelegramClient except ae_telegram.client._new_client."""
    found = []
    for path in ROOT.glob("**/*.py"):
        if any(p in path.parts for p in (".venv", "node_modules", ".data")):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", "")) == "TelegramClient"
            ):
                found.append(str(path.relative_to(ROOT)))
    assert found == ["packages/py-telegram/src/ae_telegram/client.py"]


def test_login_with_code() -> None:
    fake = FakeTelethon(
        send_code_request=SimpleNamespace(phone_code_hash="H1", type=type("SentCodeTypeApp", (), {})()),
        sign_in=SimpleNamespace(first_name="Rohit", last_name=None, username="rk"),
    )
    gw = gateway(fake)
    started = run(gw.send_code(1, "hash", "+919800000210"))
    assert (started.session, started.code_hash, started.via) == ("SESSION-AFTER", "H1", "app")
    session, name = run(gw.sign_in_code(1, "hash", started.session, "+919800000210", "12345", "H1"))
    assert (session, name) == ("SESSION-AFTER", "Rohit (@rk)")
    assert fake.calls == ["connect", "send_code_request", "disconnect", "connect", "sign_in", "disconnect"]


def test_two_step_password_and_errors() -> None:
    gw = gateway(FakeTelethon(sign_in=errors.SessionPasswordNeededError(request=None)))
    with pytest.raises(PasswordNeeded) as exc:
        run(gw.sign_in_code(1, "h", "S", "+91", "1", "H"))
    assert exc.value.session == "SESSION-AFTER"  # kept for the password step
    with pytest.raises(CodeInvalid):
        run(
            gateway(FakeTelethon(sign_in=errors.PhoneCodeInvalidError(request=None))).sign_in_code(
                1, "h", "S", "+91", "1", "H"
            )
        )
    flood = errors.FloodWaitError(request=None, capture=42)
    with pytest.raises(Flood) as fl:
        run(gateway(FakeTelethon(send_code_request=flood)).send_code(1, "h", "+91"))
    assert fl.value.seconds == 42


def test_chats_and_a_revoked_session() -> None:
    dialogs = [
        SimpleNamespace(id=-100123, name="Nifty Sensex VIP setups", is_channel=True, is_group=False,
                        entity=SimpleNamespace(username="vipsetups")),
        SimpleNamespace(id=42, name="Mom", is_channel=False, is_group=False, entity=SimpleNamespace(username=None)),
        SimpleNamespace(id=-77, name="Traders", is_channel=True, is_group=True, entity=SimpleNamespace()),
    ]  # fmt: skip
    chats = run(gateway(FakeTelethon(is_user_authorized=True, dialogs=dialogs)).chats(1, "h", "S"))
    assert [(c.id, c.title, c.kind, c.username) for c in chats] == [
        (-100123, "Nifty Sensex VIP setups", "channel", "vipsetups"),
        (-77, "Traders", "group", None),
    ]
    with pytest.raises(SessionInvalid):
        run(gateway(FakeTelethon(is_user_authorized=False)).chats(1, "h", "S"))
    revoked = errors.AuthKeyUnregisteredError(request=None)
    with pytest.raises(SessionInvalid):
        run(gateway(FakeTelethon(is_user_authorized=revoked)).chats(1, "h", "S"))


def test_log_out() -> None:
    fake = FakeTelethon(is_user_authorized=True)
    run(gateway(fake).log_out(1, "h", "S"))
    assert fake.calls == ["connect", "is_user_authorized", "log_out", "disconnect"]
