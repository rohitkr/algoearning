"""a Kite price feed (ADR 0021): each index's Kite tradingsymbol, so the feed can find its instrument token

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-02 18:30:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# tradingsymbols of the indices in Zerodha's instrument lists (segment INDICES on NSE / BSE)
KITE = [
    ("NIFTY", "NIFTY 50"),
    ("BANKNIFTY", "NIFTY BANK"),
    ("FINNIFTY", "NIFTY FIN SERVICE"),
    ("MIDCPNIFTY", "NIFTY MID SELECT"),
    ("SENSEX", "SENSEX"),
]


def upgrade() -> None:
    op.add_column("instruments", sa.Column("kite_symbol", sa.String(length=40), nullable=True))
    for code, symbol in KITE:
        op.execute(sa.text("UPDATE instruments SET kite_symbol = :s WHERE code = :c").bindparams(s=symbol, c=code))


def downgrade() -> None:
    op.drop_column("instruments", "kite_symbol")
