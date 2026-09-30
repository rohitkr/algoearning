"""Shared test fixtures: a real PostgreSQL database (TEST_DATABASE_URL, default: the local `make db-up` one),
migrated from scratch once per run, and emptied before every test that asks for it.

Locally, DB tests are skipped with a clear reason if Postgres is not running; in CI (REQUIRE_DB=1) that is
an error instead, so a green CI always means the database tests really ran."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://algoearning:algoearning@localhost:5432/algoearning_test"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")


def pytest_configure(config: pytest.Config) -> None:
    """Tests must not depend on a developer's local .env (e.g. DEV_AUTH=true would leak into every Settings)."""
    from ae_api.settings import Settings

    Settings.model_config["env_file"] = None


ALEMBIC_INI = os.path.join(os.path.dirname(__file__), "packages/py-db/alembic.ini")
TABLES = (
    "audit_log",
    "trade_events",
    "orders",
    "trades",
    "strategy_runs",
    "strategies",
    "broker_sessions",
    "broker_accounts",
    "payments",
    "subscriptions",
    "webhook_events",
    "user_overrides",
    "user_risk_settings",
    "notification_settings",
    "notifications",
    "platform_settings",
    "users",
)


def _alembic() -> Config:
    cfg = Config(ALEMBIC_INI)
    cfg.attributes["url"] = TEST_DATABASE_URL
    return cfg


@pytest.fixture(scope="session")
def migrated_db() -> Iterator[str]:
    engine = create_engine(TEST_DATABASE_URL)
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:
        engine.dispose()
        if os.environ.get("REQUIRE_DB") == "1":
            raise
        pytest.skip(f"PostgreSQL not reachable at TEST_DATABASE_URL ({type(exc).__name__}); run `make db-up`")
    engine.dispose()
    cfg = _alembic()
    command.downgrade(cfg, "base")  # every run proves the migrations work from scratch, both ways
    command.upgrade(cfg, "head")
    yield TEST_DATABASE_URL


@pytest.fixture
def clean_db(migrated_db: str) -> Iterator[str]:
    engine = create_engine(migrated_db)
    with engine.begin() as c:
        c.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    engine.dispose()
    yield migrated_db


@pytest.fixture
async def db(clean_db: str) -> AsyncIterator[object]:
    from ae_db.session import Database

    database = Database(clean_db, pool_size=2)
    yield database
    await database.dispose()
