"""backtest runs: wait N minutes after each open before judging a trade held overnight

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-15 12:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("backtest_runs", sa.Column("open_delay_min", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("backtest_runs", "open_delay_min")
