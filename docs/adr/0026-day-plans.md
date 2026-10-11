# 0026 Day plans: a strategy is a list of plans

Status: Accepted, built in one go (model, engine, backtest, deploy, builder). Builds on ADR 0017 (backtesting), 0022 (rule
based strategies) and 0023 (conditions).

**Context.** The rules builder had one schedule, one entry rule and one exit rule for the whole week, and per-weekday
legs bolted on (`day_legs`). A user could not say "Monday: sell a strangle at 09:20; Wednesday: buy on an opening-range
breakout; Friday: an iron condor on Sensex" without three strategies, and the page repeated the same fields in several
places.

**Decision.**

1. A **plan** is one complete routine: name, instrument, weekdays, entry (at a time / when conditions are met / on a
   Telegram tip, window, trades a day, days before expiry), legs (any buy or sell, CE or PE, strike rule, per-leg
   stop-loss, target, trailing, re-entry), holding and exit time, and whole-trade limits (max loss, profit target, profit
   lock, combined premium stop, exit all on a leg's stop, exit on a condition). It is exactly a rules strategy plus a name
   and its weekdays; a plan's weekdays are its entry days (`Plan.to_rules()`).
2. A strategy of kind `plans` is a list of 1-12 plans. Plans may share a day: each runs independently, with its own state
   and positions, one trade at a time within the plan.
3. **The engine is unchanged in substance.** `PlansRunner` wraps one `RulesRunner` per plan, hands every plan the same
   market, merges the orders, and sends each fill or refusal back to the plan that placed the order (a position id ->
   plan map). Its state (`{"plans": {id: state}, "owner": {...}}`) is plain JSON, so a restart rebuilds it.
4. **One run per instrument.** A run trades one instrument (one market feed). A strategy with plans on several
   instruments deploys as one run per instrument ("name · SENSEX"), each holding every plan of its instrument; the
   backtest likewise replays each instrument's plans on that instrument's history and puts the results together.
5. **Old strategies.** `rules`, `time_based` and their weekday legs keep running as they are. The builder opens them as the
   plans they always meant (`plans_from_rules`: the same trades, tested for identical results) and saves plans; nothing
   changes in storage until the user saves. The per-weekday legs UI is gone: a weekday with its own legs is a plan.
   `rules` presets reach the builder already converted.
6. **Builder.** Plans as tabs with a week-at-a-glance strip (who trades each weekday); per plan, one card per step:
   1 Entry, 2 Legs, 3 Exit. One-click starting points for conditions: opening-range breakout / breakdown, previous-day
   high / low break, EMA 9/21 crossover, RSI 30/70, Supertrend flip. Errors are shown on the plan and field they belong to.
7. Tip entries (ADR 0025) work per plan; a strategy with tip plans cannot be backtested yet.

**Safety.** Tests: the older strategies convert to plans with identical trades (five kinds, including weekday legs and a
held-two-days strategy); overlapping plans run independently and see only their own positions; the runner state survives a
restart with a trade held overnight; a stress run over many random price paths with every kind of exit asserts that every
plan trades every day with prices and nothing is left open (the check that caught the lost-exit bug of ADR 0017).
