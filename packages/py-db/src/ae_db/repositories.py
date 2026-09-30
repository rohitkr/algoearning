"""Repositories: the only way services touch tables.

UserScopedRepo binds a user id at construction and adds `WHERE user_id = :me` to every statement, and sets
user_id itself on create (callers cannot pass another user's id). Together with row-level security (rls.py)
that is two independent layers keeping users apart.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar, Generic, TypeVar, cast

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .enums import RunStatus, UserStatus
from .models import AuditLog, BrokerAccount, Plan, Strategy, StrategyRun, Subscription, User
from .pagination import Page, clamp_limit, decode_cursor, encode_cursor

M = TypeVar("M", Strategy, BrokerAccount)

# Columns a caller may never set through create()/update().
PROTECTED = frozenset({"id", "user_id", "created_at", "updated_at", "deleted_at"})


class UserScopedRepo(Generic[M]):
    model: ClassVar[type[Any]]
    soft_delete: ClassVar[bool] = False

    def __init__(self, session: AsyncSession, user_id: uuid.UUID) -> None:
        self.s, self.user_id = session, user_id

    def _q(self) -> Select[Any]:
        q = select(self.model).where(self.model.user_id == self.user_id)
        if self.soft_delete:
            q = q.where(self.model.deleted_at.is_(None))
        return q

    async def get(self, id_: uuid.UUID) -> M | None:
        res = await self.s.execute(self._q().where(self.model.id == id_))
        return cast("M | None", res.scalar_one_or_none())

    async def list(self, cursor: str | None = None, limit: int | None = None) -> Page[M]:
        n = clamp_limit(limit)
        q = self._q().order_by(self.model.created_at.desc(), self.model.id.desc()).limit(n + 1)
        if cursor:
            ts, id_ = decode_cursor(cursor)
            q = q.where(or_(self.model.created_at < ts, and_(self.model.created_at == ts, self.model.id < id_)))
        rows: list[M] = list((await self.s.execute(q)).scalars())
        more = len(rows) > n
        rows = rows[:n]
        return Page(rows, encode_cursor(rows[-1].created_at, rows[-1].id) if more and rows else None)

    async def count(self) -> int:
        q = select(func.count()).select_from(self._q().subquery())
        return int((await self.s.execute(q)).scalar_one())

    async def create(self, **fields: Any) -> M:
        obj = cast("M", self.model(**_writable(fields), user_id=self.user_id))
        self.s.add(obj)
        await self.s.flush()
        await self.s.refresh(obj)
        return obj

    async def update(self, obj: M, **fields: Any) -> M:
        if obj.user_id != self.user_id:  # defence in depth: never write another user's row
            raise PermissionError("not your row")
        for k, v in _writable(fields).items():
            setattr(obj, k, v)
        await self.s.flush()
        await self.s.refresh(obj)
        return obj

    async def delete(self, obj: M) -> None:
        if obj.user_id != self.user_id:
            raise PermissionError("not your row")
        if self.soft_delete:
            setattr(obj, "deleted_at", datetime.now(UTC))  # noqa: B010  (only soft-delete models have it)
            await self.s.flush()
        else:
            await self.s.delete(obj)
            await self.s.flush()


def _writable(fields: dict[str, Any]) -> dict[str, Any]:
    bad = PROTECTED & fields.keys()
    if bad:
        raise ValueError(f"cannot set {sorted(bad)}")
    return fields


class StrategyRepo(UserScopedRepo[Strategy]):
    model = Strategy
    soft_delete = True

    async def duplicate(self, src: Strategy, name: str | None = None) -> Strategy:
        return await self.create(
            name=name or f"{src.name} (copy)",
            description=src.description,
            kind=src.kind,
            config=dict(src.config),
            status=src.status,
        )


class BrokerAccountRepo(UserScopedRepo[BrokerAccount]):
    model = BrokerAccount


class UserRepo:
    """Users are created/updated by the system on sign-in (auth provider -> our users table)."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get(self, id_: uuid.UUID) -> User | None:
        return await self.s.get(User, id_)

    async def by_auth_subject(self, subject: str) -> User | None:
        return (await self.s.execute(select(User).where(User.auth_subject == subject))).scalar_one_or_none()

    async def upsert_from_auth(
        self, subject: str, email: str, name: str | None = None, avatar_url: str | None = None
    ) -> User:
        email = email.strip().lower()
        user = await self.by_auth_subject(subject)
        if user is None:
            user = User(auth_subject=subject, email=email, name=name, avatar_url=avatar_url)
            self.s.add(user)
        else:
            user.email, user.name, user.avatar_url = email, name or user.name, avatar_url or user.avatar_url
        user.last_seen_at = datetime.now(UTC)
        await self.s.flush()
        await self.s.refresh(user)
        return user

    async def mark_deleted(self, subject: str) -> User | None:
        """The auth provider deleted the account: keep the row (trades/audit reference it) but lock it out."""
        user = await self.by_auth_subject(subject)
        if user is not None:
            user.status = UserStatus.DELETED
            await self.s.flush()
        return user

    @staticmethod
    def is_active(user: User) -> bool:
        return user.status == UserStatus.ACTIVE


class PlanRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def list_active(self) -> list[Plan]:
        q = select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order, Plan.price_paise)
        return list((await self.s.execute(q)).scalars())

    async def list_all(self) -> list[Plan]:
        return list((await self.s.execute(select(Plan).order_by(Plan.sort_order, Plan.price_paise))).scalars())

    async def by_code(self, code: str) -> Plan | None:
        return (await self.s.execute(select(Plan).where(Plan.code == code))).scalar_one_or_none()


RUNNING = (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.STOPPING)


class UsageRepo:
    """What a user currently uses, for plan limits (runs as that user: RLS applies)."""

    def __init__(self, session: AsyncSession, user_id: uuid.UUID) -> None:
        self.s, self.user_id = session, user_id

    async def _count(self, q: Select[Any]) -> int:
        return int((await self.s.execute(select(func.count()).select_from(q.subquery()))).scalar_one())

    async def strategies(self) -> int:
        return await self._count(
            select(Strategy.id).where(Strategy.user_id == self.user_id, Strategy.deleted_at.is_(None))
        )

    async def running_strategies(self) -> int:
        return await self._count(
            select(StrategyRun.id).where(StrategyRun.user_id == self.user_id, StrategyRun.status.in_(RUNNING))
        )

    async def broker_accounts(self) -> int:
        return await self._count(select(BrokerAccount.id).where(BrokerAccount.user_id == self.user_id))

    async def subscriptions(self) -> list[Subscription]:
        q = select(Subscription).where(Subscription.user_id == self.user_id)
        return list((await self.s.execute(q)).scalars())


class AuditRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def record(
        self,
        action: str,
        *,
        user_id: uuid.UUID | None,
        actor: str = "user",
        target_type: str | None = None,
        target_id: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
        request_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.s.add(
            AuditLog(
                action=action,
                user_id=user_id,
                actor=actor,
                target_type=target_type,
                target_id=target_id,
                ip=ip,
                user_agent=(user_agent or "")[:400] or None,
                request_id=request_id,
                detail=detail or {},
            )
        )
        await self.s.flush()
