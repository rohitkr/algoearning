# 0022 Rule-based strategies: one builder for intraday and overnight trades

**Context.** The builder (`time_based`, ADR 0010) could only enter legs at one time and exit them the same day. Users
want to build, without code, the strategies Indian options traders actually run: a straddle sold at 15:00 and bought
back next morning (BTST), positional strangles a few days before expiry held to expiry day, a combined-premium stop,
profit locking, strikes chosen by premium or distance from the index; later opening-range breakouts and indicator
entries. Plan: [the no-code builder plan](https://claude.ai/code/artifact/d4afb036-58c1-492f-be12-44865a055526).

**Decision.** A new strategy kind `rules` (`ae_core.strategy.RulesConfig`) replaces `time_based` as the builder's
config; one runner (`RulesRunner`) runs it live, on paper and in backtests (ADR 0017). It is built in phases, each
approved separately: 1 schedule and holding (this ADR), 2 conditions, breakouts and reference levels, 3 indicators on
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

**Not yet:** indicators (phase 3; conditions and levels arrived in ADR 0023); exchange holiday
calendars; option-premium indicators. A plain-English strategy writer was considered and not taken up for now.

**Legs per weekday.** `day_legs` (optional, MON..FRI) gives a weekday its own legs, replacing `legs` on that day: a
strangle on Monday, an iron condor on Tuesday, an iron fly on Wednesday, each leg with its own strike rule (premium,
points from the index, ATM offset ...). A weekday without an entry uses `legs`; `legs` may be empty, and then a weekday with no legs of its own does not trade (a config needs at least one leg somewhere). Leg ids are unique across all sets (a
position held overnight finds its leg by id); a weekday with its own legs must be one of the entry days. The builder
offers Strangle / Straddle / Iron condor / Iron fly per weekday (strikes by premium or points), copy to the other
weekdays, and the normal leg editor to fine-tune.

