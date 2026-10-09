"""Replaying a tips channel's trades exactly as the tip gave them (ADR 0025, replay), over stored 1-minute history.

For every tradable signal (one with its stop-loss and targets) the replay does what a follower would have done:
buy the named option, with the tip's stop-loss and targets, one third of the position per target, and whatever is
left leaves at 15:15 (the tips are intraday). Nothing is optimised: no filters, no trailing, no second guessing.

Entry. The order goes in when the message arrives, so the first price a follower could get is the OPEN of the first
minute after the message. The tip names an entry range; most of the time the option has already moved:
    IN_ZONE      the open is inside, or below, the range: bought at the open
    CHASED       the open is above the range by no more than the buffer (5 points for NIFTY, 10 for SENSEX; premium
                 points): bought at the open and marked, because entering higher is riskier
    NOT_PLACED   the open is above range + buffer, or at/under the stop-loss already: no order is placed
    NO_DATA      no prices for that contract that day (or no stop-loss in the tip): cannot be replayed
The tip never names an expiry, so the expiry is inferred: of the next few expiries, the one whose price at the signal
minute is closest to the tip's entry range. It is shown with every trade.

Exits, minute by minute without look-ahead: when the stop and a target were both reachable in one minute the stop
is assumed to come first; a stop that the price gapped through fills at the open; a target the price gapped over fills
at the open; stop-loss and the 15:15 exit are market orders (slippage applies), targets are limit orders (none)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Literal, Protocol

from .backtest import Candle, Costs, slip, to_tick
from .reports import ClosedTrade, daily, summarize
from .trading.model import IST

Entry = Literal["IN_ZONE", "CHASED", "NOT_PLACED", "NO_DATA"]
BUFFER = {"NIFTY": 5.0, "SENSEX": 10.0}  # premium points accepted above the top of the entry range
DEFAULT_BUFFER = 5.0
EXIT_AT = time(15, 15)
EXPIRIES_TRIED = 3


class TipPrices(Protocol):
    def expiries(self, underlying: str) -> list[date]: ...

    def option(self, key: str, day: date) -> Mapping[datetime, Candle]: ...


@dataclass(frozen=True)
class Tip:
    """A signal as stored (ae_core.signals.Signal / the signals table), reduced to what the replay needs."""

    id: int
    date: datetime  # when the header message was posted
    index: str
    strike: int
    option_type: str
    action: str
    entry_low: float
    entry_high: float
    stop_loss: float | None
    targets: Sequence[float]
    status: str = "OPEN"  # what the channel itself reported: OPEN / T1 / T2 / T3 / SL_HIT


@dataclass(frozen=True)
class ReplayParams:
    lots: int = 3  # split as evenly as possible over the targets (3 lots = one per target)
    lot_sizes: Mapping[str, int] = field(default_factory=lambda: {"NIFTY": 75, "SENSEX": 20})
    slippage_pct: float = 0.05  # on market orders: the stop-loss and the end-of-day exit
    buffer: Mapping[str, float] = field(default_factory=lambda: dict(BUFFER))
    costs: Costs = field(default_factory=Costs)


@dataclass(frozen=True)
class Fill:
    time: datetime
    price: float
    qty: int
    reason: str  # TARGET 1..3, STOP LOSS, END OF DAY, NO EXIT DATA


@dataclass
class TipResult:
    tip: Tip
    entry: Entry
    note: str
    expiry: date | None = None
    contract: str | None = None
    price_at_signal: float | None = None  # the first price a follower could have got
    above_zone: float | None = None  # points over the top of the range (positive: chased)
    buffer: float | None = None
    entry_time: datetime | None = None
    entry_price: float | None = None
    qty: int = 0
    exits: list[Fill] = field(default_factory=list)
    gross: float = 0.0
    charges: float = 0.0

    @property
    def net(self) -> float:
        return round(self.gross - self.charges, 2)

    @property
    def traded(self) -> bool:
        return self.entry_price is not None


@dataclass
class ReplayResult:
    results: list[TipResult] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        done = [r for r in self.results if r.traded]
        s = summarize(ClosedTrade(r.tip.date.astimezone(IST).date(), r.net) for r in done)
        counts: dict[str, int] = defaultdict(int)
        for r in self.results:
            counts[r.entry] += 1
        return {
            "tips": len(self.results),
            "traded": len(done),
            "entries": dict(counts),
            "gross_pnl": round(sum(r.gross for r in done), 2),
            "charges": round(sum(r.charges for r in done), 2),
            "net_pnl": s.total_pnl,
            "wins": s.wins,
            "losses": s.losses,
            "win_rate": s.win_rate,
            "avg_win": s.avg_win,
            "avg_loss": s.avg_loss,
            "profit_factor": s.profit_factor,
            "max_drawdown": s.max_drawdown,
            "max_consecutive_losses": s.max_consecutive_losses,
            "best_day": None if s.best_day is None else {"day": s.best_day.day.isoformat(), "pnl": s.best_day.pnl},
            "worst_day": None if s.worst_day is None else {"day": s.worst_day.day.isoformat(), "pnl": s.worst_day.pnl},
        }

    def daily(self) -> list[dict[str, Any]]:
        ds = daily(ClosedTrade(r.tip.date.astimezone(IST).date(), r.net) for r in self.results if r.traded)
        return [{"day": d.day.isoformat(), "pnl": d.pnl, "trades": d.trades, "cumulative": d.cumulative} for d in ds]


def split_lots(lots: int, targets: int) -> list[int]:
    """Lots per target: as even as possible, the extra ones to the earliest targets (1 lot -> all at target 1)."""
    if targets <= 0:
        return []
    base, extra = divmod(max(lots, 0), targets)
    return [base + (1 if i < extra else 0) for i in range(targets)]


def contract_key(index: str, expiry: date, strike: int, option_type: str) -> str:
    return f"{index}:{expiry.isoformat()}:{strike}:{option_type}"


def candidate_expiries(prices: TipPrices, index: str, day: date) -> list[date]:
    return [e for e in prices.expiries(index) if e >= day][:EXPIRIES_TRIED]


def _signal_minute(tip: Tip) -> datetime:
    """The first minute whose open a follower could trade at: the one after the message's minute."""
    posted = tip.date.astimezone(IST).replace(second=0, microsecond=0)
    return posted + timedelta(minutes=1)


def _pick_expiry(
    prices: TipPrices, tip: Tip, day: date, first: datetime
) -> tuple[date, Mapping[datetime, Candle], float] | None:
    """The expiry whose price at the signal minute is nearest the tip's entry range."""
    mid = (tip.entry_low + tip.entry_high) / 2
    best: tuple[float, date, Mapping[datetime, Candle], float] | None = None
    for exp in candidate_expiries(prices, tip.index, day):
        bars = prices.option(contract_key(tip.index, exp, tip.strike, tip.option_type), day)
        at = next((bars[t] for t in sorted(bars) if t >= first), None)  # a quiet minute: the next bar that traded
        if at is None:
            continue
        gap = abs(at.open - mid)
        if best is None or gap < best[0]:
            best = (gap, exp, bars, at.open)
    return None if best is None else (best[1], best[2], best[3])


def replay_tip(tip: Tip, prices: TipPrices, params: ReplayParams) -> TipResult:
    day = tip.date.astimezone(IST).date()
    if tip.stop_loss is None or not tip.targets:
        return TipResult(tip, "NO_DATA", "the tip has no stop-loss / targets (details message missing)")
    if tip.action != "BUY":
        return TipResult(tip, "NO_DATA", f"{tip.action} tips are not replayed (only BUY tips exist in this channel)")
    first = _signal_minute(tip)
    picked = _pick_expiry(prices, tip, day, first)
    if picked is None:
        tried = candidate_expiries(prices, tip.index, day)
        why = (f"tried expiries {', '.join(map(str, tried))}" if tried
               else f"no {tip.index} expiry on or after that day in the stored history")  # fmt: skip
        return TipResult(
            tip, "NO_DATA", f"no stored prices for {tip.index} {tip.strike} {tip.option_type} that day ({why})"
        )
    exp, bars, _ = picked
    key = contract_key(tip.index, exp, tip.strike, tip.option_type)
    times = sorted(t for t in bars if t >= first)
    open_ = bars[times[0]].open
    buf = params.buffer.get(tip.index, DEFAULT_BUFFER)
    res = TipResult(tip, "NOT_PLACED", "", expiry=exp, contract=key, price_at_signal=open_, buffer=buf,
                    above_zone=round(open_ - tip.entry_high, 2))  # fmt: skip
    if open_ <= tip.stop_loss:
        res.note = f"price {open_:g} was already at or under the stop-loss {tip.stop_loss:g}"
        return res
    if open_ > tip.entry_high + buf:
        res.note = (f"price {open_:g} was already {open_ - tip.entry_high:g} points above the entry range "
                    f"(top {tip.entry_high:g}, buffer {buf:g}): order not placed")  # fmt: skip
        return res
    res.entry = "IN_ZONE" if open_ <= tip.entry_high else "CHASED"
    if res.entry == "IN_ZONE":
        res.note = f"bought at {open_:g}, inside the entry range"
    else:
        res.note = f"bought at {open_:g}, {open_ - tip.entry_high:g} points above the range (within the {buf:g} buffer)"
    lot = params.lot_sizes.get(tip.index, 1)
    tranches = split_lots(params.lots, len(tip.targets))
    res.qty = sum(tranches) * lot
    if res.qty == 0:
        res.entry, res.note = "NOT_PLACED", "zero lots"
        return res
    res.entry_time, res.entry_price = times[0], to_tick(open_)
    res.charges += params.costs.charges("BUY", res.qty, res.entry_price)

    remaining = [n * lot for n in tranches]  # units still open per target
    stop = tip.stop_loss
    cutoff = datetime.combine(day, EXIT_AT, tzinfo=IST)

    def sell(ts: datetime, px: float, qty: int, reason: str, market: bool) -> None:
        price = slip(px, "SELL", params.slippage_pct) if market else to_tick(px)
        res.exits.append(Fill(ts, price, qty, reason))
        res.gross += (price - res.entry_price) * qty  # type: ignore[operator]
        res.charges += params.costs.charges("SELL", qty, price)

    last_bar = bars[times[0]]
    for ts in times:
        bar = last_bar = bars[ts]
        if ts >= cutoff:
            break
        if bar.low <= stop:  # stop first when a target was also reachable in the same minute
            px = min(bar.open, stop)
            sell(ts, px, sum(remaining), "STOP LOSS", market=True)
            remaining = [0] * len(remaining)
            break
        for i, tp in enumerate(tip.targets):
            if remaining[i] and bar.high >= tp:
                sell(ts, max(bar.open, tp), remaining[i], f"TARGET {i + 1}", market=False)
                remaining[i] = 0
        if not any(remaining):
            break
    if any(remaining):
        # the day's last known price: the 15:15 minute's open, else the last bar there is
        at = next((bars[t] for t in times if t >= cutoff), None)
        if at is not None:
            ts, px, why = at.ts, at.open, "END OF DAY"
        else:
            ts, px, why = last_bar.ts, last_bar.close, "END OF DAY (last price in the data)"
        sell(ts, px, sum(remaining), why, market=True)
    res.gross, res.charges = round(res.gross, 2), round(res.charges, 2)
    return res


def replay(tips: Sequence[Tip], prices: TipPrices, params: ReplayParams | None = None) -> ReplayResult:
    p = params or ReplayParams()
    return ReplayResult([replay_tip(t, prices, p) for t in sorted(tips, key=lambda t: (t.date, t.id))])
