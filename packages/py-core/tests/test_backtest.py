"""The backtester over hand-made history: fills at open, stops and targets inside a minute (level or gap), timed exits,
charges, missing data, and the end of the range."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from ae_core.backtest import Candle, Costs, MemoryHistory, simulate, summarize_result
from ae_core.strategy import parse
from ae_core.trading.model import IST

DAY = date(2026, 10, 6)  # Tuesday, an expiry
KEY = f"NIFTY:{DAY:%Y-%m-%d}:25000:CE"


def ts(hm: str, day: date = DAY) -> datetime:
    return datetime.combine(day, time.fromisoformat(hm), tzinfo=IST)


def history(opt: dict[str, tuple[float, float, float, float]], day: date = DAY, spot: float = 25000) -> MemoryHistory:
    """Index flat all day; the option has bars only at the given minutes: (open, high, low, close)."""
    h = MemoryHistory()
    t = ts("09:15", day)
    h.add_spot("NIFTY", [Candle(t + timedelta(minutes=i), spot, spot + 1, spot - 1, spot) for i in range(375)])
    h.add_option(KEY, [Candle(ts(hm, day), *v) for hm, v in opt.items()])
    return h


def cfg(**leg: Any) -> Any:
    return parse({
        "kind": "time_based",
        "timing": {"entry": "09:20", "exit": "15:15", "days": ["TUE"]},
        "legs": [{"id": "L1", "action": "SELL", "option_type": "CE", "lots": 1, **leg}],
    })  # fmt: skip


def run(config: Any, h: MemoryHistory, **kw: Any) -> Any:
    kw.setdefault("slippage_pct", 0.0)
    kw.setdefault("costs", Costs(0, 0, 0, 0, 0, 0))
    return simulate(config, h, DAY, DAY, lot_size=65, strike_step=50, **kw)


def flat(px: float) -> tuple[float, float, float, float]:
    return (px, px, px, px)


def test_enters_at_the_minute_open_and_exits_at_the_exit_time() -> None:
    h = history({"09:20": flat(100), "15:15": flat(90)})
    r = run(cfg(), h)
    (t,) = r.trades
    assert (t.side, t.qty, t.entry_price, t.exit_price, t.reason) == ("SELL", 65, 100, 90, "exit time 15:15")
    assert t.gross == 650 and r.days_replayed == 1 and r.gross_pnl == 650


def test_stop_loss_inside_a_minute_fills_at_the_stop_level() -> None:
    h = history({"09:20": flat(100), "10:00": (105, 150, 104, 120), "15:15": flat(50)})
    (t,) = run(cfg(stop_loss={"value": 30}), h).trades
    assert (t.exit_price, t.reason, t.exit_time) == (130.0, "stop-loss", ts("10:00"))  # not the high of 150
    assert t.gross == (100 - 130) * 65


def test_a_gap_through_the_stop_fills_at_the_open() -> None:
    h = history({"09:20": flat(100), "10:00": (140, 150, 138, 145), "15:15": flat(50)})
    (t,) = run(cfg(stop_loss={"value": 30}), h).trades
    assert t.exit_price == 140.0


def test_target_fills_at_the_target_level() -> None:
    h = history({"09:20": flat(100), "11:00": (95, 96, 60, 70), "15:15": flat(90)})
    (t,) = run(cfg(target={"value": 30}), h).trades
    assert (t.exit_price, t.reason) == (70.0, "target")  # 100 - 30%, though the low was 60


def test_stop_wins_when_both_are_reachable_in_one_minute() -> None:
    h = history({"09:20": flat(100), "11:00": (100, 140, 50, 100), "15:15": flat(100)})
    (t,) = run(cfg(stop_loss={"value": 30}, target={"value": 30}), h).trades
    assert t.reason == "stop-loss"


def test_charges_and_slippage() -> None:
    h = history({"09:20": flat(100), "15:15": flat(100)})
    r = simulate(cfg(), h, DAY, DAY, lot_size=65, strike_step=50, slippage_pct=1.0)
    (t,) = r.trades
    assert (t.entry_price, t.exit_price) == (99.0, 101.0)  # sold lower, bought back higher
    assert t.charges > 40 and t.net == round(t.gross - t.charges, 2)  # two orders' brokerage alone is 40
    assert r.charges == t.charges and r.gross_pnl == (99 - 101) * 65


def test_no_data_days_and_other_weekdays() -> None:
    h = history({"09:20": flat(100)})
    assert simulate(cfg(), h, DAY + timedelta(days=30), DAY + timedelta(days=40), lot_size=65, strike_step=50).warnings
    wed = DAY + timedelta(days=1)
    h2 = history({}, day=wed)
    r = simulate(cfg(), h2, wed, wed, lot_size=65, strike_step=50)
    assert r.trades == [] and r.days_replayed == 1  # the strategy trades Tuesdays only


def test_a_position_open_at_the_end_is_closed_at_its_last_price() -> None:
    config = parse({"kind": "time_based", "timing": {"entry": "09:20", "exit": "15:29", "days": ["TUE"]},
                    "legs": [{"id": "L1", "action": "SELL", "option_type": "CE"}]})  # fmt: skip
    h = history({"09:20": flat(100), "12:00": flat(80)})
    r = run(config, h)
    assert r.trades and r.trades[-1].reason in ("exit time 15:29", "end of the tested range")


def test_result_json() -> None:
    h = history({"09:20": flat(100), "15:15": flat(90)})
    j = summarize_result(run(cfg(), h))
    assert j["summary"]["net_pnl"] == 650 and j["summary"]["trades"] == 1
    assert j["daily"] == [{"day": "2026-10-06", "pnl": 650.0, "trades": 1, "cumulative": 650.0}]
    assert j["trades"][0]["net"] == 650 and j["trades_total"] == 1


def test_days_without_option_data_are_reported() -> None:
    h = history({"09:20": flat(100), "15:15": flat(90)})
    wed = DAY + timedelta(days=1)
    h.add_spot(
        "NIFTY", [Candle(ts("09:15", wed) + timedelta(minutes=i), 25000, 25001, 24999, 25000) for i in range(375)]
    )
    r = simulate(cfg(), h, DAY, wed, lot_size=65, strike_step=50)
    assert r.days_replayed == 2 and r.days_without_options == 1
    assert "1 of 2 days have no option prices" in r.warnings[0]
    assert summarize_result(r)["summary"]["days_without_options"] == 1


def test_an_index_stop_fires_on_the_minute_extreme_and_fills_at_the_option_extreme() -> None:
    h = MemoryHistory()
    t0 = ts("09:15")
    dip = ts("10:00")
    h.add_spot(
        "NIFTY",
        [
            Candle(t, 25000, 25001, 24900 if t == dip else 24999, 25000)
            for t in (t0 + timedelta(minutes=i) for i in range(375))
        ],
    )
    h.add_option(KEY, [Candle(ts("09:20"), *flat(100)), Candle(dip, 100, 101, 80, 95), Candle(ts("15:15"), *flat(99))])
    config = parse({
        "kind": "time_based",
        "timing": {"entry": "09:20", "exit": "15:15", "days": ["TUE"]},
        "legs": [{"id": "L1", "action": "BUY", "option_type": "CE",
                  "stop_loss": {"unit": "percent", "value": 0.2, "basis": "underlying"}}],
    })  # fmt: skip
    (t,) = run(config, h).trades
    assert (t.exit_time, t.exit_price, t.reason) == (dip, 80, "stop-loss")  # the open (25000) never reached it


def test_an_overnight_rules_trade_is_held_to_the_next_day() -> None:
    mon = DAY - timedelta(days=1)
    h = history({"15:00": flat(100)}, day=mon)
    h.add_spot("NIFTY", [Candle(ts("09:15") + timedelta(minutes=i), 25000, 25001, 24999, 25000) for i in range(375)])
    h.add_option(KEY, [Candle(ts("09:30"), *flat(70))])
    config = parse({
        "kind": "rules",
        "entry": {"at": "15:00"},
        "holding": {"mode": "next_day", "exit": "09:30"},
        "legs": [{"id": "L1", "action": "SELL", "option_type": "CE"}],
    })  # fmt: skip
    r = simulate(config, h, mon, DAY, lot_size=65, strike_step=50, slippage_pct=0.0, costs=Costs(0, 0, 0, 0, 0, 0))
    (t,) = r.trades
    assert (t.entry_time, t.exit_time, t.exit_price, t.reason) == (ts("15:00", mon), ts("09:30"), 70, "exit time 09:30")
    assert t.gross == 30 * 65
