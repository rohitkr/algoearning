"""Trading on Telegram tips (ADR 0025): a bullish tip trades the up legs and a bearish one the down legs; stale,
already-closed and other sources' tips are skipped once, with the reason; an incomplete tip waits for its stop-loss;
the channel's SL hit closes our trade; the daily limit holds."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from typing import Any

from ae_core.strategy import DEFAULT_INSTRUMENTS, check, parse
from ae_core.trading.model import IST, Contract, Intent, Market, Tip
from ae_core.trading.runners import make_runner

DAY = date(2026, 10, 6)
EXPIRIES = [date(2026, 10, 6), date(2026, 10, 13)]
SOURCE = uuid.UUID("11111111-2222-3333-4444-555555555555")


def at(hm: str, secs: int = 0) -> datetime:
    return datetime.combine(DAY, time.fromisoformat(hm), tzinfo=IST) + timedelta(seconds=secs)


def config(**entry: Any) -> Any:
    return parse({
        "kind": "rules",
        "entry": {"mode": "tip", "at": "09:20", "until": "14:30", "source_id": str(SOURCE), "max_per_day": 2,
                  **entry},
        "legs": [
            {"id": "BULL", "action": "SELL", "option_type": "PE", "direction": "up", "stop_loss": {"value": 40}},
            {"id": "BEAR", "action": "SELL", "option_type": "CE", "direction": "down", "stop_loss": {"value": 40}},
        ],
    })  # fmt: skip


def tip(i: int, when: datetime, direction: str = "BULLISH", status: str = "OPEN", complete: bool = True,
        source: uuid.UUID = SOURCE) -> Tip:  # fmt: skip
    return Tip(
        i, str(source), when, direction, status, complete, f"BUY NIFTY 25000 {'CE' if direction == 'BULLISH' else 'PE'}"
    )  # type: ignore[arg-type]


class Sim:
    def __init__(self, **entry: Any) -> None:
        self.r = make_runner(config(**entry))
        self.prices = {Contract("NIFTY", DAY, k, r).key: 100.0 for k in range(24500, 25550, 50) for r in ("CE", "PE")}  # type: ignore[arg-type]
        self.tips: list[Tip] = []

    def step(self, now: datetime) -> list[Intent]:
        m = Market(now, "NIFTY", 25010.0, [], dict(self.prices), EXPIRIES, 65, 50, tips=list(self.tips))
        out = self.r.step(m)
        for i in out:
            self.r.fill(i, 100.0, now, m)
        return out

    def notes(self) -> list[tuple[str, Any]]:
        return [(n["event"], n.get("reason") or n.get("tip_id")) for n in self.r.notes]


def test_a_valid_tip_strategy() -> None:
    assert check(config(), DEFAULT_INSTRUMENTS) == []
    no_source = config(source_id=None)
    assert [i.loc for i in check(no_source, DEFAULT_INSTRUMENTS)] == [("entry", "source_id")]


def test_bullish_sells_the_put_and_bearish_the_call() -> None:
    sim = Sim()
    sim.tips = [tip(1, at("10:00"))]
    (entry,) = sim.step(at("10:00", 20))
    assert (entry.leg, entry.side, entry.contract.right, entry.contract.strike, entry.qty) == (
        "BULL",
        "SELL",
        "PE",
        25000,
        65,
    )
    assert entry.reason == "Telegram tip 1 (bullish)"
    assert sim.step(at("10:01")) == []  # one trade at a time

    sim = Sim()
    sim.tips = [tip(2, at("11:00"), "BEARISH")]
    (entry,) = sim.step(at("11:00", 5))
    assert (entry.leg, entry.contract.right) == ("BEAR", "CE")


def test_stale_closed_and_other_sources_tips_are_skipped_once() -> None:
    sim = Sim(max_tip_age_s=60)
    sim.tips = [tip(1, at("10:00")), tip(2, at("10:06"), status="SL_HIT"), tip(3, at("10:06"), source=uuid.uuid4())]
    assert sim.step(at("10:06", 30)) == []
    assert sim.notes() == [
        ("tip_skipped", "390s old (more than 60s)"),
        ("tip_skipped", "already SL_HIT on the channel"),
    ]
    sim.r.notes.clear()
    assert sim.step(at("10:07")) == [] and sim.notes() == []  # not again


def test_an_incomplete_tip_waits_for_its_stop_loss() -> None:
    sim = Sim()
    sim.tips = [tip(1, at("10:00"), complete=False)]
    assert sim.step(at("10:00", 3)) == []
    sim.tips = [tip(1, at("10:00"), complete=True)]  # the details reply arrived
    assert len(sim.step(at("10:00", 8))) == 1


def test_the_channels_sl_hit_closes_our_trade_and_the_day_allows_two() -> None:
    sim = Sim()
    sim.tips = [tip(1, at("10:00"))]
    sim.step(at("10:00", 10))
    sim.tips = [tip(1, at("10:00"), status="SL_HIT")]
    (exit_,) = sim.step(at("10:20"))
    assert (exit_.kind, exit_.reason) == ("exit", "tip closed: the channel's stop-loss hit")
    sim.tips += [tip(2, at("11:00"), "BEARISH")]
    assert [i.leg for i in sim.step(at("11:00", 10))] == ["BEAR"]  # the second trade of the day
    sim.tips[1] = tip(2, at("11:00"), "BEARISH", status="T3")
    assert sim.step(at("11:30"))[0].reason == "tip closed: the channel's target 3"
    sim.tips += [tip(3, at("12:00"))]
    assert sim.step(at("12:00", 10)) == []  # max 2 a day


def test_ignoring_the_channels_exit() -> None:
    sim = Sim(on_tip_exit="ignore")
    sim.tips = [tip(1, at("10:00"))]
    sim.step(at("10:00", 10))
    sim.tips = [tip(1, at("10:00"), status="SL_HIT")]
    assert sim.step(at("10:20")) == []  # our own stop-loss and exit time still apply
