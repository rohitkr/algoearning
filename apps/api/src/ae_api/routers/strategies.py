"""Saved strategies, always scoped to the signed-in user. Every save is validated with ae_core.strategy (the
same module the trading engine reads configs with, ADR 0010); the builder uses /catalog and /validate."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Any, Literal

from ae_core.strategy import (
    MAX_LEGS,
    MAX_LOTS,
    MAX_STRIKE_OFFSET,
    PRESETS,
    SCHEMA_VERSION,
    AnyConfig,
    Instrument,
    Instruments,
    Issue,
    check,
    parse,
    parse_issues,
    plan_warnings,
)
from ae_db.enums import RunStatus, StrategyStatus
from ae_db.models import Strategy, StrategyRun
from ae_db.repositories import InstrumentRepo, StrategyRepo
from fastapi import APIRouter, Query, Request, Response, status
from pydantic import ValidationError
from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import audit
from ..deps import CurrentUser, UserSession
from ..entitlements import load_entitlements, require_within
from ..errors import AppError, Invalid, NotFound
from ..schemas import (
    ERROR_RESPONSES,
    ConfigIssue,
    DuplicateIn,
    InstrumentOut,
    Page,
    PresetOut,
    StrategyCatalogOut,
    StrategyIn,
    StrategyLimits,
    StrategyOut,
    StrategyPatch,
    ValidateIn,
    ValidateOut,
)
from ..settings import SettingsDep

router = APIRouter(prefix="/v1/strategies", tags=["strategies"], responses=ERROR_RESPONSES)

StatusFilter = Literal["draft", "ready", "archived"]


async def _own(repo: StrategyRepo, strategy_id: uuid.UUID) -> Strategy:
    obj = await repo.get(strategy_id)
    if obj is None:  # another user's id looks exactly like a missing one
        raise NotFound("strategy not found")
    return obj


async def _check_strategy_limit(s: AsyncSession, user_id: uuid.UUID, grace_days: int) -> None:
    ent = await load_entitlements(s, user_id, timedelta(days=grace_days))
    require_within(ent, "max_strategies", ent.usage["max_strategies"])


async def _instruments(s: AsyncSession) -> dict[str, Instrument]:
    """Today's tradable instruments (database, refreshed daily: ADR 0011)."""
    return {
        r.code: Instrument(
            r.code, r.name, r.exchange, r.lot_size, r.strike_step, r.weekly_expiry, r.session_open, r.session_close
        )
        for r in await InstrumentRepo(s).list_active()
    }


def _issues(issues: list[Issue]) -> list[ConfigIssue]:
    return [ConfigIssue(loc=list(i.loc), msg=i.msg, type=i.type) for i in issues]


def _stored(config: AnyConfig, instruments: Instruments) -> dict[str, Any]:
    """The columns a config sets, after the rules that span fields pass (422 with the paths otherwise)."""
    issues = check(config, instruments)
    if issues:
        details = [{"loc": ["body", "config", *i.loc], "msg": i.msg, "type": i.type} for i in issues]
        raise Invalid(f"the strategy config is invalid: {issues[0].msg}", details)
    return {"config": config.model_dump(mode="json"), "kind": config.kind, "schema_version": SCHEMA_VERSION}


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@router.get("", response_model=Page[StrategyOut])
async def list_strategies(
    user: CurrentUser,
    s: UserSession,
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    status: Annotated[list[StatusFilter] | None, Query(description="Only these statuses (repeat it)")] = None,
    q: Annotated[str | None, Query(max_length=120, description="Name contains (case-insensitive)")] = None,
) -> Page[StrategyOut]:
    filters: list[ColumnElement[bool]] = []
    if status:
        filters.append(Strategy.status.in_([StrategyStatus(x) for x in status]))
    if q and q.strip():
        filters.append(Strategy.name.ilike(_like(q.strip()), escape="\\"))
    try:
        page = await StrategyRepo(s, user.user_id).list(cursor, limit, filters)
    except ValueError as exc:
        raise AppError(str(exc)) from exc
    return Page[StrategyOut](items=[StrategyOut.model_validate(x) for x in page.items], next_cursor=page.next_cursor)


@router.get("/catalog", response_model=StrategyCatalogOut)
async def strategy_catalog(user: CurrentUser, s: UserSession, settings: SettingsDep) -> StrategyCatalogOut:
    ent = await load_entitlements(s, user.user_id, timedelta(days=settings.subscription_grace_days))
    return StrategyCatalogOut(
        instruments=[InstrumentOut.model_validate(r) for r in await InstrumentRepo(s).list_active()],
        presets=[PresetOut(id=p.id, name=p.name, description=p.description, config=p.config) for p in PRESETS],
        limits=StrategyLimits(
            max_legs=MAX_LEGS,
            max_strike_offset=MAX_STRIKE_OFFSET,
            max_lots=MAX_LOTS,
            max_lots_per_order=ent.limit("max_lots_per_order"),
        ),
    )


@router.post("/validate", response_model=ValidateOut)
async def validate_config(body: ValidateIn, user: CurrentUser, s: UserSession, settings: SettingsDep) -> ValidateOut:
    """Check a config without saving it: field-level errors (block saving) and plan warnings (block deploying)."""
    try:
        config = parse(body.config)
    except ValidationError as exc:
        return ValidateOut(valid=False, errors=_issues(parse_issues(exc)), warnings=[])
    ent = await load_entitlements(s, user.user_id, timedelta(days=settings.subscription_grace_days))
    errors = check(config, await _instruments(s))
    warnings = plan_warnings(config, ent.limit("max_lots_per_order"))
    return ValidateOut(valid=not errors, errors=_issues(errors), warnings=_issues(warnings))


@router.post("", response_model=StrategyOut, status_code=status.HTTP_201_CREATED)
async def create_strategy(
    body: StrategyIn, user: CurrentUser, s: UserSession, request: Request, settings: SettingsDep
) -> StrategyOut:
    stored = _stored(body.config, await _instruments(s))
    await _check_strategy_limit(s, user.user_id, settings.subscription_grace_days)
    obj = await StrategyRepo(s, user.user_id).create(name=body.name, description=body.description, **stored)
    await audit(s, request, "strategy.create", user.user_id, "strategy", obj.id, name=obj.name, kind=obj.kind)
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
    changes = body.model_dump(exclude_unset=True, exclude={"config"})
    if "status" in changes:
        changes["status"] = StrategyStatus(changes["status"])
    if body.config is not None:
        changes |= _stored(body.config, await _instruments(s))
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
    # a deleted strategy stops trading: running deployments square off (the engine does it within seconds)
    for run in (await s.execute(select(StrategyRun).where(StrategyRun.strategy_id == obj.id))).scalars():
        if run.status == RunStatus.PENDING:
            run.status, run.stop_reason = RunStatus.STOPPED, "strategy deleted"
        elif run.status == RunStatus.RUNNING:
            run.status, run.stop_reason = RunStatus.STOPPING, "strategy deleted"
    await repo.delete(obj)
    await audit(s, request, "strategy.delete", user.user_id, "strategy", obj.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
