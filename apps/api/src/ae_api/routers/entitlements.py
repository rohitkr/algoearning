from __future__ import annotations

from datetime import timedelta

from ae_core.entitlements import FEATURES
from fastapi import APIRouter

from ..deps import CurrentUser, UserSession
from ..entitlements import load_entitlements
from ..schemas import ERROR_RESPONSES, EntitlementsOut, FeatureInfo, UsageItem
from ..settings import SettingsDep

router = APIRouter(prefix="/v1/me", tags=["account"], responses=ERROR_RESPONSES)


@router.get("/entitlements", response_model=EntitlementsOut)
async def my_entitlements(user: CurrentUser, s: UserSession, settings: SettingsDep) -> EntitlementsOut:
    ent = await load_entitlements(s, user.user_id, timedelta(days=settings.subscription_grace_days))
    return EntitlementsOut(
        plan_code=ent.plan_code,
        plan_name=ent.plan_name,
        subscription_status=ent.subscription_status,
        current_period_end=ent.current_period_end,
        features=dict(ent.features),
        usage={k: UsageItem(used=v, limit=ent.limit(k)) for k, v in ent.usage.items()},
        catalog=[FeatureInfo(key=f.key, kind=f.kind, label=f.label) for f in FEATURES.values()],
    )
