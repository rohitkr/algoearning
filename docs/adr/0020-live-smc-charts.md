# 0020 Live SMC charts: candles from the platform feed, zones from `smartmoneyconcepts`, pushed over SSE

**Context.** Users want to watch the indices with Smart Money Concepts drawn on them, several charts side by side,
updating live, without paying for a charting data plan. ADR 0006 says prices come from the one platform feed, not
from users' brokers, and ADR 0013 puts that feed in Redis.

**Decision.** A Charts page (`/charts`) with 1 to 4 live candlestick charts in TradingView-style layouts (TradingView Lightweight Charts),
each with its own index (Nifty 50, Sensex, Nifty Bank) and timeframe (1, 3, 5 or 15 minutes).

- **Data.** The API builds candles from the feed's completed 1-minute bars (`md:bar`) and moves the forming candle
  with its ticks (`md:tick`) between bars (`ae_marketdata.candles`). A chart starts from the history store plus the
  last days' bars in Redis (`history.recent_bars`). Candles are aligned to the session open, like the exchange's.
  Breeze stays the only source: Kite's market data would need every user's paid historical add-on, and the feed
  already streams indices at all times.
- **Edge cases at the live edge.** A tick for a minute whose bar is in is ignored (the bar is the truth). A candle
  closes when its last minute arrives, when a later minute arrives, or 20 s after its time is over; a bar that lands
  after that revises the closed candle and the chart redraws it. Duplicate bars replace, never add.
- **Zones.** `smartmoneyconcepts` (joshyattridge) computes FVGs, order blocks, BOS/CHoCH, liquidity (equal
  highs/lows) and the previous day's high/low on the closed candles, as lowercase `open/high/low/close/volume`
  frames (`ae_marketdata.smc_overlay`). The whole overlay is recomputed whenever a candle closes or is revised, and
  replaces the previous one: mitigations, breakers, confirmed swings and removed breaks are therefore always as the
  library sees them now, never patched. Two corrections: the library's forced swings on the first and newest candle
  are dropped (they produced false breaks on the newest candle), and order-block strength is shown only when there
  is volume (indices have none). Swing length is 5 candles.
- **Delivery.** One shared `ChartFeed` per index and timeframe in the API process, however many charts watch it.
  Each chart reads a server-sent-events stream (`GET /v1/charts/stream`) with `fetch`, so the Clerk token goes in the
  Authorization header like every other call. The stream starts with a full snapshot, then sends `candle`,
  `revise`, `smc` and `status` messages; a chart that falls behind gets a new snapshot instead of a backlog. The
  server ends each stream after 15 minutes and the page reconnects with a fresh token (and a fresh snapshot).
- **Screen.** Zones are canvas boxes and dashed lines drawn under the candles by a series primitive; bullish zones
  are green/cyan, bearish red/orange, in a shade per theme. Each overlay can be switched off. Layouts (1, 2 side by
  side or stacked, 3 side by side or one large + two, 4 in a grid) live in `components/charts/layouts.ts`; the
  borders between charts drag to resize them, and the board fills the window below the page title. A new index or
  timeframe resets the price axis to autoscale. The browser remembers the layout, sizes and charts. `CHART_CODES`
  (API) is where to offer more indices.

**Consequences.** The chart's zones are the library's and can differ from the strategy engine's own no-lookahead
detectors (ADR 0018): the library confirms a swing with later candles, so a zone near the live edge can still change
or disappear as candles close. The API now depends on pandas and the library (numba comes with it).
