"""Keeps one read-only Telegram reader per connected signal source (ADR 0025).

Every few seconds the sources are looked up: a source that is connected and has a chat gets a task, a source that
is no longer connected, changed its chat or logged in again gets its task replaced or stopped. A task connects,
catches up on the chat's last messages, then stores every new or edited message as it arrives; each change rebuilds
that source's recent signals and is published on Redis (`signals`) for the page and the engine. Strictly read-only.

Failures stay with their source: a flood wait sleeps as long as Telegram asks; a session that is no longer valid
turns the source to "needs_reconnect" and stops its task; anything else is retried with a growing pause."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Protocol

import structlog
from ae_core.secrets import SecretBox
from ae_core.signals import Message
from ae_db.models import SignalSource
from ae_db.session import Database
from ae_db.signal_store import rebuild, save_messages
from ae_telegram import Flood, SessionInvalid, TelegramReader
from ae_telegram import Message as TgMessage
from sqlalchemy import select, update

log = structlog.get_logger("ae_signals")

CATCH_UP = 200  # messages re-read when a reader starts, to fill anything missed while it was down
# Every start also re-reads the chat from here (saving what is already stored changes nothing): the first tip of the
# channel studied for the replay (ADR 0025) came on 9 July 2026, so history back to then is always complete.
BACKFILL_FROM = datetime(2026, 7, 9, tzinfo=timezone(timedelta(hours=5, minutes=30)))
LIVE_WINDOW = timedelta(days=3)  # signals rebuilt on each new message (tips are intraday)
SYNC_S = 5
RETRY_S = (5, 15, 30, 60, 120, 300)
CHANNEL = "signals"


class Reader(Protocol):
    async def start(self, chat_id: int) -> None: ...
    async def recent(self, limit: int, since: datetime | None = None) -> list[TgMessage]: ...
    async def listen(self, on_message: Callable[[TgMessage], Awaitable[Any]]) -> None: ...
    async def close(self) -> None: ...


ReaderFactory = Callable[[int, str, str], Reader]


class Publisher(Protocol):
    def publish(self, channel: str, message: str) -> Awaitable[Any]: ...


@dataclass(frozen=True)
class Plan:
    """What a source's reader needs; a task runs as long as its plan is unchanged."""

    source_id: uuid.UUID
    user_id: uuid.UUID
    chat_id: int
    fingerprint: tuple[Any, ...]


def _ctx(source_id: uuid.UUID, field: str) -> str:
    return f"signal_source:{source_id}:{field}"


class SignalsService:
    def __init__(
        self,
        db: Database,
        box: SecretBox,
        platform_app: tuple[int, str] | None,
        publisher: Publisher | None = None,
        reader_factory: ReaderFactory = TelegramReader,
    ) -> None:
        self.db, self.box, self.platform_app, self.publisher = db, box, platform_app, publisher
        self.reader_factory = reader_factory
        self.tasks: dict[uuid.UUID, tuple[Plan, asyncio.Task[None]]] = {}

    # -- which sources to read ------------------------------------------------------------------------------------
    async def plans(self) -> dict[uuid.UUID, Plan]:
        async with self.db.system_session() as s:
            q = select(SignalSource).where(
                SignalSource.status == "connected",
                SignalSource.chat_id.is_not(None),
                SignalSource.session_enc.is_not(None),
            )
            out = {}
            for src in (await s.execute(q)).scalars():
                assert src.chat_id is not None and src.session_enc is not None
                fp = (src.chat_id, src.api_id, src.connected_at, bytes(src.session_enc)[:16])
                out[src.id] = Plan(src.id, src.user_id, src.chat_id, fp)
            return out

    async def sync(self) -> None:
        """Start, replace or stop readers to match the sources."""
        wanted = await self.plans()
        for sid, (plan, task) in list(self.tasks.items()):
            if task.done() or wanted.get(sid) != plan:
                task.cancel()
                del self.tasks[sid]
        for sid, plan in wanted.items():
            if sid not in self.tasks:
                self.tasks[sid] = (plan, asyncio.create_task(self.run(plan), name=f"signals-{sid}"))

    async def stop(self) -> None:
        for _, task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*(t for _, t in self.tasks.values()), return_exceptions=True)
        self.tasks.clear()

    # -- one source -----------------------------------------------------------------------------------------------
    async def _set(self, source_id: uuid.UUID, **values: Any) -> None:
        async with self.db.system_session() as s:
            await s.execute(update(SignalSource).where(SignalSource.id == source_id).values(**values))

    async def _credentials(self, source_id: uuid.UUID) -> tuple[int, str, str] | None:
        async with self.db.system_session() as s:
            src = await s.get(SignalSource, source_id)
            if src is None or src.session_enc is None:
                return None
            session = self.box.decrypt(src.session_enc, _ctx(src.id, "session"))
            if src.api_id is not None and src.api_hash_enc is not None:
                return src.api_id, self.box.decrypt(src.api_hash_enc, _ctx(src.id, "api_hash")), session
        if self.platform_app is None:
            return None
        return self.platform_app[0], self.platform_app[1], session

    async def run(self, plan: Plan) -> None:
        attempt = 0
        while True:
            creds = await self._credentials(plan.source_id)
            if creds is None:
                await self._set(plan.source_id, reader_state="error",
                                reader_detail="no Telegram app configured for this source")  # fmt: skip
                return
            reader = self.reader_factory(*creds)
            try:
                await self._set(plan.source_id, reader_state="connecting", reader_detail=None)
                await reader.start(plan.chat_id)
                await self.store(plan, await reader.recent(CATCH_UP, since=BACKFILL_FROM), since=None, force=True)
                await self._set(plan.source_id, reader_state="listening", reader_detail=None)
                attempt = 0
                await reader.listen(lambda m: self.store(plan, [m], since=datetime.now(UTC) - LIVE_WINDOW))
                raise ConnectionError("the Telegram connection ended")
            except asyncio.CancelledError:
                await self._set(plan.source_id, reader_state="off", reader_detail=None)
                raise
            except SessionInvalid as exc:
                log.warning("telegram session no longer valid", source=str(plan.source_id))
                await self._set(plan.source_id, status="needs_reconnect", status_detail=str(exc), session_enc=None,
                                reader_state="off", reader_detail=None)  # fmt: skip
                return
            except Flood as exc:
                await self._set(plan.source_id, reader_state="error", reader_detail=str(exc))
                await asyncio.sleep(exc.seconds + 1)
            except Exception as exc:
                pause = RETRY_S[min(attempt, len(RETRY_S) - 1)]
                attempt += 1
                log.warning("signal reader failed", source=str(plan.source_id), error=type(exc).__name__)
                await self._set(
                    plan.source_id,
                    reader_state="error",
                    reader_detail=f"{type(exc).__name__}: {exc}"[:280] + f" (retrying in {pause}s)",
                )
                await asyncio.sleep(pause)  # fmt: skip
            finally:
                await reader.close()

    async def store(self, plan: Plan, messages: list[TgMessage], since: datetime | None, force: bool = False) -> int:
        """Store messages (new or edited) and, if anything changed, rebuild the source's signals. `force` rebuilds
        even when nothing changed: at reader start, so a better parser re-reads messages stored earlier."""
        if not messages and not force:
            return 0
        msgs = [Message(m.id, m.date, m.text, m.reply_to, m.has_media) for m in messages]
        async with self.db.system_session() as s:
            src = await s.get(SignalSource, plan.source_id)
            if src is None:
                return 0
            changed = await save_messages(s, src, msgs, {m.id: m.edit_date for m in messages})
            if changed or force:
                signals = await rebuild(s, src, since)
                newest = max((m.date for m in msgs), default=None)
                if newest and (src.last_message_at is None or newest > src.last_message_at):
                    src.last_message_at = newest
            else:
                signals = []
        if changed and self.publisher is not None:
            payload = {"user_id": str(plan.user_id), "source_id": str(plan.source_id),
                       "signals": [x.id for x in signals]}  # fmt: skip
            await self.publisher.publish(CHANNEL, json.dumps(payload))
        return changed
