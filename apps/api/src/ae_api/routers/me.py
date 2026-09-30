from __future__ import annotations

from ae_db.repositories import UserRepo
from fastapi import APIRouter

from ..deps import CurrentUser, UserSession
from ..errors import NotFound
from ..schemas import ERROR_RESPONSES, Me

router = APIRouter(prefix="/v1", tags=["account"], responses=ERROR_RESPONSES)


@router.get("/me", response_model=Me)
async def me(user: CurrentUser, s: UserSession) -> Me:
    row = await UserRepo(s).get(user.user_id)  # RLS: can only ever be the caller's own row
    if row is None:
        raise NotFound("user not found")
    return Me.model_validate(row)
