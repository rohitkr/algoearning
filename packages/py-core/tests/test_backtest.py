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


def test_an_opening_range_breakout_trades_through_the_backtester() -> None:
    h = MemoryHistory()
    t0 = ts("09:15")
    # flat until 09:45, then 25040: the 09:45-09:50 candle closes above the opening range high (25001)
    h.add_spot(
        "NIFTY",
        [
            Candle(t0 + timedelta(minutes=i), 25000, 25001 if i < 30 else 25041, 24999, 25000 if i < 30 else 25040)
            for i in range(375)
        ],
    )
    h.add_option(
        KEY, [Candle(ts("09:50"), *flat(100)), Candle(ts("11:00"), *flat(130)), Candle(ts("15:15"), *flat(90))]
    )
    config = parse({
        "kind": "rules",
        "entry": {"mode": "conditions", "at": "09:30", "until": "14:30", "signals": [
            {"direction": "up", "conditions": [{"op": "crosses_above", "candle": 5,
                                                "right": {"kind": "level", "level": "opening_high"}}]}]},
        "legs": [{"id": "L1", "action": "BUY", "option_type": "CE", "direction": "up", "target": {"value": 20}}],
    })  # fmt: skip
    r = run(config, h)
    (t,) = r.trades
    assert (t.side, t.entry_time, t.entry_price, t.exit_price, t.reason) == (
        "BUY",
        ts("09:50"),
        100,
        130,
        "target",
    )  # gapped through the 120 target


def _days(h: MemoryHistory, *days: date) -> None:
    for d in days:
        t = ts("09:15", d)
        h.add_spot("NIFTY", [Candle(t + timedelta(minutes=i), 25000, 25001, 24999, 25000) for i in range(375)])


def test_an_exit_with_no_trade_at_that_time_uses_the_last_known_price_instead_of_blocking_later_trades() -> None:
    mon = DAY - timedelta(days=1)
    h = MemoryHistory()
    _days(h, mon, DAY)  # no option bar at all on the exit day
    h.add_option(KEY, [Candle(ts("15:00", mon), *flat(100))])
    config = parse({
        "kind": "rules",
        "entry": {"at": "15:00"},
        "holding": {"mode": "next_day", "exit": "09:30"},
        "legs": [{"id": "L1", "action": "SELL", "option_type": "CE"}],
    })  # fmt: skip
    r = simulate(config, h, mon, DAY, lot_size=65, strike_step=50, slippage_pct=0.0, costs=Costs(0, 0, 0, 0, 0, 0))
    (t,) = r.trades
    assert (t.exit_time, t.exit_price, t.reason) == (ts("09:30"), 100, "exit time 09:30")
    assert any("last known price" in w for w in r.warnings)


def test_each_weekday_can_trade_its_own_legs() -> None:
    mon = DAY - timedelta(days=1)
    put = f"NIFTY:{DAY:%Y-%m-%d}:25000:PE"
    put_up = f"NIFTY:{DAY:%Y-%m-%d}:25100:PE"
    h = MemoryHistory()
    _days(h, mon, DAY)
    for d in (mon, DAY):
        for key in (KEY, put, put_up):
            h.add_option(key, [Candle(ts("09:20", d), *flat(100)), Candle(ts("15:15", d), *flat(90))])
    config = parse({
        "kind": "rules",
        "entry": {"at": "09:20"},
        "holding": {"mode": "intraday", "exit": "15:15"},
        "legs": [{"id": "D", "action": "SELL", "option_type": "CE"}],  # every day without its own legs
        "day_legs": {"MON": [  # an iron-fly-like set: sell the put, buy a put above it
            {"id": "M1", "action": "SELL", "option_type": "PE"},
            {"id": "M2", "action": "BUY", "option_type": "PE", "strike": {"mode": "points", "points": -100}},
        ]},
    })  # fmt: skip
    r = simulate(config, h, mon, DAY, lot_size=65, strike_step=50, slippage_pct=0.0, costs=Costs(0, 0, 0, 0, 0, 0))
    by_day: dict[date, list[str]] = {}
    for t in r.trades:
        by_day.setdefault(t.entry_time.date(), []).append(t.leg)
    by_day = {d: sorted(v) for d, v in by_day.items()}
    assert by_day == {mon: ["M1", "M2"], DAY: ["D"]}
    from ae_core.strategy import Instrument, check

    inst = {"NIFTY": Instrument("NIFTY", "Nifty 50", "NSE", 65, 50, True, "09:15", "15:30")}
    assert check(config, inst) == []
    clash = parse(
        {**config.model_dump(mode="json"), "day_legs": {"MON": [{"id": "D", "action": "SELL", "option_type": "PE"}]}}
    )
    assert [i.msg for i in check(clash, inst)] == ["leg id D is used twice"]


def test_no_default_legs_needed_when_weekdays_have_their_own() -> None:
    from ae_core.strategy import Instrument, check

    mon = DAY - timedelta(days=1)
    put = f"NIFTY:{DAY:%Y-%m-%d}:25000:PE"
    h = MemoryHistory()
    _days(h, mon, DAY)
    for d in (mon, DAY):
        h.add_option(put, [Candle(ts("09:20", d), *flat(100)), Candle(ts("15:15", d), *flat(90))])
    raw: dict[str, Any] = {
        "kind": "rules",
        "entry": {"at": "09:20"},
        "holding": {"mode": "intraday", "exit": "15:15"},
        "legs": [],
        "day_legs": {"MON": [{"id": "M1", "action": "SELL", "option_type": "PE"}]},  # Tuesday: nothing to trade
    }
    config = parse(raw)
    inst = {"NIFTY": Instrument("NIFTY", "Nifty 50", "NSE", 65, 50, True, "09:15", "15:30")}
    assert check(config, inst) == []
    r = simulate(config, h, mon, DAY, lot_size=65, strike_step=50, slippage_pct=0.0, costs=Costs(0, 0, 0, 0, 0, 0))
    assert [(t.entry_time.date(), t.leg) for t in r.trades] == [(mon, "M1")]  # Monday only, no error on Tuesday
    assert [i.msg for i in check(parse({**raw, "day_legs": None}), inst)] == ["add at least one leg"]


def test_every_replayed_day_says_whether_it_had_option_prices_and_why_it_did_not_trade() -> None:
    mon = DAY - timedelta(days=1)  # Monday: no option prices at all; Tuesday: prices, but the weekday is not ticked
    h = MemoryHistory()
    _days(h, mon, DAY)
    h.add_option(KEY, [Candle(ts("09:20", DAY), *flat(100))])
    config = parse({
        "kind": "rules",
        "entry": {"at": "09:20", "days": ["MON"]},
        "holding": {"mode": "intraday", "exit": "15:15"},
        "legs": [{"id": "L1", "action": "SELL", "option_type": "CE"}],
    })  # fmt: skip
    r = simulate(config, h, mon, DAY, lot_size=65, strike_step=50, slippage_pct=0.0, costs=Costs(0, 0, 0, 0, 0, 0))
    by_day = {row["day"]: row for row in r.day_log}
    assert by_day[mon.isoformat()]["options"] is False and by_day[mon.isoformat()]["trades"] == 0
    assert by_day[mon.isoformat()]["why"] == ["no option prices stored for this day"]
    assert by_day[DAY.isoformat()]["options"] is True
    assert by_day[DAY.isoformat()]["why"] == ["this weekday is not ticked under Trade on"]
    assert any("05 Oct" in w and "no option prices" in w for w in r.warnings)
    assert summarize_result(r)["day_log"] == r.day_log


def test_a_weekday_can_be_an_iron_condor_built_leg_by_leg() -> None:
    """Sell CE and PE near ₹60, buy the ATM CE and PE: every leg runs, each with its own strike rule."""
    h = MemoryHistory()
    _days(h, DAY)
    for k in range(24500, 25550, 50):  # a smooth smile: premium falls 0.6 per point away from the money
        for right in ("CE", "PE"):
            away = (k - 25000) if right == "CE" else (25000 - k)
            px = max(1.0, round(140 - 0.8 * away, 2))
            h.add_option(
                f"NIFTY:{DAY:%Y-%m-%d}:{k}:{right}", [Candle(ts("09:20"), *flat(px)), Candle(ts("15:15"), *flat(px))]
            )
    config = parse({
        "kind": "rules",
        "entry": {"at": "09:20", "days": ["TUE"]},
        "holding": {"mode": "intraday", "exit": "15:15"},
        "legs": [],
        "day_legs": {"TUE": [
            {"id": "T1", "action": "SELL", "option_type": "CE", "strike": {"mode": "premium", "premium": 60}},
            {"id": "T2", "action": "SELL", "option_type": "PE", "strike": {"mode": "premium_gte", "premium": 60}},
            {"id": "T3", "action": "BUY", "option_type": "CE", "strike": {"mode": "atm", "offset": 0}},
            {"id": "T4", "action": "BUY", "option_type": "PE", "strike": {"mode": "atm", "offset": 0}},
        ]},
    })  # fmt: skip
    r = simulate(config, h, DAY, DAY, lot_size=65, strike_step=50, slippage_pct=0.0, costs=Costs(0, 0, 0, 0, 0, 0))
    got = {t.leg: (t.side, t.contract) for t in r.trades}
    assert set(got) == {"T1", "T2", "T3", "T4"}
    assert got["T1"] == ("SELL", f"NIFTY {DAY:%d %b} 25100 CE")  # premium 60 is 100 points out
    assert got["T3"] == ("BUY", f"NIFTY {DAY:%d %b} 25000 CE") and got["T4"] == ("BUY", f"NIFTY {DAY:%d %b} 25000 PE")


def test_a_position_left_open_after_its_trade_ended_is_closed_and_reported() -> None:
    """If an exit is lost after the trade was closed (nothing retries it), the leg used to stay open for good and
    block every later entry; now it is closed at the last price, and the day and the result say so."""
    from unittest.mock import patch

    from ae_core.trading.runners import RulesRunner

    h = history({"09:20": flat(100), "12:00": flat(90)})
    config = parse({
        "kind": "rules",
        "entry": {"at": "09:20"},
        "holding": {"mode": "intraday", "exit": "15:15"},
        "legs": [{"id": "L1", "action": "BUY", "option_type": "CE"}],
    })  # fmt: skip

    def lost_exits(self: RulesRunner, m: Any, reason: str, event: str | None = None, **detail: Any) -> list[Any]:
        self._end_cycle(m)  # the trade is over, but no exit order comes out of it
        return []

    with patch.object(RulesRunner, "_close", lost_exits):
        r = run(config, h)
    (t,) = r.trades
    assert (t.exit_price, t.reason) == (90, "closing a leftover position")
    assert any("still open after their trade had ended" in w for w in r.warnings)
    assert not any("still open at the end" in w for w in r.warnings)


def test_a_day_that_waits_for_a_price_that_is_never_stored_says_which_contract() -> None:
    mon = DAY - timedelta(days=1)
    h = MemoryHistory()
    _days(h, mon)
    config = parse({
        "kind": "rules",
        "entry": {"at": "09:20"},
        "holding": {"mode": "intraday", "exit": "15:15"},
        "legs": [{"id": "L1", "action": "BUY", "option_type": "CE"}],
    })  # fmt: skip
    h.add_option(KEY, [Candle(ts("09:20", DAY), *flat(100))])  # the contract exists, but not on Monday
    h.add_option(f"NIFTY:{DAY:%Y-%m-%d}:25500:CE", [Candle(ts("09:20", mon), *flat(5))])  # Monday has other prices
    r = simulate(config, h, mon, mon, lot_size=65, strike_step=50, slippage_pct=0.0, costs=Costs(0, 0, 0, 0, 0, 0))
    (row,) = r.day_log
    assert row["trades"] == 0 and any("no price yet for NIFTY 06 Oct 25000 CE" in w for w in row["why"])


def _bars(day: date, *rows: tuple[str, tuple[float, float, float, float]]) -> list[Candle]:
    return [Candle(ts(hm, day), *v) for hm, v in rows]


def test_a_profit_lock_that_fires_inside_a_minute_closes_the_trade() -> None:
    """The lock's exit is a stop: it is taken at the locked amount. It used to be thrown away after the trade
    was marked as ended, leaving the leg open for the rest of the range."""
    h = MemoryHistory()
    _days(h, DAY)
    h.add_option(KEY, _bars(DAY, ("09:20", (100, 100, 100, 100)), ("09:21", (100, 120, 100, 118)),
                            ("09:22", (118, 119, 105, 106)), ("15:15", (106, 106, 106, 106))))  # fmt: skip
    config = parse({
        "kind": "rules",
        "entry": {"at": "09:20"},
        "holding": {"mode": "intraday", "exit": "15:15"},
        "legs": [{"id": "L1", "action": "BUY", "option_type": "CE"}],
        "risk": {"lock_profit": {"at": 1000, "lock": 500}},
    })  # fmt: skip
    r = run(config, h)
    (t,) = r.trades
    # closed at the locked amount (100 + 500 / 65 = 107.7), not at the minute's low of 105
    assert t.reason == "profit fell to the locked ₹500" and t.exit_price == 107.7
    assert not any("leftover" in w or "still open" in w for w in r.warnings)


def test_exit_all_on_leg_stop_loss_closes_the_other_legs_too() -> None:
    put = f"NIFTY:{DAY:%Y-%m-%d}:25000:PE"
    h = MemoryHistory()
    _days(h, DAY)
    h.add_option(KEY, _bars(DAY, ("09:20", flat(100)), ("09:21", (100, 140, 100, 130)), ("15:15", flat(130))))
    h.add_option(put, _bars(DAY, ("09:20", flat(100)), ("09:21", flat(100)), ("15:15", flat(100))))
    sl = {"unit": "percent", "value": 20, "basis": "premium"}
    config = parse({
        "kind": "rules",
        "entry": {"at": "09:20"},
        "holding": {"mode": "intraday", "exit": "15:15"},
        "legs": [{"id": "A", "action": "SELL", "option_type": "CE", "stop_loss": sl},
                 {"id": "B", "action": "SELL", "option_type": "PE", "stop_loss": sl}],
        "risk": {"exit_all_on_leg_sl": True},
    })  # fmt: skip
    r = run(config, h)
    assert {t.leg: t.reason for t in r.trades} == {"A": "stop-loss", "B": "another leg hit its stop-loss"}
    assert not any("leftover" in w or "still open" in w for w in r.warnings)


def test_an_intraday_trade_does_not_sleep_over_when_the_days_data_ends_early() -> None:
    mon = DAY - timedelta(days=1)
    h = MemoryHistory()
    t = ts("09:15", mon)  # the index and the option stop at 14:00: the 15:15 exit minute never comes
    h.add_spot("NIFTY", [Candle(t + timedelta(minutes=i), 25000, 25001, 24999, 25000) for i in range(285)])
    h.add_option(KEY, [Candle(ts("09:20", mon), *flat(100)), Candle(ts("13:00", mon), *flat(90))])
    _days(h, DAY)
    config = parse({
        "kind": "rules",
        "entry": {"at": "09:20"},
        "holding": {"mode": "intraday", "exit": "15:15"},
        "legs": [{"id": "L1", "action": "SELL", "option_type": "CE"}],
    })  # fmt: skip
    r = simulate(config, h, mon, DAY, lot_size=65, strike_step=50, slippage_pct=0.0, costs=Costs(0, 0, 0, 0, 0, 0))
    (t1,) = r.trades
    assert t1.exit_time.date() == mon and t1.exit_price == 90
    assert t1.reason == "no prices after 13:00: closed at the last price"
    assert any("closed at the last known price" in w for w in r.warnings)
    # and the report says plainly that this is not a full day, naming the day and the contract
    assert any("not full days" in w and "Mon 05 Oct" in w and "no prices after 13:00" in w for w in r.warnings)


def test_a_combined_target_is_not_reached_by_adding_up_each_legs_best_price_of_the_minute() -> None:
    """A bought CE and PE: in one minute the CE's high and the PE's high cannot both have happened together (one rises
    as the other falls), yet adding them up looked like a profit and closed the trade in the entry minute. The trade's
    combined profit is judged on prices that existed together, so here it never reaches the target."""
    put = f"NIFTY:{DAY:%Y-%m-%d}:25000:PE"
    h = MemoryHistory()
    _days(h, DAY)
    h.add_option(KEY, _bars(DAY, ("09:20", (100, 112, 98, 100)), ("09:21", (100, 100, 100, 100)),
                            ("15:15", (100, 100, 100, 100))))  # fmt: skip
    h.add_option(put, _bars(DAY, ("09:20", (100, 112, 98, 100)), ("09:21", (100, 100, 100, 100)),
                            ("15:15", (100, 100, 100, 100))))  # fmt: skip
    config = parse({
        "kind": "rules",
        "entry": {"at": "09:20"},
        "holding": {"mode": "intraday", "exit": "15:15"},
        "legs": [
            {"id": "L1", "action": "BUY", "option_type": "CE"},
            {"id": "L2", "action": "BUY", "option_type": "PE"},
        ],
        "risk": {"mtm_target": 1000},
    })  # fmt: skip
    r = run(config, h)
    assert {t.reason for t in r.trades} == {"exit time 15:15"}  # (112-100) * 65 * 2 = 1560 was never really there


def _two_bought_legs(risk: dict[str, Any], ce: list[Any], pe: list[Any]) -> Any:
    put = f"NIFTY:{DAY:%Y-%m-%d}:25000:PE"
    h = MemoryHistory()
    _days(h, DAY)
    h.add_option(KEY, _bars(DAY, *ce))
    h.add_option(put, _bars(DAY, *pe))
    config = parse({
        "kind": "rules",
        "entry": {"at": "09:20"},
        "holding": {"mode": "intraday", "exit": "15:15"},
        "legs": [
            {"id": "L1", "action": "BUY", "option_type": "CE"},
            {"id": "L2", "action": "BUY", "option_type": "PE"},
        ],
        "risk": risk,
    })  # fmt: skip
    return run(config, h)


def test_the_profit_target_fills_at_the_target_even_when_the_price_jumps_far_past_it() -> None:
    """Both legs jump at once (a spike): the trade's profit is 13,000 at the next price, but the target was 6,000 and a
    standing order would have closed it there. The same rule the other platform follows: never more than the target."""
    flat100 = ("09:20", flat(100)), ("09:59", flat(100)), ("15:15", flat(100))
    r = _two_bought_legs(
        {"mtm_target": 6000},
        [*flat100[:2], ("10:00", flat(300)), flat100[2]],
        [*flat100[:2], ("10:00", flat(100)), flat100[2]],
    )
    assert {t.reason for t in r.trades} == {"strategy profit reached ₹6000"}
    assert abs(r.gross_pnl - 6000) <= 65 * 0.1  # at the target, to a tick
    assert all(t.exit_time == ts("10:00") for t in r.trades)


def test_the_loss_limit_fills_at_the_limit_when_the_price_gaps_past_it() -> None:
    flat100 = ("09:20", flat(100)), ("09:59", flat(100)), ("15:15", flat(100))
    r = _two_bought_legs(
        {"mtm_stop_loss": 2000},
        [*flat100[:2], ("10:00", flat(20)), flat100[2]],
        [*flat100[:2], ("10:00", flat(100)), flat100[2]],
    )  # the CE falls 80 points (5,200 loss) in one step
    assert {t.reason for t in r.trades} == {"strategy loss reached ₹2000"}
    assert abs(r.gross_pnl + 2000) <= 65 * 0.1


def test_locked_profit_exits_at_the_locked_amount_not_at_the_lower_price_it_gapped_to() -> None:
    flat100 = ("09:20", flat(100)), ("09:59", flat(100)), ("15:15", flat(100))
    r = _two_bought_legs(
        {"lock_profit": {"at": 1000, "lock": 500}},
        [*flat100[:2], ("10:00", flat(130)), ("10:01", flat(100)), flat100[2]],
        [*flat100[:2], ("10:00", flat(100)), ("10:01", flat(100)), flat100[2]],
    )  # +1,950 at 10:00 (the lock engages at 1,000), then back to 0 at 10:01
    assert {t.reason for t in r.trades} == {"profit fell to the locked ₹500"}
    assert abs(r.gross_pnl - 500) <= 65 * 0.1


def test_an_exit_on_a_price_that_is_hours_old_makes_the_day_not_a_full_day() -> None:
    """The option's candles stop at 12:08, the exit time comes at 15:15: the trade used to look like a normal trade
    that exited on time (at the 12:08 price). The report must say the day is not a full day."""
    h = MemoryHistory()
    _days(h, DAY)
    h.add_option(KEY, _bars(DAY, ("09:20", flat(100)), ("12:08", flat(130))))
    config = parse({
        "kind": "rules",
        "entry": {"at": "09:20"},
        "holding": {"mode": "intraday", "exit": "15:15"},
        "legs": [{"id": "L1", "action": "BUY", "option_type": "CE"}],
    })  # fmt: skip
    r = run(config, h)
    (t,) = r.trades
    assert t.exit_price == 130
    assert any("not full days" in w and "no prices after 12:08" in w and "" in w for w in r.warnings)


def test_an_entry_time_after_the_days_last_stored_minute_is_reported_not_silently_skipped() -> None:
    """The index's history ends at 15:29 (15:39 with the closing session), so an entry at 15:45 can never happen: the
    day log and a warning say so."""
    h = history({"09:20": flat(100), "15:15": flat(90)})
    config = parse({
        "kind": "rules",
        "entry": {"at": "15:45"},
        "holding": {"mode": "next_day", "exit": "09:30"},
        "legs": [{"id": "L1", "action": "BUY", "option_type": "CE"}],
    })  # fmt: skip
    r = run(config, h)
    assert r.trades == []
    assert "the entry time (15:45) is after this day's last stored minute (15:39)" in r.day_log[0]["why"][0]
    assert any("could not trade because the entry time is after the last stored minute" in w for w in r.warnings)


def test_session_bars_drop_after_hours_ticks_and_add_the_closing_session() -> None:
    """The index stops at 15:29; options trade 15:30-15:39 from 3 Aug 2026, so the index's close is held for those
    minutes. Ticks archived after the close (e.g. 19:30) are not market data."""
    from ae_core.backtest import session_bars

    def bars(d: date) -> list[Candle]:
        at = [time(9, 14), time(9, 15), time(15, 29), time(19, 30)]
        return [Candle(datetime.combine(d, t, tzinfo=IST), 100, 101, 99, 100.5) for t in at]

    new = session_bars(bars(date(2026, 10, 12)), date(2026, 10, 12))
    times = [b.ts.astimezone(IST).time() for b in new]
    assert times[:2] == [time(9, 15), time(15, 29)] and times[-1] == time(15, 39) and len(new) == 12
    assert {(b.open, b.close) for b in new[2:]} == {(100.5, 100.5)}
    old = session_bars(bars(date(2026, 7, 31)), date(2026, 7, 31))
    assert [b.ts.astimezone(IST).time() for b in old] == [time(9, 15), time(15, 29)]


def test_an_entry_in_the_closing_session_trades_on_the_options_prices() -> None:
    """15:35 is in the closing auction session (options only, from 3 Aug 2026): the index's 15:29 close stands in."""
    h = history({"15:35": flat(80), "15:38": flat(70)})
    config = parse({
        "kind": "time_based",
        "timing": {"entry": "15:35", "exit": "15:38", "days": ["TUE"]},
        "legs": [{"id": "L1", "action": "SELL", "option_type": "CE", "lots": 1}],
    })  # fmt: skip
    (t,) = run(config, h).trades
    assert (t.entry_price, t.exit_price, t.reason) == (80, 70, "exit time 15:38")


def test_a_held_trade_can_wait_some_minutes_after_the_open_before_it_is_judged() -> None:
    mon = DAY - timedelta(days=1)
    h = history({"15:00": flat(100)}, day=mon)
    h.add_spot("NIFTY", [Candle(ts("09:15") + timedelta(minutes=i), 25000, 25001, 24999, 25000) for i in range(375)])
    h.add_option(KEY, [Candle(ts("09:15"), *flat(150)), Candle(ts("09:20"), *flat(110))])  # a spike at the open
    config = parse({
        "kind": "rules",
        "entry": {"at": "15:00"},
        "holding": {"mode": "next_day", "exit": "09:40"},
        "legs": [{"id": "L1", "action": "SELL", "option_type": "CE", "stop_loss": {"unit": "percent", "value": 20}}],
    })  # fmt: skip
    kw = {"lot_size": 65, "strike_step": 50, "slippage_pct": 0.0, "costs": Costs(0, 0, 0, 0, 0, 0)}
    (now,) = simulate(config, h, mon, DAY, **kw).trades  # type: ignore[arg-type]
    assert (now.exit_time, now.exit_price, now.reason) == (ts("09:15"), 150, "stop-loss")
    r = simulate(config, h, mon, DAY, open_delay_min=10, **kw)  # type: ignore[arg-type]
    (later,) = r.trades
    assert (later.exit_time, later.exit_price, later.reason) == (ts("09:40"), 110, "exit time 09:40")
    assert any("first 10 minute" in w for w in r.warnings)
