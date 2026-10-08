"""signal sources (ADR 0025, phase A): a user's Telegram login and the chat it reads tips from, and the plan
features for them

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-08 15:00:00+00:00
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from ae_db import rls
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# the starter plans: everyone may read one tips channel; live trading on signals from the top plan
FEATURES = {
    "free": {"max_signal_sources": 1, "signal_trading": False},
    "pro": {"max_signal_sources": 1, "signal_trading": False},
    "pro_plus": {"max_signal_sources": 3, "signal_trading": True},
}


def upgrade() -> None:
    op.create_table(
        "signal_sources",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=True),
        sa.Column("api_id", sa.Integer(), nullable=True),
        sa.Column("api_hash_enc", sa.LargeBinary(), nullable=True),
        sa.Column("phone_enc", sa.LargeBinary(), nullable=True),
        sa.Column("session_enc", sa.LargeBinary(), nullable=True),
        sa.Column("login_state_enc", sa.LargeBinary(), nullable=True),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("status_detail", sa.String(length=300), nullable=True),
        sa.Column("flood_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("account_name", sa.String(length=120), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("chat_title", sa.String(length=200), nullable=True),
        sa.Column("chat_kind", sa.String(length=10), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_signal_sources_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signal_sources")),
    )
    op.create_index(op.f("ix_signal_sources_user_id"), "signal_sources", ["user_id"], unique=False)
    for stmt in rls.table_sql("signal_sources"):
        op.execute(stmt)
    for code, features in FEATURES.items():
        op.execute(
            sa.text("UPDATE plans SET features = features || CAST(:f AS jsonb) WHERE code = :code").bindparams(
                f=json.dumps(features), code=code
            )
        )


def downgrade() -> None:
    op.execute("UPDATE plans SET features = features - 'max_signal_sources' - 'signal_trading'")
    op.drop_index(op.f("ix_signal_sources_user_id"), table_name="signal_sources")
    op.drop_table("signal_sources")
