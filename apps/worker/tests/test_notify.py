"""Notifications: settings decide who gets what, email and Telegram go out, failures retry then fail, Telegram
linking by code, and the engine-down alert."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import httpx
import pytest
from ae_core.notifications import compose, enabled_events
from ae_db.models import Notification, NotificationSettings, Strategy, StrategyRun, User
from ae_db.session import Database
from ae_worker.notify import EmailConfig, Sender, check_engine, dispatch_pending, link_telegram
from sqlalchemy import select


class FakeSMTP:
    sent: ClassVar[list[Any]] = []
    fail: ClassVar[bool] = False

    def __init__(self, host: str, port: int, timeout: int = 0) -> None:
        pass

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def starttls(self) -> None: ...
    def login(self, u: str, p: str) -> None: ...

    def send_message(self, msg: Any) -> None:
        if FakeSMTP.fail:
            raise OSError("connection refused")
        FakeSMTP.sent.append(msg)


class FakeTelegram:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.updates: list[dict[str, Any]] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        method = req.url.path.rsplit("/", 1)[1]
        body = json.loads(req.content)
        self.calls.append((method, body))
        result: Any = self.updates if method == "getUpdates" else {"message_id": 1}
        return httpx.Response(200, json={"ok": True, "result": result})


def sender(tg: FakeTelegram | None = None, email: bool = True) -> Sender:
    return Sender(
        EmailConfig("smtp.test") if email else None,
        "bot-token" if tg else None,
        httpx.MockTransport(tg.handler) if tg else None,
        FakeSMTP,
    )


@pytest.fixture(autouse=True)
def reset() -> None:
    FakeSMTP.sent, FakeSMTP.fail = [], False


async def user_with(db: Database, **settings: Any) -> User:
    async with db.system_session() as s:
        u = User(auth_subject="sub", email="rohit@example.com")
        s.add(u)
        await s.flush()
        if settings:
            s.add(NotificationSettings(user_id=u.id, **settings))
        return u


async def queue(db: Database, user: User, event: str, title: str = "Order failed: S") -> None:
    async with db.system_session() as s:
        s.add(Notification(user_id=user.id, event=event, title=title, body="Reason: margin"))


async def rows(db: Database) -> list[Notification]:
    async with db.system_session() as s:
        return list((await s.execute(select(Notification).order_by(Notification.created_at))).scalars())


def test_catalogue() -> None:
    assert "stop_loss" in enabled_events(None) and "trade_opened" not in enabled_events(None)
    assert enabled_events(["trade_opened", "bogus"]) == {"trade_opened"}
    assert compose("S", "t", {"contract": "NIFTY 25000 CE", "reason": "margin", "x": 1}) == (
        "Strategy: S\nContract: NIFTY 25000 CE\nReason: margin"
    )


async def test_defaults_email_the_account_address(db: Database) -> None:
    u = await user_with(db)
    await queue(db, u, "order_problem")
    await queue(db, u, "trade_opened", "Position opened")  # off by default
    assert await dispatch_pending(db, sender()) == 2
    a, b = await rows(db)
    assert (a.status, a.sent_via, b.status) == ("sent", ["email"], "skipped")
    assert FakeSMTP.sent[0]["To"] == "rohit@example.com" and "[AlgoEarning] Order failed" in FakeSMTP.sent[0]["Subject"]


async def test_choices_telegram_only_and_custom_events(db: Database) -> None:
    tg = FakeTelegram()
    u = await user_with(db, email_enabled=False, telegram_enabled=True, telegram_chat_id="42", events=["trade_opened"])
    await queue(db, u, "trade_opened", "Position opened")
    await queue(db, u, "order_problem")  # not chosen
    await dispatch_pending(db, sender(tg))
    a, b = await rows(db)
    assert (a.status, a.sent_via, b.status) == ("sent", ["telegram"], "skipped")
    assert tg.calls[0][0] == "sendMessage" and tg.calls[0][1]["chat_id"] == "42" and FakeSMTP.sent == []


async def test_no_channel_and_retries_then_fails(db: Database) -> None:
    u = await user_with(db, email_enabled=False, telegram_enabled=False)
    await queue(db, u, "order_problem")
    await dispatch_pending(db, sender())
    (n,) = await rows(db)
    assert n.status == "skipped" and "no channel" in (n.error or "")

    async with db.system_session() as s:
        st = (await s.execute(select(NotificationSettings))).scalar_one()
        st.email_enabled = True
        n2 = Notification(user_id=u.id, event="order_problem", title="x", body="y")
        s.add(n2)
    FakeSMTP.fail = True
    for _ in range(3):
        await dispatch_pending(db, sender())
    failed = next(r for r in await rows(db) if r.title == "x")
    assert failed.status == "failed" and failed.attempts == 3 and "connection refused" in (failed.error or "")


async def test_email_not_configured_is_reported(db: Database) -> None:
    u = await user_with(db)
    await queue(db, u, "order_problem")
    for _ in range(3):
        await dispatch_pending(db, sender(email=False))
    (n,) = await rows(db)
    assert n.status == "failed" and "not configured" in (n.error or "")


async def test_telegram_linking_by_code(db: Database) -> None:
    tg = FakeTelegram()
    u = await user_with(db, telegram_link_code="CODE123")
    tg.updates = [
        {"update_id": 10, "message": {"text": "/start CODE123", "chat": {"id": 777}}},
        {"update_id": 11, "message": {"text": "/start nope", "chat": {"id": 888}}},
        {"update_id": 12, "message": {"text": "hello", "chat": {"id": 888}}},
    ]
    assert await link_telegram(db, sender(tg)) == 1
    async with db.system_session() as s:
        st = (await s.execute(select(NotificationSettings).where(NotificationSettings.user_id == u.id))).scalar_one()
        assert (st.telegram_chat_id, st.telegram_enabled, st.telegram_link_code) == ("777", True, None)
    replies = [c[1]["text"] for c in tg.calls if c[0] == "sendMessage"]
    assert replies[0].startswith("Connected") and "not valid" in replies[1]
    tg.calls.clear()
    await link_telegram(db, sender(tg))
    assert tg.calls[0][1]["offset"] == 13  # already-read updates are not read again


async def test_engine_down_alert_once_an_hour(db: Database) -> None:
    u = await user_with(db)
    now = datetime.now(UTC)
    async with db.system_session() as s:
        st = Strategy(user_id=u.id, name="S", config={}, kind="time_based")
        s.add(st)
        await s.flush()
        s.add(StrategyRun(user_id=u.id, strategy_id=st.id, mode="paper", status="running", config_snapshot={},
                          strategy_name="S", heartbeat_at=now - timedelta(minutes=5)))  # fmt: skip
    assert await check_engine(db, now) == 1
    assert await check_engine(db, now) == 0
    assert (await rows(db))[0].event == "engine_down"
