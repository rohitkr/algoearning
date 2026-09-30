from typing import Any

import pytest
from ae_core.strategy import (
    DEFAULT_INSTRUMENTS,
    PRESETS,
    SCHEMA_VERSION,
    Instrument,
    RangeBreakoutConfig,
    TimeBasedConfig,
    ZeroDteConfig,
    check,
    default_config,
    migrate,
    parse,
    parse_issues,
    plan_warnings,
)
from pydantic import ValidationError


def time_based(**over: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "kind": "time_based",
        "underlying": "NIFTY",
        "timing": {"entry": "09:20", "exit": "15:15", "days": ["MON", "TUE"]},
        "legs": [{"id": "L1", "action": "SELL", "option_type": "CE"}],
    }
    raw.update(over)
    return raw


def leg(**over: Any) -> dict[str, Any]:
    return {"id": "L1", "action": "SELL", "option_type": "CE", **over}


def locs(raw: dict[str, Any]) -> list[tuple[str | int, ...]]:
    return [i.loc for i in check(parse(raw), DEFAULT_INSTRUMENTS)]


def test_presets_and_default_are_valid_and_round_trip() -> None:
    assert len({p.id for p in PRESETS}) == len(PRESETS)
    for p in (*PRESETS, None):
        cfg = p.config if p else default_config()
        assert check(cfg, DEFAULT_INSTRUMENTS) == [], p and p.id
        assert parse(cfg.model_dump(mode="json")) == cfg  # what the API stores reads back identically


def test_kind_selects_the_model() -> None:
    assert isinstance(parse(time_based()), TimeBasedConfig)
    assert isinstance(parse({"kind": "range_breakout"}), RangeBreakoutConfig)
    assert isinstance(parse({"kind": "zero_dte", "lots": 2}), ZeroDteConfig)


@pytest.mark.parametrize(
    ("raw", "loc"),
    [
        ({"kind": "iron_fly"}, ()),
        (time_based(legs=[]), ("legs",)),
        (time_based(legs=[leg()] * 7), ("legs",)),
        (time_based(legs=[leg(lots=0)]), ("legs", 0, "lots")),
        (time_based(legs=[leg(stoploss={"value": 1})]), ("legs", 0, "stoploss")),  # typo: refused, not ignored
        (time_based(legs=[leg(strike={"offset": 21})]), ("legs", 0, "strike", "offset")),
        (time_based(timing={"entry": "9:20"}), ("timing", "entry")),
        (time_based(timing={"days": []}), ("timing", "days")),
        (time_based(underlying="DOW"), ("underlying",)),
    ],
)
def test_shape_errors_point_at_the_field(raw: dict[str, Any], loc: tuple[str | int, ...]) -> None:
    with pytest.raises(ValidationError) as exc:
        parse(raw)
    assert loc in [i.loc for i in parse_issues(exc.value)]


def test_cross_field_rules() -> None:
    assert locs(time_based(timing={"entry": "15:15", "exit": "09:20"})) == [("timing", "exit")]
    assert locs(time_based(timing={"entry": "08:00", "exit": "16:00"})) == [("timing", "entry"), ("timing", "exit")]
    assert locs(time_based(timing={"days": ["MON", "MON"]})) == [("timing", "days")]
    assert locs(time_based(legs=[leg(), leg()])) == [("legs", 1, "id")]
    assert locs(time_based(underlying="BANKNIFTY")) == [("legs", 0, "expiry")]
    assert locs(time_based(underlying="BANKNIFTY", legs=[leg(expiry="current_month")])) == []
    assert locs(time_based(legs=[leg(strike={"mode": "premium"})])) == [("legs", 0, "strike", "premium")]
    assert locs(time_based(legs=[leg(trailing={"trigger": 10, "step": 5})])) == [("legs", 0, "trailing")]
    assert locs(time_based(legs=[leg(reentry_on_sl={})])) == [("legs", 0, "reentry_on_sl")]
    assert locs(time_based(legs=[leg(reentry_on_target={})])) == [("legs", 0, "reentry_on_target")]
    assert locs(time_based(risk={"exit_all_on_leg_sl": True})) == [("risk", "exit_all_on_leg_sl")]


def test_percent_limits_follow_the_side() -> None:
    # a seller's stop-loss can be 200% (premium triples); a seller's target cannot exceed 100% (premium to zero)
    assert locs(time_based(legs=[leg(stop_loss={"value": 200})])) == []
    assert locs(time_based(legs=[leg(target={"value": 150})])) == [("legs", 0, "target", "value")]
    assert locs(time_based(legs=[leg(action="BUY", stop_loss={"value": 150})])) == [("legs", 0, "stop_loss", "value")]
    assert locs(time_based(legs=[leg(action="BUY", target={"value": 300})])) == []
    assert locs(time_based(legs=[leg(stop_loss={"value": 20, "basis": "underlying"})])) == [
        ("legs", 0, "stop_loss", "value")
    ]
    assert locs(time_based(legs=[leg(stop_loss={"value": 40, "unit": "points"})])) == []


def test_proven_strategies_rules() -> None:
    assert locs({"kind": "range_breakout", "underlying": "BANKNIFTY"}) == [("underlying",)]
    assert locs({"kind": "range_breakout", "itm_points": 120}) == [("itm_points",)]
    assert locs({"kind": "range_breakout", "hedge_width": 275}) == [("hedge_width",)]
    assert locs({"kind": "range_breakout", "range_end": "09:15"}) == [("range_end",)]
    assert locs({"kind": "range_breakout", "last_entry": "10:00"}) == [("last_entry",)]
    assert locs({"kind": "zero_dte", "first_entry": "15:00"}) == [("last_entry",)]
    assert locs({"kind": "zero_dte", "exit_time": "14:30"}) == [("exit_time",)]
    assert locs({"kind": "zero_dte", "underlying": "SENSEX"}) == []


def test_plan_warnings() -> None:
    cfg = parse(time_based(legs=[leg(lots=1), leg(id="L2", lots=3)]))
    assert [(w.loc, w.type) for w in plan_warnings(cfg, 2)] == [(("legs", 1, "lots"), "plan_limit")]
    assert plan_warnings(cfg, None) == []
    assert [w.loc for w in plan_warnings(parse({"kind": "zero_dte", "lots": 2}), 1)] == [("lots",)]


def test_instruments_and_migrate() -> None:
    assert DEFAULT_INSTRUMENTS["NIFTY"].weekly_expiry and not DEFAULT_INSTRUMENTS["BANKNIFTY"].weekly_expiry
    assert migrate(SCHEMA_VERSION, {"kind": "zero_dte"}) == {"kind": "zero_dte"}
    with pytest.raises(ValueError, match="schema version"):
        migrate(99, {})


def test_rules_follow_the_instruments_passed_in() -> None:
    """Lot sizes, expiry types and trading hours are data: the same config is judged by today's instruments."""
    cfg = parse(time_based(timing={"entry": "09:20", "exit": "15:40"}, underlying="SENSEX"))
    assert check(cfg, DEFAULT_INSTRUMENTS) == []  # F&O trades until 15:40
    early = {"SENSEX": Instrument("SENSEX", "BSE Sensex", "BFO", 20, 100, True, "09:15", "15:30")}
    assert [i.loc for i in check(cfg, early)] == [("timing", "exit")]
    monthly = {"SENSEX": Instrument("SENSEX", "BSE Sensex", "BFO", 20, 100, False)}
    assert [i.loc for i in check(cfg, monthly)] == [("legs", 0, "expiry")]
    assert [i.loc for i in check(cfg, {})] == [("underlying",)]
