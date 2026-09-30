"""Backtesting: replay a strategy's runner over stored 1-minute history (ADR 0017).

The runner is the SAME class the engine runs live (ae_core.trading.runners), fed a Market built from history, so a
backtest cannot drift from live behaviour. Pure and deterministic: history goes in through `History`, a result comes
out; loading data, queueing and storage live elsewhere.

How a minute is replayed (no look-ahead: at the start of a minute only earlier bars are known):
    step A   prices are the minute's OPEN; entries, timed exits and index-based rules decide here
    step B   each open position's ADVERSE extreme of the minute (a short's high, a long's low): stop-losses and MTM
             limits can fire; an exit fills at the stop level (or the open if it gapped through), never better
    step C   the FAVOURABLE extreme: targets can fire, filled at the target level (or the open if it gapped)
When both a stop and a target were reachable inside one minute, the stop is assumed to come first (conservative).
Entries the runner produces in B and C are dropped (it re-decides at the next minute)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Protocol

from .reports import ClosedTrade, Day, Summary, daily, summarize
from .strategy import AnyConfig
from .trading.model import IST, Intent, Market, Position
from .trading.runners import Runner, make_runner

TICK = 0.05


@dataclass(frozen=True)
class Candle:
    ts: datetime  # the minute it covers (start), IST
    open: float
    high: float
    low: float
    close: float


class History(Protocol):
    def days(self, underlying: str, start: date, end: date) -> list[date]:
        """Trading days that have index bars, in order."""
        ...

    def spot(self, underlying: str, day: date) -> Sequence[Candle]: ...

    def option(self, key: str, day: date) -> Mapping[datetime, Candle]:
        """One contract's bars that day by minute (missing minutes = no trade)."""
        ...

    def expiries(self, underlying: str) -> list[date]:
        """Every expiry present in the data, sorted."""
        ...

    def has_options(self, underlying: str, day: date) -> bool:
        """Is there any option data for the underlying that day?"""
        ...


@dataclass(frozen=True)
class Costs:
    """Approximate Indian F&O options charges (verify against your broker's contract note)."""

    brokerage_per_order: float = 20.0
    stt_sell_pct: float = 0.1  # on sell-side premium turnover
    exchange_pct: float = 0.03503
    sebi_per_crore: float = 10.0
    stamp_buy_pct: float = 0.003
    gst_pct: float = 18.0  # on brokerage + exchange + SEBI

    def charges(self, side: str, qty: int, price: float) -> float:
        turnover = qty * price
        exch = turnover * self.exchange_pct / 100
        sebi = turnover / 1e7 * self.sebi_per_crore
        gst = (self.brokerage_per_order + exch + sebi) * self.gst_pct / 100
        stt = turnover * self.stt_sell_pct / 100 if side == "SELL" else 0.0
        stamp = turnover * self.stamp_buy_pct / 100 if side == "BUY" else 0.0
        return round(self.brokerage_per_order + exch + sebi + gst + stt + stamp, 2)


@dataclass
class BacktestTrade:
    leg: str
    contract: str
    side: str
    qty: int
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    reason: str
    gross: float
    charges: float

    @property
    def net(self) -> float:
        return round(self.gross - self.charges, 2)


@dataclass
class BacktestResult:
    trades: list[BacktestTrade] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    days_replayed: int = 0
    days_without_data: int = 0
    days_without_options: int = 0  # replayed, but no option prices existed: nothing option-based could trade
    gross_pnl: float = 0.0
    charges: float = 0.0

    def closed(self) -> list[ClosedTrade]:
        return [ClosedTrade(t.exit_time.date(), t.net) for t in self.trades]

    def daily(self) -> list[Day]:
        return daily(self.closed())

    def summary(self) -> Summary:
        return summarize(self.closed())


def to_tick(px: float) -> float:
    return round(round(px / TICK) * TICK, 2)


def slip(price: float, side: str, pct: float) -> float:
    if pct <= 0:
        return to_tick(price)
    s = max(TICK, price * pct / 100)
    return max(TICK, to_tick(price + s if side == "BUY" else price - s))


def _at(day: date, t: time) -> datetime:
    return datetime.combine(day, t, tzinfo=IST)


class _Prices:
    """A day's option prices, minute by minute, with forward-filled last-known values (a quiet contract has no bar)."""

    def __init__(self, history: History, day: date) -> None:
        self.history, self.day = history, day
        self.bars: dict[str, Mapping[datetime, Candle]] = {}
        self.last: dict[str, float] = {}

    def bar(self, key: str, ts: datetime) -> Candle | None:
        if key not in self.bars:
            self.bars[key] = self.history.option(key, self.day)
        return self.bars[key].get(ts)

    def open(self, key: str, ts: datetime) -> float | None:
        b = self.bar(key, ts)
        if b is not None:
            self.last[key] = b.open
            return b.open
        return self.last.get(key)

    def close(self, key: str, ts: datetime) -> None:
        b = self.bar(key, ts)
        if b is not None:
            self.last[key] = b.close


def simulate(
    config: AnyConfig,
    history: History,
    start: date,
    end: date,
    *,
    multiplier: int = 1,
    lot_size: int,
    strike_step: int,
    slippage_pct: float = 0.05,
    costs: Costs | None = None,
) -> BacktestResult:
    costs = costs or Costs()
    runner: Runner = make_runner(config, multiplier)
    u = config.underlying
    result = BacktestResult()
    expiries = history.expiries(u)
    days = history.days(u, start, end)
    if not days:
        result.warnings.append(f"no {u} index data between {start} and {end}")
        return result
    if not expiries:
        result.warnings.append(f"no {u} option data at all: only index-based logic can run")

    def fill(intent: Intent, px: float, ts: datetime, m: Market) -> None:
        price = slip(px, intent.side, slippage_pct)
        pos = runner.fill(intent, price, ts, m)
        if pos is None:
            return
        c = costs.charges(intent.side, intent.qty, price)
        if intent.kind == "entry":
            pending_charges[pos.id] = c
        else:
            result.trades.append(
                BacktestTrade(
                    pos.leg,
                    pos.contract.label,
                    pos.side,
                    pos.qty,
                    pos.entry_time,
                    pos.entry_price,
                    ts,
                    price,
                    intent.reason,
                    pos.pnl(),
                    round(pending_charges.pop(pos.id, 0.0) + c, 2),
                )
            )

    pending_charges: dict[str, float] = {}
    noted: dict[str, int] = {}
    for day in days:
        spot = history.spot(u, day)
        if not spot:
            result.days_without_data += 1
            continue
        result.days_replayed += 1
        if not history.has_options(u, day):
            result.days_without_options += 1
        prices = _Prices(history, day)
        live_expiries = [e for e in expiries if e >= day]
        for i, bar in enumerate(spot):
            now = bar.ts
            done = list(spot[:i])
            keys = runner.wanted(Market(now, u, bar.open, done, {}, live_expiries, lot_size, strike_step))
            opens = {k: p for k in keys if (p := prices.open(k, now)) is not None}
            m = Market(now, u, bar.open, done, opens, live_expiries, lot_size, strike_step)
            # step A: the minute's open
            for intent in runner.step(m):
                p = m.price(intent.contract)
                if p is None:
                    runner.reject(intent, "no price for the contract in the data")
                else:
                    fill(intent, p, now, m)
            _extremes(runner, m, prices, now, fill, adverse=True)
            _extremes(runner, m, prices, now, fill, adverse=False)
            for k in keys:
                prices.close(k, now)
            for note in runner.notes:
                if note["event"] in _NOTED:
                    noted[note["event"]] = noted.get(note["event"], 0) + 1
            runner.notes.clear()
    for event, n in noted.items():
        result.warnings.append(f"{n} time(s): {_NOTED[event]}")
    if result.days_without_options:
        result.warnings.insert(
            0,
            f"{result.days_without_options} of {result.days_replayed} days have no option prices in the stored "
            "history, so nothing could trade on them. Option history covers only part of the range.",
        )
    # positions still open when the range ends are closed at their last known price
    last_day = days[-1]
    for pos in runner.open_positions():
        last_px = _last_price(history, pos, last_day)
        if last_px is None:
            result.warnings.append(f"{pos.contract.label} was still open at the end and has no price: not counted")
            continue
        end_ts = _at(last_day, time(15, 29))
        intent = Intent(
            "exit",
            "SELL" if pos.side == "BUY" else "BUY",
            pos.contract,
            pos.lots,
            pos.qty,
            "end of the tested range",
            pos.leg,
            position_id=pos.id,
        )
        runner.exiting.add(pos.id)
        fill(intent, last_px, end_ts, Market(end_ts, u, None, [], {}, [], lot_size, strike_step))
        result.warnings.append(f"{pos.contract.label} was still open at the end: closed at its last price")
    result.gross_pnl = round(sum(t.gross for t in result.trades), 2)
    result.charges = round(sum(t.charges for t in result.trades), 2)
    return result


def _last_price(history: History, pos: Position, day: date) -> float | None:
    bars = history.option(pos.contract.key, day)
    return bars[max(bars)].close if bars else None


def _extremes(runner: Runner, m: Market, prices: _Prices, now: datetime, fill: Any, adverse: bool) -> None:
    """Steps B (adverse) and C (favourable): only exits are taken, at the trigger level or the open if it gapped."""
    opens = runner.open_positions()
    if not opens:
        return
    ext: dict[str, float] = dict(m.prices)
    for p in opens:
        b = prices.bar(p.contract.key, now)
        if b is None:
            continue
        short = p.side == "SELL"
        ext[p.contract.key] = (b.high if short else b.low) if adverse else (b.low if short else b.high)
    m2 = Market(now, m.underlying, m.spot, m.spot_bars, ext, m.expiries, m.lot_size, m.strike_step)
    for intent in runner.step(m2):
        if intent.kind != "exit" or (intent.reason not in _TRIGGERED and not intent.reason.startswith("strategy")):
            runner.reject(intent, "decided again next minute")
            runner.notes.pop()  # an internal retry, not something to tell the user
            continue
        pos = runner.position(intent.position_id)
        opened = m.price(intent.contract)
        level = pos.sl if adverse and pos is not None else pos.target if pos is not None else None
        extreme = ext.get(intent.contract.key)
        px: float | None
        if level is not None and pos is not None and pos.sl_basis == "premium" and opened is not None and adverse:
            px = max(level, opened) if pos.side == "SELL" else min(level, opened)
        elif (
            level is not None
            and pos is not None
            and not adverse
            and opened is not None
            and pos.target_basis == "premium"
        ):
            px = min(level, opened) if pos.side == "SELL" else max(level, opened)
        else:
            px = extreme if extreme is not None else opened
        if px is None:
            runner.reject(intent, "no price for the contract in the data")
        else:
            fill(intent, px, now, m2)


_TRIGGERED = ("stop-loss", "target")
# runner notes worth telling the user about, counted (not listed per day), with their plain meaning
_NOTED = {
    "no_trade_day": "a day was skipped: too few index bars in the strategy's range window",
    "no_expiry": "a signal was skipped: no listed expiry after that day in the data",
    "order_not_placed": "an order could not be placed: the contract had no price in the data",
    "no_price_for_entry": "a breakout was skipped: no price for its contracts within 5 minutes",
}


def summarize_result(r: BacktestResult) -> dict[str, Any]:
    """JSON for storage and the API."""
    s = r.summary()
    return {
        "summary": {
            "net_pnl": s.total_pnl,
            "gross_pnl": r.gross_pnl,
            "charges": r.charges,
            "trades": s.trades,
            "wins": s.wins,
            "losses": s.losses,
            "win_rate": s.win_rate,
            "avg_win": s.avg_win,
            "avg_loss": s.avg_loss,
            "profit_factor": s.profit_factor,
            "max_drawdown": s.max_drawdown,
            "trading_days": s.trading_days,
            "days_replayed": r.days_replayed,
            "days_without_data": r.days_without_data,
            "days_without_options": r.days_without_options,
            "best_day": None if s.best_day is None else {"day": s.best_day.day.isoformat(), "pnl": s.best_day.pnl},
            "worst_day": None if s.worst_day is None else {"day": s.worst_day.day.isoformat(), "pnl": s.worst_day.pnl},
        },
        "daily": [
            {"day": d.day.isoformat(), "pnl": d.pnl, "trades": d.trades, "cumulative": d.cumulative} for d in r.daily()
        ],
        "trades": [
            {
                "leg": t.leg,
                "contract": t.contract,
                "side": t.side,
                "qty": t.qty,
                "entry_time": t.entry_time.isoformat(),
                "entry_price": t.entry_price,
                "exit_time": t.exit_time.isoformat(),
                "exit_price": t.exit_price,
                "reason": t.reason,
                "gross": t.gross,
                "charges": t.charges,
                "net": t.net,
            }
            for t in r.trades[:5000]
        ],
        "trades_total": len(r.trades),
        "warnings": r.warnings,
    }


class MemoryHistory:
    """History held in memory (tests, and the loader's result)."""

    def __init__(self) -> None:
        self._spot: dict[tuple[str, date], list[Candle]] = {}
        self._opt: dict[tuple[str, date], dict[datetime, Candle]] = {}
        self._exp: dict[str, set[date]] = {}
        self._opt_days: set[tuple[str, date]] = set()

    def add_spot(self, underlying: str, candles: Iterable[Candle]) -> None:
        for c in sorted(candles, key=lambda c: c.ts):
            self._spot.setdefault((underlying, c.ts.date()), []).append(c)

    def add_option(self, key: str, candles: Iterable[Candle]) -> None:
        u, e, _, _ = key.split(":")
        self._exp.setdefault(u, set()).add(date.fromisoformat(e))
        for c in candles:
            self._opt.setdefault((key, c.ts.date()), {})[c.ts] = c
            self._opt_days.add((u, c.ts.date()))

    def days(self, underlying: str, start: date, end: date) -> list[date]:
        return sorted(d for (u, d) in self._spot if u == underlying and start <= d <= end)

    def spot(self, underlying: str, day: date) -> Sequence[Candle]:
        return self._spot.get((underlying, day), [])

    def option(self, key: str, day: date) -> Mapping[datetime, Candle]:
        return self._opt.get((key, day), {})

    def expiries(self, underlying: str) -> list[date]:
        return sorted(self._exp.get(underlying, set()))

    def has_options(self, underlying: str, day: date) -> bool:
        return (underlying, day) in self._opt_days
