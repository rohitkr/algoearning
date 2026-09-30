# 0014 The trading engine: one process, isolated runs, paper first

Implements the engine of 0003 on the feed of 0013.

**Decision.** A deployment of a strategy is a **run** (`strategy_runs`: mode, multiplier, the exact config it runs,
the runner's state, P&L, heartbeat). One engine process (`python -m ae_engine`) steps every active run of every
user once a second:

1. **Market view** from the price feed (Redis): the index's last price (ignored if older than 2 minutes, so nothing
   enters on stale data), today's 1-minute bars, the prices of the contracts the run holds or wants (the engine
   asks the feed to stream them), and the instrument's lot size, strike step and listed expiries (ADR 0011).
2. **Runner** (`ae_core.trading.runners`, pure and deterministic): `time_based` for builder strategies, and
   `range_breakout` / `zero_dte`, ported from algo-trading-claude's live strategies on its backtested rules
   (`ae_core.trading.rules`). It answers with order intents; its state is plain JSON saved on the run after every
   step, so restarts and positions held overnight continue exactly.
3. **Risk** (`ae_core.trading.risk`) before every entry, never before an exit: platform halt (Monitor), the user's
   kill switch, daily loss / profit limits across all their runs (breaching one squares everything off), max open
   positions, max entries per day, and the plan's lots per order. Deploying also checks the plan (paper/live flags,
   running strategies, lots per order) and that the strategy is ready and still valid.
4. **Execution**: the paper exchange fills at the feed's last price with slippage (0.05%, one tick minimum).
5. **Records**: each position is a `trades` row, each fill an `orders` row, each decision (entries, exits, skipped
   signals, refused orders, risk limits) a `trade_events` row. All user-owned, under row-level security.

**Isolation.** Each user's runs are stepped in their own transaction; an exception stops that run (status `error`,
with the reason) and never the others. A Redis lease makes a second engine copy stand by instead of trading twice.

**Live orders are not wired yet.** Deploying live is refused, and the engine refuses a live run it finds. The
algo-trading-claude strategy engine never sent real orders either; live execution (Zerodha order path: basket
margin, hedge first, freeze slicing, marketable limits, repricing, reconciliation) ships as its own step after
paper runs have been watched, behind the plan's live flag and a per-user unlock.

**Known simplifications.** `zero_dte` enters at `first_entry` (the walk-forward choice of the best recent entry time
needs a store of past option prices). `range_breakout` needs the feed to have run through the range window (at least
80% of its bars), otherwise the day is skipped and logged.
