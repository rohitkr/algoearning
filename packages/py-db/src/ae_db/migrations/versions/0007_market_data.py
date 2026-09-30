"""market data (phase 10): feed codes per instrument, and platform secrets (the daily Breeze session)

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-30 10:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from ae_db import rls
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Breeze stock codes, checked against Breeze's SecurityMaster and live API on 2026-09-27 (algo-trading-claude)
FEED = [
    ("NIFTY", "NSE", "NIFTY"),
    ("BANKNIFTY", "NSE", "CNXBAN"),
    ("FINNIFTY", "NSE", "NIFFIN"),
    ("MIDCPNIFTY", "NSE", "NIFSEL"),
    ("SENSEX", "BSE", "BSESEN"),
]


def upgrade() -> None:
    op.create_table(
        "platform_secrets",
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("value_enc", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_platform_secrets_updated_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_platform_secrets")),
        sa.UniqueConstraint("name", name=op.f("uq_platform_secrets_name")),
    )
    for stmt in rls.system_only_sql("platform_secrets"):
        op.execute(stmt)
    op.add_column("instruments", sa.Column("spot_exchange", sa.String(length=10), server_default="NSE", nullable=False))
    op.add_column("instruments", sa.Column("feed_code", sa.String(length=20), nullable=True))
    for code, exchange, feed in FEED:
        op.execute(
            sa.text("UPDATE instruments SET spot_exchange = :x, feed_code = :f WHERE code = :c").bindparams(
                x=exchange, f=feed, c=code
            )
        )


def downgrade() -> None:
    op.drop_column("instruments", "feed_code")
    op.drop_column("instruments", "spot_exchange")
    op.drop_table("platform_secrets")
