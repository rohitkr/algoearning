"""The signed-in user's plan, features and usage, and the checks that enforce them.

    ent = await load_entitlements(session, user_id, grace)
    require_within(ent, "max_strategies", used=await usage.strategies())
    require_feature(ent, "live_trading")

Errors carry machine-readable details so the UI can say exactly what to upgrade."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from ae_core.entitlements import FEATURES, FREE_PLAN, Entitlements, SubscriptionView, effective_subscription
from ae_db.repositories import PlanRepo, UsageRepo
from sqlalchemy.ext.asyncio import AsyncSession

from .errors import NotInPlan, PlanLimitReached, Unavailable


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
            raise Unavailable("the free plan is missing: run the database migrations")
        plan, status, end = free, None, None
    used = {
        "max_strategies": await usage.strategies(),
        "max_running_strategies": await usage.running_strategies(),
        "max_broker_accounts": await usage.broker_accounts(),
    }
    return Entitlements(plan.code, plan.name, dict(plan.features), status, end, used)


def require_feature(ent: Entitlements, feature: str) -> None:
    if not ent.allows(feature):
        raise NotInPlan(
            f"{FEATURES[feature].label} is not included in the {ent.plan_name} plan",
            {"feature": feature, "plan": ent.plan_code},
        )


def require_within(ent: Entitlements, feature: str, used: int, adding: int = 1) -> None:
    if not ent.within(feature, used, adding):
        limit = ent.limit(feature)
        raise PlanLimitReached(
            f"Your {ent.plan_name} plan allows {limit} ({FEATURES[feature].label.lower()}); you have {used}",
            {"feature": feature, "limit": limit, "used": used, "plan": ent.plan_code},
        )
