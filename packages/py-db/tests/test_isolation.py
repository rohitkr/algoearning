"""Users can never see or change each other's data: checked at the repository layer AND, independently,
by PostgreSQL row-level security (a raw query with no user filter still sees only the caller's rows)."""

from __future__ import annotations

import pytest
from ae_db.models import Plan, Strategy, User
from ae_db.repositories import AuditRepo, StrategyRepo, UserRepo
from ae_db.session import Database
from alembic import command
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, ProgrammingError

pytestmark = pytest.mark.usefixtures("clean_db")


async def two_users(db: Database) -> tuple[User, User]:
    async with db.system_session() as s:
        a = await UserRepo(s).upsert_from_auth("sub-a", "A@Example.com", "Alice")
        b = await UserRepo(s).upsert_from_auth("sub-b", "b@example.com", "Bob")
    return a, b


async def test_repository_scopes_every_read_and_write(db: Database) -> None:
    a, b = await two_users(db)
    async with db.user_session(a.id) as s:
        mine = await StrategyRepo(s, a.id).create(name="A straddle", config={"legs": []})
    async with db.user_session(b.id) as s:
        repo = StrategyRepo(s, b.id)
        await repo.create(name="B condor")
        assert await repo.get(mine.id) is None
        assert [x.name for x in (await repo.list()).items] == ["B condor"]
        with pytest.raises(PermissionError):
            await repo.update(mine, name="hacked")
        with pytest.raises(PermissionError):
            await repo.delete(mine)
        with pytest.raises(ValueError, match="user_id"):
            await repo.create(name="x", user_id=a.id)


async def test_row_level_security_hides_other_users_rows_even_without_a_filter(db: Database) -> None:
    a, b = await two_users(db)
    async with db.user_session(a.id) as s:
        await StrategyRepo(s, a.id).create(name="A1")
    async with db.user_session(b.id) as s:
        await StrategyRepo(s, b.id).create(name="B1")
        await StrategyRepo(s, b.id).create(name="B2")

    async with db.user_session(a.id) as s:
        # deliberately no WHERE user_id: the database filters anyway
        assert (await s.execute(select(func.count()).select_from(Strategy))).scalar_one() == 1
        assert (await s.execute(select(func.count()).select_from(User))).scalar_one() == 1
        res = await s.execute(update(Strategy).values(name="hacked"))  # tries to rename everything
        assert res.rowcount == 1  # only A's own row matched
    async with db.system_session() as s:
        names = sorted((await s.execute(select(Strategy.name))).scalars())
        assert names == ["B1", "B2", "hacked"]


async def test_row_level_security_rejects_writing_rows_for_another_user(db: Database) -> None:
    a, b = await two_users(db)
    with pytest.raises(DBAPIError, match="row-level security"):
        async with db.user_session(a.id) as s:
            s.add(Strategy(name="planted", user_id=b.id))
            await s.flush()


async def test_no_user_context_sees_nothing(db: Database) -> None:
    a, _ = await two_users(db)
    async with db.user_session(a.id) as s:
        await StrategyRepo(s, a.id).create(name="A1")
    async with db.engine.connect() as c, c.begin():
        await c.execute(text("SET LOCAL ROLE ae_app"))  # app role, but no app.user_id
        assert (await c.execute(text("SELECT count(*) FROM strategies"))).scalar_one() == 0


async def test_app_role_privileges(db: Database) -> None:
    a, _ = await two_users(db)
    async with db.user_session(a.id) as s:
        assert {p.code for p in (await s.execute(select(Plan))).scalars()} >= {"free"}  # plans: readable
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with db.user_session(a.id) as s:
            await s.execute(text("SELECT * FROM webhook_events"))  # system only
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with db.user_session(a.id) as s:
            await s.execute(text("UPDATE plans SET price_paise = 0"))  # read only
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with db.user_session(a.id) as s:
            await s.execute(text("DELETE FROM users"))  # system workflow only


async def test_audit_log_is_append_only_even_for_the_system(db: Database) -> None:
    a, _ = await two_users(db)
    async with db.user_session(a.id) as s:
        await AuditRepo(s).record("strategy.create", user_id=a.id, target_type="strategy", target_id="x")
    for stmt in ("UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log"):
        with pytest.raises(DBAPIError, match="append-only"):
            async with db.system_session() as s:
                await s.execute(text(stmt))


async def test_keyset_pagination_and_soft_delete(db: Database) -> None:
    a, _ = await two_users(db)
    async with db.user_session(a.id) as s:
        repo = StrategyRepo(s, a.id)
        for i in range(25):
            await repo.create(name=f"s{i:02d}")
        seen, cursor = [], None
        while True:
            page = await repo.list(cursor, 10)
            seen += [x.name for x in page.items]
            cursor = page.next_cursor
            if cursor is None:
                break
        assert len(seen) == 25 and len(set(seen)) == 25
        with pytest.raises(ValueError, match="cursor"):
            await repo.list("not-a-cursor", 10)
        first = (await repo.list(limit=1)).items[0]
        copy = await repo.duplicate(first)
        assert copy.name == f"{first.name} (copy)" and copy.id != first.id
        await repo.delete(first)
        assert await repo.get(first.id) is None and await repo.count() == 25  # 25 + copy - deleted


async def test_user_upsert_is_idempotent_and_normalises_email(db: Database) -> None:
    async with db.system_session() as s:
        u1 = await UserRepo(s).upsert_from_auth("sub-x", "  Mixed@Case.COM ")
        u2 = await UserRepo(s).upsert_from_auth("sub-x", "mixed@case.com", "Named")
    assert u1.id == u2.id and u2.email == "mixed@case.com" and u2.name == "Named"


def test_models_match_the_migrations(migrated_db: str) -> None:
    from conftest import _alembic

    command.check(_alembic())  # raises if the models changed without a migration


async def test_users_read_their_own_overrides_but_cannot_write_them(db: Database) -> None:
    from ae_db.models import UserOverride

    a, b = await two_users(db)
    async with db.system_session() as s:
        s.add(UserOverride(user_id=a.id, features={"max_broker_accounts": 5}))
        s.add(UserOverride(user_id=b.id, features={"live_trading": True}))
    async with db.user_session(a.id) as s:
        rows = (await s.execute(select(UserOverride.features))).scalars().all()
        assert rows == [{"max_broker_accounts": 5}]  # only their own
        with pytest.raises(ProgrammingError):  # no UPDATE privilege at all
            await s.execute(update(UserOverride).values(features={"max_broker_accounts": None}))
    async with db.user_session(a.id) as s:
        with pytest.raises(ProgrammingError):
            s.add(UserOverride(user_id=a.id, features={}))
            await s.flush()
