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


# -- how old the prices behind a run's positions are (no database needed) ----------------------------------------------
def _fresh_world(now_hm: str, quotes: dict[str, str], state: dict | None = None):  # type: ignore[no-untyped-def]
    import uuid
    from datetime import datetime, time
    from types import SimpleNamespace

    from ae_core.trading.model import IST
    from ae_db.enums import TradingMode
    from ae_engine.engine import Engine, Inst

    day = datetime(2026, 10, 13, tzinfo=IST)  # a Tuesday
    at = lambda hm: datetime.combine(day.date(), time.fromisoformat(hm), tzinfo=IST)  # noqa: E731
    eng = Engine(None, None)  # type: ignore[arg-type]
    eng.tick_ts = {k: at(v) for k, v in quotes.items()}
    added: list = []
    s = SimpleNamespace(add=added.append)
    r = SimpleNamespace(user_id=uuid.uuid4(), id=uuid.uuid4(), state=state or {}, mode=TradingMode.PAPER,
                        dry_run=False, strategy_name="Fly")  # fmt: skip
    held = [SimpleNamespace(contract=SimpleNamespace(key=k)) for k in ("A", "B")]
    rn = SimpleNamespace(open_positions=lambda: held)
    inst = Inst("NIFTY", "NFO", 65, 50, [], session_open="09:15", session_close="15:40")
    return eng, s, r, rn, inst, at(now_hm), added


def test_trading_hours_are_the_instruments_and_weekdays_only() -> None:
    from datetime import datetime

    from ae_core.trading.model import IST
    from ae_engine.engine import Engine, Inst

    inst = Inst("NIFTY", "NFO", 65, 50, [], session_open="09:15", session_close="15:40")
    d = lambda y, m, day, h, mi: datetime(y, m, day, h, mi, tzinfo=IST)  # noqa: E731
    assert Engine.in_session(inst, d(2026, 10, 13, 15, 39))  # F&O trades until 15:40
    assert not Engine.in_session(inst, d(2026, 10, 13, 15, 41))
    assert not Engine.in_session(inst, d(2026, 10, 13, 9, 14))
    assert not Engine.in_session(inst, d(2026, 10, 17, 11, 0))  # a Saturday


def test_old_prices_during_the_session_raise_one_warning_and_a_notice_when_they_return() -> None:
    eng, s, r, rn, inst, now, added = _fresh_world("15:20", {"A": "15:10", "B": "15:08"})
    out = eng._freshness(s, r, rn, inst, now)  # no quote for 10 minutes while the market is open
    assert out["_prices_stale"] is True and out["_prices_at"].endswith("+05:30") and "15:08" in out["_prices_at"]
    warn = [x for x in added if x.__class__.__name__ == "TradeEvent" and x.event == "prices_stale"]
    assert len(warn) == 1 and "No live prices since 15:10" in warn[0].detail["message"]
    assert any(getattr(x, "event", "") == "prices_stale" and x.__class__.__name__ == "Notification" for x in added)
    r.state = out  # the engine saves it: the next step does not warn again
    added.clear()
    eng._freshness(s, r, rn, inst, now)
    assert not added
    eng.tick_ts = {k: now for k in ("A", "B")}  # prices return
    again = eng._freshness(s, r, rn, inst, now)
    assert again["_prices_stale"] is False
    assert [x.event for x in added if x.__class__.__name__ == "TradeEvent"] == ["prices_back"]


def test_no_warning_after_the_close_but_the_time_of_the_last_price_is_kept() -> None:
    eng, s, r, rn, inst, now, added = _fresh_world("16:10", {"A": "15:39", "B": "15:38"})
    out = eng._freshness(s, r, rn, inst, now)
    assert out["_prices_stale"] is False and not added
    assert "15:38" in out["_prices_at"]  # the P&L is as of the oldest quote


def test_the_index_price_from_1529_stays_valid_in_the_closing_session() -> None:
    from datetime import datetime

    from ae_core.trading.model import IST
    from ae_engine.engine import closing_price

    def at(h: int, m: int, d: int = 12) -> datetime:
        return datetime(2026, 10, d, h, m, 30, tzinfo=IST)

    assert closing_price(at(15, 29), at(15, 36)) and closing_price(at(15, 29), at(15, 39))
    assert not closing_price(at(15, 20), at(15, 36))  # an index that stopped earlier is stale
    assert not closing_price(at(15, 29), at(15, 45)) and not closing_price(at(15, 29, 9), at(15, 36))
