"""strategy config schema version (phase 8)

Configs saved before phase 8 had no defined shape. They are reset to the builder's starting config (one leg,
kind time_based) as drafts, so every stored config parses with ae_core.strategy; the old JSON is kept in the
description so nothing is lost.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-30 06:49:29.685710+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ae_core.strategy.default_config() at schema version 1, frozen here as migrations must not change later
DEFAULT_CONFIG = """{
  "kind": "time_based", "underlying": "NIFTY",
  "timing": {"entry": "09:20", "exit": "15:15", "days": ["MON", "TUE", "WED", "THU", "FRI"]},
  "legs": [{"id": "L1", "action": "BUY", "option_type": "CE", "lots": 1, "expiry": "current_week",
            "strike": {"mode": "atm", "offset": 0, "premium": null}, "stop_loss": null, "target": null,
            "trailing": null, "reentry_on_sl": null, "reentry_on_target": null}],
  "risk": {"mtm_stop_loss": null, "mtm_target": null, "exit_all_on_leg_sl": false}
}"""


def upgrade() -> None:
    op.add_column("strategies", sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False))
    op.execute(
        sa.text(
            """
            UPDATE strategies
               SET description = concat_ws(E'\\n\\n', description, 'Config before phase 8: ' || config::text),
                   config = CAST(:cfg AS jsonb), kind = 'time_based', status = 'draft'
             WHERE coalesce(config->>'kind', '') NOT IN ('time_based', 'range_breakout', 'zero_dte')
            """
        ).bindparams(cfg=DEFAULT_CONFIG)
    )


def downgrade() -> None:
    op.drop_column("strategies", "schema_version")
