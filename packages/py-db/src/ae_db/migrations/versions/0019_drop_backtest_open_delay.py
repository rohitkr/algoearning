"""backtest runs: the open delay moved into the strategy's holding settings, so the column is gone

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-15 15:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("backtest_runs", "open_delay_min")


def downgrade() -> None:
    op.add_column("backtest_runs", sa.Column("open_delay_min", sa.Integer(), server_default="0", nullable=False))
