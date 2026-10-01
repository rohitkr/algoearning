"""The SMC options scalper (ADR 0018): reads Smart Money Concepts on the index and buys a call (bullish) or a put
(bearish), with the stop and targets on the index.

Each completed entry candle is decided on once, in order:

    1. setup timeframe   when a setup candle closes with a BOS/CHoCH in the direction of a clear bias, look back for
                         the sweep of opposite liquidity and the displacement that led to it; the FVGs / order block
                         the move left (in discount for longs, premium for shorts) are armed as points of interest
    2. entry timeframe   the armed POI must be tapped, then confirmed by a CHoCH (a close beyond the pullback's last
                         swing); a close beyond the sweep or through the POI, or waiting too long, cancels it
    3. signal            entry = the index now, SL beyond the sweep extreme, TP1 1R, TP2 halfway, TP3 the chosen R;
                         refused when the stop is too tight or too wide, the opposing liquidity is too close, or a
                         daily limit / cooldown applies. Then the option is chosen and checked for liquidity.

Open trades are managed on the index every step: stop, targets (one tranche each), stop to breakeven at TP1 and to
TP1 at TP2, a premium stop as a backstop, and the exit time. Every decision is written down (notes), so a trade on
the dashboard can be explained and a rejected setup can be counted."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from ..strategy import SmcScalpConfig, smc_tranches
from . import options, rules, smc
from .model import Contract, Intent, Market, Position
from .runners import Runner

PRIOR_DAYS = 3  # sessions of history the bias timeframe needs before today's first candle
PENDING_LIMIT = timedelta(minutes=2)  # a signal waits this long for its option's price, then is dropped


def _iso(ts: datetime) -> str:
    return ts.isoformat()


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


class SmcScalpRunner(Runner):
    kind = "smc_scalp"
    cfg: SmcScalpConfig
    prior_days = PRIOR_DAYS

    def __init__(self, config: Any, multiplier: int = 1, state: Mapping[str, Any] | None = None) -> None:
        super().__init__(config, multiplier, state)
        self._cache: dict[str, Any] = {}  # analysis of the setup / bias timeframes, recomputed when they change

    # -- bookkeeping ---------------------------------------------------------------------------------------------
    def _count(self, what: str) -> None:
        f = self.s.setdefault("funnel", {})
        f[what] = f.get(what, 0) + 1

    def _new_day(self, m: Market) -> None:
        today = m.now.date().isoformat()
        if self.s.get("day") != today:
            self.drop_closed_before(m.now.date())
            self.s.update(day=today, trades_today=0, losses_today=0, cooldown_until=None, setup=None,
                          cursor=None, setup_cursor=None, pending=None, poi_wait=None)  # fmt: skip
            self._cache = {}

    # -- engine interface ----------------------------------------------------------------------------------------
    def wanted(self, m: Market) -> set[str]:
        keys = super().wanted(m)
        pend = self.s.get("pending")
        if pend:
            keys.add(pend["contract"])
        setup = self.s.get("setup")
        if setup and m.spot is not None:  # stream the likely contracts before the trigger, so entry is immediate
            right: rules.Right = "CE" if setup["dir"] == "up" else "PE"
            expiry = self._expiry(m)
            if expiry is not None:
                strike = self.cfg.option.strike
                offs = [strike.offset + d for d in (-1, 0, 1)] if strike.mode == "atm" else options.CANDIDATE_OFFSETS
                keys |= {c.key for c in options.candidates(m, right, expiry, offs)}
        return keys

    def step(self, m: Market) -> list[Intent]:
        self._new_day(m)
        out: list[Intent] = []
        t = rules.hhmm(m.now)
        if t >= self.cfg.session.exit:
            self.s["setup"], self.s["pending"] = None, None
            if self.open_positions():
                return self.exit_all(f"exit time {self.cfg.session.exit}")
            return []
        out += self._manage(m)
        out += self._pending(m)
        bars = [b for b in m.spot_bars if (b.ts.hour, b.ts.minute) >= smc.SESSION_START]
        entry_bars = smc.resample(bars, self.cfg.timeframes.entry)
        cursor = self.s.get("cursor")
        new = [b for b in entry_bars if cursor is None or _iso(b.ts) > cursor]
        if new:
            self.s["cursor"] = _iso(new[-1].ts)
            # after a restart several candles may be new: only the latest is decided on (no stale signals)
            out += self._on_entry_bar(m, bars, entry_bars, len(entry_bars) - 1)
        return out

    # -- 1. setup timeframe --------------------------------------------------------------------------------------
    def _bias(self, m: Market, bars: list[Any]) -> smc.Structure:
        tf, rl = self.cfg.timeframes, self.cfg.rules
        hb = smc.resample([*m.prior_spot_bars, *bars], tf.bias)
        key = _iso(hb[-1].ts) if hb else ""
        if self._cache.get("bias_key") != key:
            self._cache["bias_key"], self._cache["bias"] = key, smc.structure(hb, rl.swing_bias)
        st: smc.Structure = self._cache["bias"]
        return st

    def _detect(self, m: Market, bars: list[Any]) -> None:
        """A setup candle just closed: is there a new setup to arm?"""
        tf, rl = self.cfg.timeframes, self.cfg.rules
        prior = m.prior_spot_bars
        prior_day = [b for b in prior if b.ts.date() == prior[-1].ts.date()] if prior else []
        sb = smc.resample([*prior_day, *bars], tf.setup)
        if len(sb) < 3:
            return
        k = len(sb) - 1
        if sb[k].ts.date() != m.now.date():
            return
        if _iso(sb[k].ts) == self.s.get("setup_cursor"):
            return
        self.s["setup_cursor"] = _iso(sb[k].ts)
        st = smc.structure(sb, rl.swing_setup)
        brk = st.last
        # a break on this candle, or the previous one's again when it had no POI yet: the FVG of a displacement
        # candle is completed by the candle after it
        retry = brk is not None and brk.i == k - 1 and self.s.get("poi_wait") == _iso(brk.ts)
        self.s["poi_wait"] = None
        if brk is None or not (brk.i == k or retry):
            return  # no structure break on this candle: nothing new
        if not retry:
            self._count("breaks")
        bias = self._bias(m, bars)
        if bias.trend is None or bias.streak < rl.bias_min_breaks:
            self._reject("bias unclear", brk, detail=f"{tf.bias}m structure has no clear trend")
            return
        if brk.dir != bias.trend:
            self._reject("against bias", brk, detail=f"{tf.bias}m trend is {bias.trend}")
            return
        atrs = smc.atr(sb, rl.atr_period)
        pools = [
            *smc.day_pools(smc.bars_of(prior_day), smc.bars_of(bars), rl.opening_range_minutes),
            *smc.swing_pools(sb, [s for s in st.swings if s.confirmed < k], rl.equal_level_pct, tf.setup),
        ]
        sweep = smc.find_sweep(sb, pools, k - rl.sweep_lookback, k, brk.dir, rl.sweep_min_pct, rl.sweep_reclaim)
        if sweep is None:
            self._reject("no liquidity sweep", brk)
            return
        disp_i = next(
            (i for i in range(sweep.i, k + 1) if smc.displacement(sb[i], atrs[i], rl.displacement_atr) == brk.dir),
            None,
        )
        if disp_i is None:
            self._reject("no displacement", brk)
            return
        zones = self._zones(sb, sweep, disp_i, k, brk.dir, atrs[k] or 0.0)
        if not zones:
            if retry:
                self._reject("no order block / FVG", brk)
            else:
                self.s["poi_wait"] = _iso(brk.ts)
            return
        eq = bias.equilibrium
        if rl.premium_discount and eq is not None:
            zones = [z for z in zones if (z.hi <= eq if brk.dir == "up" else z.lo >= eq)]
            if not zones:
                self._reject("POI not in discount" if brk.dir == "up" else "POI not in premium", brk)
                return
        armed = sb[k].ts + timedelta(minutes=tf.setup)
        self.s["setup"] = {
            "dir": brk.dir,
            "zones": [z.to_dict() for z in zones],
            "sweep": {"pool": sweep.pool.name, "level": round(sweep.pool.price, 2), "extreme": round(sweep.extreme, 2),
                      "ts": _iso(sb[sweep.i].ts)},
            "break": {"kind": brk.kind, "level": round(brk.level, 2), "ts": _iso(brk.ts)},
            "displacement": {"ts": _iso(sb[disp_i].ts),
                             "body_atr": round(abs(sb[disp_i].close - sb[disp_i].open) / (atrs[disp_i] or 1), 2)},
            "bias": {"trend": bias.trend, "strong": _r(bias.strong), "weak": _r(bias.weak), "eq": _r(eq),
                     "streak": bias.streak, "last": bias.last.kind if bias.last else None},
            "armed": _iso(armed),
            "expires": _iso(armed + timedelta(minutes=tf.setup * rl.poi_max_age)),
            "tapped": None,
        }  # fmt: skip
        self._count("armed")
        self.note(
            "smc_setup", **{k2: v for k2, v in self.s["setup"].items() if k2 not in ("armed", "expires", "tapped")}
        )

    def _zones(
        self, sb: list[smc.Bar], sweep: smc.Sweep, disp_i: int, k: int, d: smc.Dir, atr_k: float
    ) -> list[smc.Zone]:
        rl = self.cfg.rules
        fv = smc.fvgs(sb, sweep.i, k, d, rl.fvg_min_atr * atr_k)
        ob = smc.order_block(sb, disp_i, sweep.i - 1, d, rl.ob_zone == "body")

        # a zone already traded through after it formed is spent
        def fresh(z: smc.Zone) -> bool:
            later = sb[z.i + 1 : k + 1]
            return all((b.low > z.lo) if d == "up" else (b.high < z.hi) for b in later)

        fv = [z for z in fv if fresh(z)]
        obs = [ob] if ob is not None and fresh(ob) else []
        if rl.poi == "fvg":
            return fv
        if rl.poi == "ob":
            return obs
        if rl.poi == "either":
            return [*fv, *obs]
        out = []  # both: where the order block and an FVG overlap
        for z in fv:
            for o in obs:
                lo, hi = max(z.lo, o.lo), min(z.hi, o.hi)
                if lo < hi:
                    out.append(smc.Zone("OB", d, lo, hi, max(z.i, o.i)))
        return out

    def _reject(self, why: str, brk: smc.Break | None = None, detail: str | None = None) -> None:
        self._count(why)
        extra = {"break": f"{brk.kind} {brk.dir} through {brk.level:.2f}"} if brk else {}
        self.note("smc_no_trade", reason=why, **extra, **({"detail": detail} if detail else {}))

    # -- 2. entry timeframe --------------------------------------------------------------------------------------
    def _on_entry_bar(self, m: Market, bars: list[Any], eb: list[smc.Bar], i: int) -> list[Intent]:
        t = rules.hhmm(eb[i].ts)
        ss = self.cfg.session
        if ss.start <= t <= ss.last_entry or self.s.get("setup"):
            self._detect(m, bars)
        setup = self.s.get("setup")
        if not setup:
            return []
        b = eb[i]
        if _iso(b.ts) < setup["armed"]:
            return []
        up = setup["dir"] == "up"
        lo = min(z["lo"] for z in setup["zones"])
        hi = max(z["hi"] for z in setup["zones"])
        if (b.close < setup["sweep"]["extreme"]) if up else (b.close > setup["sweep"]["extreme"]):
            return self._cancel("closed beyond the sweep")
        if (b.close < lo) if up else (b.close > hi):
            return self._cancel("closed through the POI")
        if not setup["tapped"]:
            if _iso(b.ts) >= setup["expires"]:
                return self._cancel("POI not reached in time")
            if (b.low <= hi) if up else (b.high >= lo):
                setup["tapped"] = _iso(b.ts)
                self.note("smc_poi_tapped", price=b.low if up else b.high)
            else:
                return []
        tapped = _ts(setup["tapped"])
        since_tap = [x for x in eb if x.ts >= tapped]
        if len(since_tap) > self.cfg.rules.confirm_bars:
            return self._cancel("no entry confirmation after the tap")
        seg = [x for x in eb if x.ts > _ts(setup["break"]["ts"])]
        ref = self._confirm_ref(seg, up)
        if ref is None or not ((b.close > ref) if up else (b.close < ref)):
            return []
        setup["confirm"] = {"level": round(ref, 2), "ts": _iso(b.ts)}
        return self._signal(m, setup)

    def _confirm_ref(self, seg: list[smc.Bar], up: bool) -> float | None:
        """The pullback's last confirmed swing (high for longs): a close beyond it is the entry CHoCH."""
        sw = [s for s in smc.swings(seg, self.cfg.rules.swing_entry) if s.high == up and s.confirmed < len(seg)]
        return sw[-1].price if sw else None

    def _cancel(self, why: str) -> list[Intent]:
        self.s["setup"] = None
        self._count(why)
        self.note("smc_setup_cancelled", reason=why)
        return []

    # -- 3. signal -----------------------------------------------------------------------------------------------
    def _signal(self, m: Market, setup: dict[str, Any]) -> list[Intent]:
        self.s["setup"] = None
        cfg, r = self.cfg, self.cfg.risk
        up = setup["dir"] == "up"
        sign = 1 if up else -1
        entry = m.spot if m.spot is not None else float(setup["confirm"]["level"])
        sl = setup["sweep"]["extreme"] * (1 - sign * r.sl_buffer_pct / 100)
        risk = (entry - sl) * sign
        if risk <= 0:
            return self._skip("price already beyond the stop")
        if risk < entry * r.min_risk_pct / 100:
            return self._skip(f"stop too tight ({risk:.1f} pts)")
        if risk > entry * r.max_risk_pct / 100:
            return self._skip(f"stop too wide ({risk:.1f} pts)")
        tps = [entry + sign * risk * x for x in (1, (1 + r.rr) / 2, r.rr)]
        room = self._room(m, entry, up)
        if room is not None and room < cfg.rules.min_room_r * risk:
            return self._skip(f"opposing liquidity only {room / risk:.1f}R away")
        guard = self._guard(m)
        if guard:
            return self._skip(guard)
        right: rules.Right = "CE" if up else "PE"
        expiry = self._expiry(m)
        if expiry is None:
            return self._skip("no expiry listed")
        contract = self._contract(m, right, expiry)
        if isinstance(contract, str):
            return self._skip(contract)
        plan = {
            "dir": setup["dir"], "right": right, "entry": _r(entry), "sl": _r(sl), "tp": [_r(x) for x in tps],
            "risk": _r(risk), "reward": _r(risk * r.rr), "rr": r.rr, "setup": setup,
        }  # fmt: skip
        if m.price(contract) is None:
            self.s["pending"] = {**plan, "contract": contract.key, "since": _iso(m.now)}
            return []
        return self._open(m, plan, contract)

    def _guard(self, m: Market) -> str | None:
        r, t = self.cfg.risk, rules.hhmm(m.now)
        if not (self.cfg.session.start <= t <= self.cfg.session.last_entry):
            return f"outside entry hours ({self.cfg.session.start}-{self.cfg.session.last_entry})"
        if self.open_positions() or self.s.get("pending"):
            return "a trade is already open"
        if self.s.get("trades_today", 0) >= r.max_trades_per_day:
            return f"daily trade limit ({r.max_trades_per_day}) reached"
        if self.s.get("losses_today", 0) >= r.max_losses_per_day:
            return f"daily loss limit ({r.max_losses_per_day} losing trades) reached"
        cd = self.s.get("cooldown_until")
        if cd and m.now < _ts(cd):
            return "cooling down after a loss"
        return None

    def _room(self, m: Market, entry: float, up: bool) -> float | None:
        """Distance to the nearest opposing liquidity (buy-side above for longs), None when there is none: previous
        day and opening range extremes and the setup timeframe's confirmed swings beyond the entry."""
        bars = smc.bars_of(m.spot_bars)
        prior = m.prior_spot_bars
        prior_day = smc.bars_of([b for b in prior if b.ts.date() == prior[-1].ts.date()]) if prior else []
        rl = self.cfg.rules
        levels = [p.price for p in smc.day_pools(prior_day, bars, rl.opening_range_minutes) if p.high == up]
        st = smc.structure(smc.resample([*prior_day, *bars], self.cfg.timeframes.setup), rl.swing_setup)
        levels += [s.price for s in st.swings if s.high == up]
        gaps = [(lv - entry) if up else (entry - lv) for lv in levels]
        return min((g for g in gaps if g > 0), default=None)

    def _expiry(self, m: Market) -> Any:
        o = self.cfg.option
        return options.nearest_expiry(m.expiries, m.now, o.expiry, o.expiry_day_cutoff)

    def _contract(self, m: Market, right: rules.Right, expiry: Any) -> Contract | str:
        """The configured strike, or one strike nearer the money when it is illiquid; else why not."""
        o = self.cfg.option
        shifts = [0]
        if o.strike.mode == "atm" and o.strike.offset != 0:
            shifts.append(-1 if o.strike.offset > 0 else 1)
        why = ""
        for sh in shifts:
            c = options.pick(m, right, o.strike, expiry, sh)
            if isinstance(c, str):
                return c
            bad = options.illiquid(m.quotes.get(c.key), o.min_volume, o.min_oi, o.max_spread_pct)
            if bad is None:
                return c
            why = f"{c.label}: {bad}"
        return f"option too illiquid ({why})"

    def _skip(self, why: str) -> list[Intent]:
        self._count("skipped: " + why.split(" (")[0])
        self.note("smc_no_trade", reason=why)
        return []

    def _pending(self, m: Market) -> list[Intent]:
        p = self.s.get("pending")
        if not p:
            return []
        c = Contract.from_key(p["contract"])
        if m.price(c) is not None:
            self.s["pending"] = None
            return self._open(m, p, c)
        if m.now - _ts(p["since"]) > PENDING_LIMIT:
            self.s["pending"] = None
            return self._skip("no price for the option in time")
        return []

    def _open(self, m: Market, plan: dict[str, Any], c: Contract) -> list[Intent]:
        r = self.cfg.risk
        gid = f"SMC-{m.now:%Y%m%d%H%M}"
        self.s["_group"] = gid
        self.s["trade"] = {k: plan[k] for k in ("dir", "entry", "sl", "tp", "risk", "rr")} | {"id": gid, "hit": 0}
        self.s["trades_today"] = self.s.get("trades_today", 0) + 1
        self._count("signals")
        premium = m.price(c)
        self.note(
            "smc_signal",
            signal="BUY " + plan["right"],
            contract=c.label,
            premium=premium,
            entry=plan["entry"],
            stop_loss=plan["sl"],
            tp1=plan["tp"][0],
            tp2=plan["tp"][1],
            tp3=plan["tp"][2],
            risk=plan["risk"],
            reward=plan["reward"],
            rr=f"1:{r.rr}",
            reason=explain(plan["setup"], self.cfg),
            setup=plan["setup"],
        )
        out = []
        for n, lots in enumerate(smc_tranches(r.lots, r.tranches), start=1):
            if lots:
                why = f"SMC {'bullish' if plan['dir'] == 'up' else 'bearish'} entry, tranche for TP{n}"
                out.append(self._enter(m, c, "BUY", lots, f"TP{n}", why))
        return out

    def on_entry(self, pos: Position, intent: Intent, m: Market) -> None:
        tr = self.s.get("trade")
        if not tr:
            return
        pos.group = tr["id"]
        pos.sl, pos.sl_basis = tr["sl"], "underlying"
        n = int(pos.leg[2:]) if pos.leg.startswith("TP") else 3
        pos.target, pos.target_basis = tr["tp"][n - 1], "underlying"
        pos.best = tr["entry"]

    def on_reject(self, intent: Intent, reason: str) -> None:
        tr = self.s.get("trade")
        if intent.kind == "entry" and tr and not any(p.group == tr["id"] for p in self.positions):
            self.s["trade"] = None  # nothing was bought: the trade never existed
            self.s["trades_today"] = max(0, self.s.get("trades_today", 1) - 1)

    def on_exit(self, pos: Position, m: Market) -> None:
        tr = self.s.get("trade")
        if not tr or pos.group != tr["id"] or any(p.open and p.group == tr["id"] for p in self.positions):
            return
        pnl = round(sum(p.pnl() for p in self.positions if p.group == tr["id"]), 2)
        if pnl < 0:
            self.s["losses_today"] = self.s.get("losses_today", 0) + 1
            if self.cfg.risk.cooldown_minutes and pos.exit_time:
                self.s["cooldown_until"] = _iso(pos.exit_time + timedelta(minutes=self.cfg.risk.cooldown_minutes))
        self._count("wins" if pnl > 0 else "losses")
        self.note("smc_trade_closed", pnl=pnl, targets_hit=tr["hit"], last_exit=pos.exit_reason)
        self.s["trade"] = None

    # -- trade management ----------------------------------------------------------------------------------------
    def _manage(self, m: Market) -> list[Intent]:
        tr = self.s.get("trade")
        opens = [p for p in self.open_positions() if p.id not in self.exiting]
        if not tr or not opens:
            return []
        out: list[Intent] = []
        up = tr["dir"] == "up"
        s = m.spot
        if s is not None:
            if (s <= tr["sl"]) if up else (s >= tr["sl"]):
                what = "breakeven" if tr["hit"] >= 1 else "the sweep"
                return [i for p in opens if (i := self._exit(p, f"stop-loss: index at {tr['sl']:.2f} ({what})"))]
            for n, lv in enumerate(tr["tp"], start=1):
                if tr["hit"] < n and ((s >= lv) if up else (s <= lv)):
                    tr["hit"] = n
                    self._move_stop(tr, n)
            for p in opens:
                if p.target is not None and ((s >= p.target) if up else (s <= p.target)):
                    i = self._exit(p, f"target {p.leg} at {p.target:.2f}")
                    if i:
                        out.append(i)
        cap = self.cfg.risk.premium_stop_pct
        if cap:
            for p in opens:
                px = m.price(p.contract)
                if p.id not in self.exiting and px is not None and px <= p.entry_price * (1 - cap / 100):
                    i = self._exit(p, f"stop-loss: premium fell {cap:g}%")
                    if i:
                        out.append(i)
        return out

    def _move_stop(self, tr: dict[str, Any], hit: int) -> None:
        r = self.cfg.risk
        new = None
        if hit == 1 and r.breakeven_at_tp1:
            new = tr["entry"]
        elif hit == 2 and r.trail_at_tp2:
            new = tr["tp"][0]
        if new is None:
            return
        up = tr["dir"] == "up"
        if (new > tr["sl"]) if up else (new < tr["sl"]):
            tr["sl"] = new
            for p in self.open_positions():
                if p.group == tr["id"]:
                    p.sl = new
            self.note("smc_stop_moved", stop_loss=new, after=f"TP{hit}")


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 2)


def explain(setup: Mapping[str, Any], cfg: SmcScalpConfig) -> str:
    """One readable line: why this trade was taken."""
    tf = cfg.timeframes
    up = setup["dir"] == "up"
    b = setup["bias"]
    zones = ", ".join(f"{z['kind']} {z['lo']:.2f}-{z['hi']:.2f}" for z in setup["zones"])
    side = "discount" if up else "premium"
    parts = [
        f"{tf.bias}m {'bullish' if up else 'bearish'} structure ({b['streak']} breaks, last {b['last']}; "
        f"strong {'low' if up else 'high'} {b['strong']}, equilibrium {b['eq']})",
        f"{'sell' if up else 'buy'}-side liquidity swept: {setup['sweep']['pool']} {setup['sweep']['level']} "
        f"(to {setup['sweep']['extreme']})",
        f"{tf.setup}m displacement ({setup['displacement']['body_atr']}x ATR) and {setup['break']['kind']} "
        f"through {setup['break']['level']}",
        f"POI {zones}" + (f" in {side}" if cfg.rules.premium_discount else ""),
        f"{tf.entry}m CHoCH through {setup['confirm']['level']} after the tap",
    ]
    return " · ".join(parts)
