"""Replaying a tip exactly as given (ADR 0025): entry zone / chase buffer / not placed, stop and targets by minute."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta

from ae_core.backtest import Candle
from ae_core.tip_replay import ReplayParams, Tip, TipResult, replay, replay_tip, split_lots
from ae_core.trading.model import IST

DAY = date(2026, 10, 8)
T0 = datetime(2026, 10, 8, 10, 15, tzinfo=IST)  # the tip is posted at 10:15:20
POSTED = datetime(2026, 10, 8, 10, 15, 20, tzinfo=IST).astimezone(UTC)
KEY = "NIFTY:2026-10-13:22450:CE"
NOFEE = ReplayParams(slippage_pct=0)


class Prices:
    def __init__(self, bars: dict[str, dict[datetime, Candle]], expiries: list[date]) -> None:
        self.bars, self._exp = bars, expiries

    def expiries(self, underlying: str) -> list[date]:
        return self._exp

    def option(self, key: str, day: date) -> Mapping[datetime, Candle]:
        return self.bars.get(key, {})


def bars(
    *ohlc: tuple[float, float, float, float], start: datetime = T0 + timedelta(minutes=1)
) -> dict[datetime, Candle]:
    return {start + timedelta(minutes=i): Candle(start + timedelta(minutes=i), *x) for i, x in enumerate(ohlc)}


def tip(**kw: object) -> Tip:
    base: dict[str, object] = dict(id=1, date=POSTED, index="NIFTY", strike=22450, option_type="CE", action="BUY",
                                   entry_low=150, entry_high=154, stop_loss=135, targets=[169, 184, 204])  # fmt: skip
    return Tip(**{**base, **kw})  # type: ignore[arg-type]


def run(b: dict[datetime, Candle], t: Tip | None = None, params: ReplayParams = NOFEE) -> TipResult:
    return replay_tip(t or tip(), Prices({KEY: b}, [date(2026, 10, 13)]), params)


def test_in_zone_then_each_target_takes_a_third() -> None:
    r = run(bars((152, 160, 151, 158), (158, 170, 157, 168), (168, 186, 167, 185), (185, 205, 184, 204)))
    assert (r.entry, r.entry_price, r.qty) == ("IN_ZONE", 152, 3 * 75)
    assert [(e.reason, e.price, e.qty) for e in r.exits] == [("TARGET 1", 169, 75), ("TARGET 2", 184, 75),
                                                              ("TARGET 3", 204, 75)]  # fmt: skip
    assert r.gross == 75 * ((169 - 152) + (184 - 152) + (204 - 152))


def test_chased_within_the_buffer_is_bought_and_marked() -> None:
    r = run(bars((158, 160, 150, 152)))  # opens 4 above the range top (154), buffer 5
    assert (r.entry, r.above_zone, r.entry_price) == ("CHASED", 4, 158)


def test_beyond_the_buffer_nothing_is_placed_and_sensex_has_a_wider_buffer() -> None:
    r = run(bars((160, 170, 150, 152)))  # 6 above the top
    assert (r.entry, r.entry_price, r.exits, r.net) == ("NOT_PLACED", None, [], 0.0) and "not placed" in r.note
    key = "SENSEX:2026-10-13:72900:PE"
    t = tip(index="SENSEX", strike=72900, option_type="PE", entry_low=311, entry_high=315, stop_loss=291)
    sx = Prices({key: bars((324, 330, 320, 325))}, [date(2026, 10, 13)])  # 9 above, buffer 10
    assert replay_tip(t, sx, NOFEE).entry == "CHASED"
    sx = Prices({key: bars((326, 330, 320, 325))}, [date(2026, 10, 13)])  # 11 above
    assert replay_tip(t, sx, NOFEE).entry == "NOT_PLACED"


def test_the_stop_wins_a_minute_that_reached_both_and_gaps_fill_at_the_open() -> None:
    r = run(bars((152, 172, 134, 140)))  # target 1 and the stop inside one minute
    assert [(e.reason, e.price) for e in r.exits] == [("STOP LOSS", 135)]
    assert r.gross == 3 * 75 * (135 - 152)
    r = run(bars((152, 153, 150, 151), (130, 131, 128, 129)))  # gaps under the stop: filled at the open
    assert {e.price for e in r.exits} == {130}


def test_stop_after_a_target_only_closes_the_rest_and_the_day_ends_at_1515() -> None:
    r = run(
        bars((152, 170, 151, 168), (168, 169, 134, 136)), params=ReplayParams(slippage_pct=0, breakeven={"NIFTY": 999})
    )
    assert [(e.reason, e.price, e.qty) for e in r.exits] == [("TARGET 1", 169, 75), ("STOP LOSS", 135, 150)]
    quiet = {T0 + timedelta(minutes=i): Candle(T0 + timedelta(minutes=i), 152, 153, 151, 152) for i in range(1, 330)}
    r = run(quiet)
    assert {e.reason for e in r.exits} == {"END OF DAY"} and r.exits[0].time.hour == 15 and r.exits[0].time.minute == 15


def test_expiry_is_the_one_priced_like_the_tip_and_missing_data_is_flagged() -> None:
    near, far = "NIFTY:2026-10-08:22450:CE", "NIFTY:2026-10-13:22450:CE"
    p = Prices({near: bars((40, 45, 38, 42)), far: bars((152, 153, 150, 151))}, [date(2026, 10, 8), date(2026, 10, 13)])
    r = replay_tip(tip(), p, NOFEE)
    assert r.expiry == date(2026, 10, 13) and r.entry == "IN_ZONE"
    assert replay_tip(tip(), Prices({}, [date(2026, 10, 13)]), NOFEE).entry == "NO_DATA"
    assert replay_tip(tip(stop_loss=None), p, NOFEE).entry == "NO_DATA"
    assert (
        replay_tip(tip(), Prices({KEY: bars((135, 136, 130, 131))}, [date(2026, 10, 13)]), NOFEE).entry == "NOT_PLACED"
    )


def test_lots_split_charges_and_summary() -> None:
    assert split_lots(3, 3) == [1, 1, 1] and split_lots(1, 3) == [1, 0, 0] and split_lots(5, 3) == [2, 2, 1]
    res = replay([tip()], Prices({KEY: bars((152, 170, 151, 168), (168, 169, 134, 136))}, [date(2026, 10, 13)]))
    (r,) = res.results
    assert r.charges > 0 and r.net < r.gross
    s = res.summary()
    assert s["traded"] == 1 and s["entries"] == {"IN_ZONE": 1} and s["net_pnl"] == r.net
    assert res.daily()[0]["day"] == "2026-10-08"


def test_stop_moves_to_cost_after_10_points_then_trails_the_highest_price() -> None:
    # entry 152; minute 1 reaches 163 (+11): from minute 2 the stop is at cost (peak 163 - trail 10 = 153 > 152)
    r = run(bars((152, 163, 151, 160), (160, 161, 150, 152)))
    assert [(e.reason, e.price, e.qty) for e in r.exits] == [("TRAILING STOP", 153, 225)]
    assert (r.peak_price, r.peak_points, r.breakeven_time is not None, r.final_stop) == (163, 11, True, 153)
    # a pullback to exactly cost right after arming (trail wider than the trigger): the stop sits at cost
    wide = ReplayParams(slippage_pct=0, trail={"NIFTY": 30})
    r = run(bars((152, 163, 151, 160), (160, 161, 150, 152)), params=wide)
    assert [(e.reason, e.price) for e in r.exits] == [("STOP AT COST", 152)] and r.gross == 0
    # the trail only ever moves up: 152 -> 153 (cost, peak 163) -> 158 (peak 168 - 10)
    r = run(bars((152, 163, 151, 160), (160, 168, 158, 166), (166, 167, 157, 158)))
    assert [(e.reason, e.price) for e in r.exits] == [("TRAILING STOP", 158)] and r.trail_moves == 1


def test_target_3_books_everything_left_and_targets_split_a_third_each() -> None:
    r = run(bars((152, 170, 151, 168), (168, 186, 167, 185), (185, 205, 184, 204)), params=NOFEE)
    assert [(e.reason, e.qty) for e in r.exits] == [("TARGET 1", 75), ("TARGET 2", 75), ("TARGET 3", 75)]
    # T1 and T2 skipped by a gap straight to T3: the last target still books the whole position
    r = run(bars((152, 205, 151, 204)))
    assert [(e.reason, e.price, e.qty) for e in r.exits] == [
        ("TARGET 1", 169, 75),
        ("TARGET 2", 184, 75),
        ("TARGET 3", 204, 75),
    ]


def test_did_the_market_move_our_way_before_the_stop() -> None:
    straight_down = run(bars((152, 153, 140, 141), (141, 142, 130, 131)))
    assert straight_down.stopped and (straight_down.peak_points, straight_down.breakeven_time) == (1, None)
    went_up_first = run(bars((152, 156, 151, 155), (155, 156, 134, 136)))
    assert went_up_first.stopped and went_up_first.peak_points == 4  # up 4 (under the 10-point trigger), then the SL
    s = replay(
        [tip(), tip(id=2, date=POSTED + timedelta(hours=1))],
        Prices({KEY: bars((152, 153, 140, 141), (141, 142, 130, 131))}, [date(2026, 10, 13)]),
    ).summary()
    assert (
        s["stopped_out"] >= 1
        and s["stop_moved_to_cost"] == 0
        and s["stopped_never_moved_up"] == s["stopped_out"] - s["stopped_after_moving_up"]
    )
