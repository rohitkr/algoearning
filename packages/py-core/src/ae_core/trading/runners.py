"""One state machine per strategy kind. The engine calls `step(market)` about once a second; a runner answers with
order intents, the engine fills them (paper exchange or broker) and reports each fill back with `fill()`.

Runners decide on the prices they are given and nothing else, so the same code can run live, on paper and over
history. Their state is plain JSON (`state()`), stored on the run after every step."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

from ..strategy import (
    AnyConfig,
    Leg,
    RangeBreakoutConfig,
    RulesConfig,
    SmcScalpConfig,
    TimeBasedConfig,
    ZeroDteConfig,
)
from . import options, rules
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


def _buy_first(intents: list[Intent]) -> list[Intent]:
    """Hedge first: buy legs go before sells so the margin benefit exists when the short is placed."""
    return sorted(intents, key=lambda i: i.side != "BUY")


# -- time based (the builder) -----------------------------------------------------------------------------------------
class LegRunner(Runner):
    """What a builder leg does once it is open, shared by the time-based builder and rule-based strategies: its
    stop-loss and target (on the premium or the index), trailing stop, and re-entries after a stop-loss or target."""

    def _leg(self, leg_id: str) -> Leg | None:
        raise NotImplementedError

    def _resolve(self, m: Market, leg: Leg) -> Contract | str:
        """The leg's contract right now, or why it cannot be chosen yet."""
        expiry = rules.pick_expiry(m.expiries, m.now.date(), leg.expiry)
        if expiry is None:
            return f"no {leg.expiry.replace('_', ' ')} expiry listed"
        return options.pick(m, leg.option_type, leg.strike, expiry)

    def _leg_keys(self, m: Market, leg: Leg) -> set[str]:
        """Contracts to stream so the leg can be chosen: every candidate for a premium strike, else the one strike."""
        expiry = rules.pick_expiry(m.expiries, m.now.date(), leg.expiry)
        if expiry is None or m.spot is None:
            return set()
        if leg.strike.mode == "premium":
            return {c.key for c in options.candidates(m, leg.option_type, expiry)}
        r = self._resolve(m, leg)
        return {r.key} if isinstance(r, Contract) else set()

    def _leg_exits(self, m: Market) -> tuple[list[Intent], list[Position]]:
        """Stop-losses, targets and trailing stops of the open legs: (exit intents, positions stopped out)."""
        out: list[Intent] = []
        stopped: list[Position] = []
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
                            stopped.append(pos)
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
        return out, stopped

    def _reentries(self, m: Market) -> list[Intent]:
        """Re-enter legs whose re-entry condition is met; keep waiting for the others."""
        out: list[Intent] = []
        keep = []
        for r in self.s.get("pending", []):
            if r.get("sent"):  # ordered, waiting for the fill (or a refusal, which re-arms it)
                keep.append(r)
                continue
            c = Contract.from_key(r["contract"])
            px = m.price(c)
            # at cost: back at the entry price (after a stop-loss: from the losing side; after a target: the other)
            side = 1 if r["side"] == "BUY" else -1
            kind = 1 if r["kind"] == "sl" else -1
            ready = r["mode"] == "immediate" or (px is not None and (px - r["entry_price"]) * side * kind >= 0)
            again = self._leg(r["leg"])
            if ready and px is not None and again is not None:
                why = "re-entry after " + ("stop-loss" if r["kind"] == "sl" else "target")
                intent = self._enter(m, c, again.action, again.lots, again.id, why)
                r["sent"] = intent.position_id
                out.append(intent)
            keep.append(r)
        self.s["pending"] = keep
        return out

    def on_reject(self, intent: Intent, reason: str) -> None:
        for r in self.s.get("pending", []):
            if r.get("sent") == intent.position_id:
                r.pop("sent")  # try again on a later step

    def on_entry(self, pos: Position, intent: Intent, m: Market) -> None:
        self.s["pending"] = [r for r in self.s.get("pending", []) if r.get("sent") != pos.id]
        leg = self._leg(pos.leg)
        if leg is None:
            return
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


class TimeBasedRunner(LegRunner):
    """Enter every leg at the entry time on chosen weekdays; each leg has its own stop-loss, target, trailing stop and
    re-entries; strategy-wide MTM stop-loss / target; everything exits at the exit time the same day."""

    kind = "time_based"
    cfg: TimeBasedConfig

    def _leg(self, leg_id: str) -> Leg | None:
        return next((leg for leg in self.cfg.legs if leg.id == leg_id), None)

    def _new_day(self, m: Market) -> None:
        today = m.now.date()
        if self.s.get("day") != today.isoformat():
            self.drop_closed_before(today)
            self.s.update(day=today.isoformat(), phase="waiting", reentries={}, pending=[], contracts={})

    def wanted(self, m: Market) -> set[str]:
        keys = super().wanted(m)
        keys |= {p["contract"] for p in self.s.get("pending", [])}
        if self.s.get("phase", "waiting") == "waiting":
            for leg in self.cfg.legs:
                keys |= self._leg_keys(m, leg)
        return keys

    def step(self, m: Market) -> list[Intent]:
        self._new_day(m)
        now, cfg = m.now, self.cfg
        t = rules.hhmm(now)
        timing = cfg.timing
        out: list[Intent] = []
        phase = self.s["phase"]
        if phase == "waiting":
            if now.strftime("%a").upper()[:3] not in timing.days:
                self.s["phase"] = "done"
                self.note("not_a_trading_day_for_this_strategy", weekday=now.strftime("%A"))
                return []
            if t < timing.entry or t >= timing.exit:
                if t >= timing.exit:
                    self.s["phase"] = "done"
                return []
            contracts: list[tuple[Leg, Contract]] = []
            for leg in cfg.legs:
                r = self._resolve(m, leg)
                if isinstance(r, str):
                    if self.s.get("waiting_reason") != r:
                        self.s["waiting_reason"] = r
                        self.note("waiting_to_enter", reason=r)
                    return []
                contracts.append((leg, r))
            if any(m.price(c) is None for _, c in contracts):
                return []  # the prices were just requested: enter once they stream
            self.s["phase"] = "in"
            return _buy_first(
                [self._enter(m, c, leg.action, leg.lots, leg.id, f"entry at {timing.entry}") for leg, c in contracts]
            )

        if phase != "in":
            return []
        if t >= timing.exit:
            self.s["phase"], self.s["pending"] = "done", []
            return self.exit_all(f"exit time {timing.exit}")

        # leg stop-loss / target / trailing
        out, stopped = self._leg_exits(m)
        sl_hit = bool(stopped)
        if sl_hit and cfg.risk.exit_all_on_leg_sl:
            self.s["pending"] = []
            out += self.exit_all("another leg hit its stop-loss")
            self.s["phase"] = "done"
            return out

        # strategy MTM
        total = self.realized + self.unrealized(m)
        risk = cfg.risk
        if risk.mtm_stop_loss and total <= -risk.mtm_stop_loss:
            self.s["phase"], self.s["pending"] = "done", []
            self.note("mtm_stop_loss", pnl=round(total, 2))
            return out + self.exit_all(f"strategy loss reached ₹{risk.mtm_stop_loss:g}")
        if risk.mtm_target and total >= risk.mtm_target:
            self.s["phase"], self.s["pending"] = "done", []
            self.note("mtm_target", pnl=round(total, 2))
            return out + self.exit_all(f"strategy profit reached ₹{risk.mtm_target:g}")

        # re-entries
        out += self._reentries(m)
        keep = self.s["pending"]
        if not self.open_positions() and not keep and not any(i.kind == "entry" for i in out) and not self.exiting:
            self.s["phase"] = "done"
        return out


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
    if isinstance(config, RulesConfig):
        from .rules_runner import RulesRunner  # it builds on this module

        return RulesRunner(config, multiplier, state)
    if isinstance(config, SmcScalpConfig):
        from .smc_runner import SmcScalpRunner  # it builds on this module

        return SmcScalpRunner(config, multiplier, state)
    if isinstance(config, TimeBasedConfig):
        return TimeBasedRunner(config, multiplier, state)
    if isinstance(config, RangeBreakoutConfig):
        return RangeBreakoutRunner(config, multiplier, state)
    return ZeroDteRunner(config, multiplier, state)
