"""Database access with the caller's identity attached to every transaction.

    db = Database(url)
    async with db.user_session(user_id) as s:   # ae_app role + app.user_id: RLS limits s to this user
        ...
    async with db.system_session() as s:        # ae_system role: trusted server paths only
        ...

Each context is one transaction (commit on success, rollback on error). The role and user id are
SET LOCAL, so they vanish at commit/rollback and a pooled connection can never leak one user's identity
into the next request.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .rls import APP_ROLE, SYSTEM_ROLE


def async_url(url: str) -> str:
    """postgresql://... or postgres://... -> postgresql+psycopg://... (psycopg 3 drives both sync and async)."""
    for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


class Database:
    def __init__(self, url: str, *, pool_size: int = 10, echo: bool = False) -> None:
        self.engine: AsyncEngine = create_async_engine(
            async_url(url), pool_size=pool_size, max_overflow=pool_size, pool_pre_ping=True, echo=echo
        )
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def user_session(self, user_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with self._sessions() as s, s.begin():
            await s.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))
            await s.execute(text("SELECT set_config('app.user_id', :uid, true)"), {"uid": str(user_id)})
            yield s

    @asynccontextmanager
    async def system_session(self) -> AsyncIterator[AsyncSession]:
        async with self._sessions() as s, s.begin():
            await s.execute(text(f"SET LOCAL ROLE {SYSTEM_ROLE}"))
            yield s

    async def ping(self) -> None:
        async with self.engine.connect() as c:
            await c.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        await self.engine.dispose()
