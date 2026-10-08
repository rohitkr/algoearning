"""Storing what a signal source's chat says and the signals assembled from it (ADR 0025). Used by the reader process
(system session) and the API (the user's session, row-level security); the parsing itself is ae_core.signals.

Messages are stored exactly as received: a new message is inserted, an edited one replaces its text and records the
edit time, nothing else ever changes them. Signals are always rebuilt from the messages (plus the user's overrides),
so they can never drift from what the channel said."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime

from ae_core.signals import Kind, Message, Signal, assemble
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import SignalMessage, SignalOverride, SignalRow, SignalSource


async def save_messages(
    s: AsyncSession, src: SignalSource, messages: Iterable[Message], edits: dict[int, datetime | None]
) -> int:
    """Insert new messages, update edited ones (their text and edit_date); returns how many rows changed. `edits`:
    the edit time of each message (None = never edited)."""
    changed = 0
    for m in messages:
        stmt = insert(SignalMessage).values(
            user_id=src.user_id, source_id=src.id, msg_id=m.msg_id, date=m.date, edit_date=edits.get(m.msg_id),
            text=m.text, reply_to=m.reply_to, has_media=m.has_media,
        )  # fmt: skip
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_id", "msg_id"],
            set_={"text": stmt.excluded.text, "edit_date": stmt.excluded.edit_date,
                  "has_media": stmt.excluded.has_media},
            # only a real change: re-reading the same history does nothing
            where=(SignalMessage.text != stmt.excluded.text)
            | (SignalMessage.edit_date.is_distinct_from(stmt.excluded.edit_date)),
        )  # fmt: skip
        r = await s.execute(stmt.returning(SignalMessage.id))
        changed += len(r.all())  # a row comes back only when it was inserted or really changed
    return changed


async def load_messages(s: AsyncSession, source_id: uuid.UUID, since: datetime | None = None) -> list[Message]:
    q = select(SignalMessage).where(SignalMessage.source_id == source_id)
    if since is not None:
        q = q.where(SignalMessage.date >= since)
    rows = (await s.execute(q.order_by(SignalMessage.date, SignalMessage.msg_id))).scalars()
    return [Message(r.msg_id, r.date, r.text, r.reply_to, r.has_media) for r in rows]


async def load_overrides(s: AsyncSession, source_id: uuid.UUID) -> dict[int, Kind]:
    rows = await s.execute(
        select(SignalOverride.msg_id, SignalOverride.kind).where(SignalOverride.source_id == source_id)
    )
    return {int(m): k for m, k in rows.all()}  # type: ignore[misc]


async def rebuild(s: AsyncSession, src: SignalSource, since: datetime | None = None) -> list[Signal]:
    """Re-assemble the source's signals from its messages since `since` (None: all of them) and store them: new
    signals inserted, changed ones updated, and signals whose header in that window no longer reads as one (an
    override) removed. Returns the assembled signals."""
    signals, _ = assemble(await load_messages(s, src.id, since), src.profile, await load_overrides(s, src.id))
    keep = []
    for sig in signals:
        values = {
            "date": sig.date, "index": sig.index, "strike": sig.strike, "option_type": sig.option_type,
            "action": sig.action, "direction": sig.direction, "entry_low": sig.entry_low,
            "entry_high": sig.entry_high, "stop_loss": sig.stop_loss, "targets": sig.targets,
            "targets_done": sig.targets_done, "rationale": (sig.rationale or "")[:300] or None,
            "valid_for": (sig.valid_for or "")[:100] or None, "intraday": sig.intraday, "status": sig.status,
            "last_price": sig.last_price, "complete": sig.complete, "message_ids": sig.message_ids,
        }  # fmt: skip
        stmt = insert(SignalRow).values(user_id=src.user_id, source_id=src.id, header_msg_id=sig.id, **values)
        await s.execute(stmt.on_conflict_do_update(index_elements=["source_id", "header_msg_id"], set_=values))
        keep.append(sig.id)
    gone = delete(SignalRow).where(SignalRow.source_id == src.id, SignalRow.header_msg_id.not_in(keep or [-1]))
    if since is not None:
        gone = gone.where(SignalRow.date >= since)
    await s.execute(gone)
    return signals
