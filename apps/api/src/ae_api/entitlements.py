"""The signed-in user's plan, features and usage, and the checks that enforce them.

    ent = await load_entitlements(session, user_id, grace)
    require_within(ent, "max_strategies", used=await usage.strategies())
    require_feature(ent, "live_trading")

Errors carry machine-readable details so the UI can say exactly what to upgrade."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from ae_core.entitlements import FEATURES, Entitlements
from ae_db.entitlements import FreePlanMissing
from ae_db.entitlements import load_entitlements as _load
from sqlalchemy.ext.asyncio import AsyncSession

from .errors import NotInPlan, PlanLimitReached, Unavailable


async def load_entitlements(
    s: AsyncSession, user_id: uuid.UUID, grace: timedelta, now: datetime | None = None
) -> Entitlements:
    try:
        return await _load(s, user_id, grace, now)
    except FreePlanMissing as exc:
        raise Unavailable(str(exc)) from exc


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
