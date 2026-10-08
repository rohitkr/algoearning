"""What a user's plan allows. Pure logic, no I/O: the API and the trading engine both use it, so a limit can
never be enforced in one place and forgotten in the other.

A plan's `features` (stored as JSON on the plan, editable by admins) is validated against FEATURES:
    flag   true / false                 e.g. live_trading
    limit  whole number >= 0, or null   e.g. max_strategies (null = unlimited)

Which plan applies (effective_subscription): the best plan among the user's subscriptions that currently grant
access; otherwise the free plan.
    active                                  -> yes
    cancelled, period not yet over          -> yes (paid until the end of the period)
    past_due, within the grace period       -> yes (renewal failed; payment is being retried)
    pending / expired / anything else       -> no
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

FREE_PLAN = "free"


@dataclass(frozen=True)
class Feature:
    key: str
    kind: Literal["flag", "limit"]
    label: str
    default: bool | int | None  # used when a plan does not mention the feature (the most restrictive value)


FEATURES: dict[str, Feature] = {
    f.key: f
    for f in (
        Feature("live_trading", "flag", "Live trading with real orders", False),
        Feature("paper_trading", "flag", "Paper trading on live prices", True),
        Feature("backtesting", "flag", "Backtesting", False),
        Feature("max_strategies", "limit", "Saved strategies", 3),
        Feature("max_running_strategies", "limit", "Strategies running at once", 1),
        Feature("max_broker_accounts", "limit", "Broker accounts", 1),
        Feature("max_lots_per_order", "limit", "Lots per order", 1),
        Feature("max_signal_sources", "limit", "Telegram signal sources", 1),
        Feature("signal_trading", "flag", "Live trading on Telegram signals", False),
    )
}


class InvalidFeatures(ValueError):
    pass


def validate_features(raw: Mapping[str, Any]) -> dict[str, bool | int | None]:
    """Normalise a plan's features: every known key present, types checked, unknown keys refused (a typo such
    as `live_trade: true` must fail loudly instead of silently granting nothing)."""
    unknown = set(raw) - FEATURES.keys()
    if unknown:
        raise InvalidFeatures(f"unknown features: {sorted(unknown)}")
    out: dict[str, bool | int | None] = {}
    for key, f in FEATURES.items():
        v = raw.get(key, f.default)
        if f.kind == "flag":
            if not isinstance(v, bool):
                raise InvalidFeatures(f"{key} must be true or false")
        elif v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
            raise InvalidFeatures(f"{key} must be a whole number >= 0, or null for unlimited")
        out[key] = v
    return out


def validate_overrides(raw: Mapping[str, Any]) -> dict[str, bool | int | None]:
    """A user's overrides: only the features given (the rest come from the plan), same types as a plan."""
    unknown = set(raw) - FEATURES.keys()
    if unknown:
        raise InvalidFeatures(f"unknown features: {sorted(unknown)}")
    full = validate_features({k: raw[k] for k in raw})
    return {k: full[k] for k in raw}


def effective_features(
    plan: Mapping[str, bool | int | None], overrides: Mapping[str, bool | int | None]
) -> dict[str, bool | int | None]:
    """What a user may do: their plan's features, with an admin's per-user overrides on top (e.g. a tester with
    more broker accounts than their plan). Overrides apply whatever the plan, and stay until an admin removes them."""
    return {**plan, **overrides}


@dataclass(frozen=True)
class SubscriptionView:
    """The fields of a subscription this module needs (decoupled from the ORM)."""

    plan_code: str
    plan_rank: int  # plans' sort_order: higher = better when several grant access
    status: str
    current_period_end: datetime | None


def grants_access(s: SubscriptionView, now: datetime, grace: timedelta) -> bool:
    end = s.current_period_end
    if s.status == "active":
        return end is None or now < end + grace
    if s.status == "cancelled":
        return end is not None and now < end
    if s.status == "past_due":
        return end is not None and now < end + grace
    return False


def effective_subscription(
    subs: Iterable[SubscriptionView], now: datetime, grace: timedelta = timedelta(days=3)
) -> SubscriptionView | None:
    live = [s for s in subs if grants_access(s, now, grace)]
    return max(live, key=lambda s: s.plan_rank) if live else None


@dataclass(frozen=True)
class Entitlements:
    plan_code: str
    plan_name: str
    features: Mapping[str, bool | int | None]
    subscription_status: str | None = None  # None: on the free plan
    current_period_end: datetime | None = None
    usage: Mapping[str, int] = field(default_factory=dict)
    overrides: Mapping[str, bool | int | None] = field(default_factory=dict)  # already merged into `features`

    def allows(self, feature: str) -> bool:
        f = FEATURES[feature]
        if f.kind != "flag":
            raise KeyError(f"{feature} is a limit, not a flag")
        return bool(self.features.get(feature, f.default))

    def limit(self, feature: str) -> int | None:
        f = FEATURES[feature]
        if f.kind != "limit":
            raise KeyError(f"{feature} is a flag, not a limit")
        v = self.features.get(feature, f.default)
        return None if v is None else int(v)

    def within(self, feature: str, used: int, adding: int = 1) -> bool:
        """Would `used + adding` stay within the limit?"""
        cap = self.limit(feature)
        return cap is None or used + adding <= cap
