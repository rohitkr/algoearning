"""plan catalogue and full feature sets

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-30 01:53:07.778095+00:00
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Proposed starter catalogue (prices in paise, INR/month). Admins can change prices, limits and availability
# later through the admin API without a migration; this only seeds sensible defaults.
PLANS = [
    (
        "free",
        "Free",
        0,
        0,
        {
            "live_trading": False,
            "paper_trading": True,
            "backtesting": False,
            "max_strategies": 5,
            "max_running_strategies": 1,
            "max_broker_accounts": 1,
            "max_lots_per_order": 10,
        },
    ),
    (
        "pro",
        "Pro",
        99_900,
        10,
        {
            "live_trading": True,
            "paper_trading": True,
            "backtesting": False,
            "max_strategies": 25,
            "max_running_strategies": 3,
            "max_broker_accounts": 2,
            "max_lots_per_order": 20,
        },
    ),
    (
        "pro_plus",
        "Pro+",
        249_900,
        20,
        {
            "live_trading": True,
            "paper_trading": True,
            "backtesting": True,
            "max_strategies": None,
            "max_running_strategies": 10,
            "max_broker_accounts": 5,
            "max_lots_per_order": 50,
        },
    ),
]


def upgrade() -> None:
    for code, name, price, rank, features in PLANS:
        op.execute(
            sa.text(
                "INSERT INTO plans (code, name, price_paise, currency, interval, features, is_active, sort_order) "
                "VALUES (:code, :name, :price, 'INR', 'month', CAST(:features AS jsonb), true, :rank) "
                "ON CONFLICT (code) DO UPDATE SET features = EXCLUDED.features, sort_order = EXCLUDED.sort_order"
            ).bindparams(code=code, name=name, price=price, rank=rank, features=json.dumps(features))
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM plans WHERE code IN ('pro', 'pro_plus') AND NOT EXISTS "
        "(SELECT 1 FROM subscriptions s WHERE s.plan_id = plans.id)"
    )
