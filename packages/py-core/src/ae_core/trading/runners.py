"""One state machine per strategy kind. The engine calls `step(market)` about once a second; a runner answers with
order intents, the engine fills them (paper exchange or broker) and reports each fill back with `fill()`.

Runners decide on the prices they are given and nothing else, so the same code can run live, on paper and over
history. Their state is plain JSON (`state()`), stored on the run after every step."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

from ..strategy import (
    PREMIUM_MODES,
    AnyConfig,
    Leg,
    RangeBreakoutConfig,
    RulesConfig,
    SmcScalpConfig,
    TimeBasedConfig,
    ZeroDteConfig,
    rules_conditions,
    rules_from_time_based,
    uses_previous_day,
)
from . import conditions, options, rules
from .model import IST, Contract, Intent, Market, Position, Side
from .rules import Right


class Runner:
    kind = ""
    prior_days = 0  # earlier sessions of index bars this runner needs in Market.prior_spot_bars

    def __init__(self, config: Any, multiplier: int = 1, state: Mapping[str, Any] | None = None) -> None:
        self.cfg = config
        self.multiplier = max(1, int(multiplier))
        st = dict(state or {})
        self.positions: list[Position] = [Position.from_dict(p) for p in st.pop("positions", [])]
        self.exiting: set[str] = set(st.pop("exiting", []))
        self.realized: float = float(st.pop("realized", 0.0))
        self.s: dict[str, Any] = st  # the subclass's own state
        self.notes: list[dict[str, Any]] = []  # decisions without an order, for the run's event log

    # -- engine interface ----------------------------------------------------------------------------------------
    def step(self, m: Market) -> list[Intent]:
        raise NotImplementedError

    def wanted(self, m: Market) -> set[str]:
        """Contract prices this runner needs streamed (open positions; subclasses add candidates)."""
        return {p.contract.key for p in self.positions if p.open}

    def fill(self, intent: Intent, price: float, ts: datetime, m: Market) -> Position | None:
        if intent.kind == "entry":
            pos = Position(
                id=intent.position_id,
                leg=intent.leg,
                contract=intent.contract,
                side=intent.side,
                lots=intent.lots,
                qty=intent.qty,
                entry_price=price,
                entry_time=ts,
                entry_spot=m.spot,
                group=intent.group,
            )
            self.on_entry(pos, intent, m)
            self.positions.append(pos)
            return pos
        closing = self.position(intent.position_id)
        if closing is None or not closing.open:
            return None
        pos = closing
        pos.exit_price, pos.exit_time, pos.exit_reason = price, ts, intent.reason
        self.exiting.discard(pos.id)
        self.realized = round(self.realized + pos.pnl(), 2)
        self.on_exit(pos, m)
        return pos

    def reject(self, intent: Intent, reason: str) -> None:
        """The engine refused or could not fill an intent (risk check, no price, broker rejection)."""
        self.exiting.discard(intent.position_id)
        self.note("order_not_placed", leg=intent.leg, contract=intent.contract.label, reason=reason)
        self.on_reject(intent, reason)

    def exit_all(self, reason: str, m: Market | None = None) -> list[Intent]:
        """Close every open position (stop button, kill switch, risk limit). Wings go last."""
        out = [self._exit(p, reason) for p in self._open_sorted_for_exit()]
        return [i for i in out if i is not None]

    def unrealized(self, m: Market) -> float:
        return round(sum(p.pnl(m.price(p.contract)) for p in self.positions if p.open), 2)

    def state(self) -> dict[str, Any]:
        return {
            **self.s,
            "positions": [p.to_dict() for p in self.positions],
            "exiting": sorted(self.exiting),
            "realized": self.realized,
        }

    def drop_closed_before(self, day: date) -> None:
        """Closed positions of earlier days are in the database already; keep the state small."""
        self.positions = [p for p in self.positions if p.open or (p.exit_time and p.exit_time.date() >= day)]

    # -- hooks ---------------------------------------------------------------------------------------------------
    def on_entry(self, pos: Position, intent: Intent, m: Market) -> None: ...
    def on_exit(self, pos: Position, m: Market) -> None: ...
    def on_reject(self, intent: Intent, reason: str) -> None: ...

    # -- helpers -------------------------------------------------------------------------------------------------
    def note(self, event: str, **detail: Any) -> None:
        self.notes.append({"event": event, **detail})

    def position(self, pid: str) -> Position | None:
        return next((p for p in self.positions if p.id == pid), None)

    def open_positions(self) -> list[Position]:
        return [p for p in self.positions if p.open]

    def _open_sorted_for_exit(self) -> list[Position]:
        # close shorts before their long hedges, so a margin benefit is never lost first
        return sorted(self.open_positions(), key=lambda p: p.side == "BUY")

    def _enter(self, m: Market, contract: Contract, side: Side, lots: int, leg: str, reason: str) -> Intent:
        n = lots * self.multiplier
        return Intent("entry", side, contract, n, n * m.lot_size, reason, leg, group=self.s.get("_group"))

    def _exit(self, pos: Position, reason: str) -> Intent | None:
        if pos.id in self.exiting or not pos.open:
            return None
        self.exiting.add(pos.id)
        return Intent("exit", "SELL" if pos.side == "BUY" else "BUY", pos.contract, pos.lots, pos.qty, reason, pos.leg,
                      position_id=pos.id)  # fmt: skip


ARM_FOR = timedelta(minutes=2)  # a fired signal stays valid this long while option prices stream in


def _buy_first(intents: list[Intent]) -> list[Intent]:
    """Hedge first: buy legs go before sells so the margin benefit exists when the short is placed."""
    return sorted(intents, key=lambda i: i.side != "BUY")


# -- rules (the builder, ADR 0022) ------------------------------------------------------------------------------------
class RulesRunner(Runner):
    """Enter every leg at the entry time on chosen weekdays (and days before expiry); each leg has its own stop-loss,
    target, trailing stop and re-entries; trade-wide MTM stop-loss / target, combined-premium stop and profit lock;
    everything exits at the holding's exit: the same day, the next trading day, N trading days later or on expiry.

    One trade (a cycle) at a time and at most one entry a day. A held trade carries over the night with its
    positions, pending re-entries and limits; when it closes, a new one may start the same day (exit 09:30, enter
    again 15:00)."""

    kind = "rules"
    cfg: RulesConfig

    def __init__(self, config: Any, multiplier: int = 1, state: Mapping[str, Any] | None = None) -> None:
        super().__init__(config, multiplier, state)
        self.prior_days = 1 if uses_previous_day(self.cfg) else 0
        self._fresh: frozenset[int] = frozenset()
        self._sizes = sorted({c.candle for c in rules_conditions(self.cfg)})

    def _leg(self, leg_id: str) -> Leg | None:
        return next((leg for leg in self.cfg.legs if leg.id == leg_id), None)

    def _new_day(self, m: Market) -> None:
        today = m.now.date()
        if self.s.get("day") == today.isoformat():
            return
        self.drop_closed_before(today)
        self.s["day"] = today.isoformat()
        self.s.pop("waiting_reason", None)
        if self.s.get("phase") == "in" and (self.open_positions() or self.s.get("pending")):
            return  # a held trade carries on
        self.s.update(phase="waiting", reentries={}, pending=[], cursor={})

    def _resolve(self, m: Market, leg: Leg) -> Contract | str:
        """The leg's contract right now, or why it cannot be chosen yet."""
        expiry = rules.pick_expiry(m.expiries, m.now.date(), leg.expiry)
        if expiry is None:
            return f"no {leg.expiry.replace('_', ' ')} expiry listed"
        return options.pick(m, leg.option_type, leg.strike, expiry)

    def wanted(self, m: Market) -> set[str]:
        keys = super().wanted(m)
        keys |= {p["contract"] for p in self.s.get("pending", [])}
        if self.s.get("phase", "waiting") == "waiting":
            for leg in self.cfg.legs:
                expiry = rules.pick_expiry(m.expiries, m.now.date(), leg.expiry)
                if expiry is None or m.spot is None:
                    continue
                if leg.strike.mode in PREMIUM_MODES:
                    keys |= {c.key for c in options.candidates(m, leg.option_type, expiry)}
                else:
                    r = self._resolve(m, leg)
                    if isinstance(r, Contract):
                        keys.add(r.key)
        return keys

    def on_entry(self, pos: Position, intent: Intent, m: Market) -> None:
        leg = self._leg(pos.leg)
        if leg is None:
            return
        if pos.side == "SELL" and not intent.reason.startswith("re-entry"):
            self.s.setdefault("sold", {})[pos.id] = [pos.entry_price, None]
        spot = m.spot or 0.0
        adverse = -pos.sign  # premium: a buyer loses when it falls, a seller when it rises
        # on the index, a long call / short put loses when it falls
        idx_adverse = -1 if (pos.contract.right == "CE") == (pos.side == "BUY") else 1
        if leg.stop_loss:
            base = pos.entry_price if leg.stop_loss.basis == "premium" else spot
            d = rules.distance(leg.stop_loss.value, leg.stop_loss.unit, base)
            sign = adverse if leg.stop_loss.basis == "premium" else idx_adverse
            pos.sl, pos.sl_basis = round(base + sign * d, 2), leg.stop_loss.basis
            pos.best = base
        if leg.target:
            base = pos.entry_price if leg.target.basis == "premium" else spot
            d = rules.distance(leg.target.value, leg.target.unit, base)
            sign = -adverse if leg.target.basis == "premium" else -idx_adverse
            pos.target, pos.target_basis = round(max(0.05, base + sign * d), 2), leg.target.basis

    def on_exit(self, pos: Position, m: Market) -> None:
        sold = self.s.get("sold", {})
        if pos.id in sold:
            sold[pos.id][1] = pos.exit_price

    def _value(self, pos: Position, basis: str, m: Market) -> float | None:
        return m.price(pos.contract) if basis == "premium" else m.spot

    def _favourable(self, pos: Position) -> int:
        """+1 when a rising value (on the SL's basis) is good for this position."""
        if pos.sl_basis == "premium":
            return pos.sign
        return 1 if (pos.contract.right == "CE") == (pos.side == "BUY") else -1

    def _trail(self, pos: Position, leg: Leg, value: float) -> None:
        t = leg.trailing
        if t is None or pos.sl is None or pos.best is None:
            return
        fav = self._favourable(pos)
        base = pos.entry_price if pos.sl_basis == "premium" else (pos.entry_spot or value)
        if (value - pos.best) * fav <= 0:
            return
        trigger = rules.distance(t.trigger, t.unit, base)
        step = rules.distance(t.step, t.unit, base)
        before = int(((pos.best - base) * fav) // trigger) if trigger > 0 else 0
        pos.best = value
        after = int(((value - base) * fav) // trigger) if trigger > 0 else 0
        if after > before:
            pos.sl = round(pos.sl + fav * step * (after - before), 2)
            self.note("trailing_sl_moved", leg=pos.leg, sl=pos.sl)

    # -- entry ------------------------------------------------------------------------------------------------------
    def _until(self) -> str | None:
        e, h = self.cfg.entry, self.cfg.holding
        return e.until or (h.exit if h.mode == "intraday" else None)

    def _final(self, m: Market) -> datetime | str:
        """When the trade entered now must be out, or why it cannot be worked out."""
        h, today = self.cfg.holding, m.now.date()
        if h.mode == "intraday":
            day = today
        elif h.mode == "next_day":
            day = rules.add_weekdays(today, 1)
        elif h.mode == "days":
            day = rules.add_weekdays(today, h.days)
        else:
            leg = self.cfg.legs[0]
            found = rules.pick_expiry(m.expiries, today, leg.expiry)
            if found is None:
                return f"no {leg.expiry.replace('_', ' ')} expiry listed"
            day = found
        return rules.at(day, h.exit, IST)

    # -- conditions (ADR 0023) --------------------------------------------------------------------------------------
    def _context(self, m: Market) -> conditions.Context:
        return conditions.Context(m.now, m.spot_bars, m.prior_spot_bars, self._fresh)

    def _signal(self, m: Market, only: str | None = None) -> str | None:
        """The direction of the first entry signal that is true now (restricted to one direction), or None."""
        ctx = self._context(m)
        for sig in self.cfg.entry.signals:
            if only is not None and sig.direction != only:
                continue
            if conditions.group_holds(sig, ctx):
                return sig.direction
        return None

    def _advance_cursors(self, m: Market) -> None:
        cur = self.s.setdefault("cursor", {})
        for size in self._sizes:
            c = conditions.latest(m.spot_bars, size, m.now)
            if c is not None:
                cur[str(size)] = c.ts.isoformat()

    def _entries_today(self, m: Market) -> int:
        today = m.now.date().isoformat()
        if self.s.get("entries_day") == today:
            return int(self.s.get("entries", 0))
        return 1 if self.s.get("entered") == today else 0  # state of a run from before ADR 0023

    def _skip_today(self, m: Market) -> str | None:
        """Why no trade starts today (wrong weekday, not the chosen days before expiry), or None."""
        e, today = self.cfg.entry, m.now.date()
        if today.strftime("%a").upper()[:3] not in e.days:
            return "not_a_trading_day_for_this_strategy"
        if e.dte is not None:
            expiry = rules.pick_expiry(m.expiries, today, self.cfg.legs[0].expiry)
            if expiry is None or rules.weekdays_between(today, expiry) not in e.dte:
                return "not_a_chosen_day_before_expiry"
        return None

    def _try_enter(self, m: Market) -> list[Intent]:
        if self.open_positions() or self.exiting:
            return []  # the last trade is still closing
        now, e, today = m.now, self.cfg.entry, m.now.date().isoformat()
        t, until = rules.hhmm(now), self._until()
        why = self._skip_today(m)
        if why:
            self.s["phase"] = "done"
            self.note(why, weekday=now.strftime("%A"))
            return []
        if t < e.at:
            return []
        if until is not None and t >= until:
            self.s["phase"] = "done"
            return []
        direction = "always"
        if e.mode == "conditions":
            armed = self.s.get("armed")
            if armed and now - datetime.fromisoformat(armed["at"]) <= ARM_FOR:
                direction = armed["dir"]  # the signal fired a moment ago and the entry is still being prepared
            else:
                found = self._signal(m)
                if found is None:
                    self.s.pop("armed", None)
                    return []
                direction = found
                self.s["armed"] = {"dir": found, "at": now.isoformat()}
        legs = [leg for leg in self.cfg.legs if leg.direction in ("always", direction)]
        if not legs:
            return []
        final = self._final(m)
        if isinstance(final, datetime) and final <= now:
            final = f"the exit ({final:%a %d %b} {self.cfg.holding.exit}) has passed"
        contracts: list[tuple[Leg, Contract]] = []
        for leg in legs:
            r = final if isinstance(final, str) else self._resolve(m, leg)
            if isinstance(r, str):
                if self.s.get("waiting_reason") != r:
                    self.s["waiting_reason"] = r
                    self.note("waiting_to_enter", reason=r)
                return []
            contracts.append((leg, r))
        assert isinstance(final, datetime)
        expiring = next(((leg, c) for leg, c in contracts if c.expiry < final.date()), None)
        if expiring:
            self.s["phase"] = "done"
            leg, c = expiring
            self.note("leg_expires_before_the_exit", leg=leg.id, expiry=c.expiry.isoformat(), exit=final.isoformat())
            return []
        if any(m.price(c) is None for _, c in contracts):
            return []  # the prices were just requested: enter once they stream
        self.s.pop("entered", None)
        self.s.pop("armed", None)
        self.s.update(phase="in", entries_day=today, entries=self._entries_today(m) + 1, final=final.isoformat(),
                      cycle_realized=self.realized, reentries={}, pending=[], sold={}, lock_floor=None,
                      direction=direction)  # fmt: skip
        why_in = f"{direction} signal" if e.mode == "conditions" else f"entry at {e.at}"
        return _buy_first([self._enter(m, c, leg.action, leg.lots, leg.id, why_in) for leg, c in contracts])

    # -- in a trade -------------------------------------------------------------------------------------------------
    def _close(self, m: Market, reason: str, event: str | None = None, **detail: Any) -> list[Intent]:
        self._end_cycle(m)
        if event:
            self.note(event, **detail)
        return self.exit_all(reason, m)

    def _cycle_pnl(self, m: Market) -> float:
        return round(self.realized - float(self.s.get("cycle_realized") or 0.0) + self.unrealized(m), 2)

    def _combined_hit(self, m: Market) -> bool:
        cs, sold = self.cfg.risk.combined_stop, self.s.get("sold", {})
        if cs is None or not sold:
            return False
        base = sum(entry for entry, _ in sold.values())
        now = 0.0
        for pid, (_, exit_price) in sold.items():
            if exit_price is not None:
                now += exit_price
                continue
            pos = self.position(pid)
            px = m.price(pos.contract) if pos else None
            if px is None:
                return False
            now += px
        return bool(now >= base + rules.distance(cs.value, cs.unit, base))

    def _lock_hit(self, pnl: float) -> bool:
        lp = self.cfg.risk.lock_profit
        if lp is None:
            return False
        floor = self.s.get("lock_floor")
        if floor is None and pnl >= lp.at:
            floor = lp.lock
            self.note("profit_locked", pnl=pnl, floor=floor)
        if floor is not None and lp.trail_every and lp.trail_by and pnl >= lp.at:
            raised = lp.lock + int((pnl - lp.at) // lp.trail_every) * lp.trail_by
            if raised > floor:
                floor = raised
                self.note("profit_lock_raised", pnl=pnl, floor=floor)
        self.s["lock_floor"] = floor
        return floor is not None and pnl <= floor

    def step(self, m: Market) -> list[Intent]:
        self._new_day(m)
        if self._sizes:
            cur = self.s.get("cursor", {})
            fresh = set()
            for size in self._sizes:
                c = conditions.latest(m.spot_bars, size, m.now)
                if c is not None and cur.get(str(size)) != c.ts.isoformat():
                    fresh.add(size)
            self._fresh = frozenset(fresh)
        out = self._step(m)
        if self._sizes:
            self._advance_cursors(m)
        return out

    def _step(self, m: Market) -> list[Intent]:
        phase = self.s["phase"]
        if phase == "waiting":
            return self._try_enter(m)
        if phase != "in":
            return []
        cfg, risk, out = self.cfg, self.cfg.risk, []
        if "final" not in self.s:  # a trade started before ADR 0022 (time_based): it ends today
            self.s["final"] = rules.at(m.now.date(), cfg.holding.exit, IST).isoformat()
        if m.now >= datetime.fromisoformat(self.s["final"]):
            return self._close(m, f"exit time {cfg.holding.exit}")

        # leg stop-loss / target / trailing
        sl_hit = False
        for pos in self.open_positions():
            found = self._leg(pos.leg)
            if found is None or pos.id in self.exiting:
                continue
            leg = found
            if pos.sl is not None:
                v = self._value(pos, pos.sl_basis, m)
                if v is not None:
                    self._trail(pos, leg, v)
                    if (v - pos.sl) * self._favourable(pos) <= 0:
                        i = self._exit(pos, "stop-loss")
                        if i:
                            out.append(i)
                            sl_hit = True
                            self._queue_reentry(pos, leg, "sl")
                        continue
            if pos.target is not None:
                v = self._value(pos, pos.target_basis, m)
                fav = pos.sign if pos.target_basis == "premium" else self._favourable(pos)
                if v is not None and (v - pos.target) * fav >= 0:
                    i = self._exit(pos, "target")
                    if i:
                        out.append(i)
                        self._queue_reentry(pos, leg, "target")
        if sl_hit and risk.exit_all_on_leg_sl:
            return out + self._close(m, "another leg hit its stop-loss")

        # the whole trade
        pnl = self._cycle_pnl(m)
        if risk.mtm_stop_loss and pnl <= -risk.mtm_stop_loss:
            return out + self._close(m, f"strategy loss reached ₹{risk.mtm_stop_loss:g}", "mtm_stop_loss", pnl=pnl)
        if risk.mtm_target and pnl >= risk.mtm_target:
            return out + self._close(m, f"strategy profit reached ₹{risk.mtm_target:g}", "mtm_target", pnl=pnl)
        if self._combined_hit(m):
            cs = risk.combined_stop
            assert cs is not None
            u = "%" if cs.unit == "percent" else " points"
            return out + self._close(m, f"sold premiums up {cs.value:g}{u}", "combined_stop", pnl=pnl)
        if self._lock_hit(pnl):
            floor = self.s["lock_floor"]
            return out + self._close(m, f"profit fell to the locked ₹{floor:g}", "profit_lock_exit", pnl=pnl)

        # exit on a condition or on the opposite signal
        ex = cfg.exit
        if ex.when and conditions.group_holds(ex.when, self._context(m)):
            return out + self._close(m, "exit condition", "exit_condition", pnl=pnl)
        if ex.on_opposite_signal and self.s.get("direction") in ("up", "down"):
            other = "down" if self.s["direction"] == "up" else "up"
            if self._signal(m, only=other):
                self.s["armed"] = {"dir": other, "at": m.now.isoformat()}  # the next trade, if one is left today
                return out + self._close(m, f"{other} signal", "opposite_signal", pnl=pnl)

        # re-entries
        keep = []
        for r in self.s.get("pending", []):
            c = Contract.from_key(r["contract"])
            px = m.price(c)
            # at cost: back at the entry price (after a stop-loss: from the losing side; after a target: the other)
            side = 1 if r["side"] == "BUY" else -1
            kind = 1 if r["kind"] == "sl" else -1
            ready = r["mode"] == "immediate" or (px is not None and (px - r["entry_price"]) * side * kind >= 0)
            again = self._leg(r["leg"])
            if ready and px is not None and again is not None:
                why = "re-entry after " + ("stop-loss" if r["kind"] == "sl" else "target")
                out.append(self._enter(m, c, again.action, again.lots, again.id, why))
                continue
            keep.append(r)
        self.s["pending"] = keep
        if not self.open_positions() and not keep and not any(i.kind == "entry" for i in out) and not self.exiting:
            self._end_cycle(m)
        return out

    def _end_cycle(self, m: Market) -> None:
        """The trade is over: another may start later today unless one already started today."""
        self.s["phase"] = "done" if self._entries_today(m) >= self.cfg.entry.max_per_day else "waiting"
        self.s["pending"] = []

    def _queue_reentry(self, pos: Position, leg: Leg, kind: str) -> None:
        cfg = leg.reentry_on_sl if kind == "sl" else leg.reentry_on_target
        if cfg is None:
            return
        used = self.s.setdefault("reentries", {}).get(f"{leg.id}:{kind}", 0)
        if used >= cfg.count:
            return
        self.s["reentries"][f"{leg.id}:{kind}"] = used + 1
        self.s.setdefault("pending", []).append(
            {"leg": leg.id, "contract": pos.contract.key, "mode": cfg.mode, "kind": kind,
             "entry_price": pos.entry_price, "side": pos.side}
        )  # fmt: skip


class TimeBasedRunner(RulesRunner):
    """The builder before ADR 0022 (enter and exit at fixed times the same day): runs as the intraday rules it is."""

    kind = "time_based"

    def __init__(self, config: Any, multiplier: int = 1, state: Mapping[str, Any] | None = None) -> None:
        if isinstance(config, TimeBasedConfig):
            config = rules_from_time_based(config)
        super().__init__(config, multiplier, state)


# -- positional range breakout ----------------------------------------------------------------------------------------
class RangeBreakoutRunner(Runner):
    """algo-trading-claude's PositionalBreakout: the first close outside the range_start..range_end high/low sells an
    ITM option (upside: put, downside: call) on the weekly expiry after the entry day, hedged by a wing. Stop-loss on
    the index; one re-entry at cost; exit at exit_time on expiry day (or the same day when intraday_only)."""

    kind = "range_breakout"
    cfg: RangeBreakoutConfig

    def _final(self, entry_day: date, expiry: date) -> datetime:
        day = entry_day if self.cfg.intraday_only else expiry
        return rules.at(day, self.cfg.exit_time, IST)

    def step(self, m: Market) -> list[Intent]:
        out: list[Intent] = self._pending_entry(m)
        bars = [b for b in m.spot_bars if not self.s.get("cursor") or b.ts.isoformat() > self.s["cursor"]]
        for bar in bars:
            out += self._on_bar(bar.ts, float(bar.close), m)
            self.s["cursor"] = bar.ts.isoformat()
        return [i for i in out if i.kind == "exit"] + _buy_first([i for i in out if i.kind == "entry"])

    def _on_bar(self, ts: datetime, close: float, m: Market) -> list[Intent]:
        cfg, out = self.cfg, []
        pos = self.s.get("pos")
        waiting = self.s.get("waiting")
        exited_now = False
        t = rules.hhmm(ts)
        if pos:
            final = datetime.fromisoformat(pos["final"])
            if ts > datetime.fromisoformat(pos["entry_ts"]):
                if (
                    t <= cfg.exit_time
                    and ts <= final
                    and rules.spot_stop_hit(close, pos["entry_spot"], pos["right"], cfg.stop_loss_pct)
                ):
                    out += self._close(f"stop: {m.underlying} {cfg.stop_loss_pct}% against the entry")
                    self.s["waiting"] = pos if (cfg.reentry and not pos["reentered"]) else None
                    exited_now = True
                elif ts >= final:
                    out += self._close("intraday exit" if cfg.intraday_only else "expiry-day exit")
                    self.s["waiting"] = None
                    exited_now = True
        elif waiting and t <= cfg.exit_time:
            final = datetime.fromisoformat(waiting["final"])
            if not rules.reentry_window_open(ts, final):
                if ts >= final:
                    self.s["waiting"] = None
                    self.note("reentry_expired")
            elif rules.spot_reentry_ok(close, waiting["orig_spot"], waiting["right"]):
                out += self._open(m, ts, close, waiting["contracts"], waiting, reentry=True)
                self.s["waiting"] = None

        d = ts.date().isoformat()
        if self.s.get("day") != d:
            self.s.update(day=d, hi=None, lo=None, day_done=False)
            self.drop_closed_before(ts.date())
        if self.s["day_done"] or not (cfg.range_end <= t <= cfg.last_entry):
            return out
        if self.s["hi"] is None:
            rng = [
                b
                for b in m.spot_bars
                if b.ts.date() == ts.date() and cfg.range_start <= rules.hhmm(b.ts) < cfg.range_end
            ]
            expected = _minutes(cfg.range_start, cfg.range_end)
            if len(rng) < max(1, int(expected * 0.8)):
                self.s["day_done"] = True
                self.note("no_trade_day", reason=f"only {len(rng)} of {expected} range bars (the feed started late?)")
                return out
            self.s["hi"], self.s["lo"] = max(b.high for b in rng), min(b.low for b in rng)
            self.note("range", high=self.s["hi"], low=self.s["lo"], bars=len(rng))
        direction = rules.breakout(close, self.s["hi"], self.s["lo"])
        if not direction:
            return out
        self.s["day_done"] = True  # only the first breakout of the day counts
        if self.s.get("pos") or exited_now:
            self.note("signal_skipped", signal=direction, spot=close, reason="previous position still open")
            return out
        if self.s.get("waiting"):
            self.note("reentry_cancelled", reason="new breakout signal")
            self.s["waiting"] = None
        right = rules.breakout_right(direction)
        expiry = rules.expiry_after(m.expiries, ts.date(), cfg.expiry_offset)
        if expiry is None:
            self.note("no_expiry", reason="no listed expiry after today")
            return out
        strike = rules.itm_strike(close, right, cfg.itm_points, m.strike_step)
        contracts = {"short": Contract(m.underlying, expiry, strike, right).key}
        if cfg.hedge_width:
            contracts["wing"] = Contract(
                m.underlying, expiry, rules.wing_strike(strike, right, cfg.hedge_width), right
            ).key
        info = {"right": right, "orig_spot": close, "final": self._final(ts.date(), expiry).isoformat()}
        if any(m.price(k) is None for k in contracts.values()):
            # the contracts were only just chosen, so the feed is not streaming them yet: ask for them and enter as
            # soon as their prices arrive (the breakout signal is kept, not lost)
            self.s["pending_entry"] = {"contracts": contracts, "info": info, "close": close, "ts": m.now.isoformat()}
            self.note("waiting_for_prices", signal=direction, contracts=sorted(contracts.values()))
            return out
        return out + self._open(m, ts, close, contracts, info, reentry=False)

    def _pending_entry(self, m: Market) -> list[Intent]:
        """Enter a breakout whose contracts had no price yet, once they have one (give up after 5 minutes)."""
        pe = self.s.get("pending_entry")
        if not pe:
            return []
        if self.s.get("pos"):
            self.s["pending_entry"] = None
            return []
        if all(m.price(k) is not None for k in pe["contracts"].values()):
            self.s["pending_entry"] = None
            return self._open(m, m.now, pe["close"], pe["contracts"], pe["info"], reentry=False)
        if m.now - datetime.fromisoformat(pe["ts"]) > timedelta(minutes=5):
            self.s["pending_entry"] = None
            self.note(
                "no_price_for_entry", reason="no price for the contracts within 5 minutes: the breakout was skipped"
            )
        return []

    def _open(
        self, m: Market, ts: datetime, spot: float, contracts: dict[str, str], info: dict[str, Any], reentry: bool
    ) -> list[Intent]:
        self.s["_group"] = f"PB-{ts:%Y%m%d%H%M}"
        self.s["pos"] = {**info, "contracts": contracts, "entry_spot": spot, "entry_ts": ts.isoformat(),
                         "reentered": reentry, "group": self.s["_group"]}  # fmt: skip
        why = (
            "re-entry at cost"
            if reentry
            else f"{m.underlying} broke the {self.cfg.range_start}-{self.cfg.range_end} range"
        )
        out = [self._enter(m, Contract.from_key(contracts["short"]), "SELL", self.cfg.lots, "short", why)]
        if "wing" in contracts:
            out.append(self._enter(m, Contract.from_key(contracts["wing"]), "BUY", self.cfg.lots, "wing", "hedge"))
        return out

    def _close(self, reason: str) -> list[Intent]:
        self.s["pos"] = None
        return self.exit_all(reason)

    def wanted(self, m: Market) -> set[str]:
        keys = super().wanted(m)
        for p in (self.s.get("pos"), self.s.get("waiting"), self.s.get("pending_entry")):
            if p:
                keys |= set(p["contracts"].values())
        return keys

    def on_entry(self, pos: Position, intent: Intent, m: Market) -> None:
        p = self.s.get("pos") or {}
        pos.group, pos.entry_spot = p.get("group"), p.get("entry_spot", m.spot)
        if intent.leg == "short" and p:
            pos.sl, pos.sl_basis = (
                round(rules.spot_stop_level(p["entry_spot"], p["right"], self.cfg.stop_loss_pct), 2),
                "underlying",
            )

    def on_reject(self, intent: Intent, reason: str) -> None:
        if intent.kind == "entry" and intent.leg == "short":
            self.s["pos"] = None  # a refused entry: no position and no re-entry for it
            for p in self.open_positions():  # a wing bought first is closed again
                self._exit(p, "short leg not placed")


def _minutes(a: str, b: str) -> int:
    ah, am = (int(x) for x in a.split(":"))
    bh, bm = (int(x) for x in b.split(":"))
    return (bh * 60 + bm) - (ah * 60 + am)


# -- expiry-day ITM straddle ------------------------------------------------------------------------------------------
class ZeroDteRunner(Runner):
    """algo-trading-claude's ZeroDteStraddle: on each expiry day sell a call at ATM - itm and a put at ATM + itm
    (optionally hedged), stop-loss stop_loss_pct on each leg's premium, one re-entry when the premium is back at the
    first entry price, exit at exit_time. Entry time: first_entry. (The walk-forward choice of the best recent entry
    time needs a history store of option prices; until then the earliest candidate time is used.)"""

    kind = "zero_dte"
    cfg: ZeroDteConfig

    def step(self, m: Market) -> list[Intent]:
        cfg, today = self.cfg, m.now.date()
        t = rules.hhmm(m.now)
        if self.s.get("day") != today.isoformat():
            self.drop_closed_before(today)
            self.s.update(day=today.isoformat(), phase="waiting", legs={}, reentered={})
        phase = self.s["phase"]
        if phase == "waiting":
            if today not in m.expiries:
                self.s["phase"] = "done"
                self.note("not_an_expiry_day")
                return []
            if t < cfg.first_entry or t >= cfg.exit_time or m.spot is None:
                if t >= cfg.exit_time:
                    self.s["phase"] = "done"
                return []
            strikes = rules.straddle_strikes(m.spot, cfg.itm_points, m.strike_step)
            legs: dict[Right, dict[str, str]] = {}
            for right, k in strikes.items():
                short = Contract(m.underlying, today, k, right)
                legs[right] = {"short": short.key}
                if cfg.hedge_width:
                    legs[right]["wing"] = Contract(
                        m.underlying, today, rules.wing_strike(k, right, cfg.hedge_width), right
                    ).key
            keys = [k for v in legs.values() for k in v.values()]
            self.s["legs"] = legs
            if any(m.price(k) is None for k in keys):
                return []  # requested: enter once they stream
            self.s["phase"] = "in"
            entries: list[Intent] = []
            for right, v in legs.items():
                self.s["_group"] = f"ZD-{right}"
                short = Contract.from_key(v["short"])
                entries.append(self._enter(m, short, "SELL", cfg.lots, f"short-{right}", f"0DTE entry at {t}"))
                if "wing" in v:
                    wing = Contract.from_key(v["wing"])
                    entries.append(self._enter(m, wing, "BUY", cfg.lots, f"wing-{right}", "hedge"))
            return _buy_first(entries)
        if phase != "in":
            return []
        if t >= cfg.exit_time:
            self.s["phase"] = "done"
            return self.exit_all(f"exit time {cfg.exit_time}")
        out: list[Intent] = []
        for pos in self.open_positions():
            if not pos.leg.startswith("short") or pos.sl is None or pos.id in self.exiting:
                continue
            px = m.price(pos.contract)
            if px is not None and px >= pos.sl:
                for p in self.open_positions():
                    if p.group == pos.group:
                        i = self._exit(
                            p,
                            f"stop-loss {cfg.stop_loss_pct:g}% on the premium"
                            if p is pos
                            else "hedge of a stopped leg",
                        )
                        if i:
                            out.append(i)
                right = pos.contract.right
                if cfg.reentry and not self.s["reentered"].get(right):
                    self.s.setdefault("waiting", {})[right] = pos.entry_price
        for right, first in list(self.s.get("waiting", {}).items()):
            v = self.s["legs"][right]
            px = m.price(v["short"])
            if px is not None and rules.premium_reentry_ok(px, first):
                del self.s["waiting"][right]
                self.s["reentered"][right] = True
                self.s["_group"] = f"ZD-{right}-R"
                out.append(
                    self._enter(
                        m, Contract.from_key(v["short"]), "SELL", cfg.lots, f"short-{right}", "re-entry at cost"
                    )
                )
                if "wing" in v:
                    out.append(self._enter(m, Contract.from_key(v["wing"]), "BUY", cfg.lots, f"wing-{right}", "hedge"))
        return [i for i in out if i.kind == "exit"] + _buy_first([i for i in out if i.kind == "entry"])

    def wanted(self, m: Market) -> set[str]:
        keys = super().wanted(m)
        for v in self.s.get("legs", {}).values():
            keys |= set(v.values())
        if self.s.get("phase") == "waiting" and m.spot is not None and m.now.date() in m.expiries:
            for right, k in rules.straddle_strikes(m.spot, self.cfg.itm_points, m.strike_step).items():
                keys.add(Contract(m.underlying, m.now.date(), k, right).key)
        return keys

    def on_entry(self, pos: Position, intent: Intent, m: Market) -> None:
        if pos.leg.startswith("short"):
            pos.sl = round(rules.premium_stop_level(pos.entry_price, self.cfg.stop_loss_pct), 2)


def make_runner(config: AnyConfig, multiplier: int = 1, state: Mapping[str, Any] | None = None) -> Runner:
    if isinstance(config, SmcScalpConfig):
        from .smc_runner import SmcScalpRunner  # it builds on this module

        return SmcScalpRunner(config, multiplier, state)
    if isinstance(config, RulesConfig):
        return RulesRunner(config, multiplier, state)
    if isinstance(config, TimeBasedConfig):
        return TimeBasedRunner(config, multiplier, state)
    if isinstance(config, RangeBreakoutConfig):
        return RangeBreakoutRunner(config, multiplier, state)
    return ZeroDteRunner(config, multiplier, state)
