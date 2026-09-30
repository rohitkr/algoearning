# 0010 Strategy configs: one typed, versioned schema shared by API and engine

**Decision.** A strategy's `config` (JSONB on `strategies`, owned by one user and protected by RLS like every
user table) has one definition: `ae_core.strategy`, Pydantic models that the API validates every save with and the
trading engine (phase 9) will read configs with, so the two can never disagree about what a field means. The web
app gets the same shapes as TypeScript through OpenAPI (`make types`).

**Kinds.** `config.kind` selects the model and is mirrored into the `kind` column for filtering:

- `time_based`: the builder. 1-6 option legs entered at a fixed time on chosen weekdays and exited at a fixed
  time. Each leg: BUY/SELL, CE/PE, lots, expiry (current/next week or month), strike (ATM +/- N strikes, or closest
  premium), optional stop-loss and target (points or %, on the premium or the index), trailing stop-loss, and
  re-entry after a stop-loss or target. Strategy-wide: MTM stop-loss and target in rupees, and exit-all when any
  leg's stop-loss hits.
- `range_breakout` and `zero_dte`: the two proven strategies of algo-trading-claude, with their backtested
  parameters (RangeBreakoutParams / ZeroDteParams) rather than legs, so phase 9 runs the ported state machines
  unchanged. Indicator-based entries wait for the live feed (phase 10).

**Validation** has two layers: model types and bounds (unknown fields are refused, so a typo fails loudly), then
`check()` for rules spanning fields (entry before exit, within market hours, weekly expiries only where the exchange
lists them, a trailing stop needs a stop-loss, % limits that can never trigger, ...). Errors carry the field path;
`POST /v1/strategies/validate` runs the same checks without saving, for the builder. Plan limits
(`max_lots_per_order`) are warnings when saving and are enforced when a strategy is deployed.

**Platform vs user data.** Everything in a config is the user's. The only platform-wide data is the instrument
catalogue (lot size, strike step, weekly or monthly expiries), because the exchange sets it; it lives in code next
to the schema and changes with a release.

**Versions.** `schema_version` records the config's shape; changing the shape means bumping `SCHEMA_VERSION` and
teaching `migrate()` to upgrade older configs (the engine reads through it). `version` counts edits of one strategy;
each run keeps the exact config it ran (`strategy_runs.config_snapshot`), so no separate history table.
Configs saved before phase 8 were reset to the builder's starting config as drafts by migration 0004, with the
old JSON kept in the description.
