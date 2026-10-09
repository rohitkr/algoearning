"""The signal reader with a fake Telegram connection: catch-up and live messages become stored messages and signals,
an edit is applied, a revoked session turns the source to "reconnect", a disconnected source stops being read."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest
from ae_core.secrets import SecretBox, new_master_key
from ae_db.models import SignalMessage, SignalRow, SignalSource, User
from ae_db.session import Database
from ae_signals.service import SignalsService
from ae_telegram import Message, SessionInvalid
from sqlalchemy import select

T0 = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)
HEADER = "🟢 BUY NIFTY 22450 CE\n💰 Entry : ₹150 - ₹154\n📊 Intraday Trade"
DETAILS = "🎯 TP 1: ₹169\n🎯 TP 2: ₹184\n🎯 TP 3: ₹204\n🛑 Stop Loss: ₹135\n📝 Rationale: Reversal"


def m(i: int, secs: int, text: str, reply_to: int | None = None, edited: bool = False) -> Message:
    return Message(i, T0 + timedelta(seconds=secs), text, T0 + timedelta(minutes=30) if edited else None,
                   reply_to, False)  # fmt: skip


class FakeReader:
    """Catch-up returns `history`; listen hands the callback to the test and waits until closed."""

    instances: ClassVar[list[FakeReader]] = []
    fail: ClassVar[Exception | None] = None
    history: ClassVar[list[Message]] = []

    def __init__(self, api_id: int, api_hash: str, session: str) -> None:
        self.args = (api_id, api_hash, session)
        self.deliver: Callable[[Message], Awaitable[None]] | None = None
        self.closed = asyncio.Event()
        FakeReader.instances.append(self)

    async def start(self, chat_id: int) -> None:
        self.chat_id = chat_id
        if FakeReader.fail:
            raise FakeReader.fail

    async def recent(self, limit: int, since: datetime | None = None) -> list[Message]:
        return list(reversed(FakeReader.history))  # Telegram gives newest first

    async def listen(self, on_message: Callable[[Message], Awaitable[None]]) -> None:
        self.deliver = on_message
        await self.closed.wait()

    async def close(self) -> None:
        self.closed.set()


class Pub:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def publish(self, channel: str, message: str) -> None:
        self.sent.append((channel, json.loads(message)))


async def eventually(check: Callable[[], Awaitable[bool]]) -> None:
    """Poll (up to 3 seconds: the state is in the database, there is no event to wait on) until `check` is true."""
    for _ in range(150):
        if await check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


@pytest.fixture
async def setup(db: Database) -> Any:
    box = SecretBox(
        {1: __import__("ae_core.secrets", fromlist=["load_master_key"]).load_master_key(new_master_key())}, 1
    )
    async with db.system_session() as s:
        u = User(auth_subject="sub-a", email="a@example.com")
        s.add(u)
        await s.flush()
        src = SignalSource(id=uuid.uuid4(), user_id=u.id, key_version=1, status="connected", chat_id=-100123,
                           chat_title="VIP", connected_at=T0)  # fmt: skip
        src.session_enc = box.encrypt("SESSION", f"signal_source:{src.id}:session")
        s.add(src)
    FakeReader.instances, FakeReader.fail = [], None
    FakeReader.history = [m(1, 0, HEADER), m(2, 5, DETAILS, 1), m(3, 60, "₹157 🔥🔥🔥", 1)]
    pub = Pub()
    service = SignalsService(db, box, (611335, "f" * 32), publisher=pub, reader_factory=FakeReader)
    yield service, src.id, pub
    await service.stop()


async def rows(db: Database, model: Any, source_id: uuid.UUID) -> list[Any]:
    async with db.system_session() as s:
        return list((await s.execute(select(model).where(model.source_id == source_id))).scalars())


async def source(db: Database, source_id: uuid.UUID) -> SignalSource:
    async with db.system_session() as s:
        src = await s.get(SignalSource, source_id)
        assert src is not None
        return src


async def test_catch_up_then_live_messages(db: Database, setup: Any) -> None:
    service, sid, pub = setup
    await service.sync()
    await eventually(lambda: _state(db, sid, "listening"))
    reader = FakeReader.instances[0]
    assert reader.args == (611335, "f" * 32, "SESSION") and reader.chat_id == -100123  # the platform app
    (sig,) = await rows(db, SignalRow, sid)
    assert (sig.header_msg_id, sig.status, sig.complete, sig.last_price, sig.stop_loss) == (1, "OPEN", True, 157, 135)
    assert len(await rows(db, SignalMessage, sid)) == 3

    assert reader.deliver is not None
    await reader.deliver(m(4, 300, "🎯 Target 1 done\n💹 Ltp ₹170", 1))
    await reader.deliver(m(3, 60, "₹158 🔥🔥🔥", 1, edited=True))  # an edit replaces the text
    await reader.deliver(m(3, 60, "₹158 🔥🔥🔥", 1, edited=True))  # the same again: nothing changes
    (sig,) = await rows(db, SignalRow, sid)
    assert (sig.status, sig.targets_done, sig.last_price) == ("T1", [1], 170)
    msgs = {x.msg_id: x for x in await rows(db, SignalMessage, sid)}
    assert msgs[3].text == "₹158 🔥🔥🔥" and msgs[3].edit_date is not None and len(msgs) == 4
    assert [p[1]["signals"] for p in pub.sent] == [[1], [1], [1]]  # catch-up, the target, the edit
    assert (await source(db, sid)).last_message_at == T0 + timedelta(seconds=300)


async def test_a_restart_rebuilds_signals_from_the_stored_messages(db: Database, setup: Any) -> None:
    """A better parser must re-read messages stored earlier: nothing changed on Telegram, signals still come back."""
    from sqlalchemy import delete

    service, sid, _ = setup
    await service.sync()
    await eventually(lambda: _state(db, sid, "listening"))
    await service.stop()
    async with db.system_session() as s:  # signals as an older parser left them: none
        await s.execute(delete(SignalRow).where(SignalRow.source_id == sid))
    assert await rows(db, SignalRow, sid) == []
    await service.sync()
    await eventually(lambda: _state(db, sid, "listening"))
    (sig,) = await rows(db, SignalRow, sid)
    assert (sig.header_msg_id, sig.complete) == (1, True)


async def test_a_revoked_session_asks_to_reconnect(db: Database, setup: Any) -> None:
    service, sid, _ = setup
    FakeReader.fail = SessionInvalid("the Telegram session is no longer valid: reconnect Telegram")
    await service.sync()
    await eventually(lambda: _status(db, sid, "needs_reconnect"))
    src = await source(db, sid)
    assert src.session_enc is None and src.reader_state == "off"
    await service.sync()
    assert service.tasks == {}  # nothing left to read


async def test_a_disconnected_source_stops_being_read(db: Database, setup: Any) -> None:
    service, sid, _ = setup
    await service.sync()
    await eventually(lambda: _state(db, sid, "listening"))
    async with db.system_session() as s:
        src = await s.get(SignalSource, sid)
        assert src is not None
        src.status, src.session_enc = "disconnected", None
    await service.sync()
    assert service.tasks == {}
    await eventually(lambda: _state(db, sid, "off"))
    assert FakeReader.instances[0].closed.is_set()


async def _state(db: Database, sid: uuid.UUID, want: str) -> bool:
    return (await source(db, sid)).reader_state == want


async def _status(db: Database, sid: uuid.UUID, want: str) -> bool:
    return (await source(db, sid)).status == want
