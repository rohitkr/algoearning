"""data sync jobs: history downloads asked for from Monitor > Data sync, run by the worker, with their progress

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-14 10:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from ae_db import rls
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_sync_jobs",
        sa.Column("underlying", sa.String(length=20), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=10), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("progress", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.id"], name=op.f("fk_data_sync_jobs_requested_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_sync_jobs")),
    )
    op.create_index("ix_data_sync_jobs_created", "data_sync_jobs", ["created_at"])
    for stmt in rls.system_only_sql("data_sync_jobs"):
        op.execute(stmt)


def downgrade() -> None:
    op.drop_table("data_sync_jobs")
