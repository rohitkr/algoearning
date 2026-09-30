"""Load a user's entitlements from the database: the best plan they hold (or free), their usage and an admin's
overrides. Shared by the API (plan limits on requests) and the engine (limits on orders)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from ae_core.entitlements import (
    FREE_PLAN,
    Entitlements,
    SubscriptionView,
    effective_features,
    effective_subscription,
)
from sqlalchemy.ext.asyncio import AsyncSession

from .repositories import PlanRepo, UsageRepo


class FreePlanMissing(LookupError):
    pass


async def load_entitlements(
    s: AsyncSession, user_id: uuid.UUID, grace: timedelta, now: datetime | None = None
) -> Entitlements:
    usage = UsageRepo(s, user_id)
    subs = await usage.subscriptions()
    views = {
        id(x): SubscriptionView(x.plan.code, x.plan.sort_order, x.status.value, x.current_period_end) for x in subs
    }
    best = effective_subscription(views.values(), now or datetime.now(UTC), grace)
    if best is not None:
        sub = next(x for x in subs if views[id(x)] is best)
        plan, status, end = sub.plan, sub.status.value, sub.current_period_end
    else:
        free = await PlanRepo(s).by_code(FREE_PLAN)
        if free is None:
            raise FreePlanMissing("the free plan is missing: run the database migrations")
        plan, status, end = free, None, None
    used = {
        "max_strategies": await usage.strategies(),
        "max_running_strategies": await usage.running_strategies(),
        "max_broker_accounts": await usage.broker_accounts(),
    }
    overrides = await usage.overrides()
    features = effective_features(plan.features, overrides)
    return Entitlements(plan.code, plan.name, features, status, end, used, overrides)
