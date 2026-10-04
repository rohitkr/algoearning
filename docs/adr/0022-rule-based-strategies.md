# 0022 Rule-based strategies: one builder for intraday and overnight trades

**Context.** The builder (`time_based`, ADR 0010) could only enter legs at one time and exit them the same day. Users
want to build, without code, the strategies Indian options traders actually run: a straddle sold at 15:00 and bought
back next morning (BTST), positional strangles a few days before expiry held to expiry day, a combined-premium stop,
profit locking, strikes chosen by premium or distance from the index; later opening-range breakouts and indicator
entries. Plan: [the no-code builder plan](https://claude.ai/code/artifact/d4afb036-58c1-492f-be12-44865a055526).

**Decision.** A new strategy kind `rules` (`ae_core.strategy.RulesConfig`) replaces `time_based` as the builder's
config; one runner (`RulesRunner`) runs it live, on paper and in backtests (ADR 0017). It is built in phases, each
approved separately: 1 schedule and holding, 2 conditions, breakouts and reference levels, 3 indicators on
the index, 4 templates and backtest improvements.

- **Entry** (`entry`): at a time on chosen weekdays, optionally only N trading days before the first leg's expiry
  (`dte`, 0 = expiry day), never after `until` (default: the exit time for intraday, no limit for positional).
- **Holding** (`holding`): `intraday` (exit the same day), `next_day`, `days` (N trading days later) or `expiry` (the
  first leg's expiry day), always at `exit`. The final exit time is fixed when the trade starts; if the engine was not
  running then (a holiday, a restart), the trade exits at its first step after it. Weekdays count as trading days;
  holidays are not known to the runner.
- **One trade at a time, at most one entry a day.** A trade carries its positions, re-entries and limits over the
  night; when it closes another may start the same day, so a 15:00 BTST sold on Monday exits Tuesday 09:30 and sells
  again Tuesday 15:00. A trade is not entered if a leg's option expires before its exit.
- **Strikes:** ATM +/- offset, premium closest to / at least / at most a price, or the strike nearest the index +/-
  points (positive = out of the money). "A 60-point straddle" is the call and put whose premiums are nearest ₹60.
- **Risk on the whole trade** (from entry to final exit, not the day or the run): MTM stop-loss and target, exit all on
  any leg's stop-loss, a combined-premium stop (the sold legs' premiums together rise X% or X points above their total
  at entry; re-entered legs are not counted), and profit locking (once profit reaches `at`, never give back below
  `lock`, raised by `trail_by` every `trail_every`). Each leg keeps its stop-loss, target, trailing and re-entries.
- **Orders** of a strategy that can hold overnight (`holds_overnight`: rules not intraday, range breakout not
  intraday-only) use the NRML product; the rest MIS, which the broker squares off before the close.
- **`time_based` is converted:** migration 0014 rewrites saved `time_based` configs as intraday `rules` with the same
  legs and limits; run and backtest snapshots keep what they ran with, and `time_based` still parses and runs (as
  those rules), so history stays readable.

**Phase 2: conditions** (`entry.mode = "signal"`). From `entry.at` until the last entry time, a trade starts when a
condition group holds: `when` enters the legs as written, `when_mirrored` the same legs with calls and puts swapped, so
one set of legs trades a breakout both ways (above the high: buy the call; below the low: buy the put). A group is
all/any of up to 6 conditions `left op right` on a timeframe (1-60 minute candles aligned to 09:15, built from the
1-minute bars the feed and the history store hold): `above`/`below` hold while true, a cross only on the candle that
crossed. Operands: a number, the index candle's open/high/low/close, or a level with an optional points offset: the
opening range of the first N minutes, today's open/high/low so far, the previous session's high/low/close, or the
price at a time (for "50 points above the 09:20 price"). `holding.exit_when` / `exit_when_mirrored` close a trade
early. `entry.max_entries` allows up to 10 trades a day, still one at a time. Conditions are read once per completed
1-minute bar (`trading.conditions`, pure); a signal waits up to 5 minutes for its contracts' prices, else it is
skipped and logged. Each entry notes an `entry_signal` (direction and the readings that fired it), shown with
backtest results; backtests load the 10 days before the start for the previous session's levels. Presets: opening
range breakout, previous day high/low breakout, momentum from 09:20.

**Not yet:** indicators on the index (phase 3), exchange holiday calendars, option-premium conditions. A plain-English
strategy writer was considered and not taken up for now.
