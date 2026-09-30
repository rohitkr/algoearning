# 0017 Backtesting: the live runners, replayed over stored history

**Decision.** A backtest replays a strategy's runner (`ae_core.trading.runners`, the same class the engine runs live)
over stored 1-minute history, so backtest and live behaviour cannot drift apart. `ae_core.backtest.simulate` is pure:
history goes in through the `History` interface, a result comes out.

- **History** is platform data in `history_candles` (key, minute, OHLC, volume; the price feed's keys, e.g.
  `NIFTY` and `NIFTY:2026-10-06:25000:CE`; system-only). It comes from a one-off import of algo-trading-claude's DuckDB
  store (`uv run --with duckdb python -m ae_worker import-history FILE`; today 318k index and 423k option candles:
  NIFTY index from 2023-09, options 2026-06 to 2026-09, SENSEX from 2026-04 / 2026-06) and then grows by itself: the
  worker archives every day's feed bars from Redis at 16:05 IST.
- **How a minute is replayed**, without look-ahead: at the minute's open the runner decides (entries, timed exits,
  index rules); then each open position's adverse extreme (a short's high, a long's low) lets stop-losses and MTM limits
  fire, filled at the stop level or the open if it gapped through; then the favourable extreme lets targets fire at
  their level. When a stop and a target were both reachable inside one minute, the stop is assumed first. A contract
  with no bar in a minute keeps its last known price.
- **Costs:** slippage (default 0.05%) plus approximate Indian options charges (brokerage, STT on sells, exchange, SEBI,
  GST, stamp). Results show gross, charges and net.
- **Honesty:** days with index data but no option prices cannot trade and are counted and warned about; quantities use
  today's lot size; positions still open at the end are closed at their last price and flagged.
- **Running:** users queue a backtest (plan flag `backtesting`, at most 3 pending); the worker takes them one at a time,
  replaying in a thread (a quarter of NIFTY takes seconds). The strategy's config is snapshotted with the run, and the
  result (summary, daily P&L, trades, warnings) is stored as JSON on the user's `backtest_runs` row (RLS).

**Not yet:** the walk-forward entry-time choice of `zero_dte` (it uses its earliest entry time, as live does), historical
lot sizes, holidays/expiry calendars beyond what the data contains, and index options other than NIFTY and SENSEX for
lack of stored data.
