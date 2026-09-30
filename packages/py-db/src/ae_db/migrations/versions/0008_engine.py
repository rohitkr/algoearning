"""trading engine (phase 9): run state and P&L, users' risk settings, platform switches, instrument expiries

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-30 12:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from ae_db import rls
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_settings",
        sa.Column("key", sa.String(length=60), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_platform_settings_updated_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_platform_settings")),
        sa.UniqueConstraint("key", name=op.f("uq_platform_settings_key")),
    )
    op.create_table(
        "user_risk_settings",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("max_daily_loss", sa.Numeric(precision=16, scale=2), nullable=True),
        sa.Column("max_daily_profit", sa.Numeric(precision=16, scale=2), nullable=True),
        sa.Column("max_open_positions", sa.Integer(), nullable=True),
        sa.Column("max_trades_per_day", sa.Integer(), nullable=True),
        sa.Column("kill_switch", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_risk_settings_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_risk_settings")),
        sa.UniqueConstraint("user_id", name=op.f("uq_user_risk_settings_user_id")),
    )
    op.add_column(
        "instruments",
        sa.Column("expiries", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
    )
    op.add_column("strategy_runs", sa.Column("strategy_name", sa.String(length=120), server_default="", nullable=False))
    op.add_column("strategy_runs", sa.Column("kind", sa.String(length=40), server_default="time_based", nullable=False))
    op.add_column("strategy_runs", sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False))
    op.add_column("strategy_runs", sa.Column("multiplier", sa.Integer(), server_default="1", nullable=False))
    op.add_column(
        "strategy_runs",
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
    )
    op.add_column(
        "strategy_runs",
        sa.Column("unrealized_pnl", sa.Numeric(precision=16, scale=2), server_default="0", nullable=False),
    )
    op.add_column("strategy_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("strategy_runs", sa.Column("stop_reason", sa.Text(), nullable=True))
    for stmt in rls.system_only_sql("platform_settings") + rls.table_sql("user_risk_settings"):
        op.execute(stmt)


def downgrade() -> None:
    op.drop_column("strategy_runs", "stop_reason")
    op.drop_column("strategy_runs", "heartbeat_at")
    op.drop_column("strategy_runs", "unrealized_pnl")
    op.drop_column("strategy_runs", "state")
    op.drop_column("strategy_runs", "multiplier")
    op.drop_column("strategy_runs", "schema_version")
    op.drop_column("strategy_runs", "kind")
    op.drop_column("strategy_runs", "strategy_name")
    op.drop_column("instruments", "expiries")
    op.drop_table("user_risk_settings")
    op.drop_table("platform_settings")
