"""instruments as data (ADR 0011): lot size, strike step, expiry type and trading hours per underlying

Seeded with the values of 2026-09-30; the worker refreshes the exchange facts daily from Zerodha's instrument list.
F&O trading hours close at 15:40 (closing session).

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-30 08:10:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from ae_db import rls
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED = [  # code, name, exchange, lot size, strike step, weekly expiries
    ("NIFTY", "Nifty 50", "NFO", 65, 50, True),
    ("BANKNIFTY", "Nifty Bank", "NFO", 30, 100, False),
    ("FINNIFTY", "Nifty Financial Services", "NFO", 60, 50, False),
    ("MIDCPNIFTY", "Nifty Midcap Select", "NFO", 120, 25, False),
    ("SENSEX", "BSE Sensex", "BFO", 20, 100, True),
]


def upgrade() -> None:
    table = op.create_table(
        "instruments",
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("exchange", sa.String(length=10), nullable=False),
        sa.Column("lot_size", sa.Integer(), nullable=False),
        sa.Column("strike_step", sa.Integer(), nullable=False),
        sa.Column("weekly_expiry", sa.Boolean(), nullable=False),
        sa.Column("session_open", sa.String(length=5), nullable=False),
        sa.Column("session_close", sa.String(length=5), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instruments")),
        sa.UniqueConstraint("code", name=op.f("uq_instruments_code")),
    )
    op.bulk_insert(
        table,
        [
            {
                "code": c,
                "name": n,
                "exchange": x,
                "lot_size": lot,
                "strike_step": step,
                "weekly_expiry": weekly,
                "session_open": "09:15",
                "session_close": "15:40",
                "is_active": True,
                "source": "seed",
            }
            for c, n, x, lot, step, weekly in SEED
        ],
    )
    for stmt in rls.public_read_sql("instruments"):
        op.execute(stmt)


def downgrade() -> None:
    op.drop_table("instruments")
