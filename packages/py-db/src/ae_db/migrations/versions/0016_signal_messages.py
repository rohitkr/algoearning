"""signal messages (ADR 0025, phase B): every message read from a source's chat, the signals assembled from them,
users' corrections, and the reader's state on the source

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-08 18:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from ae_db import rls
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _owned(table: str) -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
    ]


def _keys(table: str) -> list[sa.Constraint]:
    return [
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f(f"fk_{table}_user_id_users"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_id"], ["signal_sources.id"], name=op.f(f"fk_{table}_source_id_signal_sources"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
    ]


def upgrade() -> None:
    op.add_column("signal_sources", sa.Column("profile", sa.String(40), server_default="vip_setups", nullable=False))
    op.add_column("signal_sources", sa.Column("reader_state", sa.String(20), server_default="off", nullable=False))
    op.add_column("signal_sources", sa.Column("reader_detail", sa.String(300), nullable=True))
    op.add_column("signal_sources", sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "signal_messages",
        *_owned("signal_messages"),
        sa.Column("msg_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edit_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("reply_to", sa.BigInteger(), nullable=True),
        sa.Column("has_media", sa.Boolean(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        *_keys("signal_messages"),
        sa.UniqueConstraint("source_id", "msg_id", name=op.f("uq_signal_messages_source_id_msg_id")),
    )
    op.create_index("ix_signal_messages_source_date", "signal_messages", ["source_id", "date"])
    op.create_index(op.f("ix_signal_messages_user_id"), "signal_messages", ["user_id"])

    op.create_table(
        "signals",
        *_owned("signals"),
        sa.Column("header_msg_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("index", sa.String(20), nullable=False),
        sa.Column("strike", sa.Integer(), nullable=False),
        sa.Column("option_type", sa.String(2), nullable=False),
        sa.Column("action", sa.String(4), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("entry_low", sa.Float(), nullable=False),
        sa.Column("entry_high", sa.Float(), nullable=False),
        sa.Column("stop_loss", sa.Float(), nullable=True),
        sa.Column("targets", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("targets_done", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rationale", sa.String(300), nullable=True),
        sa.Column("valid_for", sa.String(100), nullable=True),
        sa.Column("intraday", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("last_price", sa.Float(), nullable=True),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("message_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        *_keys("signals"),
        sa.UniqueConstraint("source_id", "header_msg_id", name=op.f("uq_signals_source_id_header_msg_id")),
    )
    op.create_index("ix_signals_source_date", "signals", ["source_id", "date"])
    op.create_index(op.f("ix_signals_user_id"), "signals", ["user_id"])

    op.create_table(
        "signal_overrides",
        *_owned("signal_overrides"),
        sa.Column("msg_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        *_keys("signal_overrides"),
        sa.UniqueConstraint("source_id", "msg_id", name=op.f("uq_signal_overrides_source_id_msg_id")),
    )
    op.create_index(op.f("ix_signal_overrides_user_id"), "signal_overrides", ["user_id"])

    for t in ("signal_messages", "signals", "signal_overrides"):
        for stmt in rls.table_sql(t):
            op.execute(stmt)
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {rls.APP_ROLE}, {rls.SYSTEM_ROLE}")


def downgrade() -> None:
    for t in ("signal_overrides", "signals", "signal_messages"):
        op.drop_table(t)
    for c in ("last_message_at", "reader_detail", "reader_state", "profile"):
        op.drop_column("signal_sources", c)
