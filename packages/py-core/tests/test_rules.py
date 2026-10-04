"""Rule-based strategies (ADR 0022): indicators, conditions, validation, and whole strategies replayed through the
backtester over hand-made history: an overnight premium straddle and an opening range breakout."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any

import pytest
from ae_core.backtest import Candle, Costs, MemoryHistory, simulate
from ae_core.strategy import DEFAULT_INSTRUMENTS, ConditionGroup, LevelOperand, RulesConfig, check, parse
from ae_core.trading import conditions
from ae_core.trading import indicators as ind
from ae_core.trading.model import IST
from ae_core.trading.rules_runner import days_to_expiry
from ae_core.trading.smc import Bar


def at(day: date, hm: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hm), tzinfo=IST)


def minutes(day: date, start: str = "09:15", n: int = 375) -> list[datetime]:
    return [at(day, start) + timedelta(minutes=i) for i in range(n)]


def rules_cfg(**kw: Any) -> RulesConfig:
    base: dict[str, Any] = {"kind": "rules", "signals": [{"id": "S1", "legs": [{"id": "L1", "action": "BUY",
                                                                                "option_type": "CE"}]}]}  # fmt: skip
    base.update(kw)
    c = parse(base)
    assert isinstance(c, RulesConfig)
    return c


# -- indicators ---------------------------------------------------------------------------------------------------
def test_sma_and_ema() -> None:
    assert ind.sma([1, 2, 3, 4, 5], 3) == [None, None, 2, 3, 4]
    e = ind.ema([1, 2, 3, 4, 5], 3)
    assert e[:2] == [None, None] and e[2] == 2 and e[3] == 3 and e[4] == 4  # k = 0.5 on a straight line


def test_rsi_is_100_when_only_rising_and_50_when_balanced() -> None:
    assert ind.rsi([float(i) for i in range(20)], 14)[-1] == 100
    zigzag = [100.0 + (i % 2) for i in range(40)]
    v = ind.rsi(zigzag, 14)[-1]
    assert v is not None and 45 < v < 55


def test_macd_bollinger_atr_adx_shapes() -> None:
    closes = [100 + i * 0.5 for i in range(60)]
    line, sig, hist = ind.macd(closes)
    assert line[24] is None and line[25] is not None and sig[33] is not None and hist[-1] is not None
    up, mid, lo = ind.bollinger([10.0] * 20 + [12.0], 20, 2)
    assert up[19] == mid[19] == lo[19] == 10.0 and up[20] > mid[20] > lo[20]  # type: ignore[operator]
    bars = [Bar(at(date(2026, 10, 5), "09:15"), c, c + 1, c - 1, c) for c in closes]
    assert ind.atr(bars, 14)[13] is not None
    adx, pdi, mdi = ind.adx(bars, 14)
    assert adx[-1] is not None and pdi[-1] > mdi[-1]  # type: ignore[operator]


def test_supertrend_flips_with_the_trend() -> None:
    day = date(2026, 10, 5)
    up = [100 + i for i in range(30)]
    down = [129 - 3 * i for i in range(30)]
    bars = [Bar(at(day, "09:15"), c, c + 1, c - 1, c) for c in up + down]
    st = ind.supertrend(bars, 10, 3)
    assert st[25] is not None and st[25] < bars[25].close  # uptrend: below the price
    assert st[-1] is not None and st[-1] > bars[-1].close  # downtrend: above it


# -- conditions ---------------------------------------------------------------------------------------------------
def cond(raw: dict[str, Any]) -> ConditionGroup:
    return ConditionGroup.model_validate({"conditions": [raw]})


def flat_bars(day: date, closes: list[float], start: str = "09:15") -> list[Bar]:
    return [Bar(t, c, c + 1, c - 1, c) for t, c in zip(minutes(day, start, len(closes)), closes, strict=True)]


def test_a_cross_fires_only_on_the_candle_that_crossed() -> None:
    day = date(2026, 10, 5)
    g = cond(
        {"timeframe": 1, "left": {"kind": "price"}, "op": "crosses_above", "right": {"kind": "number", "value": 105}}
    )
    closes = [100.0, 102.0, 104.0, 106.0, 107.0]
    seen = [conditions.holds(conditions.Context([], flat_bars(day, closes[: i + 1])), g)[0] for i in range(5)]
    assert seen == [False, False, False, True, False]


def test_a_five_minute_cross_counts_only_when_the_candle_completes() -> None:
    day = date(2026, 10, 5)
    g = cond(
        {"timeframe": 5, "left": {"kind": "price"}, "op": "crosses_above", "right": {"kind": "number", "value": 105}}
    )
    closes = [100.0] * 5 + [106.0] * 5 + [106.0] * 3
    results = [conditions.holds(conditions.Context([], flat_bars(day, closes[:n])), g)[0] for n in range(1, 14)]
    assert results.index(True) == 9 and results.count(True) == 1  # the 09:20 candle completes with the 09:24 bar


def test_opening_range_and_previous_day_levels() -> None:
    prev, day = date(2026, 10, 2), date(2026, 10, 5)
    prior = flat_bars(prev, [200.0] * 375)
    today = flat_bars(day, [100.0, 110.0, 90.0] + [100.0] * 20)
    ctx = conditions.Context(prior, today)
    lv = LevelOperand
    assert ctx.level(lv(name="opening_range_high", minutes=15)) == 111  # highs are close + 1
    assert ctx.level(lv(name="opening_range_low", minutes=15)) == 89
    assert ctx.level(lv(name="prev_close")) == 200 and ctx.level(lv(name="prev_high")) == 201
    assert ctx.level(lv(name="day_open")) == 100
    early = conditions.Context(prior, today[:10])
    assert early.level(lv(name="opening_range_high", minutes=15)) is None  # the range is not complete yet


def test_indicator_condition_reads_through_the_prior_sessions() -> None:
    prev, day = date(2026, 10, 2), date(2026, 10, 5)
    prior = flat_bars(prev, [100.0 + i * 0.1 for i in range(375)])
    today = flat_bars(day, [140.0] * 3)
    g = cond({"timeframe": 1, "left": {"kind": "price"}, "op": "above",
              "right": {"kind": "indicator", "name": "ema", "period": 50}})  # fmt: skip
    ok, why = conditions.holds(conditions.Context(prior, today), g)
    assert ok and "EMA50" in why[0]


def test_days_to_expiry() -> None:
    exp = [date(2026, 10, 1), date(2026, 10, 8)]
    assert days_to_expiry(date(2026, 10, 1), exp) == 0
    assert days_to_expiry(date(2026, 9, 28), exp) == 3  # Mon -> Thu
    assert days_to_expiry(date(2026, 10, 2), exp) == 4  # Fri -> next Thu, weekend skipped


# -- validation ---------------------------------------------------------------------------------------------------
def issues(c: RulesConfig) -> dict[tuple[Any, ...], str]:
    return {i.loc: i.msg for i in check(c, DEFAULT_INSTRUMENTS)}


def test_a_valid_config_has_no_issues_and_round_trips() -> None:
    c = orb_config()
    assert issues(c) == {}
    assert parse(c.model_dump(mode="json")) == c


def test_validation_catches_cross_field_mistakes() -> None:
    num = {"kind": "number", "value": 1}
    c = rules_cfg(
        timing={"start": "14:00", "last_entry": "10:00"},
        holding={"mode": "intraday", "exit": "09:30"},
        signals=[
            {"id": "A", "when": {"conditions": [{"left": num, "op": "above", "right": num}]},
             "legs": [{"id": "L1", "action": "SELL", "option_type": "CE", "strike": {"mode": "premium"}}]},
            {"id": "A", "legs": [{"id": "L1", "action": "BUY", "option_type": "PE"}],
             "exit_when": {"conditions": []}},
        ],
        risk={"profit_lock": {"reach": 1000, "lock": 2000}, "sl_to_cost_on_leg_sl": True},
    )  # fmt: skip
    got = issues(c)
    assert ("timing", "last_entry") in got and ("holding", "exit") in got
    assert ("signals", 1, "id") in got and ("signals", 1, "legs", 0, "id") in got
    assert ("signals", 0, "when", "conditions", 0, "right") in got
    assert ("signals", 0, "legs", 0, "strike", "premium") in got
    assert ("signals", 1, "exit_when") in got
    assert ("risk", "profit_lock", "lock") in got and ("risk", "sl_to_cost_on_leg_sl") in got


def test_indicator_lines_and_opening_range_length_are_checked() -> None:
    c = rules_cfg(
        timing={"start": "09:20", "last_entry": "09:40"},
        signals=[{"id": "S1", "legs": [{"id": "L1", "action": "BUY", "option_type": "CE"}], "when": {"conditions": [
            {"left": {"kind": "indicator", "name": "rsi", "line": "upper"}, "op": "above",
             "right": {"kind": "level", "name": "opening_range_high", "minutes": 60}},
        ]}}],
    )  # fmt: skip
    got = issues(c)
    assert ("signals", 0, "when", "conditions", 0, "left", "line") in got
    assert ("signals", 0, "when", "conditions", 0, "right", "minutes") in got


def test_unknown_fields_are_refused() -> None:
    with pytest.raises(ValueError):
        rules_cfg(timing={"start": "09:20", "bogus": 1})


# -- whole strategies through the backtester ----------------------------------------------------------------------
def run(c: RulesConfig, h: MemoryHistory, start: date, end: date, lot: int, step: int) -> Any:
    return simulate(c, h, start, end, lot_size=lot, strike_step=step, slippage_pct=0, costs=Costs(0, 0, 0, 0, 0, 0))


def add_chain(h: MemoryHistory, u: str, expiry: date, spot: int, step: int, day: date,
              price: Callable[[str, int, datetime], float]) -> None:  # fmt: skip
    """Every strike from 6 ITM to 16 OTM, both rights, a bar every minute: price(right, strikes OTM, minute)."""
    for right in ("CE", "PE"):
        for k in range(-6, 17):
            strike = spot + k * step if right == "CE" else spot - k * step
            key = f"{u}:{expiry:%Y-%m-%d}:{strike}:{right}"
            h.add_option(key, [Candle(t, p, p, p, p) for t in minutes(day) if (p := price(right, k, t))])


MON, TUE, THU = date(2026, 9, 28), date(2026, 9, 29), date(2026, 10, 1)


def straddle_config(**risk: Any) -> RulesConfig:
    """Example 1: sell a SENSEX straddle of the strikes priced about ₹60 at 15:15, exit the next morning."""
    leg = {"action": "SELL", "lots": 1, "strike": {"mode": "premium", "premium": 60},
           "stop_loss": {"unit": "percent", "value": 50}}  # fmt: skip
    return rules_cfg(
        underlying="SENSEX",
        timing={"start": "15:15", "last_entry": "15:20", "days": ["MON"]},
        signals=[{"id": "STRADDLE", "legs": [{**leg, "id": "CE", "option_type": "CE"},
                                             {**leg, "id": "PE", "option_type": "PE"}]}],
        holding={"mode": "next_day", "exit": "09:30"},
        risk=risk,
    )  # fmt: skip


def straddle_history(tue_ce: float = 40.0) -> MemoryHistory:
    h = MemoryHistory()
    for day in (MON, TUE):
        h.add_spot("SENSEX", [Candle(t, 82000, 82001, 81999, 82000) for t in minutes(day)])

    def price(day: date) -> Callable[[str, int, datetime], float]:
        def p(right: str, k: int, t: datetime) -> float:
            base = max(5.0, 200.0 - 20 * k)  # 7 strikes OTM is priced 60
            if day == TUE and k == 7:
                return tue_ce if right == "CE" else 40.0
            return base

        return p

    for day in (MON, TUE):
        add_chain(h, "SENSEX", THU, 82000, 100, day, price(day))
    return h


def test_example_1_overnight_premium_straddle() -> None:
    c = straddle_config()
    assert issues(c) == {}
    r = run(c, straddle_history(), MON, TUE, lot=20, step=100)
    got = sorted((t.leg, t.contract, t.side, t.entry_time, t.entry_price, t.exit_time, t.exit_price, t.reason)
                 for t in r.trades)  # fmt: skip
    assert got == [
        ("CE", "SENSEX 01 Oct 82700 CE", "SELL", at(MON, "15:15"), 60.0, at(TUE, "09:30"), 40.0, "next-day exit 09:30"),
        ("PE", "SENSEX 01 Oct 81300 PE", "SELL", at(MON, "15:15"), 60.0, at(TUE, "09:30"), 40.0, "next-day exit 09:30"),
    ]
    assert r.gross_pnl == 2 * (60 - 40) * 20
    assert r.signals and r.signals[0]["signal"] == "STRADDLE"


def test_example_1_stop_loss_overnight_and_trade_profit_cap() -> None:
    # the call gaps to 100 the next morning: its 50% stop (90) fills at the open, the put stays until 09:30
    r = run(straddle_config(), straddle_history(tue_ce=100.0), MON, TUE, lot=20, step=100)
    ce = next(t for t in r.trades if t.leg == "CE")
    assert (ce.exit_time, ce.exit_price, ce.reason) == (at(TUE, "09:15"), 100.0, "stop-loss")
    # with a ₹600 profit cap the whole trade closes as soon as both legs are 20 down: Tuesday's first minute
    r = run(straddle_config(mtm_target=600), straddle_history(), MON, TUE, lot=20, step=100)
    assert {t.reason for t in r.trades} == {"strategy profit reached ₹600"}
    assert {t.exit_time for t in r.trades} == {at(TUE, "09:15")}


D = date(2026, 10, 5)  # Monday
NIFTY_EXP = date(2026, 10, 6)


def orb_config(**kw: Any) -> RulesConfig:
    """Example 2: the first 15 minutes' high/low; a 5-minute close above the high buys the ATM call, below the low
    buys the ATM put; 30% stop, 50% target, one trade a day."""

    def leg(right: str) -> dict[str, Any]:
        return {"id": right, "action": "BUY", "option_type": right, "stop_loss": {"value": 30},
                "target": {"value": 50}}  # fmt: skip

    def breakout(op: str, level: str) -> dict[str, Any]:
        return {"conditions": [{"timeframe": 5, "left": {"kind": "price", "field": "close"}, "op": op,
                                "right": {"kind": "level", "name": level, "minutes": 15}}]}  # fmt: skip

    kw.setdefault("timing", {"start": "09:30", "last_entry": "14:30", "max_entries_per_day": 1})
    return rules_cfg(
        signals=[
            {"id": "UP", "when": breakout("crosses_above", "opening_range_high"), "legs": [leg("CE")]},
            {"id": "DOWN", "when": breakout("crosses_below", "opening_range_low"), "legs": [leg("PE")]},
        ],
        holding={"mode": "intraday", "exit": "15:15"},
        **kw,
    )


def orb_history(path: Callable[[int], float]) -> MemoryHistory:
    """Index closes by minute index (0 = 09:15); options priced off the index move from 25000."""
    h = MemoryHistory()
    spots = [path(i) for i in range(375)]
    h.add_spot("NIFTY", [Candle(t, s, s + 2, s - 2, s) for t, s in zip(minutes(D), spots, strict=True)])
    idx = {t: s for t, s in zip(minutes(D), spots, strict=True)}

    def price(right: str, k: int, t: datetime) -> float:
        strike = 25000 + k * 50 if right == "CE" else 25000 - k * 50
        intrinsic = max(0.0, idx[t] - strike) if right == "CE" else max(0.0, strike - idx[t])
        return round(80 + intrinsic - 5 * k, 2)

    add_chain(h, "NIFTY", NIFTY_EXP, 25000, 50, D, price)
    return h


def breakout_path(i: int) -> float:
    if i < 15:
        return 25000 + (40 if i % 2 else -40)  # the range: 24958 .. 25042 (highs/lows are +/- 2)
    if i < 45:
        return 25000
    return 25000 + min(200, (i - 44) * 4)  # from 10:00 the index climbs 4 points a minute, to +200


def test_example_2_opening_range_breakout_buys_the_call_once() -> None:
    r = run(orb_config(), orb_history(breakout_path), D, D, lot=65, step=50)
    (t,) = r.trades
    # the 5-minute closes: 10:05-10:09 at 25040 (not above the 25042 high), 10:10-10:14 at 25060: the cross. That
    # candle is complete with the 10:14 bar, so the entry is at 10:15's open, the ATM call then (index 25064)
    assert (t.leg, t.contract, t.side, t.entry_time) == ("CE", "NIFTY 06 Oct 25050 CE", "BUY", at(D, "10:15"))
    assert t.reason == "target" and t.exit_price >= t.entry_price * 1.5  # at the level, or the open past it
    assert r.signals[0]["signal"] == "UP" and "range high" in r.signals[0]["why"][0]


def test_example_2_downside_breakout_buys_the_put_and_quiet_days_do_not_trade() -> None:
    r = run(orb_config(), orb_history(lambda i: 2 * 25000 - breakout_path(i)), D, D, lot=65, step=50)
    (t,) = r.trades
    assert (t.leg, t.side, t.entry_time) == ("PE", "BUY", at(D, "10:15"))
    quiet = run(orb_config(), orb_history(lambda i: 25000.0), D, D, lot=65, step=50)
    assert quiet.trades == []


def test_exit_signal_and_entry_limit() -> None:
    # exit when the 1-minute close falls back below the range high; up to 3 entries a day
    c = orb_config(timing={"start": "09:30", "last_entry": "14:30", "max_entries_per_day": 3})
    back_inside = {"timeframe": 1, "left": {"kind": "price"}, "op": "below",
                   "right": {"kind": "level", "name": "opening_range_high", "minutes": 15}}  # fmt: skip
    up = c.signals[0].model_copy(update={"exit_when": cond(back_inside)})
    c = c.model_copy(update={"signals": [up, c.signals[1]]})

    def whipsaw(i: int) -> float:
        if i < 15:
            return 25000 + (40 if i % 2 else -40)
        cycle = (i - 15) % 20  # 10 minutes above the range, 10 below, over and over
        return 25100 if cycle < 10 else 25000

    r = run(c, orb_history(whipsaw), D, D, lot=65, step=50)
    assert len(r.trades) == 3  # the limit, though the index breaks out many more times
    assert all(t.reason.startswith("exit signal") for t in r.trades)


def test_profit_lock_gives_back_no_more_than_the_locked_amount() -> None:
    c = orb_config(risk={"profit_lock": {"reach": 3000, "lock": 1000}})
    up = c.signals[0].model_copy(update={"legs": [c.signals[0].legs[0].model_copy(update={"target": None})]})
    c = c.model_copy(update={"signals": [up, c.signals[1]]})

    def spike(i: int) -> float:
        if i < 45:
            return breakout_path(i)
        return 25000 + min(150, (i - 44) * 10) if i < 70 else max(24900, 25150 - (i - 70) * 3)  # up, then down

    r = run(c, orb_history(spike), D, D, lot=65, step=50)
    (t,) = r.trades
    assert t.reason == "strategy profit lock ₹1000" and 0 < t.gross < 3000  # kept about the locked ₹1000
