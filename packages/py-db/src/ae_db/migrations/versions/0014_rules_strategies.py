"""rule-based strategies (ADR 0022): saved time_based builder configs become `rules` configs

A time_based config (enter at timing.entry, exit at timing.exit the same day) is the intraday `rules` config with
the same legs and risk. Run and backtest snapshots keep the config they ran with (time_based still parses and runs).

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-04 14:30:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# the defaults of TimeBasedConfig.timing / risk at schema version 1, frozen here as migrations must not change later
UPGRADE = """
UPDATE strategies
   SET kind = 'rules',
       config = jsonb_build_object(
           'kind', 'rules',
           'underlying', coalesce(config->'underlying', '"NIFTY"'),
           'entry', jsonb_build_object(
               'mode', 'time',
               'at', coalesce(config#>'{timing,entry}', '"09:20"'),
               'until', NULL,
               'days', coalesce(config#>'{timing,days}', '["MON","TUE","WED","THU","FRI"]'),
               'dte', NULL),
           'holding', jsonb_build_object(
               'mode', 'intraday', 'exit', coalesce(config#>'{timing,exit}', '"15:15"'), 'days', 1),
           'legs', config->'legs',
           'risk', jsonb_build_object(
               'mtm_stop_loss', config#>'{risk,mtm_stop_loss}',
               'mtm_target', config#>'{risk,mtm_target}',
               'exit_all_on_leg_sl', coalesce(config#>'{risk,exit_all_on_leg_sl}', 'false'),
               'combined_stop', NULL,
               'lock_profit', NULL))
 WHERE config->>'kind' = 'time_based'
"""

DOWNGRADE = """
UPDATE strategies
   SET kind = 'time_based',
       config = jsonb_build_object(
           'kind', 'time_based',
           'underlying', config->'underlying',
           'timing', jsonb_build_object(
               'entry', config#>'{entry,at}', 'exit', config#>'{holding,exit}', 'days', config#>'{entry,days}'),
           'legs', config->'legs',
           'risk', jsonb_build_object(
               'mtm_stop_loss', config#>'{risk,mtm_stop_loss}',
               'mtm_target', config#>'{risk,mtm_target}',
               'exit_all_on_leg_sl', config#>'{risk,exit_all_on_leg_sl}'))
 WHERE config->>'kind' = 'rules' AND config#>>'{holding,mode}' = 'intraday'
"""


def upgrade() -> None:
    op.execute(sa.text(UPGRADE))


def downgrade() -> None:
    # only intraday rules have a time_based form; the rest stay rules (and need this revision's code to run)
    op.execute(sa.text(DOWNGRADE))
