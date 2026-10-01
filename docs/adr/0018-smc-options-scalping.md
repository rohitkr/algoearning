# 0018 SMC options scalping: Smart Money Concepts as objective rules, options bought

**Context.** Smart Money Concepts (SMC) are usually traded by eye. The platform needs rules a computer can apply the
same way live, on paper and over history (ADRs 0010, 0014, 0017), and that a user can read back for any trade.

**Decision.** A new strategy kind, `smc_scalp` (`SmcScalpConfig`), run by `SmcScalpRunner`. It reads the index and
buys options only: a bullish signal buys a call, a bearish one a put. It is intraday: nothing is held overnight.
The detectors are pure functions in `ae_core.trading.smc`. They only see completed candles, so nothing looks ahead.

**Timeframes.** All are built from the index's 1-minute candles, aligned to 09:15, and a higher-timeframe candle is
used only once its last minute has closed. The defaults are 15m for bias, 5m for the setup and 1m for the entry.
Earlier sessions reach the runner through `Market.prior_spot_bars`, filled by the engine from `history_candles` or
Redis and by the backtester from the days before the range.

**Definitions** (default thresholds; every one is a config field):

| Concept                         | Rule                                                                                                                                                                                                                                 |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Swing high / low                | Fractal: a high strictly above the 2 candles before it and at least the 2 after it. Known once those have closed                                                                                                                     |
| BOS / CHoCH                     | A candle **close** beyond the latest confirmed swing: with the trend it is a BOS; the first break against it is a CHoCH                                                                                                              |
| Strong / weak                   | After an up-break, the lowest low of the breaking leg is the strong (protected) low, and the highest high since is the weak high (the next buy-side target). Mirror for down                                                         |
| Clear bias                      | The bias timeframe's trend has at least `bias_min_breaks` consecutive breaks in one direction (default 1). No break yet, or a close beyond the strong level, means unclear and no trade                                              |
| Displacement                    | A setup candle whose body is ≥ 1.0 × ATR(14) and closes in the outer quarter of its range                                                                                                                                            |
| Fair value gap                  | A gap between candle 1's high and candle 3's low (bullish), or the mirror; it must be ≥ 0.25 × ATR and not traded through since                                                                                                      |
| Order block                     | The last opposite-coloured candle before the displacement; the zone is its body (option: full range)                                                                                                                                 |
| Liquidity                       | Buy-side above price, sell-side below: previous day high/low, opening range (15 min) high/low, confirmed swings and equal highs/lows (within 0.03 %) of the setup timeframe, and the entry timeframe's swings (`internal_liquidity`) |
| Sweep                           | A wick ≥ 0.01 % beyond a pool that was still untaken, closing back inside within 2 candles. A pool touched less than that, or closed beyond, is gone                                                                                 |
| Premium / discount, equilibrium | Equilibrium is the middle of the impulse leg (the sweep's extreme to the furthest price since). Longs buy only the part of a POI below it; shorts only the part above it. Option: the bias timeframe's dealing range instead         |
| Entry confirmation              | After the POI is tapped, an entry-candle close beyond the pullback's last confirmed swing (the 1m CHoCH), or beyond the tapping candle when the pullback made no swing                                                               |

**Setup → signal.** A setup candle closes with a BOS or CHoCH in the bias direction. Within the 12 candles before it,
opposite liquidity was swept, and a displacement candle sits between the sweep and the break. The FVGs and order
block of that leg, lying between the sweep and the break, become the POI. The POI is cancelled if it is not tapped
within 24 setup candles, if a setup candle closes through it, or if an entry candle closes beyond the sweep. After
the tap, the confirmation must come within 15 entry candles. Every rejection is counted and written to
`trade_events` as `smc_no_trade` with its reason, so the dashboard and backtests show how many setups failed at
which step.

**Levels.**

- Entry is the index when the confirmation closes. Backtests fill at the next minute's open, and live fills at
  once; nothing assumes a limit fill.
- The stop sits beyond the sweep's extreme by 0.03 %. If it is tighter than 0.05 % or wider than 0.35 % of the
  index, there is no trade.
- TP1 = 1R, TP2 = (1 + RR) / 2 R, TP3 = RR, where RR is 2, 3 or 4.
- If the nearest external opposing liquidity is closer than `min_room_r` (default 1R), there is no trade.

**Management.**

- Lots are split into three tranches, each a `Position` with its own index target. With 1–2 lots, the later
  targets get them.
- At TP1 the stop moves to breakeven, and at TP2 it moves to TP1.
- A 40 % premium stop is the backstop. Everything exits at 15:10.
- Entries run 09:30–14:30, with at most 2 trades and 2 losing trades a day, a 15-minute pause after a loss, and
  one trade at a time. The platform's risk checks (kill switch, daily limits, plan lots, live unlock) apply as for
  every runner.

**Option selection** is `ae_core.trading.options`, now shared with the builder's legs.

- Strike: ATM ± N, or the closest premium.
- Expiry: the nearest one; on expiry day from 12:00, the next.
- Liquidity check: volume today, open interest and bid/ask spread.
  - The feed now keeps Breeze's best bid/ask, traded quantity and OI on each tick (`Tick`, `Quote`; the field
    names `bPrice`, `sPrice`, `ttq` and `OI` are to be verified with a live session).
  - An illiquid strike falls back one strike toward the money, else the signal is skipped.
  - A figure the feed does not send is not held against a contract.

**Signal record.** Each trade writes an `smc_signal` event: the option and premium, the index entry, SL, TP1–3,
risk, reward, R:R and a step-by-step reason. Examples of steps are "15m bullish structure…", "sell-side liquidity
swept: …", "5m displacement… and CHoCH through …", "POI FVG … in discount" and "1m CHoCH through …". The run page
shows these as signal cards.

**Backtesting changes (all strategies).**

- Inside a minute the backtester now also moves the index to its low and high. Index-based stops fire on the
  minute's extreme and fill at the option's adverse extreme; index-based targets fill at the option's close. Before
  this they fired only at the minute's open. Builder strategies with index-basis stops will show different, more
  honest, results.
- In-minute exits accept exact reasons (`stop-loss`, `target`) or `stop-loss:` / `target ` prefixes, so the
  expiry-day straddle keeps its old behaviour.
- New metrics: expectancy, max consecutive losses, average holding time, charges as % of gross, and per-signal
  statistics.
- SMC backtests add a modelled bid/ask spread of 0.3 % of the premium (history has trades, not quotes) and are
  replayed a month at a time to bound memory. The backtester derives volume so far and OI from stored candles for
  the liquidity check.

**History.** `python -m ae_worker backfill NIFTY|BANKNIFTY|SENSEX --from 2025-01-01 [--dry-run]` fetches
1-minute history from Breeze's `historical_data_v2` into `history_candles`.

- It fetches the index first, then every strike within 4 strikes of each day's index range, both rights. It covers
  that day's nearest expiry, plus the next one on expiry day, and only the days not stored yet.
- Expiry calendars, paging (1,000 rows, newest first) and the IST timestamp convention come from
  algo-trading-claude's verified downloader. Holidays come from the stored index days.
- It counts calls in the feed's daily counter and stops with 500 left for the live feed. It is resumable, and run
  after market hours. Migration 0012 adds a nullable `oi` column.

**Validation protocol.** Parameters were fixed before any profit or loss was looked at: only how often each rule
fired was examined, on NIFTY and SENSEX June–September 2026 (the first strict defaults produced no trades in four
months). Two profiles are evaluated, never tuned toward a result:

- **strict**: the defaults above.
- **balanced**: 3m setups, with no premium/discount or room filter.

`python -m ae_worker smc-report` runs both profiles × every index × 1:2 / 1:3 / 1:4 over the whole range, and
reports in-sample and out-of-sample halves separately. Win rate is reported as measured; nothing is tuned toward a
target.

**Not modelled.** Bid/ask in history (a modelled spread instead), limit fills, partial fills, IV changes beyond
what option prices show, historical lot sizes, and several setups at once.
