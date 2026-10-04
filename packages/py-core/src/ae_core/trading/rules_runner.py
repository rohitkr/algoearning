"""The runner of rule-based strategies (ADR 0022): signals made of conditions, legs, holding period, trade risk.

One trade at a time. A trade starts when a signal's conditions hold (read when a candle completes; a signal without
conditions fires at the start time), enters that signal's legs together, and ends when every leg is closed: by its
own stop-loss / target (with re-entries), the trade's ₹ stop-loss / target / profit lock, the signal's exit rule,
or the holding period (intraday, next trading day, N trading days, expiry day). Entries obey the weekdays, days to
expiry, the entry window and the daily entry limit; exits apply whenever the trade is open, overnight included."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

from ..strategy import Leg, RulesConfig, Signal
from . import conditions, rules
from .model import Contract, Intent, Market, Position
from .runners import LegRunner, _buy_first


def days_to_expiry(today: date, expiries: list[date] | tuple[date, ...]) -> int | None:
    """Weekdays from today to the nearest expiry (0 on expiry day); exchange holidays are not known here."""
    live = sorted(e for e in expiries if e >= today)
    if not live:
        return None
    n, d = 0, today
    while d < live[0]:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


class RulesRunner(LegRunner):
    kind = "rules"
    cfg: RulesConfig

    def __init__(self, config: RulesConfig, multiplier: int = 1, state: Mapping[str, Any] | None = None) -> None:
        super().__init__(config, multiplier, state)
        self.prior_days = conditions.warmup_days(config)
        self._legs = {leg.id: leg for leg in config.all_legs()}
        self._prior_cache: dict[tuple[object, ...], Any] = {}
        self._ctx: tuple[tuple[object, ...], conditions.Context] | None = None

    # -- lookups ---------------------------------------------------------------------------------------------------
    def _leg(self, leg_id: str) -> Leg | None:
        return self._legs.get(leg_id)

    def _signal(self, sid: str | None) -> Signal | None:
        return next((s for s in self.cfg.signals if s.id == sid), None)

    def _context(self, m: Market) -> conditions.Context:
        """One context per set of bars, shared by wanted() and every step over the same minute."""
        last = m.spot_bars[-1].ts if m.spot_bars else None
        key = (last, len(m.spot_bars), len(m.prior_spot_bars))
        if self._ctx is None or self._ctx[0] != key:
            self._ctx = (key, conditions.Context(m.prior_spot_bars, m.spot_bars, self._prior_cache))
        return self._ctx[1]

    def _trade(self) -> dict[str, Any] | None:
        trade: dict[str, Any] | None = self.s.get("trade")
        return trade

    def _trade_positions(self) -> list[Position]:
        t = self._trade()
        return [p for p in self.positions if t and p.group == t["id"]]

    # -- engine interface ------------------------------------------------------------------------------------------
    def wanted(self, m: Market) -> set[str]:
        keys = super().wanted(m)
        keys |= {p["contract"] for p in self.s.get("pending", [])}
        pe = self.s.get("pending_entry")
        if pe:
            sig = self._signal(pe["signal"])
            for leg in sig.legs if sig else []:
                keys |= self._leg_keys(m, leg)
        elif self._trade() is None and self._may_enter(m, quiet=True):
            # the signal that would fire now: stream its contracts so the entry has prices this very minute
            sig, _ = self._firing(m)
            for leg in sig.legs if sig else []:
                keys |= self._leg_keys(m, leg)
        return keys

    def step(self, m: Market) -> list[Intent]:
        self._new_day(m)
        out: list[Intent] = []
        if self._trade() is not None:
            out += self._manage(m)
            self._maybe_end_trade(out)
        if self._trade() is None and not self.open_positions() and not self.exiting:
            out += self._entries(m)
        return [i for i in out if i.kind == "exit"] + _buy_first([i for i in out if i.kind == "entry"])

    def on_reject(self, intent: Intent, reason: str) -> None:
        super().on_reject(intent, reason)
        t = self._trade()
        if intent.kind != "entry" or t is None or intent.reason.startswith("re-entry"):
            return
        # a leg of a new trade was refused: undo the trade (legs already in are closed again)
        self.s["pending"] = []
        for p in self._trade_positions():
            if p.open:
                self._exit(p, "another leg of the trade was not placed")
        if not self._trade_positions():
            self.s["entries"] = max(0, self.s.get("entries", 0) - 1)  # nothing was bought or sold: not an entry
        if not any(p.open for p in self._trade_positions()):
            self.s["trade"] = None

    # -- day -------------------------------------------------------------------------------------------------------
    def _new_day(self, m: Market) -> None:
        today = m.now.date().isoformat()
        if self.s.get("day") == today:
            return
        self.drop_closed_before(m.now.date())
        self.s.update(day=today, entries=0, last_eval=None, last_exit_eval=None, told=[], pending_entry=None)
        t = self._trade()
        if t is not None:
            t["days_held"] = t.get("days_held", 0) + 1

    def _tell(self, event: str, **detail: Any) -> None:
        """A note at most once a day per event."""
        told = self.s.setdefault("told", [])
        if event not in told:
            told.append(event)
            self.note(event, **detail)

    # -- entries ---------------------------------------------------------------------------------------------------
    def _may_enter(self, m: Market, quiet: bool = False) -> bool:
        tm, now = self.cfg.timing, m.now
        if now.strftime("%a").upper()[:3] not in tm.days:
            if not quiet:
                self._tell("not_a_trading_day_for_this_strategy", weekday=now.strftime("%A"))
            return False
        if tm.dte is not None:
            dte = days_to_expiry(now.date(), list(m.expiries))
            if dte not in tm.dte:
                if not quiet:
                    self._tell("not_a_trading_day_for_this_strategy", days_to_expiry=dte, wanted=tm.dte)
                return False
        t = rules.hhmm(now)
        if t < tm.start or t > tm.last_entry:
            return False
        if self.s.get("entries", 0) >= tm.max_entries_per_day:
            if not quiet:
                self._tell("entry_limit_reached", entries=self.s.get("entries", 0))
            return False
        return True

    def _firing(self, m: Market) -> tuple[Signal | None, list[str]]:
        """The first signal whose conditions hold now. Conditions are read once per completed bar; a signal
        without conditions fires at any step in the window."""
        ctx = self._context(m)
        fresh = ctx.last_minute is not None and ctx.last_minute.isoformat() != self.s.get("last_eval")
        for sig in self.cfg.signals:
            if sig.when.conditions and not fresh:
                continue
            ok, why = conditions.holds(ctx, sig.when)
            if ok:
                return sig, why
        return None, []

    def _entries(self, m: Market) -> list[Intent]:
        pe = self.s.get("pending_entry")
        if not self._may_enter(m):
            self.s["pending_entry"] = None
            return []
        if pe:
            return self._open(m, pe)
        sig, why = self._firing(m)
        ctx = self._context(m)
        if ctx.last_minute is not None:
            self.s["last_eval"] = ctx.last_minute.isoformat()
        if sig is None:
            return []
        pe = {"signal": sig.id, "why": why, "ts": m.now.isoformat()}
        self.s["pending_entry"] = pe
        return self._open(m, pe)

    def _open(self, m: Market, pe: dict[str, Any]) -> list[Intent]:
        """Enter the signal's legs once every contract is chosen and priced (the feed may need a moment)."""
        sig = self._signal(pe["signal"])
        if sig is None:
            self.s["pending_entry"] = None
            return []
        chosen: list[tuple[Leg, Contract]] = []
        for leg in sig.legs:
            r = self._resolve(m, leg)
            if isinstance(r, str) or m.price(r) is None:
                if m.now - datetime.fromisoformat(pe["ts"]) > timedelta(minutes=5):
                    self.s["pending_entry"] = None
                    self.note("no_price_for_entry", signal=sig.id, reason=r if isinstance(r, str) else "no price")
                return []
            chosen.append((leg, r))
        self.s["pending_entry"] = None
        tid = f"R-{m.now:%Y%m%d%H%M}"
        self.s["trade"] = {"id": tid, "signal": sig.id, "entered": m.now.isoformat(), "days_held": 0, "floor": None}
        self.s["_group"] = tid
        self.s["entries"] = self.s.get("entries", 0) + 1
        self.s["pending"], self.s["reentries"] = [], {}
        why = "; ".join(pe["why"]) if pe["why"] else f"entry at {self.cfg.timing.start}"
        self.note("entry_signal", signal=sig.id, group=tid, why=pe["why"], spot=m.spot)
        return [self._enter(m, c, leg.action, leg.lots, leg.id, f"{sig.id}: {why}") for leg, c in chosen]

    # -- exits -----------------------------------------------------------------------------------------------------
    def _due(self, m: Market) -> str | None:
        """Why the holding period is over now, or None."""
        t, h = self._trade(), self.cfg.holding
        if t is None:
            return None
        now, hm = m.now, rules.hhmm(m.now)
        held = int(t.get("days_held", 0))
        if h.mode == "intraday":
            if held > 0 or hm >= h.exit:
                return f"exit time {h.exit}"
            return None
        if h.mode in ("next_day", "days"):
            n = 1 if h.mode == "next_day" else h.days
            if held > n or (held == n and hm >= h.exit):
                return f"next-day exit {h.exit}" if h.mode == "next_day" else f"held {n} trading days, exit {h.exit}"
            return None
        open_ = [p for p in self._trade_positions() if p.open]
        if not open_:
            return None
        expiry = min(p.contract.expiry for p in open_)
        if now.date() > expiry or (now.date() == expiry and hm >= h.exit):
            return f"expiry-day exit {h.exit}"
        return None

    def _close_trade(self, reason: str) -> list[Intent]:
        self.s["pending"] = []
        return self.exit_all(reason)

    def _manage(self, m: Market) -> list[Intent]:
        risk = self.cfg.risk
        due = self._due(m)
        if due:
            return self._close_trade(due)
        out, stopped = self._leg_exits(m)
        if stopped and risk.exit_all_on_leg_sl:
            return out + self._close_trade("another leg hit its stop-loss")
        if stopped and risk.sl_to_cost_on_leg_sl:
            for p in self.open_positions():
                if p.id in self.exiting or p.sl is None:
                    continue
                cost = p.entry_price if p.sl_basis == "premium" else p.entry_spot
                if cost is not None and (cost - p.sl) * self._favourable(p) > 0:
                    p.sl = cost
                    self.note("sl_moved_to_cost", leg=p.leg, sl=cost)

        pnl = round(sum(p.pnl(m.price(p.contract)) for p in self._trade_positions()), 2)
        if risk.mtm_stop_loss and pnl <= -risk.mtm_stop_loss:
            self.note("mtm_stop_loss", pnl=pnl)
            return out + self._close_trade(f"strategy loss reached ₹{risk.mtm_stop_loss:g}")
        if risk.mtm_target and pnl >= risk.mtm_target:
            self.note("mtm_target", pnl=pnl)
            return out + self._close_trade(f"strategy profit reached ₹{risk.mtm_target:g}")
        lock = risk.profit_lock
        t = self._trade()
        if lock is not None and t is not None:
            if pnl >= lock.reach:
                floor = lock.lock
                if lock.trail_every and lock.trail_by:
                    floor += ((pnl - lock.reach) // lock.trail_every) * lock.trail_by
                if t.get("floor") is None or floor > t["floor"]:
                    t["floor"] = round(floor, 2)
                    self.note("profit_locked", floor=t["floor"], pnl=pnl)
            if t.get("floor") is not None and pnl <= t["floor"]:
                return out + self._close_trade(f"strategy profit lock ₹{t['floor']:g}")

        sig = self._signal(t["signal"] if t else None)
        ctx = self._context(m)
        if sig and sig.exit_when and ctx.last_minute is not None:
            stamp = ctx.last_minute.isoformat()
            if stamp != self.s.get("last_exit_eval"):
                self.s["last_exit_eval"] = stamp
                ok, why = conditions.holds(ctx, sig.exit_when)
                if ok:
                    return out + self._close_trade("exit signal: " + "; ".join(why))
        return out + self._reentries(m)

    def _maybe_end_trade(self, out: list[Intent]) -> None:
        if any(i.kind == "entry" for i in out) or self.exiting or self.s.get("pending"):
            return
        if not any(p.open for p in self._trade_positions()):
            self.s["trade"] = None
