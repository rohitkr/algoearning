"""Choosing an option contract: expiry, strike and a liquidity check. Shared by every runner that picks strikes,
so an ATM offset or a premium target means the same thing in the builder and in the SMC scalper."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from ..strategy import Strike
from . import rules
from .model import Contract, Market, Quote
from .rules import Right

CANDIDATE_OFFSETS = range(-4, 16)  # strikes looked at when choosing by premium (ITM 4 .. OTM 15)


def nearest_expiry(
    expiries: Sequence[date], now: datetime, which: str = "nearest", cutoff: str | None = None
) -> date | None:
    """The nearest listed expiry (or the one after it with which="next"). On expiry day from `cutoff` (HH:MM) on,
    the next one: a bought option loses its time value fastest in its last hours."""
    live = sorted(e for e in expiries if e >= now.date())
    if live and live[0] == now.date() and cutoff is not None and rules.hhmm(now) >= cutoff:
        live = live[1:]
    idx = 1 if which == "next" else 0
    return live[idx] if len(live) > idx else None


def candidates(m: Market, right: Right, expiry: date, offsets: Sequence[int] = CANDIDATE_OFFSETS) -> list[Contract]:
    if m.spot is None:
        return []
    return [
        Contract(m.underlying, expiry, rules.offset_strike(m.spot, right, k, m.strike_step), right) for k in offsets
    ]


def pick(m: Market, right: Right, strike: Strike, expiry: date, offset_shift: int = 0) -> Contract | str:
    """The contract for a strike rule right now, or why it cannot be chosen yet. `offset_shift` moves an ATM-offset
    choice that many strikes (the liquidity fallback)."""
    if m.spot is None:
        return "no index price yet"
    if strike.mode == "atm":
        k = rules.offset_strike(m.spot, right, strike.offset + offset_shift, m.strike_step)
        return Contract(m.underlying, expiry, k, right)
    if strike.mode == "points":
        # the strike nearest the index +/- points, out of the money for positive points (calls up, puts down)
        target = m.spot + (strike.points or 0) * (1 if right == "CE" else -1)
        return Contract(m.underlying, expiry, rules.atm_strike(target, m.strike_step), right)
    prices = {c.strike: m.price(c) for c in candidates(m, right, expiry)}
    known = {k: v for k, v in prices.items() if v is not None}
    if len(known) < len(prices) // 2 or not known:
        return "waiting for option prices to choose the strike"
    want = float(strike.premium or 0)
    if strike.mode == "premium_gte":
        known = {k: v for k, v in known.items() if v >= want}
        chosen = min(known, key=lambda k: known[k]) if known else None
    elif strike.mode == "premium_lte":
        known = {k: v for k, v in known.items() if v <= want}
        chosen = max(known, key=lambda k: known[k]) if known else None
    else:
        chosen = rules.closest_premium(known, want)
    if chosen is None:
        side = "at least" if strike.mode == "premium_gte" else "at most"
        return f"no {right} strike costs {side} ₹{want:g}"
    return Contract(m.underlying, expiry, int(chosen), right)


def illiquid(q: Quote | None, min_volume: int, min_oi: int, max_spread_pct: float) -> str | None:
    """Why a contract is too illiquid to buy, or None. A figure the feed did not provide is not held against it."""
    if q is None:
        return None
    if min_volume and q.volume is not None and q.volume < min_volume:
        return f"volume {q.volume:,} below {min_volume:,}"
    if min_oi and q.oi is not None and q.oi < min_oi:
        return f"open interest {q.oi:,} below {min_oi:,}"
    spread = q.spread_pct
    if spread is not None and spread > max_spread_pct:
        return f"bid/ask spread {spread:.2f}% above {max_spread_pct:g}%"
    return None
