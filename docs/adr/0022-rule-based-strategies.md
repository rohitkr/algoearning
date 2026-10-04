# 0022 Rule-based strategies: building blocks instead of fixed strategies

Extends 0010 (one typed strategy schema), runs on 0014 (engine) and 0017 (backtests).

**Context.** The product promise is that a user can build the strategy they trade without writing code. The builder
could only make one shape (`time_based`: legs entered at a clock time, exited the same day); everything else was a
fixed strategy with a few settings (`range_breakout`, `zero_dte`, `smc_scalp`). Common retail strategies were out of
reach: an overnight straddle by premium, an opening range breakout, an EMA/RSI or Supertrend entry, an expiry-day
straddle, stop-loss to cost after one leg is hit.

**Decision.** A new kind, `rules` (`ae_core.strategy.RulesConfig`), assembled from six blocks:

1. **Timing** - weekdays, days to the nearest expiry (0 = expiry day), entry window, entries per day.
2. **Signals** (1-4) - each is a condition group (all/any of up to 6 conditions) and its own legs, so the market can
   pick the direction (an upside breakout buys a call, a downside one a put). A condition is `left op right` on a
   timeframe (1-60 minute candles aligned to 09:15); an operand is a number, the index candle's OHLC, an indicator
   (EMA, SMA, RSI, MACD, Supertrend, Bollinger, ATR, ADX) or a level (opening range of N minutes, today's
   open/high/low, previous day's high/low/close). `above`/`below` hold while true; a cross is true only on the
   candle that crossed. A signal without conditions fires at the start time. Optional per-signal exit conditions.
3. **Legs** - the builder's `Leg`, unchanged: buy/sell, call/put, lots, expiry, strike (ATM +/- N or closest
   premium), stop-loss / target (premium or index, points or %), trailing stop, re-entries.
4. **Holding** - intraday (exit HH:MM), next trading day, N trading days, or the legs' expiry day. Stops apply the
   whole time, overnight included.
5. **Risk per trade** - ₹ stop-loss / target, a profit lock (once ₹X, keep ₹Y, optionally trailed), exit all legs or
   move the others' stops to cost when a leg stops out.
6. **One trade at a time.**

**Mechanics.** `ae_core.trading.rules_runner.RulesRunner` is a runner like the others, so the engine runs it live or
on paper and the backtester replays it with no extra code. Leg mechanics are shared with `time_based` in
`LegRunner`, so a leg behaves the same in both. Conditions and indicators are pure functions of the index candles
(`trading.conditions`, `trading.indicators`; Wilder smoothing as charting platforms do). Earlier sessions warm the
indicators up and give the previous day's levels (`Runner.prior_days`, from the longest indicator); backtests load
10 days before the start for this. Conditions are read once per completed 1-minute bar; when a signal fires the
runner asks the feed for its contracts and enters as soon as they are priced (up to 5 minutes, else it is skipped
and logged). Each entry records an `entry_signal` with the readings that fired it, shown with backtest results.

**Not yet.** Conditions on option premiums (e.g. "wait and trade": enter when a premium moves X% from 09:20),
strike adjustments, delta/IV strikes, VWAP (index candles have no volume). The editing UI comes next (phase 2: a
step-by-step builder, templates, the plain-English summary); until then rule strategies come from the presets and
show read-only in the builder. Backtests are limited by stored option history: a premium-based strike needs most of
the chain on that day, which the archived feed bars often lack until options history is backfilled.
