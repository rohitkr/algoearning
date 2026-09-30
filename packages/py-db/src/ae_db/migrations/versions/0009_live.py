"""live execution: dry-run runs, the admin's per-user live unlock, exchange freeze quantity per instrument

Freeze quantities are the exchanges' per-order limits as known on 2026-09-30 (verify; editable in Monitor).

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-30 13:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FREEZE = {"NIFTY": 1800, "BANKNIFTY": 900, "FINNIFTY": 1800, "MIDCPNIFTY": 2800, "SENSEX": 1000}


def upgrade() -> None:
    op.add_column("instruments", sa.Column("freeze_qty", sa.Integer(), server_default="1800", nullable=False))
    op.add_column("strategy_runs", sa.Column("dry_run", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("user_overrides", sa.Column("live_unlocked", sa.Boolean(), server_default="false", nullable=False))
    for code, qty in FREEZE.items():
        op.execute(sa.text("UPDATE instruments SET freeze_qty = :q WHERE code = :c").bindparams(q=qty, c=code))


def downgrade() -> None:
    op.drop_column("user_overrides", "live_unlocked")
    op.drop_column("strategy_runs", "dry_run")
    op.drop_column("instruments", "freeze_qty")
