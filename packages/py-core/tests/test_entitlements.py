from datetime import UTC, datetime, timedelta

import pytest
from ae_core.entitlements import (
    FEATURES,
    Entitlements,
    InvalidFeatures,
    SubscriptionView,
    effective_subscription,
    validate_features,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
GRACE = timedelta(days=3)


def sub(code: str, status: str, end: datetime | None, rank: int = 10) -> SubscriptionView:
    return SubscriptionView(code, rank, status, end)


def test_features_are_normalised_and_checked() -> None:
    full = validate_features({"live_trading": True, "max_strategies": None})
    assert set(full) == set(FEATURES) and full["live_trading"] is True and full["max_strategies"] is None
    assert full["max_broker_accounts"] == FEATURES["max_broker_accounts"].default  # missing -> restrictive default
    for bad in (
        {"live_trade": True},
        {"live_trading": 1},
        {"max_strategies": -1},
        {"max_strategies": True},
        {"max_strategies": 2.5},
    ):
        with pytest.raises(InvalidFeatures):
            validate_features(bad)


@pytest.mark.parametrize(
    ("status", "end", "grants"),
    [
        ("active", NOW + timedelta(days=10), True),
        ("active", None, True),
        ("active", NOW - timedelta(days=5), False),  # webhook missed, well past grace
        ("cancelled", NOW + timedelta(days=1), True),  # paid until period end
        ("cancelled", NOW - timedelta(seconds=1), False),
        ("past_due", NOW - timedelta(days=2), True),  # within 3-day grace
        ("past_due", NOW - timedelta(days=4), False),
        ("pending", NOW + timedelta(days=30), False),  # first payment not confirmed
        ("expired", NOW + timedelta(days=30), False),
    ],
)
def test_which_subscriptions_grant_access(status: str, end: datetime | None, grants: bool) -> None:
    got = effective_subscription([sub("pro", status, end)], NOW, GRACE)
    assert (got is not None) is grants


def test_best_granting_plan_wins() -> None:
    subs = [
        sub("pro", "active", NOW + timedelta(days=5), rank=10),
        sub("pro_plus", "cancelled", NOW + timedelta(days=2), rank=20),
        sub("mega", "expired", NOW + timedelta(days=9), rank=99),
    ]
    assert effective_subscription(subs, NOW, GRACE).plan_code == "pro_plus"  # type: ignore[union-attr]
    assert effective_subscription([], NOW, GRACE) is None


def test_entitlement_checks() -> None:
    e = Entitlements("free", "Free", validate_features({"max_strategies": 2, "live_trading": False}))
    assert not e.allows("live_trading") and e.allows("paper_trading")
    assert e.within("max_strategies", used=1) and not e.within("max_strategies", used=2)
    unlimited = Entitlements("x", "X", validate_features({"max_strategies": None}))
    assert unlimited.limit("max_strategies") is None and unlimited.within("max_strategies", used=10_000)
    with pytest.raises(KeyError):
        e.allows("max_strategies")


def test_overrides_are_partial_and_win_over_the_plan() -> None:
    from ae_core.entitlements import effective_features, validate_overrides

    assert validate_overrides({"max_broker_accounts": 5}) == {"max_broker_accounts": 5}
    assert validate_overrides({}) == {}
    for bad in ({"max_brokers": 5}, {"max_broker_accounts": -1}, {"live_trading": "yes"}):
        with pytest.raises(InvalidFeatures):
            validate_overrides(bad)
    plan = validate_features({"max_broker_accounts": 1})
    eff = effective_features(plan, {"max_broker_accounts": None, "live_trading": True})
    assert eff["max_broker_accounts"] is None and eff["live_trading"] is True
    assert eff["max_strategies"] == plan["max_strategies"]
    e = Entitlements("free", "Free", eff, overrides={"max_broker_accounts": None})
    assert e.within("max_broker_accounts", 100)
