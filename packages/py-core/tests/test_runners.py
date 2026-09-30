"""Strategy runners, driven minute by minute with hand-made prices: entries, stop-losses, targets, trailing, re-entries,
MTM limits, exit time, and the two proven strategies' rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from ae_core.strategy import parse
from ae_core.trading import rules
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
