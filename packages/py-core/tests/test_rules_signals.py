"""Rules strategies, phase 2 (ADR 0022): conditions on the index and the day's levels, signal entries in both
directions (mirrored legs), exit conditions, entries per day; whole strategies replayed through the backtester."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any

import pytest
from ae_core.backtest import Candle, Costs, MemoryHistory, simulate
from ae_core.strategy import DEFAULT_INSTRUMENTS, ConditionGroup, LevelOperand, RulesConfig, check, parse
from ae_core.trading import conditions
from ae_core.trading.model import IST
from ae_core.trading.smc import Bar


def at(day: date, hm: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hm), tzinfo=IST)


def minutes(day: date, start: str = "09:15", n: int = 375) -> list[datetime]:
    return [at(day, start) + timedelta(minutes=i) for i in range(n)]


def flat_bars(day: date, closes: list[float]) -> list[Bar]:
    return [Bar(t, c, c + 1, c - 1, c) for t, c in zip(minutes(day, n=len(closes)), closes, strict=True)]


def group(*conds: dict[str, Any], match: str = "all") -> ConditionGroup:
    return ConditionGroup.model_validate({"match": match, "conditions": list(conds)})


def num(v: float) -> dict[str, Any]:
    return {"kind": "number", "value": v}


def level(name: str, **kw: Any) -> dict[str, Any]:
    return {"kind": "level", "name": name, **kw}


PRICE = {"kind": "price", "field": "close"}
D = date(2026, 10, 5)  # a Monday
PREV = date(2026, 10, 2)  # the Friday before


# -- conditions ---------------------------------------------------------------------------------------------------
def test_a_cross_fires_only_on_the_candle_that_crossed() -> None:
    g = group({"timeframe": 1, "left": PRICE, "op": "crosses_above", "right": num(105)})
    closes = [100.0, 102.0, 104.0, 106.0, 107.0]
    seen = [conditions.holds(conditions.Context([], flat_bars(D, closes[: i + 1])), g)[0] for i in range(5)]
    assert seen == [False, False, False, True, False]


def test_a_five_minute_cross_counts_only_when_its_candle_completes() -> None:
    g = group({"timeframe": 5, "left": PRICE, "op": "crosses_above", "right": num(105)})
    closes = [100.0] * 5 + [106.0] * 5 + [106.0] * 3
    seen = [conditions.holds(conditions.Context([], flat_bars(D, closes[:n])), g)[0] for n in range(1, 14)]
    assert seen.index(True) == 9 and seen.count(True) == 1  # the 09:20 candle completes with the 09:24 bar


def test_above_holds_while_true_and_any_needs_one() -> None:
    g = group({"timeframe": 1, "left": PRICE, "op": "above", "right": num(105)},
              {"timeframe": 1, "left": PRICE, "op": "below", "right": num(90)}, match="any")  # fmt: skip
    ctx = conditions.Context([], flat_bars(D, [100.0, 106.0, 107.0]))
    ok, why = conditions.holds(ctx, g)
    assert ok and why == ["1m close above 105 (107.00 vs 105.00)"]
    assert not conditions.holds(conditions.Context([], flat_bars(D, [100.0])), g)[0]


def test_levels() -> None:
    prior = flat_bars(PREV, [200.0] * 375)
    today = flat_bars(D, [100.0, 110.0, 90.0] + [100.0] * 20)
    ctx = conditions.Context(prior, today)

    def lv(name: str, **kw: Any) -> float | None:
        return ctx.level(LevelOperand(name=name, **kw))  # type: ignore[arg-type]

    assert lv("opening_range_high", minutes=15) == 111 and lv("opening_range_low", minutes=15) == 89  # highs: +1
    assert lv("opening_range_high", minutes=15, offset=10) == 121
    assert lv("prev_close") == 200 and lv("prev_high") == 201 and lv("prev_low") == 199
    assert lv("day_open") == 100 and lv("day_high") == 111 and lv("day_low") == 89
    assert lv("price_at", at="09:16") == 110 and lv("price_at", at="09:16", offset=-50) == 60
    assert lv("price_at", at="11:00") is None  # not reached yet
    early = conditions.Context(prior, today[:10])
    assert early.level(LevelOperand(name="opening_range_high", minutes=15)) is None  # the range is not over
    assert conditions.Context([], today).level(LevelOperand(name="prev_close")) is None  # no earlier session


# -- validation ---------------------------------------------------------------------------------------------------
def cfg(entry: dict[str, Any] | None = None, holding: dict[str, Any] | None = None, **kw: Any) -> RulesConfig:
    raw: dict[str, Any] = {"kind": "rules", "legs": [{"id": "L1", "action": "BUY", "option_type": "CE"}], **kw}
    if entry is not None:
        raw["entry"] = entry
    if holding is not None:
        raw["holding"] = holding
    c = parse(raw)
    assert isinstance(c, RulesConfig)
    return c


def issues(c: RulesConfig) -> dict[tuple[Any, ...], str]:
    return {i.loc: i.msg for i in check(c, DEFAULT_INSTRUMENTS)}


BREAKOUT = {"conditions": [{"timeframe": 5, "left": PRICE, "op": "crosses_above",
                            "right": level("opening_range_high", minutes=15)}]}  # fmt: skip


def test_signal_entries_validate() -> None:
    assert issues(cfg({"mode": "signal", "at": "09:30", "when": BREAKOUT})) == {}
    got = issues(cfg({"mode": "signal", "at": "09:30"}))
    assert got == {("entry", "when"): "add the conditions that start a trade"}


def test_conditions_belong_to_the_signal_mode() -> None:
    got = issues(cfg({"mode": "time", "when": BREAKOUT, "max_entries": 2}, {"exit_when": BREAKOUT}))
    assert ("entry", "when") in got and ("entry", "max_entries") in got and ("holding", "exit_when") in got


def test_condition_mistakes_are_caught() -> None:
    bad = {"conditions": [
        {"left": num(1), "op": "above", "right": num(2)},
        {"left": PRICE, "op": "above", "right": level("price_at")},
        {"left": PRICE, "op": "above", "right": level("opening_range_high", minutes=120)},
        {"left": PRICE, "op": "above", "right": level("day_high", at="10:00")},
    ]}  # fmt: skip
    got = issues(cfg({"mode": "signal", "at": "09:30", "until": "10:30", "when": bad},
                     {"exit_when_mirrored": BREAKOUT}))  # fmt: skip
    base = ("entry", "when", "conditions")
    assert (*base, 0, "right") in got  # two numbers
    assert (*base, 1, "right", "at") in got  # price at which time?
    assert got[(*base, 2, "right")] == "only known from 11:15, after the last entry (10:30)"
    assert (*base, 3, "right", "at") in got
    assert ("holding", "exit_when_mirrored") in got  # no mirrored entry to exit


def test_a_condition_group_needs_a_condition() -> None:
    with pytest.raises(ValueError):
        cfg({"mode": "signal", "when": {"conditions": []}})


# -- whole strategies through the backtester ----------------------------------------------------------------------
NIFTY_EXP = date(2026, 10, 6)


def history(path: Callable[[int], float], day: date = D, prev: Callable[[int], float] | None = None) -> MemoryHistory:
    """Index closes by minute (0 = 09:15); every strike near 25000 priced off the index; optional earlier session."""
    h = MemoryHistory()
    if prev is not None:
        h.add_spot("NIFTY", [Candle(t, prev(i), prev(i) + 2, prev(i) - 2, prev(i))
                             for i, t in enumerate(minutes(PREV))])  # fmt: skip
    spots = [path(i) for i in range(375)]
    h.add_spot("NIFTY", [Candle(t, s, s + 2, s - 2, s) for t, s in zip(minutes(day), spots, strict=True)])
    idx = dict(zip(minutes(day), spots, strict=True))
    for right in ("CE", "PE"):
        for k in range(-8, 17):
            strike = 25000 + k * 50
            key = f"NIFTY:{NIFTY_EXP:%Y-%m-%d}:{strike}:{right}"
            bars = []
            for t in minutes(day):
                intrinsic = max(0.0, idx[t] - strike) if right == "CE" else max(0.0, strike - idx[t])
                p = round(80 + intrinsic - 2 * abs(idx[t] - strike) / 50, 2)
                bars.append(Candle(t, p, p, p, p))
            h.add_option(key, bars)
    return h


def run(c: RulesConfig, h: MemoryHistory, day: date = D) -> Any:
    return simulate(c, h, day, day, lot_size=65, strike_step=50, slippage_pct=0, costs=Costs(0, 0, 0, 0, 0, 0))


def orb(**entry: Any) -> RulesConfig:
    """Example 2: the first 15 minutes' high/low; a 5-minute close above the high buys the ATM call, below the low
    the ATM put (the same leg, mirrored); 30% stop, 50% target."""
    down = {"conditions": [{"timeframe": 5, "left": PRICE, "op": "crosses_below",
                            "right": level("opening_range_low", minutes=15)}]}  # fmt: skip
    return cfg(
        {"mode": "signal", "at": "09:30", "until": "14:30", "when": BREAKOUT, "when_mirrored": down, **entry},
        {"mode": "intraday", "exit": "15:15"},
        legs=[{"id": "L1", "action": "BUY", "option_type": "CE", "stop_loss": {"value": 30}, "target": {"value": 50}}],
    )


def breakout_path(i: int) -> float:
    if i < 15:
        return 25000 + (40 if i % 2 else -40)  # the range: 24958 .. 25042 (bar highs/lows are +/- 2)
    if i < 45:
        return 25000
    return 25000 + min(200, (i - 44) * 4)  # from 10:00 the index climbs 4 points a minute


def test_example_2_opening_range_breakout_buys_the_call() -> None:
    c = orb()
    assert issues(c) == {}
    r = run(c, history(breakout_path))
    (t,) = r.trades
    # 5-minute closes: 10:05-10:09 at 25040 (not above 25042), 10:10-10:14 at 25060: the cross, complete with the
    # 10:14 bar, so the entry is at 10:15's open, in the ATM call then (index 25064)
    assert (t.contract, t.side, t.entry_time) == ("NIFTY 06 Oct 25050 CE", "BUY", at(D, "10:15"))
    assert t.reason == "target" and t.exit_price >= t.entry_price * 1.5
    (sig,) = r.signals
    assert sig["direction"] == "as_written" and "range high" in sig["why"][0]


def test_example_2_downside_breakout_buys_the_mirrored_put_and_a_quiet_day_does_not_trade() -> None:
    r = run(orb(), history(lambda i: 2 * 25000 - breakout_path(i)))
    (t,) = r.trades
    assert (t.contract, t.side, t.entry_time) == ("NIFTY 06 Oct 24950 PE", "BUY", at(D, "10:15"))
    assert r.signals[0]["direction"] == "mirrored"
    assert run(orb(), history(lambda i: 25000.0)).trades == []


def whipsaw(i: int) -> float:
    if i < 15:
        return 25000 + (40 if i % 2 else -40)
    return 25100 if (i - 15) % 20 < 10 else 25000  # 10 minutes above the range, 10 inside, again and again


def test_exit_conditions_and_entries_per_day() -> None:
    back_inside = {"conditions": [{"timeframe": 1, "left": PRICE, "op": "below",
                                   "right": level("opening_range_high", minutes=15)}]}  # fmt: skip
    c = orb(max_entries=3)
    c = c.model_copy(update={"holding": c.holding.model_copy(update={"exit_when": ConditionGroup.model_validate(
        back_inside)})})  # fmt: skip
    c = c.model_copy(update={"legs": [c.legs[0].model_copy(update={"target": None, "stop_loss": None})]})
    assert issues(c) == {}
    r = run(c, history(whipsaw))
    assert len(r.trades) == 3  # the daily limit, though the index breaks out many more times
    assert all(t.reason.startswith("exit signal") for t in r.trades)
    assert all(t.contract.endswith("CE") for t in r.trades)


def test_previous_day_high_breakout_reads_the_earlier_session() -> None:
    c = cfg(
        {"mode": "signal", "at": "09:20", "when": {"conditions": [
            {"timeframe": 1, "left": PRICE, "op": "crosses_above", "right": level("prev_high")}]}},
        legs=[{"id": "L1", "action": "BUY", "option_type": "CE"}],
    )  # fmt: skip
    h = history(lambda i: 25000 + (0 if i < 30 else 60), prev=lambda i: 25030.0)  # yesterday's high: 25032
    r = simulate(c, h, D, D, lot_size=65, strike_step=50, slippage_pct=0, costs=Costs(0, 0, 0, 0, 0, 0))
    (t,) = r.trades
    assert t.entry_time == at(D, "09:46") and t.reason == "exit time 15:15"  # 09:45 bar crossed: enter at 09:46


def test_momentum_from_the_price_at_a_time() -> None:
    # sell the ATM put once the index is 50 points above its 09:20 close
    c = cfg(
        {"mode": "signal", "at": "09:21", "when": {"conditions": [
            {"timeframe": 1, "left": PRICE, "op": "above", "right": level("price_at", at="09:20", offset=50)}]}},
        legs=[{"id": "L1", "action": "SELL", "option_type": "PE"}],
    )  # fmt: skip
    r = run(c, history(lambda i: 25000 + max(0, i - 10) * 5))  # +5 a minute from 09:25
    (t,) = r.trades
    assert t.side == "SELL" and t.contract.endswith("PE")
    assert t.entry_time == at(D, "09:37")  # 09:20 closed at 25000; the 09:36 bar is the first above 25050 (25055)
