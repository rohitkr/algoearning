# 0013 The platform price feed: Breeze streaming through Redis

Implements 0006 (one platform feed; brokers execute only).

**Decision.** One feed process (`python -m ae_marketdata`, the `feed` service) streams prices for the whole
platform and publishes them to Redis; the engine and the API only read Redis (`ae_marketdata.hub.Hub`).

- **What streams:** every active index, always; plus any option contract a reader asks for with `Hub.want` (the
  engine for the contracts it trades, the API when a screen asks). Interest expires after 3 minutes unless renewed,
  so nothing streams that nobody reads.
- **Breeze** (`BreezeSource`, the official SDK): per instrument, the 1-minute OHLC candle stream (bars) and exchange
  quotes (last price) over Breeze's websocket. Streaming costs no REST calls, so the 5,000 calls/day budget is not a
  limit on users or contracts; the few REST calls made (the session) are counted in Redis and shown in Monitor.
  The legacy engine polled `historical_data_v2` instead, which does not scale past a handful of instruments.
- **The daily session** is an admin task: Monitor > Market data links to ICICI's login; the redirect (set the Breeze
  app's redirect URL to `<web>/monitor/market-data`) or a pasted `apisession` token is stored envelope-encrypted in
  `platform_secrets` (system only) until midnight IST, and the feed reconnects with it within seconds.
- **Simulated source** for development and demos when Breeze is not configured: random-walk indices, options priced
  from them. The UI labels it "Simulated" everywhere prices show.
- **Redis layout:** `md:last` (latest tick per key), `md:bars:<key>:<date>` (the day's completed 1-minute bars, 4 days
  kept), `md:bar` / `md:tick` pub/sub, `md:subs` (wanted keys), `md:health` (the feed's status).
- Breeze codes per index live in `instruments.feed_code` / `spot_exchange` (data, like lot sizes).

**To verify with a live Breeze session** (not possible in tests, which use a fake SDK): the option expiry format in
subscriptions (`06-Oct-2026`), whether a candle's `datetime` is the minute's start, and how many instruments one
socket carries. Each is isolated in `BreezeSource` (`breeze_expiry`, `parse`).
