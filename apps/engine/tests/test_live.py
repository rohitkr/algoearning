"""The live order path against a fake Kite: slicing, marketable limits and re-pricing, the basket margin check,
hedge-first entries, unwinding a failed entry, dry runs, and finding in-flight orders again after a restart."""

from __future__ import annotations

from datetime import date

import pytest
from ae_brokers.fake_kite import FakeKite, book
from ae_brokers.kite import KiteClient
from ae_core.trading.model import Contract, Intent
from ae_engine.live import Batch, LiveAccount, LiveConfig, recover_by_tag, round_to_tick, slice_quantity, tag_for

E = date(2026, 10, 6)
SHORT = Contract("NIFTY", E, 25000, "CE")
WING = Contract("NIFTY", E, 25300, "CE")
BOOK = book(("NIFTY", E, 25000, "CE", "NIFTY26O0625000CE"), ("NIFTY", E, 25300, "CE", "NIFTY26O0625300CE"))
CFG = LiveConfig(fill_timeout_s=0, max_reprices=2)


async def no_sleep(_: float) -> None:
    return None


def account(k: FakeKite, prices: dict[str, float], client: bool = True) -> LiveAccount:
    c = KiteClient("api-key", "access-token", transport=k.transport()) if client else None
    return LiveAccount(c, BOOK, CFG, prices.get, no_sleep)


def entry(c: Contract, side: str, qty: int = 130) -> Intent:
    return Intent("entry", side, c, qty // 65, qty, "entry", "L1")  # type: ignore[arg-type]


def test_tick_rounding_and_slicing() -> None:
    assert round_to_tick(100.02, 0.05, "BUY") == 100.05 and round_to_tick(100.02, 0.05, "SELL") == 100.0
    assert slice_quantity(1950, 65, 1800) == [1755, 195]
    with pytest.raises(ValueError):
        slice_quantity(100, 65, 1800)
    assert tag_for("6f1d6c6e-8c4e-4b0f-9e11-2f3a4b5c6d7e") == "ae6f1d6c6e8c4e4b0f9e"


async def test_hedge_first_entry_with_marketable_limits() -> None:
    k = FakeKite(prices={"NIFTY26O0625000CE": 100.0, "NIFTY26O0625300CE": 20.0})
    prices = {SHORT.key: 100.0, WING.key: 20.0}
    acct = account(k, prices)
    out = await acct.process(
        Batch(__import__("uuid").uuid4(), [entry(WING, "BUY"), entry(SHORT, "SELL")], "MIS", False, 1800)
    )
    assert [o.ok for o in out] == [True, True] and [o.price for o in out] == [20.0, 100.0]
    assert [x[1] for x in k.log if x[0] == "place"] == ["BUY", "SELL"]  # the hedge is bought first
    buy = next(o for o in k.orders.values() if o["transaction_type"] == "BUY")
    assert buy["price"] == 20.4 and buy["tag"].startswith("ae")  # LTP + 2%, on tick
    assert k.net == {"NIFTY26O0625300CE": 130, "NIFTY26O0625000CE": -130}


async def test_insufficient_margin_places_nothing() -> None:
    k = FakeKite(prices={"NIFTY26O0625000CE": 100.0}, funds=10_000, margin=50_000)
    out = await account(k, {SHORT.key: 100.0}).process(
        Batch(__import__("uuid").uuid4(), [entry(SHORT, "SELL")], "MIS", False, 1800)
    )
    assert not out[0].ok and "not enough margin" in out[0].message and k.log == []


async def test_a_failed_short_unwinds_its_hedge() -> None:
    k = FakeKite(prices={"NIFTY26O0625000CE": 100.0, "NIFTY26O0625300CE": 20.0}, reject={"NIFTY26O0625000CE"})
    out = await account(k, {SHORT.key: 100.0, WING.key: 20.0}).process(
        Batch(__import__("uuid").uuid4(), [entry(WING, "BUY"), entry(SHORT, "SELL")], "MIS", False, 1800)
    )
    assert [o.ok for o in out] == [False, False]
    assert "unwound" in out[0].message and "RMS" in out[1].message
    assert k.net.get("NIFTY26O0625300CE") == 0  # the wing was sold back: never left holding half a position


async def test_repricing_then_cancel() -> None:
    k = FakeKite(prices={"NIFTY26O0625000CE": 100.0}, never_fill={"NIFTY26O0625000CE"})
    (o,) = await account(k, {SHORT.key: 100.0}).process(
        Batch(__import__("uuid").uuid4(), [Intent("exit", "BUY", SHORT, 2, 130, "stop-loss", "L1")], "MIS", False, 1800)
    )
    assert not o.ok and "not filled after 2 re-prices" in o.message
    assert [x[0] for x in k.log] == ["place", "modify", "modify", "cancel"]


async def test_dry_run_sends_nothing_and_needs_no_login() -> None:
    k = FakeKite()
    (o,) = await account(k, {SHORT.key: 101.5}, client=False).process(
        Batch(__import__("uuid").uuid4(), [entry(SHORT, "SELL")], "MIS", True, 1800)
    )
    assert o.ok and o.dry_run and o.price == 101.5 and "would SELL 130 NIFTY26O0625000CE" in o.message
    assert k.log == []


async def test_no_session_refuses_with_a_reason() -> None:
    (o,) = await account(FakeKite(), {}, client=False).process(
        Batch(__import__("uuid").uuid4(), [entry(SHORT, "SELL")], "MIS", False, 1800)
    )
    assert not o.ok and "log in to Zerodha" in o.message


async def test_expired_token_is_reported() -> None:
    k = FakeKite(token="api-key:someone-else", prices={"NIFTY26O0625000CE": 100.0})
    acct = account(k, {SHORT.key: 100.0})
    (o,) = await acct.process(
        Batch(__import__("uuid").uuid4(), [Intent("exit", "BUY", SHORT, 2, 130, "x", "L1")], "MIS", False, 1800)
    )
    assert not o.ok and acct.session_error and "expired" in acct.session_error


async def test_recover_in_flight_order_by_tag() -> None:
    k = FakeKite(prices={"NIFTY26O0625000CE": 100.0})
    acct = account(k, {SHORT.key: 100.0})
    i = entry(SHORT, "SELL")
    await acct.process(Batch(__import__("uuid").uuid4(), [i], "MIS", False, 1800))
    assert acct.client is not None
    assert await recover_by_tag(acct.client, i) == (130, 100.0)
    other = entry(SHORT, "SELL")
    assert await recover_by_tag(acct.client, other) == (0, 0.0)
