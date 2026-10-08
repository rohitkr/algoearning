"""What runners see and produce. Everything a runner keeps is plain JSON (the engine stores it on the run after every
step), so a run survives an engine restart, including positions carried overnight."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, Protocol

from .rules import Right

IST = timezone(timedelta(hours=5, minutes=30))
Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class Contract:
    underlying: str
    expiry: date
    strike: int
    right: Right

    @property
    def key(self) -> str:
        """The price feed's key for this contract (ae_marketdata InstrumentKey.id)."""
        return f"{self.underlying}:{self.expiry:%Y-%m-%d}:{self.strike}:{self.right}"

    @classmethod
    def from_key(cls, key: str) -> Contract:
        u, e, k, r = key.split(":")
        return cls(u, date.fromisoformat(e), int(k), r)  # type: ignore[arg-type]

    @property
    def label(self) -> str:
        return f"{self.underlying} {self.expiry:%d %b} {self.strike} {self.right}"


class BarLike(Protocol):
    """A completed 1-minute candle; `ts` is the minute it covers (start)."""

    @property
    def ts(self) -> datetime: ...
    @property
    def open(self) -> float: ...
    @property
    def high(self) -> float: ...
    @property
    def low(self) -> float: ...
    @property
    def close(self) -> float: ...


@dataclass(frozen=True)
class Quote:
    """What the feed knows about a contract besides its last price (None: the source did not say)."""

    bid: float | None = None
    ask: float | None = None
    volume: int | None = None  # quantity traded today
    oi: int | None = None  # open interest

    @property
    def spread_pct(self) -> float | None:
        """(ask - bid) / mid in %, None without a two-sided quote."""
        if not self.bid or not self.ask or self.ask < self.bid:
            return None
        return (self.ask - self.bid) / ((self.ask + self.bid) / 2) * 100


@dataclass(frozen=True)
class Tip:
    """A Telegram tip as the signal reader assembled it (ADR 0025): only what trading needs."""

    id: int  # the header message's id in its chat
    source_id: str
    date: datetime  # when the channel posted it
    direction: Literal["BULLISH", "BEARISH"]
    status: str  # OPEN | T1 | T2 | T3 | SL_HIT
    complete: bool  # stop-loss given: tradable
    tip: str = ""  # e.g. "BUY NIFTY 22450 CE", for the log
    index: str = ""  # the tip's underlying, e.g. NIFTY: a strategy takes only tips for its own underlying


@dataclass
class Market:
    """One moment of market data for a run's underlying, built by the engine from the price feed."""

    now: datetime  # IST
    underlying: str
    spot: float | None
    spot_bars: Sequence[BarLike]  # today's completed 1-minute bars, oldest first
    prices: Mapping[str, float]  # contract key -> last price
    expiries: Sequence[date]  # listed option expiries, sorted
    lot_size: int
    strike_step: int
    # earlier sessions' 1-minute bars, oldest first: only for runners that ask (Runner.prior_days > 0)
    prior_spot_bars: Sequence[BarLike] = ()
    quotes: Mapping[str, Quote] = field(default_factory=dict)  # contract key -> bid/ask/volume/OI, when known
    tips: Sequence[Tip] = ()  # today's Telegram tips of the user's signal sources, oldest first (ADR 0025)

    def price(self, c: Contract | str) -> float | None:
        return self.prices.get(c if isinstance(c, str) else c.key)


@dataclass
class Intent:
    """An order the runner wants: open a leg (entry) or close a position (exit)."""

    kind: Literal["entry", "exit"]
    side: Side
    contract: Contract
    lots: int
    qty: int
    reason: str
    leg: str  # the leg (or role, e.g. "short", "wing") it belongs to
    position_id: str = field(default_factory=lambda: str(uuid.uuid4()))  # exit: the position being closed
    group: str | None = None  # entries that belong together (a short and its hedge wing)


@dataclass
class Position:
    id: str
    leg: str
    contract: Contract
    side: Side
    lots: int
    qty: int
    entry_price: float
    entry_time: datetime
    entry_spot: float | None = None
    sl: float | None = None  # on the premium, or on the index when sl_basis = underlying
    sl_basis: str = "premium"
    target: float | None = None
    target_basis: str = "premium"
    best: float | None = None  # most favourable price seen on the SL's basis (trailing)
    is_reentry: bool = False
    exit_price: float | None = None
    exit_time: datetime | None = None
    exit_reason: str | None = None
    group: str | None = None  # positions that enter and exit together (a short and its hedge wing)

    @property
    def open(self) -> bool:
        return self.exit_time is None

    @property
    def sign(self) -> int:
        return 1 if self.side == "BUY" else -1

    def pnl(self, ltp: float | None = None) -> float:
        px = self.exit_price if self.exit_price is not None else ltp
        return 0.0 if px is None else round((px - self.entry_price) * self.qty * self.sign, 2)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["contract"] = self.contract.key
        d["entry_time"] = self.entry_time.isoformat()
        d["exit_time"] = self.exit_time.isoformat() if self.exit_time else None
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Position:
        d = dict(d)
        d["contract"] = Contract.from_key(d["contract"])
        d["entry_time"] = datetime.fromisoformat(d["entry_time"])
        d["exit_time"] = datetime.fromisoformat(d["exit_time"]) if d.get("exit_time") else None
        return cls(**d)
