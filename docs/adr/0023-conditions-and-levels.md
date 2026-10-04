# 0023 Conditions, levels and signals: entering and exiting on what the index does

**Context.** ADR 0022 let the builder hold trades overnight but still enter only at a clock time. Traders also enter on
what the index does: an opening-range breakout, a break of yesterday's high or low. This is phase 2 of the plan; indicators
(phase 3) build on the same pieces.

**Decision.** A `rules` config can enter on conditions (`entry.mode = "conditions"`) and exit on them
(`exit.when`, `exit.on_opposite_signal`). All of it is data in the config and runs in `RulesRunner`, so live, paper and
backtests behave the same (ADR 0017); the logic is pure functions in `ae_core/trading/conditions.py`.

- **Condition:** `left` `op` `right` on finished candles of 1, 3, 5, 15, 30 or 60 minutes. Operands are the candle's
  close (`price`), a **level** or a number. Ops: `crosses_above`, `crosses_below` (the previous candle was on the other
  side, so it fires once, on the candle that crossed), `above`, `below` (true as of the newest candle).
- **Levels:** high / low of the first N minutes (opening range), today's open, the day's high / low so far (bars before
  the candle, so a close above it is a new high), and the previous session's high / low / close (the runner asks the
  engine and the backtester for one earlier session, `prior_days`).
- **Candles** are built from 1-minute bars counted from the session's first bar, and only finished candles count, so a
  decision never uses data from inside a forming candle. A candle older than its size plus a minute is ignored (feed
  gap, engine just started), so a restart cannot trade on an old signal.
- **Signals:** up to 4, each a direction (`up`, `down`, `always`) and a group of up to 6 conditions combined with ALL or
  ANY. Between `at` and `until` the first signal that is true starts a trade. Legs have a `direction`: `always` legs
  trade on every signal, `up` / `down` legs only on that signal, so one strategy buys a call on a breakout up and a put
  on one down. `max_per_day` allows more than one trade a day.
- **A signal that fires while option prices are still being fetched is kept for two minutes**, so it is not lost.
- **Exits:** close everything when `exit.when` holds, or when a signal of the other direction fires (the next trade then
  starts from that signal if one is left that day). Stop-losses, targets, trailing, MTM limits and the exit time still apply.
- **Checks:** every signal needs a leg that can trade on it and every up/down leg a signal that says so; levels and
  numbers must be filled in; directional legs and signals need entry on conditions.
- **Presets:** opening range breakout and previous day high/low breakout.

**Not yet:** indicators (EMA, RSI, Supertrend, ...), option-premium values in conditions, pivot / CPR levels, a
"previous candle high/low" level, a preview chart, conditions across different candle sizes in one signal for crossings
(use one size per signal; a crossing fires only on the step its candle finishes).
