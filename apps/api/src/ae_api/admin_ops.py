"""Admin operations shared by the Monitor API and the operator CLI."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from ae_db.enums import SubscriptionStatus
from ae_db.models import Plan, Subscription


def complimentary_subscription(user_id: uuid.UUID, plan: Plan, days: int) -> Subscription:
    """A plan for `days` without payment (team, testers, support cases). It stacks like any subscription: the
    best plan a user holds applies, and it simply expires at the end."""
    now = datetime.now(UTC)
    return Subscription(
        user_id=user_id,
        plan_id=plan.id,
        status=SubscriptionStatus.ACTIVE,
        provider="manual",
        current_period_start=now,
        current_period_end=now + timedelta(days=days),
    )
