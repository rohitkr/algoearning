"""Saved strategies, always scoped to the signed-in user. Phase 8 adds config validation, versioning rules,
plan limits and the builder; phase 3 establishes the ownership, pagination, error and audit patterns."""

from __future__ import annotations

import uuid
from datetime import timedelta

from ae_db.enums import StrategyStatus
from ae_db.models import Strategy
from ae_db.repositories import StrategyRepo
from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import audit
from ..deps import CurrentUser, UserSession
from ..entitlements import load_entitlements, require_within
from ..errors import AppError, NotFound
from ..schemas import ERROR_RESPONSES, DuplicateIn, Page, StrategyIn, StrategyOut, StrategyPatch
from ..settings import SettingsDep

router = APIRouter(prefix="/v1/strategies", tags=["strategies"], responses=ERROR_RESPONSES)


async def _own(repo: StrategyRepo, strategy_id: uuid.UUID) -> Strategy:
    obj = await repo.get(strategy_id)
    if obj is None:  # another user's id looks exactly like a missing one
        raise NotFound("strategy not found")
    return obj


async def _check_strategy_limit(s: AsyncSession, user_id: uuid.UUID, grace_days: int) -> None:
    ent = await load_entitlements(s, user_id, timedelta(days=grace_days))
    require_within(ent, "max_strategies", ent.usage["max_strategies"])


@router.get("", response_model=Page[StrategyOut])
async def list_strategies(
    user: CurrentUser, s: UserSession, cursor: str | None = None, limit: int = Query(20, ge=1, le=100)
) -> Page[StrategyOut]:
    try:
        page = await StrategyRepo(s, user.user_id).list(cursor, limit)
    except ValueError as exc:
        raise AppError(str(exc)) from exc
    return Page[StrategyOut](items=[StrategyOut.model_validate(x) for x in page.items], next_cursor=page.next_cursor)


@router.post("", response_model=StrategyOut, status_code=status.HTTP_201_CREATED)
async def create_strategy(
    body: StrategyIn, user: CurrentUser, s: UserSession, request: Request, settings: SettingsDep
) -> StrategyOut:
    await _check_strategy_limit(s, user.user_id, settings.subscription_grace_days)
    obj = await StrategyRepo(s, user.user_id).create(**body.model_dump())
    await audit(s, request, "strategy.create", user.user_id, "strategy", obj.id, name=obj.name)
    return StrategyOut.model_validate(obj)


@router.get("/{strategy_id}", response_model=StrategyOut)
async def get_strategy(strategy_id: uuid.UUID, user: CurrentUser, s: UserSession) -> StrategyOut:
    return StrategyOut.model_validate(await _own(StrategyRepo(s, user.user_id), strategy_id))


@router.patch("/{strategy_id}", response_model=StrategyOut)
async def update_strategy(
    strategy_id: uuid.UUID, body: StrategyPatch, user: CurrentUser, s: UserSession, request: Request
) -> StrategyOut:
    repo = StrategyRepo(s, user.user_id)
    obj = await _own(repo, strategy_id)
    changes = body.model_dump(exclude_unset=True)
    if "status" in changes:
        changes["status"] = StrategyStatus(changes["status"])
    if "config" in changes:
        changes["version"] = obj.version + 1
    obj = await repo.update(obj, **changes)
    await audit(s, request, "strategy.update", user.user_id, "strategy", obj.id, fields=sorted(changes))
    return StrategyOut.model_validate(obj)


@router.post("/{strategy_id}/duplicate", response_model=StrategyOut, status_code=status.HTTP_201_CREATED)
async def duplicate_strategy(
    strategy_id: uuid.UUID,
    body: DuplicateIn,
    user: CurrentUser,
    s: UserSession,
    request: Request,
    settings: SettingsDep,
) -> StrategyOut:
    repo = StrategyRepo(s, user.user_id)
    src = await _own(repo, strategy_id)
    await _check_strategy_limit(s, user.user_id, settings.subscription_grace_days)
    copy = await repo.duplicate(src, body.name)
    await audit(s, request, "strategy.duplicate", user.user_id, "strategy", copy.id, source=str(strategy_id))
    return StrategyOut.model_validate(copy)


@router.delete("/{strategy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_strategy(strategy_id: uuid.UUID, user: CurrentUser, s: UserSession, request: Request) -> Response:
    repo = StrategyRepo(s, user.user_id)
    obj = await _own(repo, strategy_id)
    await repo.delete(obj)
    await audit(s, request, "strategy.delete", user.user_id, "strategy", obj.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
