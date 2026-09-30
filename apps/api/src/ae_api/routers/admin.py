"""Admin-only: manage the plan catalogue (prices, feature limits, availability) without a deploy.
Plans are never deleted (subscriptions reference them); set is_active=false to stop offering one."""

from __future__ import annotations

from typing import Annotated

from ae_core.entitlements import InvalidFeatures, validate_features
from ae_db.enums import PlanInterval
from ae_db.models import Plan
from ae_db.repositories import PlanRepo
from fastapi import APIRouter, Depends, Request, status

from ..audit import audit
from ..deps import CurrentUser, DbDep, Principal
from ..errors import AppError, Conflict, Forbidden, NotFound
from ..schemas import ERROR_RESPONSES, PlanAdminIn, PlanAdminOut, PlanAdminPatch


def require_admin(user: CurrentUser) -> Principal:
    if user.role != "admin":
        raise Forbidden("admin only")
    return user


Admin = Annotated[Principal, Depends(require_admin)]
router = APIRouter(prefix="/v1/admin", tags=["admin"], responses=ERROR_RESPONSES)


def _features(raw: dict[str, bool | int | None]) -> dict[str, bool | int | None]:
    try:
        return validate_features(raw)
    except InvalidFeatures as exc:
        raise AppError(str(exc), {"field": "features"}) from exc


@router.get("/plans", response_model=list[PlanAdminOut])
async def list_plans(_: Admin, db: DbDep) -> list[PlanAdminOut]:
    async with db.system_session() as s:
        return [PlanAdminOut.model_validate(p) for p in await PlanRepo(s).list_all()]


@router.post("/plans", response_model=PlanAdminOut, status_code=status.HTTP_201_CREATED)
async def create_plan(body: PlanAdminIn, admin: Admin, db: DbDep, request: Request) -> PlanAdminOut:
    async with db.system_session() as s:
        if await PlanRepo(s).by_code(body.code):
            raise Conflict(f"plan {body.code!r} already exists")
        plan = Plan(
            code=body.code,
            name=body.name,
            price_paise=body.price_paise,
            currency="INR",
            interval=PlanInterval(body.interval),
            features=_features(body.features),
            sort_order=body.sort_order,
            is_active=body.is_active,
        )
        s.add(plan)
        await s.flush()
        await s.refresh(plan)
        await audit(s, request, "plan.create", admin.user_id, "plan", plan.code, price_paise=plan.price_paise)
        return PlanAdminOut.model_validate(plan)


@router.patch("/plans/{code}", response_model=PlanAdminOut)
async def update_plan(code: str, body: PlanAdminPatch, admin: Admin, db: DbDep, request: Request) -> PlanAdminOut:
    changes = body.model_dump(exclude_unset=True)
    async with db.system_session() as s:
        plan = await PlanRepo(s).by_code(code)
        if plan is None:
            raise NotFound("plan not found")
        if code == "free" and changes.get("is_active") is False:
            raise AppError("the free plan cannot be deactivated (everyone falls back to it)")
        if "features" in changes:
            changes["features"] = _features(changes["features"])
        for k, v in changes.items():
            setattr(plan, k, v)
        await s.flush()
        await s.refresh(plan)
        await audit(s, request, "plan.update", admin.user_id, "plan", code, fields=sorted(changes))
        return PlanAdminOut.model_validate(plan)
