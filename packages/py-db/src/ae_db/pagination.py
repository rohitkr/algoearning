"""Keyset (cursor) pagination on (created_at, id): stable under inserts, fast at any depth. The cursor is an
opaque base64 token so clients cannot depend on its contents."""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, TypeVar

T = TypeVar("T")
MAX_LIMIT = 100


@dataclass
class Page(Generic[T]):
    items: list[T]
    next_cursor: str | None


def encode_cursor(created_at: datetime, id_: uuid.UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(id_)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        ts, id_ = json.loads(raw)
        return datetime.fromisoformat(ts), uuid.UUID(id_)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid cursor") from exc


def clamp_limit(limit: Any) -> int:
    return max(1, min(int(limit or 20), MAX_LIMIT))
