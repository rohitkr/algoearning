"""Monitor (the admin panel's API): users, their plans and limits, instruments, and what happened. Admin only;
every change is written to the audit log with actor=admin. Runs as the system role (sees every user)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from ae_core.entitlements import FEATURES, FREE_PLAN, InvalidFeatures, validate_overrides
from ae_db.enums import (
    BillingOrderStatus,
    BrokerAccountStatus,
    RunStatus,
    StrategyStatus,
    SubscriptionStatus,
    UserRole,
    UserStatus,
)
from ae_db.models import (
    AuditLog,
    BillingOrder,
    BrokerAccount,
    Instrument,
    Plan,
    PlatformSetting,
    Strategy,
    StrategyRun,
    Subscription,
    Trade,
    User,
    UserOverride,
)
from ae_db.pagination import clamp_limit, decode_cursor, encode_cursor
from ae_db.repositories import PlanRepo
from ae_marketdata.instruments import refresh_instruments
from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..admin_ops import complimentary_subscription
from ..audit import audit
from ..deps import DbDep
from ..entitlements import load_entitlements
from ..errors import AppError, NotFound, Unavailable
from ..schemas import (
    ERROR_RESPONSES,
    AdminBrokerAccount,
    AdminRunOut,
    AdminSubscription,
    AdminUserDetail,
    AdminUserPatch,
    AdminUserRow,
    AuditEntry,
    EngineStatus,
    EntitlementsOut,
    FeatureInfo,
    GrantIn,
    InstrumentAdminOut,
    InstrumentPatch,
    InstrumentRefreshOut,
    OverridesIn,
    Overview,
    Page,
    RecentPayment,
    TradingHaltIn,
    UsageItem,
)
from ..settings import SettingsDep
from .admin import Admin

RUN_ACTIVE = (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.STOPPING)
router = APIRouter(prefix="/v1/admin", tags=["admin"], responses=ERROR_RESPONSES)


def _grace(settings: SettingsDep) -> timedelta:
    return timedelta(days=settings.subscription_grace_days)


async def _counts(s: AsyncSession, model: Any, ids: list[uuid.UUID], *where: Any) -> dict[uuid.UUID, int]:
    q = select(model.user_id, func.count()).where(model.user_id.in_(ids), *where).group_by(model.user_id)
    return {uid: int(n) for uid, n in (await s.execute(q)).all()}


async def _rows(s: AsyncSession, users: list[User], grace: timedelta) -> list[AdminUserRow]:
    ids = [u.id for u in users]
    strategies = await _counts(s, Strategy, ids, Strategy.deleted_at.is_(None))
    brokers = await _counts(s, BrokerAccount, ids)
    overridden = set(
        (
            await s.execute(
                select(UserOverride.user_id).where(UserOverride.user_id.in_(ids), UserOverride.features != {})
            )
        )
        .scalars()
        .all()
    )
    out = []
    for u in users:
        ent = await load_entitlements(s, u.id, grace)
        out.append(
            AdminUserRow(
                id=u.id,
                email=u.email,
                name=u.name,
                avatar_url=u.avatar_url,
                role=u.role.value,
                status=u.status.value,
                plan_code=ent.plan_code,
                plan_name=ent.plan_name,
                has_overrides=u.id in overridden,
                strategies=strategies.get(u.id, 0),
                broker_accounts=brokers.get(u.id, 0),
                created_at=u.created_at,
                last_seen_at=u.last_seen_at,
            )
        )
    return out


async def _user(s: AsyncSession, user_id: uuid.UUID) -> User:
    user = await s.get(User, user_id)
    if user is None:
        raise NotFound("user not found")
    return user


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


async def _audit_entries(s: AsyncSession, q: Any) -> list[AuditEntry]:
    rows = (await s.execute(q.outerjoin(User, User.id == AuditLog.user_id).add_columns(User.email))).all()
    return [AuditEntry.model_validate(a).model_copy(update={"user_email": email}) for a, email in rows]


# -- overview --------------------------------------------------------------------------------------------------
@router.get("/overview", response_model=Overview)
async def overview(_: Admin, db: DbDep, settings: SettingsDep) -> Overview:
    now = datetime.now(UTC)
    week, month = now - timedelta(days=7), now - timedelta(days=30)

    async def n(q: Any) -> int:
        return int((await s.execute(select(func.count()).select_from(q.subquery()))).scalar_one())

    async with db.system_session() as s:
        live = User.status != UserStatus.DELETED
        paying = (  # holds a non-free plan that still grants access (paid or granted)
            select(Subscription.user_id)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(
                Subscription.status.in_(
                    [SubscriptionStatus.ACTIVE, SubscriptionStatus.CANCELLED, SubscriptionStatus.PAST_DUE]
                ),
                Subscription.current_period_end > now,
                Plan.code != FREE_PLAN,
            )
            .distinct()
        )
        revenue = (
            await s.execute(
                select(func.coalesce(func.sum(BillingOrder.amount_paise), 0)).where(
                    BillingOrder.status == BillingOrderStatus.PAID, BillingOrder.paid_at >= month
                )
            )
        ).scalar_one()
        paid = (
            await s.execute(
                select(BillingOrder, User.email)
                .join(User, User.id == BillingOrder.user_id)
                .where(BillingOrder.status == BillingOrderStatus.PAID)
                .order_by(BillingOrder.paid_at.desc())
                .limit(10)
            )
        ).all()
        recent = list((await s.execute(select(User).where(live).order_by(User.created_at.desc()).limit(5))).scalars())
        refreshed = (await s.execute(select(func.max(Instrument.refreshed_at)))).scalar_one()
        return Overview(
            users=await n(select(User.id).where(live)),
            active_users=await n(select(User.id).where(live, User.last_seen_at >= week)),
            new_users_7d=await n(select(User.id).where(live, User.created_at >= week)),
            suspended_users=await n(select(User.id).where(User.status == UserStatus.SUSPENDED)),
            paying_users=await n(paying),
            strategies=await n(select(Strategy.id).where(Strategy.deleted_at.is_(None))),
            ready_strategies=await n(
                select(Strategy.id).where(Strategy.deleted_at.is_(None), Strategy.status == StrategyStatus.READY)
            ),
            broker_accounts=await n(select(BrokerAccount.id)),
            connected_broker_accounts=await n(
                select(BrokerAccount.id).where(BrokerAccount.status == BrokerAccountStatus.CONNECTED)
            ),
            revenue_30d_paise=int(revenue),
            recent_payments=[
                RecentPayment(
                    order_id=o.provider_order_id,
                    user_email=email,
                    plan_name=o.plan.name,
                    amount_paise=o.amount_paise,
                    paid_at=o.paid_at,
                )
                for o, email in paid
            ],
            recent_users=await _rows(s, recent, _grace(settings)),
            instruments_refreshed_at=refreshed,
        )


# -- users -----------------------------------------------------------------------------------------------------
@router.get("/users", response_model=Page[AdminUserRow])
async def list_users(
    _: Admin,
    db: DbDep,
    settings: SettingsDep,
    q: Annotated[str | None, Query(max_length=200, description="Email or name contains")] = None,
    status_: Annotated[UserStatus | None, Query(alias="status")] = None,
    role: UserRole | None = None,
    cursor: str | None = None,
    limit: int = Query(25, ge=1, le=100),
) -> Page[AdminUserRow]:
    n = clamp_limit(limit)
    stmt = select(User).order_by(User.created_at.desc(), User.id.desc()).limit(n + 1)
    if q and q.strip():
        pat = _like(q.strip())
        stmt = stmt.where(or_(User.email.ilike(pat, escape="\\"), User.name.ilike(pat, escape="\\")))
    if status_:
        stmt = stmt.where(User.status == status_)
    if role:
        stmt = stmt.where(User.role == role)
    if cursor:
        try:
            ts, id_ = decode_cursor(cursor)
        except ValueError as exc:
            raise AppError(str(exc)) from exc
        stmt = stmt.where(or_(User.created_at < ts, and_(User.created_at == ts, User.id < id_)))
    async with db.system_session() as s:
        users = list((await s.execute(stmt)).scalars())
        more = len(users) > n
        users = users[:n]
        return Page[AdminUserRow](
            items=await _rows(s, users, _grace(settings)),
            next_cursor=encode_cursor(users[-1].created_at, users[-1].id) if more and users else None,
        )


async def _detail(s: AsyncSession, user: User, grace: timedelta) -> AdminUserDetail:
    ent = await load_entitlements(s, user.id, grace)
    ov = (await s.execute(select(UserOverride).where(UserOverride.user_id == user.id))).scalar_one_or_none()
    subs = (
        await s.execute(
            select(Subscription).where(Subscription.user_id == user.id).order_by(Subscription.created_at.desc())
        )
    ).scalars()
    brokers = (
        await s.execute(
            select(BrokerAccount).where(BrokerAccount.user_id == user.id).order_by(BrokerAccount.created_at)
        )
    ).scalars()
    activity = await _audit_entries(
        s, select(AuditLog).where(AuditLog.user_id == user.id).order_by(AuditLog.id.desc()).limit(20)
    )
    return AdminUserDetail(
        user=(await _rows(s, [user], grace))[0],
        entitlements=EntitlementsOut(
            plan_code=ent.plan_code,
            plan_name=ent.plan_name,
            subscription_status=ent.subscription_status,
            current_period_end=ent.current_period_end,
            features=dict(ent.features),
            overrides=dict(ent.overrides),
            usage={k: UsageItem(used=v, limit=ent.limit(k)) for k, v in ent.usage.items()},
            catalog=[FeatureInfo(key=f.key, kind=f.kind, label=f.label) for f in FEATURES.values()],
        ),
        override_note=ov.note if ov else None,
        subscriptions=[
            AdminSubscription(
                id=x.id,
                plan_code=x.plan.code,
                plan_name=x.plan.name,
                status=x.status.value,
                provider=x.provider,
                current_period_start=x.current_period_start,
                current_period_end=x.current_period_end,
                created_at=x.created_at,
            )
            for x in subs
        ],
        broker_accounts=[
            AdminBrokerAccount(
                id=b.id,
                broker=b.broker.value,
                client_id=b.client_id,
                label=b.label,
                status=b.status.value,
                engine_enabled=b.engine_enabled,
                last_login_at=b.last_login_at,
            )
            for b in brokers
        ],
        recent_activity=activity,
    )


@router.get("/users/{user_id}", response_model=AdminUserDetail)
async def get_user(user_id: uuid.UUID, _: Admin, db: DbDep, settings: SettingsDep) -> AdminUserDetail:
    async with db.system_session() as s:
        return await _detail(s, await _user(s, user_id), _grace(settings))


@router.patch("/users/{user_id}", response_model=AdminUserDetail)
async def update_user(
    user_id: uuid.UUID, body: AdminUserPatch, admin: Admin, db: DbDep, request: Request, settings: SettingsDep
) -> AdminUserDetail:
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if user_id == admin.user_id and changes:
        raise AppError("you cannot change your own role or status (ask another admin)")
    async with db.system_session() as s:
        user = await _user(s, user_id)
        if user.status == UserStatus.DELETED:
            raise AppError("the account was deleted by its owner")
        before = {"status": user.status.value, "role": user.role.value}
        if "status" in changes:
            user.status = UserStatus(changes["status"])
        if "role" in changes:
            user.role = UserRole(changes["role"])
        await s.flush()
        await audit(
            s, request, "admin.user.update", admin.user_id, "user", user.id, actor="admin", before=before, after=changes
        )
        return await _detail(s, user, _grace(settings))


@router.put("/users/{user_id}/overrides", response_model=AdminUserDetail)
async def set_overrides(
    user_id: uuid.UUID, body: OverridesIn, admin: Admin, db: DbDep, request: Request, settings: SettingsDep
) -> AdminUserDetail:
    try:
        features = validate_overrides(body.features)
    except InvalidFeatures as exc:
        raise AppError(str(exc), {"field": "features"}) from exc
    async with db.system_session() as s:
        user = await _user(s, user_id)
        ov = (await s.execute(select(UserOverride).where(UserOverride.user_id == user.id))).scalar_one_or_none()
        before = dict(ov.features) if ov else {}
        if ov is None:
            ov = UserOverride(user_id=user.id)
            s.add(ov)
        ov.features, ov.note, ov.updated_by = features, body.note, admin.user_id
        await s.flush()
        await audit(
            s,
            request,
            "admin.user.overrides",
            admin.user_id,
            "user",
            user.id,
            actor="admin",
            before=before,
            after=features,
        )
        return await _detail(s, user, _grace(settings))


@router.post("/users/{user_id}/grants", response_model=AdminUserDetail, status_code=status.HTTP_201_CREATED)
async def grant_plan(
    user_id: uuid.UUID, body: GrantIn, admin: Admin, db: DbDep, request: Request, settings: SettingsDep
) -> AdminUserDetail:
    async with db.system_session() as s:
        user = await _user(s, user_id)
        plan = await PlanRepo(s).by_code(body.plan_code)
        if plan is None:
            raise NotFound(f"no plan {body.plan_code!r}")
        sub = complimentary_subscription(user.id, plan, body.days)
        s.add(sub)
        await s.flush()
        await audit(
            s, request, "subscription.grant", admin.user_id, "subscription", sub.id, actor="admin",
            user=str(user.id), plan=plan.code, days=body.days,
        )  # fmt: skip
        return await _detail(s, user, _grace(settings))


@router.delete("/users/{user_id}/grants/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
async def end_grant(
    user_id: uuid.UUID, subscription_id: uuid.UUID, admin: Admin, db: DbDep, request: Request
) -> Response:
    """End a complimentary plan now (paid subscriptions are never ended here: refunds go through billing)."""
    async with db.system_session() as s:
        sub = await s.get(Subscription, subscription_id)
        if sub is None or sub.user_id != user_id:
            raise NotFound("subscription not found")
        if sub.provider != "manual":
            raise AppError("only granted plans can be ended here")
        sub.current_period_end = datetime.now(UTC)
        sub.status = SubscriptionStatus.EXPIRED  # no grace period: that is for failed renewals
        await s.flush()
        await audit(s, request, "subscription.end_grant", admin.user_id, "subscription", sub.id, actor="admin")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- instruments -----------------------------------------------------------------------------------------------
@router.get("/instruments", response_model=list[InstrumentAdminOut])
async def list_instruments(_: Admin, db: DbDep) -> list[InstrumentAdminOut]:
    async with db.system_session() as s:
        rows = (await s.execute(select(Instrument).order_by(Instrument.code))).scalars()
        return [InstrumentAdminOut.model_validate(r, from_attributes=True) for r in rows]


@router.patch("/instruments/{code}", response_model=InstrumentAdminOut)
async def update_instrument(
    code: str, body: InstrumentPatch, admin: Admin, db: DbDep, request: Request
) -> InstrumentAdminOut:
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    async with db.system_session() as s:
        row = (await s.execute(select(Instrument).where(Instrument.code == code))).scalar_one_or_none()
        if row is None:
            raise NotFound("instrument not found")
        before = {k: getattr(row, k) for k in changes}
        for k, v in changes.items():
            setattr(row, k, v)
        if row.session_open >= row.session_close:
            raise AppError("trading hours must open before they close")
        await s.flush()
        await s.refresh(row)
        await audit(
            s, request, "admin.instrument.update", admin.user_id, "instrument", code, actor="admin",
            before=before, after=changes,
        )  # fmt: skip
        return InstrumentAdminOut.model_validate(row, from_attributes=True)


@router.post("/instruments/refresh", response_model=InstrumentRefreshOut)
async def refresh_now(admin: Admin, db: DbDep, request: Request) -> InstrumentRefreshOut:
    """Refresh lot sizes etc. from the broker's instrument list now (the worker does it daily at 08:00 IST)."""
    try:
        r = await refresh_instruments(db)
    except Exception as exc:  # network or format problem at the broker: say so, keep the old values
        raise Unavailable(f"could not read the instrument list: {type(exc).__name__}") from exc
    async with db.system_session() as s:
        await audit(
            s, request, "admin.instrument.refresh", admin.user_id, "instrument", None, actor="admin",
            changed=sorted(r.changed), missing=r.missing,
        )  # fmt: skip
    return InstrumentRefreshOut(
        changed={c: {k: [a, b] for k, (a, b) in d.items()} for c, d in r.changed.items()},
        unchanged=r.unchanged,
        missing=r.missing,
    )


# -- audit log -------------------------------------------------------------------------------------------------
@router.get("/audit", response_model=Page[AuditEntry])
async def audit_log(
    _: Admin,
    db: DbDep,
    user_id: uuid.UUID | None = None,
    action: Annotated[str | None, Query(max_length=80, description="Action starts with, e.g. strategy.")] = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=100),
) -> Page[AuditEntry]:
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit + 1)
    if user_id:
        q = q.where(AuditLog.user_id == user_id)
    if action:
        q = q.where(AuditLog.action.startswith(action, autoescape=True))
    if cursor:
        if not cursor.isdigit():
            raise AppError("invalid cursor")
        q = q.where(AuditLog.id < int(cursor))
    async with db.system_session() as s:
        items = await _audit_entries(s, q)
    more = len(items) > limit
    items = items[:limit]
    return Page[AuditEntry](items=items, next_cursor=str(items[-1].id) if more and items else None)


# -- engine ----------------------------------------------------------------------------------------------------
HALT_KEY = "trading_halted"


async def _engine_status(s: AsyncSession) -> EngineStatus:
    row = (await s.execute(select(PlatformSetting).where(PlatformSetting.key == HALT_KEY))).scalar_one_or_none()
    value = row.value if row else None
    active = (
        await s.execute(select(func.count()).select_from(StrategyRun).where(StrategyRun.status.in_(RUN_ACTIVE)))
    ).scalar_one()
    beat = (await s.execute(select(func.max(StrategyRun.heartbeat_at)))).scalar_one()
    return EngineStatus(
        trading_halted=bool(value),
        halt_reason=value.get("reason") if isinstance(value, dict) else None,
        active_runs=int(active),
        last_heartbeat=beat,
    )


@router.get("/engine", response_model=EngineStatus)
async def engine_status(_: Admin, db: DbDep) -> EngineStatus:
    async with db.system_session() as s:
        return await _engine_status(s)


@router.put("/engine/halt", response_model=EngineStatus)
async def set_halt(body: TradingHaltIn, admin: Admin, db: DbDep, request: Request) -> EngineStatus:
    """Platform kill switch: squares off every running strategy of every user and blocks new entries until lifted."""
    async with db.system_session() as s:
        row = (await s.execute(select(PlatformSetting).where(PlatformSetting.key == HALT_KEY))).scalar_one_or_none()
        if row is None:
            row = PlatformSetting(key=HALT_KEY, value=False)
            s.add(row)
        row.value = {"reason": body.reason or "halted by an admin"} if body.halted else False
        row.updated_by = admin.user_id
        await s.flush()
        await audit(s, request, "admin.engine.halt", admin.user_id, "platform_setting", HALT_KEY, actor="admin",
                    halted=body.halted, reason=body.reason)  # fmt: skip
        return await _engine_status(s)


@router.get("/runs", response_model=list[AdminRunOut])
async def all_runs(
    _: Admin, db: DbDep, active: bool = True, limit: int = Query(100, ge=1, le=500)
) -> list[AdminRunOut]:
    from .runs import run_out

    q = (
        select(StrategyRun, User.email)
        .join(User, User.id == StrategyRun.user_id)
        .where(StrategyRun.status.in_(RUN_ACTIVE) if active else StrategyRun.status.not_in(RUN_ACTIVE))
        .order_by(StrategyRun.created_at.desc())
        .limit(limit)
    )
    async with db.system_session() as s:
        rows = (await s.execute(q)).all()
        opens = dict(
            (
                await s.execute(
                    select(Trade.run_id, func.count())
                    .where(Trade.run_id.in_([r.id for r, _ in rows]), Trade.status == "open")
                    .group_by(Trade.run_id)
                )
            ).all()
        )
        return [
            AdminRunOut(**run_out(r, int(opens.get(r.id, 0))).model_dump(), user_id=r.user_id, user_email=email)
            for r, email in rows
        ]
