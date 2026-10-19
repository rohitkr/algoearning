"""Strategy runners, driven minute by minute with hand-made prices: entries, stop-losses, targets, trailing, re-entries,
MTM limits, exit time, and the two proven strategies' rules."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Any

from ae_core.strategy import Strike, parse
from ae_core.trading import options, rules
from ae_core.trading.model import IST, Contract, Intent, Market
from ae_core.trading.runners import RangeBreakoutRunner, Runner, TimeBasedRunner, ZeroDteRunner, make_runner

DAY = date(2026, 10, 6)  # a Tuesday: NIFTY's weekly expiry
EXPIRIES = [date(2026, 10, 6), date(2026, 10, 13), date(2026, 10, 20), date(2026, 10, 27), date(2026, 11, 24)]


@dataclass
class B:
    ts: datetime
    open: float
    high: float
    low: float
    close: float


class Sim:
    """Steps a runner and fills every intent at the current price (a perfect paper exchange)."""

    def __init__(self, runner: Runner, day: date = DAY) -> None:
        self.r, self.day = runner, day
        self.spot = 25000.0
        self.prices: dict[str, float] = {}
        self.bars: list[B] = []
        self.filled: list[tuple[str, Intent, float]] = []

    def market(self, hm: str) -> Market:
        now = datetime.combine(self.day, time.fromisoformat(hm), tzinfo=IST)
        return Market(now, "NIFTY", self.spot, list(self.bars), dict(self.prices), EXPIRIES, 65, 50)

    def at(self, hm: str) -> list[Intent]:
        m = self.market(hm)
        out = self.r.step(m)
        for i in out:
            px = m.price(i.contract)
            assert px is not None, f"no price for {i.contract.key}"
            self.r.fill(i, px, m.now, m)
            self.filled.append((hm, i, px))
        return out


def c(strike: int, right: str = "CE", expiry: date = DAY) -> str:
    return Contract("NIFTY", expiry, strike, right).key  # type: ignore[arg-type]


def tb(**over: Any) -> TimeBasedRunner:
    raw: dict[str, Any] = {
        "kind": "time_based",
        "timing": {"entry": "09:20", "exit": "15:15", "days": ["TUE"]},
        "legs": [{"id": "L1", "action": "SELL", "option_type": "CE", "lots": 2, "expiry": "current_week"}],
        **over,
    }
    r = make_runner(parse(raw), multiplier=1)
    assert isinstance(r, TimeBasedRunner)
    return r


# -- rules ------------------------------------------------------------------------------------------------------------
def test_expiry_and_strike_rules() -> None:
    assert rules.pick_expiry(EXPIRIES, DAY, "current_week") == DAY
    assert rules.pick_expiry(EXPIRIES, DAY, "next_week") == date(2026, 10, 13)
    assert rules.pick_expiry(EXPIRIES, DAY, "current_month") == date(2026, 10, 27)
    assert rules.pick_expiry(EXPIRIES, DAY, "next_month") == date(2026, 11, 24)
    assert rules.pick_expiry(EXPIRIES, date(2026, 12, 1), "current_week") is None
    assert rules.expiry_after(EXPIRIES, DAY) == date(2026, 10, 13)  # never the entry day itself
    assert rules.offset_strike(25010, "CE", 2, 50) == 25100 and rules.offset_strike(25010, "PE", 2, 50) == 24900
    assert rules.offset_strike(25010, "PE", -1, 50) == 25050  # negative = in the money
    assert rules.itm_strike(25010, "PE", 100, 50) == 25100 and rules.itm_strike(25010, "CE", 100, 50) == 24900
    assert rules.closest_premium({25000: 120, 25100: 80, 25200: 45}, 60) == 25200
    assert rules.straddle_strikes(25030, 100, 50) == {"CE": 24950, "PE": 25150}


# -- time based -------------------------------------------------------------------------------------------------------
def test_enters_at_entry_time_and_exits_at_exit_time() -> None:
    sim = Sim(tb())
    sim.prices = {c(25000): 100.0}
    assert sim.at("09:19") == []
    (entry,) = sim.at("09:20")
    assert (entry.kind, entry.side, entry.contract.key, entry.lots, entry.qty) == ("entry", "SELL", c(25000), 2, 130)
    assert sim.at("09:21") == []
    sim.prices[c(25000)] = 90.0
    (ex,) = sim.at("15:15")
    assert ex.kind == "exit" and ex.side == "BUY" and ex.reason == "exit time 15:15"
    assert sim.r.realized == (100 - 90) * 130
    assert sim.at("15:16") == []


def test_other_weekdays_do_nothing() -> None:
    sim = Sim(tb(), day=DAY + timedelta(days=1))
    sim.prices = {c(25000, expiry=date(2026, 10, 13)): 100.0}
    assert sim.at("09:20") == [] and sim.r.notes[0]["event"] == "not_a_trading_day_for_this_strategy"


def test_waits_for_option_prices_then_enters() -> None:
    sim = Sim(tb())
    m = sim.market("09:20")
    assert sim.r.step(m) == [] and c(25000) in sim.r.wanted(m)
    sim.prices = {c(25000): 100.0}
    assert len(sim.at("09:20")) == 1


def test_premium_stop_loss_then_reentry_at_cost_once() -> None:
    leg = {"id": "L1", "action": "SELL", "option_type": "CE", "lots": 1, "stop_loss": {"value": 30},
           "reentry_on_sl": {"mode": "at_cost", "count": 1}}  # fmt: skip
    sim = Sim(tb(legs=[leg]))
    sim.prices = {c(25000): 100.0}
    sim.at("09:20")
    assert sim.r.positions[0].sl == 130.0
    sim.prices[c(25000)] = 131.0
    (sl,) = sim.at("10:00")
    assert sl.reason == "stop-loss"
    assert sim.at("10:01") == []  # still above the entry price
    sim.prices[c(25000)] = 99.0
    (re,) = sim.at("10:30")
    assert re.kind == "entry" and "re-entry" in re.reason
    assert sim.r.positions[-1].entry_price == 99.0 and sim.r.positions[-1].sl == 128.7
    sim.prices[c(25000)] = 140.0
    sim.at("11:00")  # second stop: no re-entry left
    sim.prices[c(25000)] = 50.0
    assert sim.at("11:30") == [] and sim.r.s["phase"] == "done"


def test_buyer_target_and_immediate_reentry() -> None:
    leg = {"id": "L1", "action": "BUY", "option_type": "PE", "lots": 1, "target": {"value": 20, "unit": "points"},
           "reentry_on_target": {"mode": "immediate", "count": 2}}  # fmt: skip
    sim = Sim(tb(legs=[leg]))
    sim.prices = {c(25000, "PE"): 100.0}
    sim.at("09:20")
    sim.prices[c(25000, "PE")] = 121.0
    out = sim.at("09:40")
    assert [(i.kind, i.reason) for i in out] == [("exit", "target"), ("entry", "re-entry after target")]
    assert sim.r.positions[-1].target == 141.0


def test_trailing_stop_follows_the_move() -> None:
    leg = {"id": "L1", "action": "BUY", "option_type": "CE", "lots": 1, "stop_loss": {"value": 20, "unit": "points"},
           "trailing": {"trigger": 10, "step": 5, "unit": "points"}}  # fmt: skip
    sim = Sim(tb(legs=[leg]))
    sim.prices = {c(25000): 100.0}
    sim.at("09:20")
    for px, sl in ((105, 80), (110, 85), (129, 90), (131, 95)):
        sim.prices[c(25000)] = px
        sim.at("09:30")
        assert sim.r.positions[0].sl == sl, px
    sim.prices[c(25000)] = 120.0  # falls back: the SL stays
    sim.at("09:31")
    assert sim.r.positions[0].sl == 95
    sim.prices[c(25000)] = 94.0
    (ex,) = sim.at("09:32")
    assert ex.reason == "stop-loss"


def test_index_based_stop_for_a_short_put() -> None:
    leg = {"id": "L1", "action": "SELL", "option_type": "PE", "lots": 1,
           "stop_loss": {"value": 100, "unit": "points", "basis": "underlying"}}  # fmt: skip
    sim = Sim(tb(legs=[leg]))
    sim.prices = {c(25000, "PE"): 100.0}
    sim.at("09:20")
    assert sim.r.positions[0].sl == 24900.0  # a short put loses when the index falls
    sim.spot = 24950
    assert sim.at("10:00") == []
    sim.spot = 24899
    (ex,) = sim.at("10:01")
    assert ex.reason == "stop-loss"


def test_mtm_stop_loss_exits_everything_and_shorts_go_first() -> None:
    legs = [
        {"id": "L1", "action": "SELL", "option_type": "CE", "lots": 1},
        {"id": "W1", "action": "BUY", "option_type": "CE", "lots": 1, "strike": {"offset": 4}},
    ]
    sim = Sim(tb(legs=legs, risk={"mtm_stop_loss": 1000}))
    sim.prices = {c(25000): 100.0, c(25200): 20.0}
    entries = sim.at("09:20")
    assert [i.side for i in entries] == ["BUY", "SELL"]  # hedge first
    sim.prices = {c(25000): 125.0, c(25200): 30.0}  # -1625 + 650 = -975
    assert sim.at("10:00") == []
    sim.prices = {c(25000): 127.0, c(25200): 30.0}  # -1755 + 650 = -1105
    out = sim.at("10:01")
    assert [i.side for i in out] == ["BUY", "SELL"] and out[0].contract.key == c(25000)  # short closed first
    assert sim.r.s["phase"] == "done" and sim.r.realized == -1105.0


def test_exit_all_on_any_leg_stop() -> None:
    legs = [
        {"id": "L1", "action": "SELL", "option_type": "CE", "lots": 1, "stop_loss": {"value": 50}},
        {"id": "L2", "action": "SELL", "option_type": "PE", "lots": 1},
    ]
    sim = Sim(tb(legs=legs, risk={"exit_all_on_leg_sl": True}))
    sim.prices = {c(25000): 100.0, c(25000, "PE"): 100.0}
    sim.at("09:20")
    sim.prices[c(25000)] = 151.0
    out = sim.at("10:00")
    assert sorted(i.reason for i in out) == ["another leg hit its stop-loss", "stop-loss"]


def test_premium_strike_selection() -> None:
    leg = {"id": "L1", "action": "SELL", "option_type": "CE", "lots": 1, "strike": {"mode": "premium", "premium": 50}}
    sim = Sim(tb(legs=[leg]))
    m = sim.market("09:20")
    wanted = sim.r.wanted(m)
    assert c(25000) in wanted and c(25500) in wanted
    sim.prices = {k: max(1.0, 100 - (Contract.from_key(k).strike - 25000) / 10) for k in wanted}
    (entry,) = sim.at("09:20")
    assert entry.contract.strike == 25500  # 100 - 50 = 50


def test_state_survives_a_restart() -> None:
    sim = Sim(tb(legs=[{"id": "L1", "action": "SELL", "option_type": "CE", "stop_loss": {"value": 30}}]))
    sim.prices = {c(25000): 100.0}
    sim.at("09:20")
    again = TimeBasedRunner(sim.r.cfg, 1, sim.r.state())
    assert again.positions[0].sl == 130.0 and again.s["phase"] == "in"
    sim.r = again
    sim.prices[c(25000)] = 131.0
    assert sim.at("10:00")[0].reason == "stop-loss"


def test_multiplier_scales_lots() -> None:
    r = make_runner(tb().cfg, multiplier=3)
    sim = Sim(r)
    sim.prices = {c(25000): 100.0}
    (e,) = sim.at("09:20")
    assert (e.lots, e.qty) == (6, 390)


# -- range breakout ---------------------------------------------------------------------------------------------------
def bars(day: date, start: str, n: int, lo: float, hi: float) -> list[B]:
    t0 = datetime.combine(day, time.fromisoformat(start), tzinfo=IST)
    return [
        B(t0 + timedelta(minutes=i), (lo + hi) / 2, hi if i == 3 else hi - 5, lo if i == 7 else lo + 5, (lo + hi) / 2)
        for i in range(n)
    ]


def test_range_breakout_sells_an_itm_put_on_an_upside_break_and_stops_on_the_index() -> None:
    day = date(2026, 10, 1)
    r = make_runner(parse({"kind": "range_breakout", "hedge_width": 300}))
    assert isinstance(r, RangeBreakoutRunner)
    sim = Sim(r, day=day)
    sim.bars = bars(day, "09:15", 120, 24950, 25050)  # 09:15..11:14
    assert sim.at("11:15") == []
    brk = B(datetime.combine(day, time(11, 20), tzinfo=IST), 25040, 25080, 25030, 25070)
    sim.bars.append(brk)
    short, wing = c(25150, "PE", date(2026, 10, 6)), c(24850, "PE", date(2026, 10, 6))  # ATM 25050 + 100; wing -300
    sim.prices = {short: 180.0, wing: 30.0}
    out = sim.at("11:21")
    assert [(i.side, i.contract.key) for i in out] == [("BUY", wing), ("SELL", short)]
    pos = next(p for p in r.positions if p.leg == "short")
    assert pos.sl == round(25070 * (1 - 0.005), 2) and pos.sl_basis == "underlying"
    # the index falls 0.5%: stop both legs, then wait to re-enter at cost
    sim.bars.append(B(brk.ts + timedelta(minutes=30), 24950, 24950, 24930, 24940))
    out = sim.at("11:51")
    assert {i.kind for i in out} == {"exit"} and len(out) == 2 and r.s["waiting"]
    sim.bars.append(B(brk.ts + timedelta(minutes=60), 25060, 25080, 25060, 25075))
    out = sim.at("12:21")
    assert [i.kind for i in out] == ["entry", "entry"] and "re-entry" in out[1].reason
    # only one breakout a day counts
    sim.bars.append(B(brk.ts + timedelta(minutes=70), 25200, 25300, 25200, 25300))
    assert sim.at("12:31") == []


def test_range_breakout_skips_a_day_with_too_few_range_bars() -> None:
    day = date(2026, 10, 1)
    r = make_runner(parse({"kind": "range_breakout"}))
    sim = Sim(r, day=day)
    late = B(datetime.combine(day, time(11, 20), tzinfo=IST), 1, 1, 1, 25100)
    sim.bars = [*bars(day, "10:30", 45, 24950, 25050), late]
    assert sim.at("11:21") == [] and r.notes[-1]["event"] == "no_trade_day"


def test_range_breakout_holds_to_expiry_day_exit() -> None:
    day = date(2026, 10, 1)
    r = make_runner(parse({"kind": "range_breakout", "hedge_width": None}))
    sim = Sim(r, day=day)
    down = B(datetime.combine(day, time(11, 20), tzinfo=IST), 1, 1, 1, 24900)
    sim.bars = [*bars(day, "09:15", 120, 24950, 25050), down]
    short = c(24800, "CE", date(2026, 10, 6))  # downside break: sell an ITM call
    sim.prices = {short: 300.0}
    (e,) = sim.at("11:21")
    assert e.contract.key == short and e.side == "SELL"
    exp = Sim(r, day=date(2026, 10, 6))
    exp.prices = {short: 150.0}
    exp.bars = [B(datetime.combine(exp.day, time(15, 15), tzinfo=IST), 1, 1, 1, 24850)]
    (x,) = exp.at("15:16")
    assert x.reason == "expiry-day exit" and r.realized == (300 - 150) * 65


# -- 0DTE -------------------------------------------------------------------------------------------------------------
def test_zero_dte_sells_itm_straddle_on_expiry_day_with_premium_stops() -> None:
    r = make_runner(parse({"kind": "zero_dte"}))
    assert isinstance(r, ZeroDteRunner)
    sim = Sim(r)
    sim.spot = 25030
    ce, pe = c(24950), c(25150, "PE")
    sim.prices = {ce: 150.0, pe: 160.0}
    assert sim.at("09:19") == []
    out = sim.at("09:20")
    assert sorted(i.contract.key for i in out) == sorted([ce, pe]) and all(i.side == "SELL" for i in out)
    sim.prices[ce] = 196.0  # +30.7%
    (x,) = sim.at("10:00")
    assert x.contract.key == ce and "stop-loss" in x.reason
    sim.prices[ce] = 149.0  # back below the first entry: re-enter once
    (re,) = sim.at("10:30")
    assert re.contract.key == ce and re.reason == "re-entry at cost"
    out = sim.at("15:15")
    assert len(out) == 2 and all(i.kind == "exit" for i in out)


def test_zero_dte_only_on_expiry_days() -> None:
    r = make_runner(parse({"kind": "zero_dte"}))
    sim = Sim(r, day=date(2026, 10, 7))
    assert sim.at("09:20") == [] and r.notes[0]["event"] == "not_an_expiry_day"


def test_risk_checks() -> None:
    from ae_core.trading.risk import RiskContext, RiskSettings, breach, check_entry

    e = Intent("entry", "SELL", Contract.from_key(c(25000)), 2, 130, "x", "L1")
    x = Intent("exit", "BUY", Contract.from_key(c(25000)), 2, 130, "x", "L1")

    def ctx(**kw: Any) -> RiskContext:
        base: dict[str, Any] = {"settings": RiskSettings(), "platform_halted": False, "max_lots_per_order": 10,
                                "day_pnl": 0.0, "open_positions": 0, "entries_today": 0}  # fmt: skip
        return RiskContext(**{**base, **kw})

    assert check_entry(e, ctx()) is None
    assert "halted" in (check_entry(e, ctx(platform_halted=True)) or "")
    assert "kill switch" in (check_entry(e, ctx(settings=RiskSettings(kill_switch=True))) or "")
    assert "loss limit" in (breach(ctx(settings=RiskSettings(max_daily_loss=5000), day_pnl=-5000)) or "")
    assert breach(ctx(settings=RiskSettings(max_daily_loss=5000), day_pnl=-4999)) is None
    assert "open positions" in (
        check_entry(e, ctx(settings=RiskSettings(max_open_positions=2), open_positions=2)) or ""
    )
    assert "entries today" in (check_entry(e, ctx(settings=RiskSettings(max_trades_per_day=3), entries_today=3)) or "")
    assert "plan" in (check_entry(e, ctx(max_lots_per_order=1)) or "")
    assert check_entry(x, ctx(platform_halted=True)) is None  # exits are never blocked


def test_range_breakout_waits_for_prices_of_freshly_chosen_contracts() -> None:
    """Regression: the contracts are only known at the breakout, so the feed cannot be streaming them yet. The signal
    must be kept until their prices arrive, not rejected (which lost every breakout in live and paper trading)."""
    day = date(2026, 10, 1)
    r = make_runner(parse({"kind": "range_breakout", "hedge_width": 300}))
    assert isinstance(r, RangeBreakoutRunner)
    sim = Sim(r, day=day)
    sim.bars = bars(day, "09:15", 120, 24950, 25050)
    brk = B(datetime.combine(day, time(11, 20), tzinfo=IST), 25040, 25080, 25030, 25070)
    sim.bars.append(brk)
    short, wing = c(25150, "PE", date(2026, 10, 6)), c(24850, "PE", date(2026, 10, 6))
    m = sim.market("11:21")
    assert r.step(m) == []  # no prices yet: nothing is ordered and nothing is lost
    assert {short, wing} <= r.wanted(m)  # the engine now asks the feed for them
    assert r.notes[-1]["event"] == "waiting_for_prices"
    sim.prices = {short: 180.0, wing: 30.0}
    out = sim.at("11:22")
    assert [(i.kind, i.side) for i in out] == [("entry", "BUY"), ("entry", "SELL")]
    assert r.s["pending_entry"] is None
    late = Sim(make_runner(parse({"kind": "range_breakout"})), day=day)
    late.bars = [*bars(day, "09:15", 120, 24950, 25050), brk]
    late.at("11:21")
    late.at("11:27")  # still no prices after more than 5 minutes: the breakout is skipped, with a reason
    assert late.r.notes[-1]["event"] == "no_price_for_entry"


# -- rules (ADR 0022) -------------------------------------------------------------------------------------------------
THU, FRI, MON = date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 12)
WEEK2 = date(2026, 10, 13)


def rr(**over: Any) -> Runner:
    raw: dict[str, Any] = {
        "kind": "rules",
        "entry": {"at": "15:00"},
        "holding": {"mode": "next_day", "exit": "09:30"},
        "legs": [{"id": "L1", "action": "SELL", "option_type": "CE", "lots": 1}],
        **over,
    }
    r = make_runner(parse(raw))
    assert r.kind == "rules"
    return r


def premium_ladder(sim: Sim, hm: str) -> None:
    """CE and PE prices that fall 10 per 50 points out of the money from 160 at the money."""
    for k in sim.r.wanted(sim.market(hm)):
        ct = Contract.from_key(k)
        away = (ct.strike - 25000) if ct.right == "CE" else (25000 - ct.strike)
        sim.prices[k] = max(1.0, 160 - away / 5)


def test_overnight_straddle_by_premium_exits_next_day_then_enters_again() -> None:
    legs = [
        {"id": "L1", "action": "SELL", "option_type": "CE", "strike": {"mode": "premium", "premium": 60}},
        {"id": "L2", "action": "SELL", "option_type": "PE", "strike": {"mode": "premium", "premium": 60}},
    ]
    sim = Sim(rr(legs=legs), day=THU)
    premium_ladder(sim, "15:00")
    assert sim.at("14:59") == []
    out = sim.at("15:00")
    assert sorted(i.contract.key for i in out) == [c(24500, "PE", WEEK2), c(25500, "CE", WEEK2)]  # 160 - 100 = 60
    assert sim.at("15:29") == []
    sim.day = FRI
    assert sim.at("09:15") == [] and sim.r.s["phase"] == "in"  # held over the night
    assert len(sim.r.open_positions()) == 2
    out = sim.at("09:30")
    assert [i.reason for i in out] == ["exit time 09:30", "exit time 09:30"]
    assert sim.r.s["phase"] == "waiting"  # entered yesterday: today's entry is still to come
    premium_ladder(sim, "15:00")
    assert len(sim.at("15:00")) == 2 and sim.r.s["phase"] == "in"
    sim.day = MON  # over the weekend
    assert sim.at("09:29") == [] and len(sim.at("09:30")) == 2


def test_a_missed_exit_happens_at_the_next_step() -> None:
    sim = Sim(rr(), day=THU)
    sim.prices = {c(25000, "CE", WEEK2): 100.0}
    sim.at("15:00")
    sim.day = MON  # Friday was a holiday (or the engine was off)
    (ex,) = sim.at("09:15")
    assert ex.reason == "exit time 09:30"


def test_hold_to_expiry_only_on_chosen_days_before_it() -> None:
    raw = {"entry": {"at": "09:30", "dte": [3]}, "holding": {"mode": "expiry", "exit": "15:15"}}
    sim = Sim(rr(**raw), day=date(2026, 10, 7))  # Wed: 4 weekdays before Tue 13th
    sim.prices = {c(25000, "CE", WEEK2): 100.0}
    assert sim.at("09:30") == [] and sim.r.notes[-1]["event"] == "not_a_chosen_day_before_expiry"
    sim = Sim(rr(**raw), day=THU)
    sim.prices = {c(25000, "CE", WEEK2): 100.0}
    assert len(sim.at("09:30")) == 1
    for day in (FRI, MON):
        sim.day = day
        assert sim.at("15:15") == []
    sim.day = WEEK2
    assert sim.at("15:14") == [] and sim.at("15:15")[0].reason == "exit time 15:15"


def test_intraday_rules_do_not_enter_after_until() -> None:
    sim = Sim(rr(entry={"at": "09:20", "until": "09:30"}, holding={"mode": "intraday", "exit": "15:15"}))
    sim.prices = {c(25000): 100.0}
    assert sim.at("09:31") == [] and sim.r.s["phase"] == "done"


def test_combined_premium_stop() -> None:
    legs = [
        {"id": "L1", "action": "SELL", "option_type": "CE"},
        {"id": "L2", "action": "SELL", "option_type": "PE"},
    ]
    sim = Sim(rr(legs=legs, entry={"at": "09:20"}, holding={"mode": "intraday"},
                 risk={"combined_stop": {"unit": "percent", "value": 20}}))  # fmt: skip
    sim.prices = {c(25000): 100.0, c(25000, "PE"): 100.0}
    sim.at("09:20")
    sim.prices = {c(25000): 150.0, c(25000, "PE"): 89.0}  # 239 < 240
    assert sim.at("10:00") == []
    sim.prices[c(25000, "PE")] = 91.0
    out = sim.at("10:01")
    assert len(out) == 2 and out[0].reason == "sold premiums up 20%"


def test_lock_profit_trails_and_exits_on_giveback() -> None:
    sim = Sim(rr(entry={"at": "09:20"}, holding={"mode": "intraday"},
                 risk={"lock_profit": {"at": 1000, "lock": 500, "trail_every": 500, "trail_by": 250}}))  # fmt: skip
    sim.prices = {c(25000): 100.0}
    sim.at("09:20")
    sim.prices[c(25000)] = 84.0  # +1040: locks 500
    assert sim.at("10:00") == [] and sim.r.s["lock_floor"] == 500
    sim.prices[c(25000)] = 76.0  # +1560: floor 750
    assert sim.at("10:01") == [] and sim.r.s["lock_floor"] == 750
    sim.prices[c(25000)] = 89.0  # +715
    (ex,) = sim.at("10:02")
    assert ex.reason == "profit fell to the locked ₹750"


def test_mtm_limits_count_only_the_current_trade() -> None:
    sim = Sim(rr(risk={"mtm_stop_loss": 1000}), day=THU)
    sim.prices = {c(25000, "CE", WEEK2): 100.0}
    sim.at("15:00")
    sim.prices[c(25000, "CE", WEEK2)] = 110.0
    sim.day = FRI
    sim.at("09:30")  # -650 on the first trade
    assert sim.r.realized == -650.0
    sim.at("15:00")
    sim.prices[c(25000, "CE", WEEK2)] = 115.0  # -325 on this one; -975 in all
    assert sim.at("15:10") == []


def test_new_strike_modes() -> None:
    sim = Sim(rr())
    m = sim.market("09:20")
    pick = options.pick
    assert pick(m, "CE", Strike(mode="points", points=300), DAY).strike == 25300  # type: ignore[union-attr]
    assert pick(m, "PE", Strike(mode="points", points=300), DAY).strike == 24700  # type: ignore[union-attr]
    assert pick(m, "PE", Strike(mode="points", points=-120), DAY).strike == 25100  # type: ignore[union-attr]
    keys = {ct.key: ct for ct in options.candidates(m, "CE", DAY)}
    m = Market(
        m.now, "NIFTY", 25000, [], {k: 160 - (ct.strike - 25000) / 5 for k, ct in keys.items()}, EXPIRIES, 65, 50
    )
    assert pick(m, "CE", Strike(mode="premium_gte", premium=65), DAY).strike == 25450  # type: ignore[union-attr]
    assert pick(m, "CE", Strike(mode="premium_lte", premium=65), DAY).strike == 25500  # type: ignore[union-attr]
    assert pick(m, "CE", Strike(mode="premium_gte", premium=500), DAY) == "no CE strike costs at least ₹500"


def test_no_overnight_entry_in_an_option_that_expires_first() -> None:
    sim = Sim(rr())  # Tuesday, expiry day: the current week's options expire tonight
    sim.prices = {c(25000): 100.0}
    assert sim.at("15:00") == [] and sim.r.notes[-1]["event"] == "leg_expires_before_the_exit"
    sim = Sim(rr(legs=[{"id": "L1", "action": "SELL", "option_type": "CE", "expiry": "next_week"}]))
    sim.prices = {c(25000, expiry=WEEK2): 100.0}
    assert len(sim.at("15:00")) == 1


def test_a_time_based_run_in_progress_carries_on_after_the_upgrade() -> None:
    sim = Sim(tb())
    sim.prices = {c(25000): 100.0}
    sim.at("09:20")
    state = sim.r.state()
    del state["final"], state["entries"], state["entries_day"], state["cycle_realized"]
    state["entered"] = DAY.isoformat()  # what the old runner stored
    sim.r = TimeBasedRunner(sim.r.cfg, 1, state)
    assert sim.at("12:00") == [] and sim.at("15:15")[0].reason == "exit time 15:15"


# -- conditions (ADR 0023) --------------------------------------------------------------------------------------------
def minute_bars(day: date, frm: str, closes: list[float], spread: float = 2.0) -> list[B]:
    t0 = datetime.combine(day, time.fromisoformat(frm), tzinfo=IST)
    out, prev = [], closes[0]
    for i, px in enumerate(closes):
        out.append(B(t0 + timedelta(minutes=i), prev, max(prev, px) + spread, min(prev, px) - spread, px))
        prev = px
    return out


def orb_runner(**over: Any) -> Runner:
    raw: dict[str, Any] = {
        "kind": "rules",
        "entry": {
            "mode": "conditions", "at": "09:30", "until": "14:30",
            "signals": [
                {"direction": "up", "conditions": [{"op": "crosses_above", "candle": 5,
                    "right": {"kind": "level", "level": "opening_high", "minutes": 15}}]},
                {"direction": "down", "conditions": [{"op": "crosses_below", "candle": 5,
                    "right": {"kind": "level", "level": "opening_low", "minutes": 15}}]},
            ],
        },
        "legs": [
            {"id": "C", "action": "BUY", "option_type": "CE", "direction": "up"},
            {"id": "P", "action": "BUY", "option_type": "PE", "direction": "down"},
        ],
        **over,
    }  # fmt: skip
    return make_runner(parse(raw))


OPENING = [25000.0] * 15  # 09:15-09:29: range 24998..25002 with the 2-point spread


def orb_sim(closes_after: list[float], **over: Any) -> Sim:
    sim = Sim(orb_runner(**over))
    sim.bars = minute_bars(DAY, "09:15", OPENING + closes_after)
    sim.prices = {c(25000): 100.0, c(25000, "PE"): 100.0}
    return sim


def test_opening_range_breakout_buys_a_call_on_an_upside_close() -> None:
    sim = orb_sim([25001, 25001, 25001, 25001, 25001, 25010, 25020, 25030, 25040, 25050])  # 09:30-09:34 inside
    assert sim.at("09:35") == [] and sim.r.s["phase"] == "waiting"  # 5-min close 25001 is inside the range
    sim.bars = minute_bars(DAY, "09:15", OPENING + [25001] * 5 + [25010, 25020, 25030, 25040, 25050])
    (e,) = sim.at("09:40")  # candle 09:35-09:40 closes at 25050, above the high of 25002
    assert (e.kind, e.side, e.contract.key, e.reason) == ("entry", "BUY", c(25000), "up signal")
    assert sim.r.s["direction"] == "up"


def test_a_downside_close_buys_the_put_and_nothing_trades_before_the_range_is_formed() -> None:
    sim = orb_sim([24990] * 5)
    assert sim.at("09:28") == []  # still inside the first 15 minutes
    (e,) = sim.at("09:35")
    assert e.contract.key == c(25000, "PE") and sim.r.s["direction"] == "down"


def test_exit_on_the_opposite_signal_and_a_second_trade() -> None:
    sim = orb_sim(
        [25020] * 5,
        exit={"on_opposite_signal": True},
        entry={**orb_runner().cfg.entry.model_dump(mode="json"), "max_per_day": 2},
    )
    (up,) = sim.at("09:35")
    assert up.contract.key == c(25000)
    sim.bars = minute_bars(DAY, "09:15", OPENING + [25020] * 5 + [24980] * 5)
    sim.prices[c(25000, "PE")] = 90.0
    out = sim.at("09:40")
    assert out[0].kind == "exit" and out[0].reason == "down signal"
    out = sim.at("09:41")
    assert [(i.kind, i.contract.key) for i in out] == [("entry", c(25000, "PE"))]  # the second trade of the day
    assert sim.r.s["entries"] == 2 and sim.r.s["phase"] == "in"


def test_exit_when_the_price_closes_back_inside_the_range() -> None:
    ex = {"when": {"conditions": [{"op": "below", "candle": 5, "right": {"kind": "level", "level": "opening_high"}}]}}
    sim = orb_sim([25020] * 5, exit=ex)
    sim.at("09:35")
    sim.bars = minute_bars(DAY, "09:15", OPENING + [25020] * 5 + [25001] * 5)
    out = sim.at("09:40")
    assert [(i.kind, i.reason) for i in out] == [("exit", "exit condition")]


def test_a_signal_waits_for_option_prices_instead_of_being_lost() -> None:
    sim = orb_sim([25020] * 5)
    sim.prices = {}
    assert sim.at("09:35") == [] and sim.r.s["armed"]["dir"] == "up"
    sim.prices = {c(25000): 80.0}
    (e,) = sim.at("09:36")  # no new candle, but the armed signal still enters
    assert e.reason == "up signal"


def test_previous_day_levels_and_state_conditions() -> None:
    cond = {"left": {"kind": "price"}, "op": "above", "candle": 5, "right": {"kind": "level", "level": "prev_high"}}
    sim = Sim(make_runner(parse({
        "kind": "rules", "entry": {"mode": "conditions", "at": "09:30",
                                   "signals": [{"conditions": [cond]}]},
        "legs": [{"id": "L1", "action": "BUY", "option_type": "CE"}],
    })))  # fmt: skip
    assert sim.r.prior_days == 1
    yesterday = date(2026, 10, 5)
    prior = minute_bars(yesterday, "09:15", [24900.0] * 10 + [25100.0] + [24950.0] * 10)  # high 25102
    sim.bars = minute_bars(DAY, "09:15", [25000.0] * 20)
    sim.prices = {c(25000): 100.0}
    base = sim.market

    def with_prior(hm: str) -> Market:
        return replace(base(hm), prior_spot_bars=prior)

    sim.market = with_prior  # type: ignore[method-assign]
    assert sim.at("09:35") == []  # 25000 is below yesterday's high
    sim.bars = minute_bars(DAY, "09:15", [25000.0] * 20 + [25200.0] * 5)
    assert len(sim.at("09:40")) == 1


def test_a_live_entry_that_fills_steps_later_is_still_managed_and_exits_next_day() -> None:
    """Live orders fill a few seconds after the step that sent them; the steps in between must not end the trade
    (14 Oct 2026: an overnight trade counted as over before its fills arrived and missed its 13:30 exit)."""
    r = rr()
    sim = Sim(r, day=THU)
    sim.prices = {c(25000, "CE", WEEK2): 100.0}
    m = sim.market("15:00")
    (entry,) = r.step(m)  # sent, not filled yet
    assert r.step(sim.market("15:00")) == [] and r.s["phase"] == "in"
    r.fill(entry, 100.0, m.now, m)
    assert r.s["awaiting"] == [] and len(r.open_positions()) == 1
    sim.day = FRI
    (ex,) = sim.at("09:30")
    assert ex.reason == "exit time 09:30"


def test_open_legs_of_a_trade_marked_over_are_managed_again() -> None:
    sim = Sim(rr(), day=THU)
    sim.prices = {c(25000, "CE", WEEK2): 100.0}
    sim.at("15:00")
    sim.day = FRI
    assert sim.at("09:15") == []
    sim.r.s["phase"] = "waiting"  # what the bug above left behind
    (ex,) = sim.at("09:31")
    assert ex.reason == "exit time 09:30"
    assert any(n["event"] == "trade_resumed" for n in sim.r.notes)


def test_an_entry_never_filled_does_not_hold_the_trade_open_into_the_next_day() -> None:
    sim = Sim(rr(), day=THU)
    sim.at("14:00")
    sim.r.s.update(phase="in", awaiting=["lost-order"], final="2026-10-08T15:30:00+05:30")  # sent, never answered
    sim.day = FRI
    sim.at("09:15")
    assert sim.r.s["awaiting"] == [] and sim.r.s["phase"] == "waiting"


def test_a_leg_switched_off_is_never_traded_and_old_configs_still_load() -> None:
    legs = [
        {"id": "L1", "action": "SELL", "option_type": "CE", "lots": 1},
        {"id": "L2", "action": "SELL", "option_type": "PE", "lots": 1, "enabled": False},
    ]
    sim = Sim(rr(legs=legs, entry={"at": "15:00"}), day=THU)
    sim.prices = {c(25000, "CE", WEEK2): 100.0, c(25000, "PE", WEEK2): 100.0}
    out = sim.at("15:00")
    assert [i.contract.right for i in out] == ["CE"]  # the PE leg is off
    assert parse({"kind": "rules", "legs": [{"id": "L1", "action": "BUY", "option_type": "CE"}]}).legs[0].enabled


def test_trade_limits_wait_until_every_leg_has_a_price() -> None:
    """A leg without a price counted as zero: on a gap day the hedge alone looked like a profit target (6 Mar 2026)."""
    hedge_strike = {"mode": "atm", "offset": 6}
    legs = [{"id": "L1", "action": "SELL", "option_type": "PE", "lots": 1},
            {"id": "L2", "action": "BUY", "option_type": "PE", "lots": 1, "strike": hedge_strike}]  # fmt: skip
    sim = Sim(rr(legs=legs, risk={"mtm_target": 5000}), day=THU)
    sold, hedge = c(25000, "PE", WEEK2), c(24700, "PE", WEEK2)
    sim.prices = {sold: 250.0, hedge: 150.0}
    assert len(sim.at("15:00")) == 2
    sim.day = FRI
    sim.prices = {hedge: 480.0}  # the sold leg has no price
    assert sim.at("09:20") == []
    assert any(n["event"] == "trade_limits_paused" for n in sim.r.notes)


def test_a_held_trade_waits_for_the_open_delay_before_stops_apply_but_intraday_never_does() -> None:
    leg = [{"id": "L1", "action": "SELL", "option_type": "CE", "stop_loss": {"unit": "percent", "value": 20}}]
    sim = Sim(rr(legs=leg, holding={"mode": "next_day", "exit": "09:40", "open_delay_min": 10}), day=THU)
    sim.prices = {c(25000, "CE", WEEK2): 100.0}
    assert len(sim.at("15:00")) == 1
    sim.day = FRI
    sim.prices = {c(25000, "CE", WEEK2): 150.0}  # far above the stop at the open
    assert sim.at("09:15") == [] and sim.at("09:24") == []  # within the first 10 minutes: not judged
    (ex,) = sim.at("09:25")
    assert ex.reason == "stop-loss"
    # the same trade held for the same day is judged at once: the delay is for held trades only
    same = Sim(
        rr(legs=leg, entry={"at": "09:20"}, holding={"mode": "intraday", "exit": "15:15", "open_delay_min": 10}),
        day=THU,
    )
    same.prices = {c(25000, "CE", WEEK2): 100.0}
    assert len(same.at("09:20")) == 1
    same.prices = {c(25000, "CE", WEEK2): 150.0}
    assert [i.reason for i in same.at("09:21")] == ["stop-loss"]


SRC = "00000000-0000-0000-0000-0000000000aa"


class TipSim(Sim):
    def __init__(self, runner: Runner, tips: list[Any], day: date = DAY) -> None:
        super().__init__(runner, day)
        self.tips = tips

    def market(self, hm: str) -> Market:
        m = super().market(hm)
        m.tips = list(self.tips)
        return m


def as_given(lots: int = 4) -> Runner:
    return rr(
        entry={"mode": "tip", "at": "09:15", "source_id": SRC, "tip_trade": "as_given", "tip_lots": lots},
        holding={"mode": "intraday", "exit": "15:15"},
        legs=[],
    )


def the_tip(minute: str = "10:30") -> Any:
    from ae_core.trading.model import Tip

    return Tip(
        7,
        SRC,
        datetime.combine(DAY, time.fromisoformat(minute), tzinfo=IST),
        "BEARISH",
        "OPEN",
        True,
        "BUY NIFTY 25000 PE",
        "NIFTY",
        "BUY",
        25000,
        "PE",
        156.0,
        160.0,
        141.0,
        (175.0, 190.0, 210.0),
    )


def test_a_tip_as_given_books_a_quarter_at_each_target_and_trails_the_stop() -> None:
    sim = TipSim(as_given(4), [the_tip()])
    pe = c(25000, "PE", DAY)
    assert sim.r.wanted(sim.market("10:30")) >= {pe}  # streamed before the entry
    sim.prices = {pe: 163.0}  # within 160 + buffer 5
    out = sim.at("10:31")
    assert [(i.side, i.lots, i.leg) for i in out] == [("BUY", 1, f"T{k}") for k in (1, 2, 3, 4)]
    sim.prices[pe] = 176.0
    (t1,) = sim.at("10:35")
    assert t1.leg == "T1" and t1.reason == "target 1 (175)" and sim.r.s["tipplan"]["sl"] == 163.0  # cost
    sim.prices[pe] = 211.0  # TP2 and TP3 in one minute
    assert [i.leg for i in sim.at("10:40")] == ["T2", "T3"] and sim.r.s["tipplan"]["sl"] == 190.0
    sim.prices[pe] = 240.0
    assert sim.at("10:45") == []  # the last quarter runs to TP3 + 20% (252)
    sim.prices[pe] = 189.0
    (last,) = sim.at("10:50")
    assert last.leg == "T4" and last.reason == "trailing stop (190)"


def test_a_tip_as_given_with_lots_not_a_multiple_of_4_exits_whole_at_target_3() -> None:
    sim = TipSim(as_given(3), [the_tip()])
    pe = c(25000, "PE", DAY)
    sim.prices = {pe: 158.0}
    (entry,) = sim.at("10:31")
    assert entry.lots == 3
    sim.prices[pe] = 192.0
    assert sim.at("10:35") == [] and sim.r.s["tipplan"]["sl"] == 175.0  # stop at TP1 after TP2
    sim.prices[pe] = 212.0
    (out,) = sim.at("10:40")
    assert out.reason == "target 3 (210)"


def test_a_tip_as_given_is_skipped_when_the_price_ran_away() -> None:
    sim = TipSim(as_given(4), [the_tip()])
    sim.prices = {c(25000, "PE", DAY): 166.0}
    assert sim.at("10:31") == []
    assert any(n["event"] == "tip_skipped" and "above the entry 160 + buffer 5" in n["reason"] for n in sim.r.notes)
