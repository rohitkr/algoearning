"""Understanding a tips channel (ADR 0025): one message -> what it is, and a channel's messages -> its signals.

Pure and deterministic, no I/O: raw messages are stored exactly as received, so history can always be re-read with a
better parser. Each channel format is a *profile*; the first (`vip_setups`) is "Nifty Sensex VIP setups", studied on
500 messages (7 Sep - 8 Oct 2026; ported from algo-trading-claude's telegram_signals/parser.py):

  * a SIGNAL is two messages in the same minute: a header "🟢 BUY NIFTY 22450 CE / 💰 Entry : ₹150 - ₹154 /
    📊 Intraday Trade" and, replying to it, the DETAILS "🎯 TP 1: ₹169 / TP 2 / TP 3 / 🛑 Stop Loss: ₹135 /
    📝 Rationale / ⏳ Valid for"
  * updates reply to the header: TARGET "🎯 Target 2 done / 💹 Ltp ₹190", SL_HIT "🛑 STOP LOSS HIT | NIFTY 22450 CE",
    TICK "₹163 🔥🔥🔥" (most of the traffic: only a last price), MEDIA (a screenshot)
  * rare free text: ADVISORY (trail / book / exit advice: shown, never traded on its own), NOISE (greetings, promos,
    call notices), UNCLEAR (looks trade-related but fits nothing: shown, never traded).

A signal is tradable (`complete`) once it has its index, strike, CE/PE, action and stop-loss. Only its direction is
used for trading: BUY CE / SELL PE = bullish, BUY PE / SELL CE = bearish. A user's correction of a message's kind
(an override) wins over the parser."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

Kind = Literal["SIGNAL", "DETAILS", "TARGET", "SL_HIT", "TICK", "MEDIA", "ADVISORY", "NOISE", "UNCLEAR"]
KINDS: tuple[Kind, ...] = ("SIGNAL", "DETAILS", "TARGET", "SL_HIT", "TICK", "MEDIA", "ADVISORY", "NOISE", "UNCLEAR")
Status = Literal["OPEN", "T1", "T2", "T3", "SL_HIT"]
Direction = Literal["BULLISH", "BEARISH"]

INDICES = ("NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "BANKEX")
_NUM = r"([\d,]+(?:\.\d+)?)"
_IDX = "|".join(INDICES)


@dataclass(frozen=True)
class Parsed:
    kind: Kind
    data: Mapping[str, Any] = field(default_factory=dict)


class Profile(Protocol):
    name: str

    def parse(self, text: str, *, reply_to: int | None = None, has_media: bool = False) -> Parsed: ...


def _f(s: str | None) -> float | None:
    return float(s.replace(",", "")) if s else None


class VipSetups:
    """'Nifty Sensex VIP setups' (and channels that post the same way)."""

    name = "vip_setups"
    SIGNAL = re.compile(rf"\b(BUY|SELL)\s+({_IDX})\s+(\d{{4,6}})\s*(CE|PE)\b", re.I)
    SYMBOL = re.compile(rf"\b({_IDX})\s+(\d{{4,6}})\s*(CE|PE)\b", re.I)
    ENTRY = re.compile(rf"Entry\s*:?\s*₹?\s*{_NUM}(?:\s*(?:-|\u2013|to)\s*₹?\s*{_NUM})?", re.I)
    TP = re.compile(rf"\bTP\s*(\d)\s*:?\s*₹?\s*{_NUM}", re.I)
    SL = re.compile(rf"(?:Stop\s*Loss|\bSL)\s*[:\-\u2013]?\s*₹?\s*{_NUM}", re.I)
    # the first weeks (from 9 July 2026): "BUY SENSEX 76900 CE @ 240", "SL - 180 / Tgt - 330, 420, 520 ++++"
    AT = re.compile(rf"@\s*₹?\s*{_NUM}(?:\s*(?:-|\u2013|to)\s*₹?\s*{_NUM})?", re.I)
    TGT = re.compile(r"\b(?:Tgts?|Targets?)\s*[:\-\u2013]\s*([^\n]+)", re.I)
    RATIONALE = re.compile(r"Rationale\s*:\s*([^\n|]+)", re.I)
    VALID = re.compile(r"Valid\s*for\s*:\s*([^\n|]+)", re.I)
    TARGET_DONE = re.compile(r"Target\s*(\d)\s*(?:done|hit|achieved)", re.I)
    SL_HIT = re.compile(r"STOP\s*LOSS\s*HIT|\bSL\s*HIT\b", re.I)
    LTP = re.compile(rf"\bLtp\s*:?\s*₹?\s*{_NUM}", re.I)
    TICK = re.compile(rf"^\s*₹?\s*{_NUM}(?:\s*(?:-|\u2013)\s*₹?\s*{_NUM})?\s*(?:[🔥🚀]\s*)+$")
    EXIT = re.compile(r"\bexit\b|\bbook\s+all\b|\bsquare\s*off\b", re.I)
    TRAIL = re.compile(rf"\btrail\w*\s+sl\b(?:\s+(?:near|at|to))?\s*{_NUM}?", re.I)
    BOOK = re.compile(rf"\b(?:book|booking)\b.*?(?:near|at)\s*{_NUM}", re.I)
    TRADEY = re.compile(r"\b(ce|pe|sl|stop|target|tgt|entry|buy|sell|strike|premium|exit|book)\b", re.I)

    def parse(self, text: str, *, reply_to: int | None = None, has_media: bool = False) -> Parsed:
        t = (text or "").strip()
        if not t:
            return Parsed("MEDIA" if has_media else "NOISE")
        m, e = self.SIGNAL.search(t), self.ENTRY.search(t) or self.AT.search(t)
        if m and e:
            lo, hi = _f(e.group(1)), _f(e.group(2)) or _f(e.group(1))
            assert lo is not None and hi is not None
            side, typ = m.group(1).upper(), m.group(4).upper()
            data: dict[str, Any] = {
                "action": side,
                "index": m.group(2).upper(),
                "strike": int(m.group(3)),
                "option_type": typ,
                "entry_low": min(lo, hi),
                "entry_high": max(lo, hi),
                "direction": "BULLISH" if (side == "BUY") == (typ == "CE") else "BEARISH",
                "intraday": bool(re.search(r"intraday", t, re.I)),
                **self._details(t),  # some posts put TP / SL in the header too
            }
            return Parsed("SIGNAL", data)
        if self.SL.search(t) and (self.TP.search(t) or self.TGT.search(t)):
            return Parsed("DETAILS", self._details(t))
        if mt := self.TARGET_DONE.search(t):
            ltp = self.LTP.search(t)
            return Parsed("TARGET", {"target": int(mt.group(1)), "ltp": _f(ltp.group(1)) if ltp else None})
        if self.SL_HIT.search(t):
            ltp, s = self.LTP.search(t), self.SYMBOL.search(t)
            sym = f"{s.group(1).upper()} {s.group(2)} {s.group(3).upper()}" if s else None
            return Parsed("SL_HIT", {"ltp": _f(ltp.group(1)) if ltp else None, "symbol": sym})
        if mk := self.TICK.match(t):
            return Parsed("TICK", {"price": _f(mk.group(2) or mk.group(1))})  # "240 - 250 🚀": it reached the top
        if self.EXIT.search(t) or self.TRAIL.search(t) or self.BOOK.search(t):
            adv: dict[str, Any] = {"exit": bool(self.EXIT.search(t))}
            tr, bk = self.TRAIL.search(t), self.BOOK.search(t)
            if tr and tr.group(1):
                adv["trail_sl"] = _f(tr.group(1))
            if bk:
                adv["book_near"] = _f(bk.group(1))
            return Parsed("ADVISORY", adv)
        if self.TRADEY.search(t):
            return Parsed("UNCLEAR")
        return Parsed("NOISE")

    def _details(self, t: str) -> dict[str, Any]:
        d: dict[str, Any] = {}
        tps = {int(n): _f(v) for n, v in self.TP.findall(t)}
        if tps:
            d["targets"] = [tps[k] for k in sorted(tps)]
        elif tg := self.TGT.search(t):
            d["targets"] = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", tg.group(1).replace(",", " "))]
        if sl := self.SL.search(t):
            d["stop_loss"] = _f(sl.group(1))
        if r := self.RATIONALE.search(t):
            d["rationale"] = r.group(1).strip()
        if v := self.VALID.search(t):
            d["valid_for"] = v.group(1).strip()
        return d


PROFILES: dict[str, Profile] = {p.name: p for p in (VipSetups(),)}
DEFAULT_PROFILE = "vip_setups"


# -- assembling signals ------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Message:
    """A stored channel message (the fields assembly needs)."""

    msg_id: int
    date: datetime
    text: str
    reply_to: int | None = None
    has_media: bool = False


@dataclass
class Signal:
    id: int  # the header message's id
    date: datetime
    index: str
    strike: int
    option_type: str
    action: str
    direction: Direction
    entry_low: float
    entry_high: float
    intraday: bool
    stop_loss: float | None = None
    targets: list[float] = field(default_factory=list)
    rationale: str | None = None
    valid_for: str | None = None
    status: Status = "OPEN"
    targets_done: list[int] = field(default_factory=list)
    last_price: float | None = None
    message_ids: list[int] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Tradable: everything known, stop-loss included."""
        return self.stop_loss is not None

    @property
    def tip(self) -> str:
        return f"{self.action} {self.index} {self.strike} {self.option_type}"

    def apply(self, data: Mapping[str, Any]) -> None:
        if "targets" in data:
            self.targets = [float(x) for x in data["targets"]]
        for k in ("stop_loss", "rationale", "valid_for"):
            if data.get(k) is not None:
                setattr(self, k, data[k])


@dataclass(frozen=True)
class Read:
    """How one message was read, and the signal it belongs to (if any)."""

    msg_id: int
    kind: Kind
    data: Mapping[str, Any]
    signal_id: int | None
    overridden: bool = False


def _latest_for(signals: Mapping[int, Signal], latest: int, symbol: str | None) -> int | None:
    """The signal a message that is not a reply belongs to: the latest one, unless the message names another contract
    ("STOP LOSS HIT | NIFTY 22450 CE"), then the latest signal on that contract, and none when there is none (an update
    for a contract we never saw must not close a different tip)."""
    if symbol is None:
        return latest
    for sig in sorted(signals.values(), key=lambda x: (x.date, x.id), reverse=True):
        if f"{sig.index} {sig.strike} {sig.option_type}" == symbol:
            return sig.id
    return None


def assemble(
    messages: Iterable[Message],
    profile: str = DEFAULT_PROFILE,
    overrides: Mapping[int, Kind] | None = None,
) -> tuple[list[Signal], list[Read]]:
    """A channel's messages (any order) -> its signals (oldest first) and how every message was read.

    Updates attach to a signal by reply; a target, SL hit or advice that is not a reply attaches to the latest signal.
    Status: OPEN -> T1 / T2 / T3 (the highest target reported) or SL_HIT; ticks only update the last price. An
    override replaces the parser's kind for that message; overridden into SIGNAL or DETAILS it must still parse as
    one, else it counts as UNCLEAR."""
    p = PROFILES[profile]
    overrides = overrides or {}
    signals: dict[int, Signal] = {}
    owner: dict[int, int] = {}  # any message of a signal (header, details, an update) -> its signal
    reads: list[Read] = []
    latest: int | None = None
    for m in sorted(messages, key=lambda x: (x.date, x.msg_id)):
        parsed = p.parse(m.text, reply_to=m.reply_to, has_media=m.has_media)
        forced = overrides.get(m.msg_id)
        if forced is not None and forced != parsed.kind:
            # a user can demote a message (e.g. to NOISE or UNCLEAR) or name an update's kind; a signal's or its
            # details' fields come only from the parser, so forcing those makes it UNCLEAR (never traded)
            parsed = Parsed("UNCLEAR") if forced in ("SIGNAL", "DETAILS") else Parsed(forced)
        kind = parsed.kind
        sid: int | None = None
        if kind == "SIGNAL":
            d = parsed.data
            sig = Signal(m.msg_id, m.date, d["index"], d["strike"], d["option_type"], d["action"], d["direction"],
                         d["entry_low"], d["entry_high"], d["intraday"])  # fmt: skip
            sig.apply(d)
            signals[m.msg_id] = sig
            latest = sid = m.msg_id
        elif m.reply_to in owner:  # a reply to the header or to any message already attached (details, an update)
            sid = owner[m.reply_to]
        elif kind in ("ADVISORY", "SL_HIT", "TARGET") and latest is not None:
            sid = _latest_for(signals, latest, parsed.data.get("symbol"))
        s = signals.get(sid) if sid is not None else None
        if s is not None:
            s.message_ids.append(m.msg_id)
            owner[m.msg_id] = s.id
            if kind == "DETAILS":
                s.apply(parsed.data)
            elif kind == "TARGET" and parsed.data.get("target"):
                s.targets_done = sorted(set(s.targets_done) | {int(parsed.data["target"])})
                if s.status != "SL_HIT":
                    s.status = f"T{min(max(s.targets_done), 3)}"  # type: ignore[assignment]
                s.last_price = parsed.data.get("ltp") or s.last_price
            elif kind == "SL_HIT":
                s.status = "SL_HIT"
                s.last_price = parsed.data.get("ltp") or s.last_price
            elif kind == "TICK" and parsed.data.get("price") is not None:
                s.last_price = parsed.data["price"]
        reads.append(Read(m.msg_id, kind, dict(parsed.data), sid, forced is not None))
    return sorted(signals.values(), key=lambda x: (x.date, x.id)), reads
