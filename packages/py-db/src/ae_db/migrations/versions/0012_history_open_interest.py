"""SMC options scalping (ADR 0018): open interest on stored option candles, for liquidity checks in backtests

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-01 06:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("history_candles", sa.Column("oi", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("history_candles", "oi")
