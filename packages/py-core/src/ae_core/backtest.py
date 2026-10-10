"""Backtesting: replay a strategy's runner over stored 1-minute history (ADR 0017).

The runner is the SAME class the engine runs live (ae_core.trading.runners), fed a Market built from history, so a
backtest cannot drift from live behaviour. Pure and deterministic: history goes in through `History`, a result comes
out; loading data, queueing and storage live elsewhere.

How a minute is replayed (no look-ahead: at the start of a minute only earlier bars are known):
    step A   prices are the minute's OPEN; entries, timed exits and index-based rules decide here
    step B   each open position's ADVERSE extreme of the minute (a short's high, a long's low), with the index at its
             low and then at its high: stop-losses and MTM limits can fire; a premium stop fills at its level (or
             the open if it gapped through), never better; an index stop at the option's adverse extreme
    step C   the FAVOURABLE extreme, the index at its high and then its low: targets can fire; a premium target
             fills at its level (or the open if it gapped), an index target at the option's close of the minute
When both a stop and a target were reachable inside one minute, the stop is assumed to come first (conservative).
Entries the runner produces in B and C are dropped (it re-decides at the next minute).

Runners that read earlier sessions (Runner.prior_days) get them from the days before `start`, so the first day
of a range trades like any other."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Protocol

from .reports import ClosedTrade, Day, Summary, daily, max_losing_streak, summarize
from .strategy import AnyConfig
from .trading.model import IST, Intent, Market, Position, Quote
from .trading.runners import Runner, make_runner

TICK = 0.05


@dataclass(frozen=True)
class Candle:
    ts: datetime  # the minute it covers (start), IST
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    oi: int | None = None  # open interest at the minute's close, when stored


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
    group: str | None = None  # positions entered together (one signal's tranches, a short and its wing)

    @property
    def net(self) -> float:
        return round(self.gross - self.charges, 2)

    @property
    def minutes(self) -> float:
        return (self.exit_time - self.entry_time).total_seconds() / 60


@dataclass
class BacktestResult:
    trades: list[BacktestTrade] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    days_replayed: int = 0
    days_without_data: int = 0
    days_without_options: int = 0  # replayed, but no option prices existed: nothing option-based could trade
    gross_pnl: float = 0.0
    charges: float = 0.0
    signals: list[dict[str, Any]] = field(default_factory=list)  # runners' signal notes (event "*_signal")
    funnel: dict[str, int] = field(default_factory=dict)  # why setups did or did not become trades, counted
    day_log: list[dict[str, Any]] = field(default_factory=list)  # per replayed day: option prices?, trades, why not

    def closed(self) -> list[ClosedTrade]:
        return [ClosedTrade(t.exit_time.date(), t.net) for t in sorted(self.trades, key=lambda t: t.exit_time)]

    def daily(self) -> list[Day]:
        return daily(self.closed())

    def summary(self) -> Summary:
        return summarize(self.closed())


_WHY = {
    "not_a_trading_day_for_this_strategy": "this weekday is not ticked under Trade on",
    "not_a_chosen_day_before_expiry": "not one of the chosen days before expiry",
    "no_legs_for_this_day": "no legs set for this weekday",
    "no_expiry": "no listed expiry on or after this day in the data",
    "leg_expires_before_the_exit": "the contract expires before the exit day: not entered",
    "no_trade_day": "too few index bars in the range window",
}


def _why(note: dict[str, Any]) -> str | None:
    """A runner note as a short reason a day did not trade (or None for notes that are not reasons)."""
    ev = str(note["event"])
    if ev in _WHY:
        return _WHY[ev]
    if ev == "waiting_to_enter":
        return f"waiting to enter: {note.get('reason', '')}"
    if ev == "order_not_placed":
        return f"order not placed: {note.get('reason', '')}"
    return None


def _ranges(days: list[date]) -> str:
    """Days as ranges of consecutive ones in the list: '1 Jul - 2 Jul, 5 Aug - 9 Oct'."""
    out: list[str] = []
    i = 0
    while i < len(days):
        j = i
        while j + 1 < len(days) and (days[j + 1] - days[j]).days <= 3:  # a weekend between is not a gap
            j += 1
        a, b = days[i], days[j]
        out.append(f"{a:%d %b}" if a == b else f"{a:%d %b} - {b:%d %b}")
        i = j + 1
    return ", ".join(out)


def to_tick(px: float) -> float:
    return round(round(px / TICK) * TICK, 2)


def slip(price: float, side: str, pct: float) -> float:
    """A fill `pct` % worse than `price` (at least one tick)."""
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
        self.volume: dict[str, int] = {}  # traded today before the current minute
        self.oi: dict[str, int] = {}

    def quote(self, key: str) -> Quote:
        """What a quote would have said at the minute's open: volume so far, last open interest (no bid/ask)."""
        # a contract with no trade yet today, or candles stored without volume, says nothing about liquidity: unknown
        return Quote(volume=self.volume.get(key) or None, oi=self.oi.get(key))

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
            self.volume[key] = self.volume.get(key, 0) + b.volume
            if b.oi is not None:
                self.oi[key] = b.oi

    def seen(self, key: str, ts: datetime) -> None:
        """Account for a contract's earlier minutes the first time it is looked at (volume and OI so far)."""
        if key in self.volume:
            return
        self.bar(key, ts)  # loads the contract's day
        before = [b for t, b in sorted(self.bars[key].items()) if t < ts]
        self.volume[key] = sum(b.volume for b in before)
        oi = [b.oi for b in before if b.oi is not None]
        if oi:
            self.oi[key] = oi[-1]


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
    spread_pct: float = 0.0,
    costs: Costs | None = None,
) -> BacktestResult:
    """`spread_pct`: a modelled bid/ask spread (% of the premium); half of it is paid on every fill, on top of
    the slippage (history has trades, not quotes)."""
    costs = costs or Costs()
    runner: Runner = make_runner(config, multiplier)
    u = config.underlying
    result = BacktestResult()
    expiries = history.expiries(u)
    days = history.days(u, start, end)
    prior: list[list[Candle]] = []  # earlier sessions' index bars, for runners that read them
    if runner.prior_days:
        for d in history.days(u, start - timedelta(days=7 + 2 * runner.prior_days), start - timedelta(days=1)):
            prior.append(list(history.spot(u, d)))
        prior = prior[-runner.prior_days :]
    if not days:
        result.warnings.append(f"no {u} index data between {start} and {end}")
        return result
    if not expiries:
        result.warnings.append(f"no {u} option data at all: only index-based logic can run")

    def fill(intent: Intent, px: float, ts: datetime, m: Market) -> None:
        price = slip(px, intent.side, slippage_pct + spread_pct / 2)
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
                    pos.group,
                )
            )

    pending_charges: dict[str, float] = {}
    noted: dict[str, int] = {}
    carried: dict[str, float] = {}  # each contract's last known price from earlier days: only for getting out
    stale_exits = 0

    no_options: list[date] = []
    for day in days:
        spot = history.spot(u, day)
        if not spot:
            result.days_without_data += 1
            continue
        result.days_replayed += 1
        has_options = history.has_options(u, day)
        if not has_options:
            result.days_without_options += 1
            no_options.append(day)
        why: dict[str, None] = {}  # this day's reasons, in order, once each
        prices = _Prices(history, day)
        live_expiries = [e for e in expiries if e >= day]
        before = [c for d in prior for c in d]
        for i, bar in enumerate(spot):
            now = bar.ts
            done = list(spot[:i])
            m0 = Market(now, u, bar.open, done, {}, live_expiries, lot_size, strike_step, before)
            keys = runner.wanted(m0)
            for k in keys:
                prices.seen(k, now)
            opens = {k: p for k in keys if (p := prices.open(k, now)) is not None}
            quotes = {k: prices.quote(k) for k in keys}
            m = Market(now, u, bar.open, done, opens, live_expiries, lot_size, strike_step, before, quotes)
            # step A: the minute's open
            for intent in runner.step(m):
                p = m.price(intent.contract)
                if p is None and intent.kind == "exit":
                    # getting out beats waiting for a trade: the contract's last known price (also from earlier days)
                    p = prices.last.get(intent.contract.key) or carried.get(intent.contract.key)
                    stale_exits += p is not None
                if p is None:
                    runner.reject(intent, "no price for the contract in the data")
                else:
                    fill(intent, p, now, m)
            # steps B and C: the index at both extremes (which came first is unknown; stops are tried first)
            for spot_px in (bar.low, bar.high):
                _extremes(runner, m, prices, now, fill, adverse=True, spot=spot_px)
            for spot_px in (bar.high, bar.low):
                _extremes(runner, m, prices, now, fill, adverse=False, spot=spot_px)
            for k in keys | {p.contract.key for p in runner.positions if p.exit_time == now}:
                prices.close(k, now)
            for note in runner.notes:
                if (reason := _why(note)) is not None and len(why) < 4:
                    why[reason] = None
                if note["event"] in _NOTED:
                    noted[note["event"]] = noted.get(note["event"], 0) + 1
                if note["event"].endswith("_signal"):
                    result.signals.append({"time": now.isoformat(), **note})
            runner.notes.clear()
        carried.update(prices.last)
        result.day_log.append({
            "day": day.isoformat(), "weekday": f"{day:%a}", "options": has_options,
            "why": list(why) if has_options else ["no option prices stored for this day"],
        })  # fmt: skip
        if runner.prior_days:
            prior = [*prior, list(spot)][-runner.prior_days :]
    if stale_exits:
        result.warnings.append(
            f"{stale_exits} exit(s) used the contract's last known price because it had no trade at that time"
        )
    for event, n in noted.items():
        result.warnings.append(f"{n} time(s): {_NOTED[event]}")
    if result.days_without_options:
        result.warnings.insert(
            0,
            f"{result.days_without_options} of {result.days_replayed} days have no option prices in the stored "
            f"history, so nothing could trade on them: {_ranges(no_options)}. Load option history for those days "
            "(import-history) and run again.",
        )
    entered: dict[str, int] = {}
    for t in result.trades:
        entered[t.entry_time.date().isoformat()] = entered.get(t.entry_time.date().isoformat(), 0) + 1
    for row in result.day_log:
        row["trades"] = entered.get(row["day"], 0)
    # positions still open when the range ends are closed at their last known price
    last_day = days[-1]
    for pos in runner.open_positions():
        last_px = _last_price(history, pos, last_day)
        if last_px is None:
            result.warnings.append(
                f"{pos.contract.label} (opened {pos.entry_time:%d %b}) was still open at the end and had no price to "
                "exit at: not counted. A strategy that holds one trade at a time cannot start another while it is open."
            )
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
    result.funnel = dict(runner.s.get("funnel", {}))
    return result


def _last_price(history: History, pos: Position, day: date) -> float | None:
    bars = history.option(pos.contract.key, day)
    return bars[max(bars)].close if bars else None


def _extremes(
    runner: Runner, m: Market, prices: _Prices, now: datetime, fill: Any, adverse: bool, spot: float | None = None
) -> None:
    """Steps B (adverse) and C (favourable): only exits are taken, at the trigger level or the open if it gapped.
    `spot` is the index price assumed for this pass (one of the minute's extremes)."""
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
    m2 = Market(now, m.underlying, m.spot if spot is None else spot, m.spot_bars, ext, m.expiries, m.lot_size,
                m.strike_step, m.prior_spot_bars, m.quotes)  # fmt: skip
    taken = _STOPS if adverse else _TARGETS
    for intent in runner.step(m2):
        if intent.kind != "exit" or not (intent.reason in taken[0] or intent.reason.startswith(taken[1])):
            runner.reject(intent, "decided again next minute")
            runner.notes.pop()  # an internal retry, not something to tell the user
            continue
        pos = runner.position(intent.position_id)
        opened = m.price(intent.contract)
        extreme = ext.get(intent.contract.key)
        px: float | None = extreme if extreme is not None else opened
        if pos is not None and opened is not None and intent.reason.startswith(("stop-loss", "target")):
            if adverse and pos.sl_basis == "premium" and pos.sl is not None:
                px = max(pos.sl, opened) if pos.side == "SELL" else min(pos.sl, opened)
            elif not adverse and pos.target_basis == "premium" and pos.target is not None:
                px = min(pos.target, opened) if pos.side == "SELL" else max(pos.target, opened)
            elif not adverse and pos.target_basis == "underlying":
                bar = prices.bar(intent.contract.key, now)
                px = bar.close if bar is not None else opened
        if px is None:
            runner.reject(intent, "no price for the contract in the data")
        else:
            fill(intent, px, now, m2)


# exits taken inside a minute (exact reasons, reason prefixes): stops and strategy-wide limits on the adverse pass,
# targets and strategy-wide limits on the favourable one
_STOPS = (("stop-loss",), ("stop-loss:", "strategy"))
_TARGETS = (("target",), ("target ", "strategy"))
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
            "expectancy": s.expectancy,
            "max_consecutive_losses": s.max_consecutive_losses,
            "avg_holding_minutes": round(sum(t.minutes for t in r.trades) / len(r.trades), 1) if r.trades else None,
            "charges_pct_of_gross": round(r.charges / r.gross_pnl * 100, 1) if r.gross_pnl > 0 else None,
        },
        "signals": signal_stats(r),
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
                "group": t.group,
            }
            for t in r.trades[:5000]
        ],
        "trades_total": len(r.trades),
        "day_log": r.day_log,
        "warnings": r.warnings,
    }


def signal_stats(r: BacktestResult) -> dict[str, Any] | None:
    """Per signal (all the positions entered together count as one trade idea): only for runners that emit
    signals. Lists the signals with their outcome, and why setups did not become trades."""
    if not r.signals and not r.funnel:
        return None
    by_group: dict[str, list[BacktestTrade]] = {}
    for t in r.trades:
        by_group.setdefault(t.group or t.entry_time.isoformat(), []).append(t)
    nets = [round(sum(t.net for t in ts), 2) for ts in by_group.values()]
    holding = [max(t.exit_time for t in ts) - min(t.entry_time for t in ts) for ts in by_group.values()]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    signals = []
    for sig in r.signals[:2000]:
        gid = f"SMC-{datetime.fromisoformat(sig['time']):%Y%m%d%H%M}"
        ts = by_group.get(gid, [])
        signals.append(
            {k: v for k, v in sig.items() if k not in ("event", "setup")}
            | {"net": round(sum(t.net for t in ts), 2) if ts else None, "exits": [t.reason for t in ts]}
        )
    return {
        "count": len(nets),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(nets), 4) if nets else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "profit_factor": round(sum(wins) / -sum(losses), 2) if losses else None,
        "expectancy": round(sum(nets) / len(nets), 2) if nets else None,
        "max_consecutive_losses": max_losing_streak(nets),
        "avg_holding_minutes": round(sum(h.total_seconds() for h in holding) / 60 / len(holding), 1)
        if holding
        else None,
        "funnel": r.funnel,
        "list": signals,
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
