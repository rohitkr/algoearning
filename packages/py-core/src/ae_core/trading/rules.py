"""Trading rules as pure functions. The range-breakout and 0DTE rules are ported unchanged from
algo-trading-claude's backtest/rules.py (the backtested specification); the time-based helpers are new."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from typing import Literal

Right = Literal["CE", "PE"]
UP, DOWN = "UP", "DOWN"
REENTRY_CUTOFF = timedelta(minutes=15)  # positional re-entry must happen this long before the final exit


def atm_strike(spot: float, step: int) -> int:
    return int(round(spot / step) * step)


# -- positional range breakout ----------------------------------------------------------------------------------
def breakout(close: float, hi: float, lo: float) -> str | None:
    """UP on a close above the range high, DOWN below the low. UP is checked first."""
    if close > hi:
        return UP
    if close < lo:
        return DOWN
    return None


def breakout_right(direction: str) -> Right:
    """Upside breakout sells a PUT, downside sells a CALL."""
    return "PE" if direction == UP else "CE"


def itm_strike(spot: float, right: Right, itm_points: int, step: int) -> int:
    """Strike `itm_points` in the money from the ATM of `spot`: PUT above ATM, CALL below."""
    atm = atm_strike(spot, step)
    return atm + itm_points if right == "PE" else atm - itm_points


def spot_stop_level(ref: float, right: Right, sl_pct: float) -> float:
    move = sl_pct / 100 * ref
    return ref - move if right == "PE" else ref + move


def spot_stop_hit(spot_close: float, ref: float, right: Right, sl_pct: float) -> bool:
    """The underlying closed sl_pct against a short `right` entered at spot `ref`."""
    move = sl_pct / 100 * ref
    return spot_close <= ref - move if right == "PE" else spot_close >= ref + move


def spot_reentry_ok(spot_close: float, entry_spot: float, right: Right) -> bool:
    """Re-entry "at cost": the underlying is back at (or through) the original entry level."""
    return spot_close >= entry_spot if right == "PE" else spot_close <= entry_spot


def reentry_window_open(ts: datetime, final_ts: datetime) -> bool:
    return ts < final_ts - REENTRY_CUTOFF


def wing_strike(strike: int, right: Right, width: int) -> int:
    """The hedge bought `width` points further out of the money than the short strike."""
    return strike - width if right == "PE" else strike + width


# -- 0DTE ITM straddle ------------------------------------------------------------------------------------------
def straddle_strikes(spot: float, itm_points: int, step: int) -> dict[Right, int]:
    """CALL at ATM - itm_points, PUT at ATM + itm_points (ATM straddle when itm_points = 0)."""
    atm = atm_strike(spot, step)
    return {"CE": atm - itm_points, "PE": atm + itm_points}


def premium_stop_level(entry_price: float, sl_pct: float) -> float:
    return entry_price * (1 + sl_pct / 100)


def premium_reentry_ok(option_price: float, first_entry: float) -> bool:
    return option_price <= first_entry


# -- expiries ---------------------------------------------------------------------------------------------------
def monthly_expiries(expiries: Sequence[date]) -> list[date]:
    """The last listed expiry of each calendar month."""
    last: dict[tuple[int, int], date] = {}
    for e in sorted(expiries):
        last[(e.year, e.month)] = e
    return sorted(last.values())


def pick_expiry(expiries: Sequence[date], today: date, which: str) -> date | None:
    """current_week / next_week: the first / second listed expiry from today; current_month / next_month: the same
    among monthly expiries. None when the list does not reach that far."""
    live = sorted(e for e in expiries if e >= today)
    pool = monthly_expiries(live) if which.endswith("month") else live
    idx = 1 if which.startswith("next") else 0
    return pool[idx] if len(pool) > idx else None


def expiry_after(expiries: Sequence[date], day: date, offset: int = 0) -> date | None:
    """The listed expiry strictly after `day`, then `offset` more (range breakout: never the entry day's own)."""
    later = sorted(e for e in expiries if e > day)
    return later[offset] if len(later) > offset else None


# -- time-based legs --------------------------------------------------------------------------------------------
def offset_strike(spot: float, right: Right, offset: int, step: int) -> int:
    """ATM +/- `offset` strikes, positive = out of the money (calls up, puts down)."""
    atm = atm_strike(spot, step)
    return atm + offset * step if right == "CE" else atm - offset * step


def closest_premium(prices: dict[int, float], premium: float) -> int | None:
    """The strike whose price is closest to `premium` (ties: the cheaper, further-out one)."""
    if not prices:
        return None
    return min(prices, key=lambda k: (abs(prices[k] - premium), prices[k]))


def distance(value: float, unit: str, ref: float) -> float:
    return value if unit == "points" else ref * value / 100


def hhmm(t: datetime) -> str:
    return t.strftime("%H:%M")


def at(day: date, hm: str, tz: object) -> datetime:
    h, m = (int(x) for x in hm.split(":"))
    return datetime.combine(day, time(h, m), tzinfo=tz)  # type: ignore[arg-type]
