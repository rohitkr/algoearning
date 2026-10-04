from ae_engine.__main__ import main


def test_engine_starts_and_exits_cleanly():
    assert main(["--once"]) == 0


def test_overnight_strategies_use_the_nrml_product() -> None:
    from ae_core.strategy import parse
    from ae_core.trading.runners import make_runner
    from ae_engine.engine import _product

    legs = [{"id": "L1", "action": "SELL", "option_type": "CE"}]
    held = parse({"kind": "rules", "holding": {"mode": "next_day", "exit": "09:30"}, "legs": legs})
    assert _product(make_runner(held)) == "NRML"
    assert _product(make_runner(parse({"kind": "rules", "legs": legs}))) == "MIS"
    assert _product(make_runner(parse({"kind": "range_breakout"}))) == "NRML"
    assert _product(None) == "MIS"
