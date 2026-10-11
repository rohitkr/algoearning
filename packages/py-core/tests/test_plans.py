"""Plans (ADR 0026): a strategy as day plans. The model and its checks, the conversion of the older strategies (the same
trades, as plans), plans running side by side in one runner, and a stress run over many price paths."""

from __future__ import annotations

import collections
import random
from datetime import date, datetime, time, timedelta
from typing import Any

import pytest
from ae_core.backtest import Candle, Costs, MemoryHistory, simulate
from ae_core.strategy import (
    Instrument,
    PlansConfig,
    check,
    parse,
    plans_from_rules,
    tip_source_ids,
    uses_tips,
)
from ae_core.trading.model import IST
from ae_core.trading.runners import PlansRunner, make_runner
from pydantic import ValidationError

INST = {
    "NIFTY": Instrument("NIFTY", "Nifty 50", "NFO", 65, 50, True, "09:15", "15:30"),
    "SENSEX": Instrument("SENSEX", "Sensex", "BFO", 20, 100, True, "09:15", "15:30"),
}
NOFEE = Costs(0, 0, 0, 0, 0, 0)


def ts(d: date, hm: str) -> datetime:
    return datetime.combine(d, time.fromisoformat(hm), tzinfo=IST)


def weekdays(start: date, end: date) -> list[date]:
    return [
        start + timedelta(days=i) for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() < 5
    ]


def world(
    start: date, end: date, seed: int = 1, vol: float = 0.003, underlying: str = "NIFTY", step: int = 50
) -> MemoryHistory:
    """Index around 25,000 and every strike of the next two Tuesday expiries, full days, a random walk per contract."""
    rnd = random.Random(seed)
    h = MemoryHistory()
    days = weekdays(start, end + timedelta(days=14))
    expiries = [d for d in days if d.weekday() == 1]
    for d in weekdays(start, end):
        t = ts(d, "09:15")
        spot = 25000 + rnd.randint(-150, 150)
        h.add_spot(underlying, [Candle(t + timedelta(minutes=i), spot, spot + 3, spot - 3, spot) for i in range(375)])
        for e in [x for x in expiries if x >= d][:2]:
            for k in range(24000, 26050, step):
                for right in ("CE", "PE"):
                    px = max(2.0, 130 - 0.8 * abs(k - 25000))
                    bars = []
                    for i in range(375):
                        o = px
                        px = max(1.0, px * (1 + rnd.uniform(-vol, vol)))
                        bars.append(
                            Candle(
                                t + timedelta(minutes=i),
                                o,
                                max(o, px) + rnd.random(),
                                max(0.5, min(o, px) - rnd.random()),
                                px,
                            )
                        )
                    h.add_option(f"{underlying}:{e}:{k}:{right}", bars)
    return h


def leg(i: str, action: str, right: str, **kw: Any) -> dict[str, Any]:
    return {"id": i, "action": action, "option_type": right, "strike": {"mode": "atm", "offset": 0},
            "expiry": "current_week", **kw}  # fmt: skip


def plan(pid: str, days: list[str], legs: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    return {"id": pid, "name": f"Plan {pid}", "underlying": "NIFTY", "days": days, "legs": legs, **kw}


def run(cfg: Any, h: MemoryHistory, start: date, end: date, slip: float = 0.05) -> Any:
    return simulate(cfg, h, start, end, lot_size=65, strike_step=50, slippage_pct=slip, costs=Costs())


def trades(r: Any) -> list[tuple[Any, ...]]:
    return sorted(
        (
            t.entry_time,
            t.contract,
            t.side,
            t.qty,
            round(t.entry_price, 2),
            t.exit_time,
            round(t.exit_price, 2),
            t.reason,
        )
        for t in r.trades
    )


START, END = date(2026, 9, 1), date(2026, 9, 30)


# -- the model -----------------------------------------------------------------------------------------------------
def test_plans_parse_derive_the_instrument_and_use_their_days_as_entry_days() -> None:
    cfg = parse({"kind": "plans", "plans": [plan("P1", ["MON", "WED"], [leg("L1", "SELL", "CE")]),
                                           {**plan("P2", ["TUE"], [leg("L1", "BUY", "PE")]),
                                            "underlying": "SENSEX"}]})  # fmt: skip
    assert isinstance(cfg, PlansConfig)
    assert cfg.underlying == "NIFTY" and cfg.underlyings() == ["NIFTY", "SENSEX"]
    assert cfg.plans[0].entry.days == ["MON", "WED"] and cfg.plans[0].to_rules().entry.days == ["MON", "WED"]
    assert [p.id for p in cfg.for_underlying("SENSEX").plans] == ["P2"] and cfg.for_underlying(
        "SENSEX"
    ).underlying == "SENSEX"
    assert parse(cfg.model_dump(mode="json")) == cfg  # what the API stores and the page sends back


def test_a_typo_or_an_empty_plan_is_refused() -> None:
    with pytest.raises(ValidationError):
        parse({"kind": "plans", "plans": [plan("P1", ["MON"], [])]})  # a plan needs a leg
    with pytest.raises(ValidationError):
        parse({"kind": "plans", "plans": []})
    with pytest.raises(ValidationError):
        parse({"kind": "plans", "plans": [{**plan("P1", ["MON"], [leg("L1", "SELL", "CE")]), "stoploss": 3}]})


def test_checks_name_the_plan_and_the_field() -> None:
    cfg = parse({"kind": "plans", "plans": [
        plan("P1", ["MON"], [leg("L1", "SELL", "CE")]),
        plan("P1", ["TUE"], [leg("L1", "SELL", "CE", strike={"mode": "premium"})], entry={"at": "16:00"}),
    ]})  # fmt: skip
    got = {(i.loc, i.msg) for i in check(cfg, INST)}
    assert (("plans", 1, "id"), "plan id P1 is used twice") in got
    assert (("plans", 1, "legs", 0, "strike", "premium"), "enter the premium to look for") in got
    assert any(loc[:3] == ("plans", 1, "entry") for loc, _ in got)
    assert check(parse({"kind": "plans", "plans": [plan("P1", ["MON"], [leg("L1", "SELL", "CE")])]}), INST) == []
    other = {**plan("P1", ["MON"], [leg("L1", "SELL", "CE")]), "underlying": "BANKNIFTY"}
    assert [i.msg for i in check(parse({"kind": "plans", "plans": [other]}), INST)] == [
        "BANKNIFTY is not available for trading right now"
    ]


def test_tip_sources_are_found_in_plans() -> None:
    sid = "11111111-1111-1111-1111-111111111111"
    cfg = parse({"kind": "plans", "plans": [
        plan("P1", ["MON"], [leg("L1", "SELL", "CE")]),
        plan("P2", ["TUE"], [leg("L1", "BUY", "CE")], entry={"mode": "tip", "source_id": sid}),
    ]})  # fmt: skip
    assert uses_tips(cfg) and [str(x) for x in tip_source_ids(cfg)] == [sid]
    assert not uses_tips(parse({"kind": "plans", "plans": [plan("P1", ["MON"], [leg("L1", "SELL", "CE")])]}))


# -- the older strategies, as plans: the very same trades -----------------------------------------------------------
OLD = {
    "intraday strangle": {
        "kind": "rules",
        "entry": {"at": "09:20"},
        "holding": {"mode": "intraday", "exit": "15:15"},
        "legs": [leg("A", "SELL", "CE"), leg("B", "SELL", "PE")],
        "risk": {
            "mtm_stop_loss": 1430,
            "mtm_target": 3000,
            "lock_profit": {"at": 2000, "lock": 1000, "trail_every": 500, "trail_by": 250},
        },
    },
    "buyer with leg stops and re-entry": {
        "kind": "rules",
        "entry": {"at": "09:20", "days": ["TUE", "WED", "THU"]},
        "holding": {"mode": "intraday", "exit": "15:15"},
        "legs": [
            leg(
                "A",
                "BUY",
                "CE",
                stop_loss={"unit": "percent", "value": 20, "basis": "premium"},
                target={"unit": "percent", "value": 30, "basis": "premium"},
                trailing={"unit": "percent", "trigger": 10, "step": 5},
                reentry_on_sl={"mode": "at_cost", "count": 1},
            ),
            leg("B", "BUY", "PE", stop_loss={"unit": "percent", "value": 20, "basis": "premium"}),
        ],
        "risk": {"exit_all_on_leg_sl": True},
    },
    "held two days": {
        "kind": "rules",
        "entry": {"at": "09:20"},
        "holding": {"mode": "days", "days": 2, "exit": "09:30"},
        "legs": [leg("A", "SELL", "CE", expiry="next_week"), leg("B", "SELL", "PE", expiry="next_week")],
    },
    "weekday legs": {
        "kind": "rules",
        "entry": {"at": "09:20"},
        "holding": {"mode": "intraday", "exit": "15:15"},
        "legs": [leg("D", "SELL", "CE")],
        "day_legs": {
            "MON": [leg("M1", "SELL", "PE"), leg("M2", "BUY", "CE", strike={"mode": "atm", "offset": 4})],
            "WED": [leg("W1", "BUY", "CE"), leg("W2", "BUY", "PE")],
        },
    },
    "time based": {
        "kind": "time_based",
        "timing": {"entry": "09:20", "exit": "15:15", "days": ["MON", "TUE", "THU"]},
        "legs": [leg("L1", "SELL", "CE")],
    },
}


@pytest.mark.parametrize("name", list(OLD))
def test_converted_strategies_trade_exactly_as_before(name: str) -> None:
    h = world(START, END, seed=7)
    old = parse(OLD[name])
    new = plans_from_rules(old)  # type: ignore[arg-type]
    assert isinstance(new, PlansConfig) and check(new, INST) == []
    a, b = run(old, h, START, END), run(new, h, START, END)
    assert a.trades and trades(a) == trades(b)
    assert (a.gross_pnl, a.charges) == (b.gross_pnl, b.charges)


def test_weekday_legs_become_one_plan_per_weekday_and_the_default_legs_the_rest() -> None:
    new = plans_from_rules(parse(OLD["weekday legs"]))  # type: ignore[arg-type]
    assert [(p.name, p.days, [leg_.id for leg_ in p.legs]) for p in new.plans] == [
        ("Plan", ["TUE", "THU", "FRI"], ["D"]), ("Mon plan", ["MON"], ["M1", "M2"]), ("Wed plan", ["WED"], ["W1", "W2"])
    ]  # fmt: skip


# -- plans side by side ---------------------------------------------------------------------------------------------
def test_each_plan_trades_only_its_days_and_overlapping_plans_run_independently() -> None:
    h = world(START, END, seed=3)
    cfg = parse({"kind": "plans", "plans": [
        plan("MON", ["MON"], [leg("A", "SELL", "CE"), leg("B", "SELL", "PE")]),
        plan("FRI", ["FRI"], [leg("A", "BUY", "CE")]),
        plan("ALL1", ["MON", "TUE", "WED", "THU", "FRI"], [leg("X", "SELL", "CE", strike={"mode": "atm", "offset": 2})],
             entry={"at": "09:40"}),
        plan("ALL2", ["MON", "TUE", "WED", "THU", "FRI"], [leg("Y", "SELL", "PE")],
             entry={"at": "10:10"}, risk={"mtm_stop_loss": 100}),
    ]})  # fmt: skip
    r = run(cfg, h, START, END)
    by_leg = collections.defaultdict(set)
    for t in r.trades:
        by_leg[t.leg].add(t.entry_time.strftime("%a"))
    assert by_leg["A"] == {"Mon", "Fri"}  # plan MON's A on Mondays, plan FRI's A on Fridays
    assert by_leg["B"] == {"Mon"}
    assert len({t.entry_time.date() for t in r.trades if t.leg == "X"}) == len(weekdays(START, END))  # every day
    assert len({t.entry_time.date() for t in r.trades if t.leg == "Y"}) == len(weekdays(START, END))
    # plan ALL2's tiny loss limit closes only its own leg: ALL1's leg X is still held to 15:15
    assert {t.reason for t in r.trades if t.leg == "X"} == {"exit time 15:15"}
    assert any(t.reason.startswith("strategy loss reached") for t in r.trades if t.leg == "Y")


def test_a_plan_sees_only_its_own_positions() -> None:
    """Two plans on the same contract and day: one sells it, one buys it. Each exits its own position."""
    h = world(START, date(2026, 9, 4), seed=2)
    cfg = parse({"kind": "plans", "plans": [
        plan("S", ["TUE"], [leg("A", "SELL", "CE")], entry={"at": "09:20"}),
        plan("B", ["TUE"], [leg("A", "BUY", "CE")], entry={"at": "09:20"}),
    ]})  # fmt: skip
    r = run(cfg, h, START, date(2026, 9, 4), slip=0)
    assert sorted(t.side for t in r.trades) == ["BUY", "SELL"] and {t.exit_time.hour for t in r.trades} == {15}
    assert round(sum(t.gross for t in r.trades), 2) == 0.0  # the same contract, opposite sides: nets to nothing


def test_the_runner_state_survives_a_restart_with_a_trade_held_overnight() -> None:
    h = world(date(2026, 9, 7), date(2026, 9, 9), seed=5)
    cfg = parse({"kind": "plans", "plans": [
        plan("H", ["MON"], [leg("A", "SELL", "CE", expiry="next_week")], holding={"mode": "next_day", "exit": "09:30"}),
    ]})  # fmt: skip
    # the engine saves `state()` after every step and rebuilds the runner from it: simulate that between days
    import ae_core.trading.runners as runners

    real = runners.PlansRunner

    class Restarting(real):  # type: ignore[valid-type, misc]
        def fill(self, *a: Any, **k: Any) -> Any:
            out = super().fill(*a, **k)
            clone = real(
                self.cfg, self.multiplier, __import__("json").loads(__import__("json").dumps(self.state(), default=str))
            )
            assert [p.id for p in clone.positions] == [p.id for p in self.positions]
            assert clone.state()["owner"] == self.state()["owner"]
            return out

    old = runners.PlansRunner
    runners.PlansRunner = Restarting  # type: ignore[misc]
    try:
        r = run(cfg, h, date(2026, 9, 7), date(2026, 9, 9))
    finally:
        runners.PlansRunner = old  # type: ignore[misc]
    (t,) = r.trades
    assert (
        t.entry_time.strftime("%a") == "Mon" and t.exit_time.strftime("%a") == "Tue" and t.reason == "exit time 09:30"
    )


def test_make_runner_builds_the_plans_runner_and_the_state_is_json() -> None:
    import json

    cfg = parse({"kind": "plans", "plans": [plan("P1", ["MON"], [leg("L1", "SELL", "CE")])]})
    rn = make_runner(cfg, 2)
    assert isinstance(rn, PlansRunner) and rn.multiplier == 2 and rn.prior_days == 0
    json.dumps(rn.state())


# -- stress: many price paths and every kind of exit; nothing may stay open, every day must trade -------------------
STRESS_RISK = {"mtm_stop_loss": 1430, "mtm_target": 3000,
               "lock_profit": {"at": 2000, "lock": 1000, "trail_every": 500, "trail_by": 250}}  # fmt: skip
SL = {"unit": "percent", "value": 25, "basis": "premium"}


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("vol", [0.002, 0.006])
def test_stress_every_plan_trades_every_day_and_nothing_is_left_open(seed: int, vol: float) -> None:
    h = world(date(2026, 9, 1), date(2026, 9, 18), seed=seed, vol=vol)
    days = weekdays(date(2026, 9, 1), date(2026, 9, 18))
    every = ["MON", "TUE", "WED", "THU", "FRI"]
    cfg = parse({"kind": "plans", "plans": [
        plan("A", every, [leg("A1", "BUY", "CE"), leg("A2", "BUY", "PE")], risk=STRESS_RISK),
        plan("B", every, [leg("B1", "SELL", "CE", stop_loss=SL, trailing={"unit": "percent", "trigger": 10, "step": 5},
                              reentry_on_sl={"mode": "at_cost", "count": 1}),
                          leg("B2", "SELL", "PE", stop_loss=SL)],
             entry={"at": "09:30"}, risk={"exit_all_on_leg_sl": True}),
        plan("C", every, [leg("C1", "SELL", "CE", strike={"mode": "atm", "offset": 6}),
                          leg("C2", "BUY", "CE", strike={"mode": "atm", "offset": 10})], entry={"at": "10:00"},
             risk={"combined_stop": {"unit": "percent", "value": 30}}),
    ]})  # fmt: skip
    r = run(cfg, h, days[0], days[-1])
    left = [w for w in r.warnings if "leftover" in w or "still open" in w or "had ended" in w or "end of the day" in w]
    assert not left, left
    per_leg = collections.defaultdict(set)
    for t in r.trades:
        per_leg[t.leg[0]].add(t.entry_time.date())
        assert t.exit_time.date() == t.entry_time.date() and t.exit_time.time() <= time(15, 15)
    assert all(per_leg[p] == set(days) for p in "ABC"), {p: len(v) for p, v in per_leg.items()}


def test_a_plans_runner_trades_one_instrument_and_ignores_orders_it_never_placed() -> None:
    import uuid

    from ae_core.trading.model import Contract, Intent

    sensex = {**plan("P2", ["TUE"], [leg("L1", "SELL", "CE")]), "underlying": "SENSEX"}
    two = parse({"kind": "plans", "plans": [plan("P1", ["MON"], [leg("L1", "SELL", "CE")]), sensex]})
    with pytest.raises(ValueError, match="one instrument"):
        make_runner(two)
    one = make_runner(parse({"kind": "plans", "plans": [plan("P1", ["MON"], [leg("L1", "SELL", "CE")])]}))
    stray = Intent("exit", "BUY", Contract("NIFTY", date(2026, 9, 8), 25000, "CE"), 1, 65, "x", "L1",
                   position_id=str(uuid.uuid4()))  # fmt: skip
    one.reject(stray, "whatever")  # no error: an unknown order is ignored
    assert one.fill(stray, 10.0, ts(date(2026, 9, 7), "10:00"), None) is None  # type: ignore[arg-type]
