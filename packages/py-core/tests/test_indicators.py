"""Indicators (ADR 0024): the formulas, indicator operands in conditions (warmed up on earlier sessions), their
validation, the sessions a strategy needs, and an EMA crossover replayed through the backtester."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from ae_core.backtest import Candle, Costs, MemoryHistory, simulate
from ae_core.strategy import (
    DEFAULT_INSTRUMENTS,
    PRESETS,
    Condition,
    Operand,
    RulesConfig,
    check,
    parse,
    prior_days_needed,
)
from ae_core.trading import conditions
from ae_core.trading import indicators as ind
from ae_core.trading.model import IST

DAY = date(2026, 10, 6)  # a Tuesday: NIFTY's weekly expiry
PREV = date(2026, 10, 5)


@dataclass
class B:
    ts: datetime
    open: float
    high: float
    low: float
    close: float


def bars(day: date, closes: list[float]) -> list[B]:
    t0 = datetime.combine(day, time(9, 15), tzinfo=IST)
    return [B(t0 + timedelta(minutes=i), c, c + 1, c - 1, c) for i, c in enumerate(closes)]


def at(hm: str, day: date = DAY) -> datetime:
    return datetime.combine(day, time.fromisoformat(hm), tzinfo=IST)


# -- formulas -----------------------------------------------------------------------------------------------------
def test_sma_and_ema() -> None:
    assert ind.sma([1, 2, 3, 4, 5], 3) == [None, None, 2, 3, 4]
    assert ind.ema([1, 2, 3, 4, 5], 3) == [None, None, 2, 3, 4]  # seeded with the mean; a straight line stays on it
    assert ind.ema([1, 2], 3) == [None, None]


def test_rsi() -> None:
    assert ind.rsi([float(i) for i in range(20)], 14)[-1] == 100  # only gains
    v = ind.rsi([100.0 + (i % 2) for i in range(40)], 14)[-1]
    assert v is not None and 45 < v < 55  # balanced


def test_macd_bollinger_atr_adx() -> None:
    closes = [100 + i * 0.5 for i in range(60)]
    line, sig, hist = ind.macd(closes)
    assert line[24] is None and line[25] is not None and sig[33] is not None and hist[-1] is not None
    assert line[-1] > 0  # type: ignore[operator]  # rising: the fast average is above the slow one
    up, mid, lo = ind.bollinger([10.0] * 20 + [12.0], 20, 2)
    assert up[19] == mid[19] == lo[19] == 10.0 and up[20] > mid[20] > lo[20]  # type: ignore[operator]
    cs = bars(DAY, closes)
    assert ind.atr(cs, 14)[12] is None and ind.atr(cs, 14)[13] == 2.0  # every candle spans 2 points
    adx, pdi, mdi = ind.adx(cs, 14)
    assert adx[-1] is not None and pdi[-1] > mdi[-1]  # type: ignore[operator]


def test_supertrend_follows_the_trend() -> None:
    cs = bars(DAY, [100.0 + i for i in range(30)] + [129.0 - 3 * i for i in range(30)])
    st = ind.supertrend(cs, 10, 3)
    assert st[25] is not None and st[25] < cs[25].close  # uptrend: under the price
    assert st[-1] is not None and st[-1] > cs[-1].close  # downtrend: over it


# -- in conditions ------------------------------------------------------------------------------------------------
def ema(n: int) -> Operand:
    return Operand(kind="indicator", indicator="ema", period=n)


def test_an_indicator_is_warmed_up_by_the_earlier_session() -> None:
    prior = bars(PREV, [100.0 + i * 0.1 for i in range(375)])
    today = bars(DAY, [140.0] * 3)
    above = Condition(op="above", left=Operand(kind="price"), right=ema(50), candle=1)
    ctx = conditions.Context(at("09:18"), today, prior, frozenset({1}))
    assert conditions.holds(above, ctx)
    assert not conditions.holds(above, conditions.Context(at("09:18"), today, [], frozenset({1})))  # cold: no value


def test_ema_crossover_fires_once_on_the_crossing_candle() -> None:
    cross = Condition(op="crosses_above", left=ema(3), right=ema(8), candle=1)
    closes = [100.0] * 20 + [99.0] * 5 + [105.0] * 5
    hits = []
    for n in range(10, len(closes) + 1):
        now = at("09:15") + timedelta(minutes=n)
        hits.append(conditions.holds(cross, conditions.Context(now, bars(DAY, closes[:n]), [], frozenset({1}))))
    assert hits.count(True) == 1


def test_rsi_and_macd_lines_as_operands() -> None:
    rising = bars(DAY, [100.0 + 0.05 * i * i for i in range(60)])  # accelerating: MACD climbs over its signal
    ctx = conditions.Context(at("10:15"), rising, [], frozenset({1}))
    rsi = Operand(kind="indicator", indicator="rsi", period=14)
    assert conditions.holds(Condition(op="above", left=rsi, right=Operand(kind="number", value=70), candle=1), ctx)
    macd = Operand(kind="indicator", indicator="macd", period=26, fast=12)
    signal = macd.model_copy(update={"line": "signal"})
    assert conditions.holds(Condition(op="above", left=macd, right=signal, candle=1), ctx)


# -- validation and warm-up ---------------------------------------------------------------------------------------
def cfg(*conds: dict[str, Any], **entry: Any) -> RulesConfig:
    c = parse({
        "kind": "rules",
        "entry": {"mode": "conditions", "at": "09:30", "signals": [{"conditions": list(conds)}], **entry},
        "legs": [{"id": "L1", "action": "BUY", "option_type": "CE"}],
    })  # fmt: skip
    assert isinstance(c, RulesConfig)
    return c


def ind_op(name: str, **kw: Any) -> dict[str, Any]:
    return {"kind": "indicator", "indicator": name, **kw}


def test_indicator_validation() -> None:
    ok = cfg({"candle": 5, "left": ind_op("ema", period=9), "op": "crosses_above", "right": ind_op("ema", period=21)})
    assert check(ok, DEFAULT_INSTRUMENTS) == []
    bad = cfg(
        {"left": {"kind": "indicator"}, "op": "above", "right": {"kind": "number", "value": 1}},
        {"left": ind_op("rsi", line="upper"), "op": "above", "right": {"kind": "number", "value": 70}},
        {"left": ind_op("ema", multiplier=2), "op": "above", "right": {"kind": "price"}},
        {"left": ind_op("macd", fast=30, period=26), "op": "above", "right": {"kind": "number", "value": 0}},
    )
    got = {i.loc[-1]: i.msg for i in check(bad, DEFAULT_INSTRUMENTS)}
    assert got["indicator"] == "pick an indicator"
    assert got["line"] == "rsi has: value"
    assert got["multiplier"] == "only bollinger and supertrend use a multiplier"
    assert got["fast"] == "must be shorter than the slow period (26)"


def test_sessions_needed_for_warm_up() -> None:
    def need(candle: int, period: int) -> int:
        return prior_days_needed(cfg({"candle": candle, "left": ind_op("ema", period=period), "op": "above",
                                      "right": {"kind": "price"}}))  # fmt: skip

    assert need(1, 20) == 1 and need(5, 21) == 1 and need(15, 50) == 7 and need(60, 200) == 10  # capped
    plain = cfg({"op": "above", "right": {"kind": "level", "level": "opening_high"}})
    assert prior_days_needed(plain) == 0


def test_indicator_presets_are_valid() -> None:
    ids = {p.id for p in PRESETS if isinstance(p.config, RulesConfig) and prior_days_needed(p.config)}
    assert {"ema_crossover", "supertrend_trend", "rsi_reversal"} <= ids
    for p in PRESETS:
        assert check(p.config, DEFAULT_INSTRUMENTS) == [], p.id


# -- through the backtester ---------------------------------------------------------------------------------------
def test_an_ema_crossover_trades_through_the_backtester_warmed_up_on_the_day_before() -> None:
    key = f"NIFTY:{DAY:%Y-%m-%d}:25000:CE"
    h = MemoryHistory()
    t_prev, t0 = at("09:15", PREV), at("09:15")
    h.add_spot("NIFTY", [Candle(t_prev + timedelta(minutes=i), 25000, 25001, 24999, 25000) for i in range(375)])

    # flat, a dip from 09:20, then up from 09:40: on 5-minute candles EMA 3 crosses above EMA 8 once
    def px(i: int) -> float:
        return 25000.0 if i < 5 else 24990.0 if i < 25 else 25000.0 + (i - 24) * 3

    h.add_spot("NIFTY", [Candle(t0 + timedelta(minutes=i), px(i), px(i) + 1, px(i) - 1, px(i)) for i in range(375)])
    h.add_option(key, [Candle(t0 + timedelta(minutes=i), 100.0, 100.0, 100.0, 100.0) for i in range(375)])
    config = parse({
        "kind": "rules",
        "entry": {"mode": "conditions", "at": "09:20", "until": "14:30", "signals": [{"conditions": [
            {"candle": 5, "left": ind_op("ema", period=3), "op": "crosses_above", "right": ind_op("ema", period=8)}]}]},
        "legs": [{"id": "L1", "action": "BUY", "option_type": "CE"}],
    })  # fmt: skip
    r = simulate(config, h, DAY, DAY, lot_size=65, strike_step=50, slippage_pct=0, costs=Costs(0, 0, 0, 0, 0, 0))
    (t,) = r.trades
    assert t.side == "BUY" and t.contract == "NIFTY 06 Oct 25000 CE" and t.reason == "exit time 15:15"
    assert at("09:40") < t.entry_time <= at("10:00")  # after the turn, on a finished 5-minute candle
