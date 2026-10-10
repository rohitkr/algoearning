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

**Test before saving.** The strategy builder has a "Test before saving" panel: `POST /v1/backtests/preview` takes the
config exactly as on screen (saved or not), runs the same `ae_marketdata.replay.replay` the worker uses, and returns the
same result JSON, shown with the same view as a saved backtest's page. Nothing is stored or queued, so a parameter can
be changed and tried again at once. It runs in the API process (CPU-bound, in a thread), with no range or concurrency cap while the owner is the only user
(limits come when customers do), needs the `backtesting` plan flag and the same checks as saving. A strategy that trades Telegram tips cannot be backtested yet (see ADR 0025).

**Exits that must not be lost (fix).** A runner marks a trade as ended when it decides to close it, before the exit
orders are filled. Two paths could then lose the exits and leave legs open for good, which blocks every later entry of
a one-trade-at-a-time strategy: (1) the backtest probes each minute's high and low for stops and targets, and threw away
anything else a probe decided (the profit lock, the combined premium stop, "exit all on a leg's stop-loss", re-entries,
exit conditions) after the runner had already consumed it; (2) an exit refused for lack of a price was never retried.
Now: the probe runs on a snapshot and restores it when it decided something that is not a stop or a target (the
strategy-wide stops count as stops); a refused exit reopens the trade and is sent again after 30 s (live and
backtest); and as a last net the backtest closes anything left open after its trade ended, and never lets an intraday
trade sleep over, at the last known price, saying so in the warnings.

