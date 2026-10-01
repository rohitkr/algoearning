"""SMC detectors on hand-made candles (one test per concept), the option chooser, the config, and the scalper end to
end over a synthetic day: a winning and a losing trade, each no-trade rule, and a restart mid-trade."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from itertools import pairwise
from typing import Any

import pytest
from ae_core.backtest import Candle, Costs, MemoryHistory, simulate, summarize_result
from ae_core.strategy import (
    DEFAULT_INSTRUMENTS,
    SmcOption,
    SmcRisk,
    SmcRules,
    SmcScalpConfig,
    SmcSession,
    SmcTimeframes,
    Strike,
    check,
    max_order_lots,
    parse,
    plan_warnings,
    smc_tranches,
)
from ae_core.trading import options, smc
from ae_core.trading.model import IST, Contract, Market, Quote
from ae_core.trading.runners import make_runner

D = date(2026, 9, 21)


def t(hm: str, day: date = D) -> datetime:
    return datetime.combine(day, time.fromisoformat(hm), tzinfo=IST)


def bars(ohlc: list[tuple[float, float, float, float]], start: str = "09:15", step: int = 1) -> list[smc.Bar]:
    return [smc.Bar(t(start) + timedelta(minutes=i * step), *x) for i, x in enumerate(ohlc)]


def zigzag(prices: list[float], start: str = "09:15") -> list[smc.Bar]:
    """Candles from close to close with 1-point wicks."""
    out, prev = [], prices[0]
    for i, p in enumerate(prices):
        out.append(smc.Bar(t(start) + timedelta(minutes=i), prev, max(prev, p) + 1, min(prev, p) - 1, p))
        prev = p
    return out


# -- timeframes -------------------------------------------------------------------------------------------------
def test_resample_aligns_to_the_session_and_keeps_only_completed_candles() -> None:
    one = bars([(100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(12)])  # 09:15 .. 09:26
    five = smc.resample(one, 5)
    assert [b.ts for b in five] == [t("09:15"), t("09:20")]  # 09:25 has two of its five minutes
    assert (five[0].open, five[0].high, five[0].low, five[0].close) == (100, 105, 99, 104.5)
    assert smc.resample([*one, *bars([(1, 1, 1, 1)], "09:30")], 5)[-1].ts == t("09:25")  # a later minute closes it


def test_atr_needs_its_period() -> None:
    a = smc.atr(bars([(10, 12, 9, 11)] * 3), 3)
    assert a[:2] == [None, None] and a[2] == pytest.approx(3)


# -- swings and structure ---------------------------------------------------------------------------------------
def test_swings_are_confirmed_only_after_n_candles() -> None:
    b = zigzag([100, 102, 105, 103, 101, 104, 108])
    sw = smc.swings(b, 2)
    assert [(s.i, s.price, s.high, s.confirmed) for s in sw] == [(2, 106, True, 4), (4, 100, False, 6)]
    assert smc.swings(b[:4], 2) == []  # the high at index 2 is not known yet


def test_bos_choch_strong_and_weak_levels() -> None:
    b = zigzag([100, 104, 108, 105, 102, 106, 110, 107, 103, 100, 97, 99, 96, 93, 95, 98])
    st = smc.structure(b, 2)
    kinds = [(x.dir, x.kind, x.level) for x in st.breaks]
    assert kinds == [("up", "CHoCH", 109), ("down", "CHoCH", 101)]
    assert st.trend == "down" and st.streak == 1
    assert st.strong == 111  # the high the breaking leg started from: protected
    assert st.weak == 92  # the lowest low since: the next sell-side target
    assert st.equilibrium == pytest.approx(101.5)


def test_losing_the_protected_low_flips_the_trend_with_a_fresh_streak() -> None:
    b = zigzag([100, 104, 108, 105, 102, 106, 110, 107, 104, 101, 99])  # up-break at 110, then below its origin
    st = smc.structure(b, 2)
    assert [(x.dir, x.kind) for x in st.breaks] == [("up", "CHoCH"), ("down", "CHoCH")]
    assert st.trend == "down" and st.streak == 1  # one break: not yet a clear trend with bias_min_breaks = 2


def test_bos_after_choch_builds_the_streak() -> None:
    b = zigzag([100, 104, 108, 105, 102, 106, 110, 107, 104, 108, 113, 116])
    st = smc.structure(b, 2)
    assert [x.kind for x in st.breaks] == ["CHoCH", "BOS"] and st.streak == 2 and st.trend == "up"


# -- displacement, FVG, order block ---------------------------------------------------------------------------
def test_displacement_needs_a_big_body_closing_near_its_extreme() -> None:
    assert smc.displacement(smc.Bar(t("09:15"), 100, 131, 99, 130), 10, 1.5) == "up"
    assert smc.displacement(smc.Bar(t("09:15"), 100, 140, 99, 120), 10, 1.5) is None  # closed mid-range
    assert smc.displacement(smc.Bar(t("09:15"), 100, 101, 89, 90), 10, 1.5) is None  # body only 1x ATR
    assert smc.displacement(smc.Bar(t("09:15"), 130, 131, 99, 100), 10, 1.5) == "down"


def test_fair_value_gaps() -> None:
    b = bars([(100, 102, 99, 101), (101, 110, 101, 109), (109, 112, 105, 111), (111, 112, 95, 96), (96, 97, 90, 91)])
    (up,) = smc.fvgs(b, 0, 4, "up", 1)
    assert (up.lo, up.hi, up.i) == (102, 105, 2)
    (down,) = smc.fvgs(b, 0, 4, "down", 1)
    assert (down.lo, down.hi) == (97, 105)  # the third candle's high .. the first candle's low
    assert smc.fvgs(b, 0, 4, "up", 5) == []  # smaller than the minimum


def test_order_block_is_the_last_opposite_candle_before_the_displacement() -> None:
    b = bars([(105, 106, 100, 101), (101, 103, 98, 99), (99, 120, 99, 119)])
    ob = smc.order_block(b, 2, 0, "up", use_body=True)
    assert ob is not None and (ob.lo, ob.hi, ob.i) == (99, 101, 1)
    rng = smc.order_block(b, 2, 0, "up", use_body=False)
    assert rng is not None and (rng.lo, rng.hi) == (98, 103)
    assert smc.order_block(b, 2, 2, "up", True) is None


# -- liquidity --------------------------------------------------------------------------------------------------
def test_equal_highs_and_lows() -> None:
    sw = [smc.Swing(1, 100.0, True, 3, t("09:16")), smc.Swing(5, 100.02, True, 7, t("09:20")),
          smc.Swing(8, 90.0, False, 10, t("09:23")), smc.Swing(12, 95.0, False, 14, t("09:27"))]  # fmt: skip
    pairs = smc.equal_levels(sw, 0.03)
    assert [(a.i, b.i) for a, b in pairs] == [(1, 5)]
    pools = smc.swing_pools(bars([(1, 1, 1, 1)] * 15), sw, 0.03, 1)
    assert any(p.name == "equal highs" and p.price == 100.02 for p in pools)


def test_day_pools() -> None:
    prior = [smc.Bar(t("15:28", date(2026, 9, 18)), 100, 120, 90, 110)]
    today = bars([(110, 115, 108, 112)] * 20)
    names = {p.name: p.price for p in smc.day_pools(prior, today, 15)}
    assert names == {"previous day high": 120, "previous day low": 90, "opening range high": 115,
                     "opening range low": 108}  # fmt: skip
    assert len(smc.day_pools(prior, today[:10], 15)) == 2  # the opening range is not over yet


def test_sweep_needs_a_wick_beyond_and_a_close_back_inside() -> None:
    pool = smc.Pool("swing low", 100, False, t("09:14"))
    swept = bars([(103, 104, 102, 103), (103, 103, 98, 101), (101, 105, 100.5, 104)])
    s = smc.find_sweep(swept, [pool], 0, 2, "up", 0.5, 2)
    assert s is not None and (s.i, s.reclaim_i, s.extreme) == (1, 1, 98)
    broke = bars([(103, 104, 102, 103), (103, 103, 98, 98.5), (98.5, 99, 97, 97.5), (97.5, 99.5, 97, 99)])
    assert smc.find_sweep(broke, [pool], 0, 3, "up", 0.5, 2) is None  # accepted below: a breakdown
    late = bars([(103, 104, 102, 103), (103, 103, 98, 101), (101, 101, 98, 101)])
    assert smc.find_sweep(late, [pool], 2, 2, "up", 0.5, 2) is None  # taken before the window: already gone
    assert smc.find_sweep(swept, [pool], 0, 2, "down", 0.5, 2) is None  # sell-side is not a short's sweep


# -- options ----------------------------------------------------------------------------------------------------
def test_nearest_expiry_skips_expiry_day_afternoon() -> None:
    ex = [date(2026, 9, 22), date(2026, 9, 29)]
    assert options.nearest_expiry(ex, t("10:00", date(2026, 9, 22)), "nearest", "12:00") == date(2026, 9, 22)
    assert options.nearest_expiry(ex, t("12:00", date(2026, 9, 22)), "nearest", "12:00") == date(2026, 9, 29)
    assert options.nearest_expiry(ex, t("10:00", date(2026, 9, 21)), "next", None) == date(2026, 9, 29)


def test_liquidity_checks() -> None:
    assert options.illiquid(Quote(100, 101, 50_000, 100_000), 10_000, 50_000, 1.0) is None
    assert "spread" in (options.illiquid(Quote(100, 103, 50_000, 100_000), 10_000, 50_000, 1.0) or "")
    assert "volume" in (options.illiquid(Quote(volume=5), 10_000, 0, 1.0) or "")
    assert "open interest" in (options.illiquid(Quote(oi=5), 0, 50_000, 1.0) or "")
    assert options.illiquid(None, 10_000, 50_000, 1.0) is None  # nothing known: not held against it


def test_pick_strike_offsets() -> None:
    m = Market(t("10:00"), "NIFTY", 24410, [], {}, [EXPIRY], 65, 50)
    c = options.pick(m, "CE", Strike(offset=1), EXPIRY)
    assert not isinstance(c, str) and c.strike == 24450
    itm = options.pick(m, "PE", Strike(offset=-2), EXPIRY)
    assert not isinstance(itm, str) and itm.strike == 24500


# -- config -----------------------------------------------------------------------------------------------------
def test_tranches_split_lots_over_the_targets() -> None:
    assert smc_tranches(1, True) == [0, 0, 1]
    assert smc_tranches(2, True) == [0, 1, 1]
    assert smc_tranches(3, True) == [1, 1, 1]
    assert smc_tranches(7, True) == [2, 2, 3]
    assert smc_tranches(8, True) == [2, 3, 3]
    assert smc_tranches(5, False) == [0, 0, 5]
    assert max_order_lots(SmcScalpConfig(risk=SmcRisk(lots=9))) == 3


def test_config_checks() -> None:
    ok = SmcScalpConfig()
    assert check(ok, DEFAULT_INSTRUMENTS) == []
    assert parse(ok.model_dump()) == ok
    bad = SmcScalpConfig(
        timeframes=SmcTimeframes(bias=15, setup=10, entry=3),
        session=SmcSession(start="10:00", last_entry="09:45", exit="16:00"),
        risk=SmcRisk(min_risk_pct=0.5, max_risk_pct=0.4),
        option=SmcOption(strike=Strike(mode="premium")),
    )
    locs = {i.loc for i in check(bad, DEFAULT_INSTRUMENTS)}
    assert {("timeframes", "bias"), ("session", "last_entry"), ("session", "exit"), ("risk", "max_risk_pct"),
            ("option", "strike", "premium")} <= locs  # fmt: skip
    assert plan_warnings(SmcScalpConfig(risk=SmcRisk(lots=6)), 1)[0].loc == ("risk", "lots")


# -- a synthetic day ---------------------------------------------------------------------------------------------
# Three trending days, then a sweep-and-reverse setup. Prices follow straight lines between waypoints (small wicks),
# so each step is placed on purpose: a 5m swing low (sell-side liquidity), a sweep below it that closes back above,
# a displacement candle breaking the 5m swing high and leaving a fair value gap, a 1m pullback into the gap, a 1m
# CHoCH, then a rally (or a collapse, for the losing variant).
DAY = date(2026, 9, 21)  # a Monday
EXPIRY = date(2026, 9, 22)
PRIOR = [date(2026, 9, 16), date(2026, 9, 17), date(2026, 9, 18)]


def path(day: date, points: list[tuple[str, float]], wick: float = 2.0) -> list[Candle]:
    """1-minute candles walking from waypoint to waypoint ("HH:MM", price); the candle at each minute opens where
    the previous one closed."""
    out: list[Candle] = []
    for (t0, p0), (t1, p1) in pairwise(points):
        a = datetime.combine(day, time.fromisoformat(t0), tzinfo=IST)
        b = datetime.combine(day, time.fromisoformat(t1), tzinfo=IST)
        n = int((b - a).total_seconds() // 60)
        for i in range(n):
            o = p0 + (p1 - p0) * i / n
            c = p0 + (p1 - p0) * (i + 1) / n
            out.append(Candle(a + timedelta(minutes=i), o, max(o, c) + wick, min(o, c) - wick, c))
    return out


def trend_day(day: date, start: float) -> list[Candle]:
    """An up day of higher highs and higher lows (+300 over the session)."""
    pts, p, t = [("09:15", start)], start, datetime.combine(day, time(9, 15))
    for _ in range(12):
        t += timedelta(minutes=15)
        p += 60
        pts.append((t.strftime("%H:%M"), p))
        t += timedelta(minutes=15)
        p -= 35
        pts.append((t.strftime("%H:%M"), p))
    pts.append(("15:30", p))
    return path(day, pts)


def setup_day(win: bool = True) -> list[Candle]:
    finish = [("12:00", 24700.0), ("15:30", 24720.0)] if win else [("11:20", 24250.0), ("15:30", 24240.0)]
    return path(
        DAY,
        [
            ("09:15", 24300.0),
            ("09:45", 24360.0),  # 5m swing high
            ("10:00", 24320.0),  # 5m swing low: sell-side liquidity
            ("10:15", 24350.0),  # lower high: the level the displacement breaks
            ("10:27", 24325.0),
            ("10:30", 24300.0),  # the sweep: 20 points under the swing low
            ("10:34", 24332.0),  # closed back above inside the same 5m candle
            ("10:35", 24335.0),
            ("10:40", 24405.0),  # displacement candle through 24350
            ("10:45", 24415.0),  # leaves a gap above the 10:30 candle's high
            ("10:49", 24409.0),
            ("10:52", 24414.0),  # 1m lower high in the pullback
            ("10:57", 24398.0),  # taps the gap
            ("11:00", 24405.0),
            ("11:03", 24424.0),  # closes above the lower high: the 1m CHoCH
            *finish,
        ],
    )


def option_bars(spot: list[Candle], strike: int, right: str) -> list[Candle]:
    """A crude premium: intrinsic value plus time value, delta about one half near the money."""

    def px(s: float) -> float:
        return round(max(1.0, 120 + 0.5 * ((s - strike) if right == "CE" else (strike - s))), 2)

    return [Candle(c.ts, px(c.open), px(c.high), px(c.low), px(c.close), 100_000, 2_000_000) for c in spot]


def history(win: bool = True) -> MemoryHistory:
    h = MemoryHistory()
    start = 23400.0
    days: list[list[Candle]] = []
    for d in PRIOR:
        days.append(trend_day(d, start))
        start += 300
    today = setup_day(win)
    for bars in [*days, today]:
        h.add_spot("NIFTY", bars)
    for k in range(24000, 24801, 50):
        for right in ("CE", "PE"):
            h.add_option(Contract("NIFTY", EXPIRY, k, right).key, option_bars(today, k, right))  # type: ignore[arg-type]
    return h


# -- the scalper end to end -------------------------------------------------------------------------------------
def scenario_cfg(**kw: Any) -> SmcScalpConfig:
    """The scenario's setup is placed above the 15m equilibrium and its stop is wide, so those rules are relaxed
    unless a test is about them."""
    rules = SmcRules(**{"bias_min_breaks": 1, "premium_discount": False, **kw.pop("rules", {})})
    risk = SmcRisk(**{"lots": 3, "max_risk_pct": 1.0, **kw.pop("risk", {})})
    return SmcScalpConfig(rules=rules, risk=risk, **kw)


def run(cfg: SmcScalpConfig, win: bool = True) -> Any:
    free = Costs(0, 0, 0, 0, 0, 0)
    return simulate(cfg, history(win), DAY, DAY, lot_size=65, strike_step=50, slippage_pct=0, costs=free)


def test_a_winning_setup_buys_a_call_and_takes_three_targets() -> None:
    r = run(scenario_cfg())
    (sig,) = r.signals
    assert sig["signal"] == "BUY CE" and sig["contract"] == "NIFTY 22 Sep 24400 CE" and sig["rr"] == "1:2"
    assert sig["stop_loss"] < 24298 < sig["entry"]  # beyond the sweep's extreme
    risk = sig["entry"] - sig["stop_loss"]
    assert sig["tp1"] == pytest.approx(sig["entry"] + risk, abs=0.02)
    assert sig["tp2"] == pytest.approx(sig["entry"] + 1.5 * risk, abs=0.02)
    assert sig["tp3"] == pytest.approx(sig["entry"] + 2 * risk, abs=0.02)
    for part in ("15m bullish", "swept: swing low 24318", "displacement", "CHoCH through 24352", "FVG", "1m CHoCH"):
        assert part in sig["reason"]
    assert [x.leg for x in r.trades] == ["TP1", "TP2", "TP3"]
    assert all(x.reason.startswith("target") and x.gross > 0 for x in r.trades)
    assert all(x.qty == 65 and x.side == "BUY" for x in r.trades)
    s = summarize_result(r)["signals"]
    assert (s["count"], s["wins"], s["funnel"]["signals"]) == (1, 1, 1) and s["funnel"]["armed"] >= 1


def test_a_failing_setup_is_stopped_out() -> None:
    r = run(scenario_cfg(risk={"premium_stop_pct": None}), win=False)
    assert len(r.signals) == 1 and len(r.trades) == 3
    assert all(x.reason.startswith("stop-loss: index") and x.gross < 0 for x in r.trades)
    assert summarize_result(r)["signals"]["losses"] == 1


def test_rr_choice_moves_tp3() -> None:
    r = run(scenario_cfg(risk={"rr": 4}))
    (sig,) = r.signals
    risk = sig["entry"] - sig["stop_loss"]
    assert sig["rr"] == "1:4" and sig["tp3"] == pytest.approx(sig["entry"] + 4 * risk, abs=0.02)


@pytest.mark.parametrize(
    ("cfg", "reason"),
    [
        # with premium/discount on, only the FVG's part below the leg's equilibrium is a buy zone: never revisited
        (SmcScalpConfig(rules=SmcRules(bias_min_breaks=1), risk=SmcRisk(max_risk_pct=1.0)), "POI not reached in time"),
        (scenario_cfg(risk={"max_risk_pct": 0.35}), "skipped: stop too wide"),
        (scenario_cfg(rules={"bias_min_breaks": 4}), "bias unclear"),
        (scenario_cfg(option=SmcOption(min_volume=10**12)), "skipped: option too illiquid"),
        (scenario_cfg(session=SmcSession(last_entry="10:50")), "skipped: outside entry hours"),
        (scenario_cfg(rules={"poi": "both"}), "no order block / FVG"),
    ],
)
def test_no_trade_rules(cfg: SmcScalpConfig, reason: str) -> None:
    r = run(cfg)
    assert r.trades == [] and r.signals == []
    assert r.funnel.get(reason, 0) >= 1, r.funnel


def test_state_survives_a_restart_mid_trade() -> None:
    """Stepping minute by minute, rebuilding the runner from its JSON state halfway, trades exactly the same."""
    import json

    h = history(True)
    prior = [c for d in sorted({c.ts.date() for c in _all_spot(h)}) if d < DAY for c in h.spot("NIFTY", d)]

    def replay(restart_at: str | None) -> list[tuple[str, str, float]]:
        runner = make_runner(scenario_cfg())
        spot = list(h.spot("NIFTY", DAY))
        fills: list[tuple[str, str, float]] = []
        for i, bar in enumerate(spot):
            if restart_at and bar.ts == t(restart_at, DAY):
                runner = make_runner(scenario_cfg(), state=json.loads(json.dumps(runner.state())))
            m0 = Market(bar.ts, "NIFTY", bar.close, spot[:i], {}, [EXPIRY], 65, 50, prior)
            keys = runner.wanted(m0)
            px = {k: c.close for k in keys if (c := h.option(k, DAY).get(bar.ts)) is not None}
            m = Market(bar.ts, "NIFTY", bar.close, spot[:i], px, [EXPIRY], 65, 50, prior)
            for intent in runner.step(m):
                p = m.price(intent.contract)
                if p is not None and runner.fill(intent, p, bar.ts, m):
                    fills.append((intent.kind, intent.leg, p))
        return fills

    straight = replay(None)
    assert len(straight) == 6  # three tranches in, three out
    assert replay("11:20") == straight


def _all_spot(h: Any) -> list[Candle]:
    return [c for (u, _), cs in h._spot.items() if u == "NIFTY" for c in cs]
