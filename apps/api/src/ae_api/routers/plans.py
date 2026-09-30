from __future__ import annotations

from ae_db.repositories import PlanRepo
from fastapi import APIRouter

from ..deps import DbDep
from ..schemas import ERROR_RESPONSES, PlanOut

router = APIRouter(prefix="/v1", tags=["plans"], responses=ERROR_RESPONSES)


@router.get("/plans", response_model=list[PlanOut])
async def plans(db: DbDep) -> list[PlanOut]:
    """Public: the pricing page lists these without signing in."""
    async with db.system_session() as s:
        return [PlanOut.model_validate(p) for p in await PlanRepo(s).list_active()]
