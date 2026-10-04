# 0024 Indicators: EMA, RSI, MACD, Supertrend and others as condition values

**Context.** ADR 0023 lets a strategy enter and exit on the candle close, levels and numbers. Most retail strategies
also read indicators: an EMA crossover, RSI leaving oversold, a close through Supertrend. This is phase 3 of the plan.

**Decision.** An indicator is a fourth kind of condition value (`Operand.kind = "indicator"`), usable on either side of
any entry or exit condition, on the condition's candles (1-60 minutes):

- **Indicators:** EMA, SMA, RSI, MACD (line / signal / histogram: `line`), Supertrend, Bollinger Bands (middle /
  upper / lower), ATR, ADX (with +DI / -DI). Settings: `period` (MACD: the slow period), `multiplier` (Supertrend's ATR
  multiple, default 3; Bollinger's band width, default 2), and MACD's `fast` and `smoothing` (signal) periods.
- **Formulas** (`trading.indicators`) as charting platforms compute them: EMA seeded with the simple mean; RSI, ATR,
  ADX and Supertrend with Wilder's smoothing. Each returns one value per candle, None until there is enough history.
- **Warm-up:** values are computed over earlier sessions' candles followed by today's, so the first candle of the day
  already has a settled EMA or RSI. A strategy reads `prior_days_needed()` sessions: about three times the longest
  period in candles (one for previous-day levels), at most 10. The engine loads them once a day per number of
  sessions; backtests load the days before the start for them. Earlier sessions' candles are cached in the runner.
- **Same rules as ADR 0023:** a cross only on the candle that crossed, finished candles only, no signal from stale
  data. Validation checks that a line exists for the indicator, the multiplier is used, MACD's fast period is shorter.
- **Presets:** EMA 9/21 crossover with an RSI filter, Supertrend (10, 3), RSI reversal from 30 / 70.

**Not yet:** indicators on option premiums, VWAP (index candles carry no volume), user-chosen sources (an indicator of
the high or of another indicator).
