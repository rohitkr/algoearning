"""What the feed carries: instrument keys, 1-minute bars and last-price ticks. Plain data, JSON on the wire."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

IST = timezone(timedelta(hours=5, minutes=30))
Right = Literal["CE", "PE"]


@dataclass(frozen=True)
class InstrumentKey:
    """An index (spot) or one option contract. `id` is the stable string form used in Redis and the API:
    "NIFTY" or "NIFTY:2026-10-06:25000:CE"."""

    underlying: str
    expiry: date | None = None
    strike: int | None = None
    right: Right | None = None

    @property
    def is_option(self) -> bool:
        return self.expiry is not None

    @property
    def id(self) -> str:
        if not self.is_option:
            return self.underlying
        return f"{self.underlying}:{self.expiry:%Y-%m-%d}:{self.strike}:{self.right}"

    @classmethod
    def parse(cls, s: str) -> InstrumentKey:
        parts = s.split(":")
        if len(parts) == 1 and parts[0]:
            return cls(parts[0])
        if len(parts) == 4 and parts[3] in ("CE", "PE"):
            return cls(parts[0], date.fromisoformat(parts[1]), int(parts[2]), parts[3])  # type: ignore[arg-type]
        raise ValueError(f"not an instrument key: {s!r}")


@dataclass(frozen=True)
class Bar:
    """A completed 1-minute candle; `ts` is the minute it covers (start, IST)."""

    key: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

    def to_json(self) -> str:
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        return json.dumps(d)

    @classmethod
    def from_json(cls, s: str | bytes) -> Bar:
        d: dict[str, Any] = json.loads(s)
        d["ts"] = datetime.fromisoformat(d["ts"])
        return cls(**d)


@dataclass(frozen=True)
class Tick:
    """The latest traded price. `prev_close` is set when the source knows it (for the day's change)."""

    key: str
    ltp: float
    ts: datetime
    prev_close: float | None = None
    bid: float | None = None  # best bid / ask, quantity traded today and open interest, when the source sends them
    ask: float | None = None
    volume: int | None = None
    oi: int | None = None

    def to_json(self) -> str:
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        return json.dumps(d)

    @classmethod
    def from_json(cls, s: str | bytes) -> Tick:
        d: dict[str, Any] = json.loads(s)
        d["ts"] = datetime.fromisoformat(d["ts"])
        return cls(**d)


def minute_start(ts: datetime) -> datetime:
    return ts.astimezone(IST).replace(second=0, microsecond=0)


class BarBuilder:
    """Ticks -> 1-minute bars, for sources that only stream prices. A bar is emitted when the first tick of a later
    minute arrives, or when `close_until(now)` is called after its minute ended (no ticks in a quiet minute)."""

    def __init__(self) -> None:
        self._open: dict[str, list[Any]] = {}  # key -> [minute, o, h, l, c, volume]

    def add(self, key: str, price: float, ts: datetime, qty: int = 0) -> Bar | None:
        m = minute_start(ts)
        cur = self._open.get(key)
        done = None
        if cur is not None and m > cur[0]:
            done = self._emit(key)
            cur = None
        if cur is None:
            self._open[key] = [m, price, price, price, price, qty]
        elif m == cur[0]:
            cur[2], cur[3], cur[4], cur[5] = max(cur[2], price), min(cur[3], price), price, cur[5] + qty
        return done

    def close_until(self, now: datetime) -> list[Bar]:
        m = minute_start(now)
        return [self._emit(k) for k, cur in list(self._open.items()) if cur[0] < m]

    def _emit(self, key: str) -> Bar:
        m, o, h, low, c, v = self._open.pop(key)
        return Bar(key, m, o, h, low, c, v)
