# 0021 A second price provider: the platform's own Kite account, Breeze stays the default

Extends 0013 (one platform feed). Still 0006: users' broker accounts only execute; this is not one of them.

**Context.** The feed only spoke ICICI Breeze. The owner wants to be able to switch the platform's prices to a Kite
Connect app on his own Zerodha account with little effort, without touching what reads the prices.

**Decision.** A provider is one `Source` (`ae_marketdata/sources.py`) behind the same Redis hub, chosen by
`MARKET_DATA_SOURCE`: `breeze`, `kite`, `simulated` or `auto` (`choose_source`). `auto` keeps Breeze first: Breeze
when its keys are set, else Kite when `KITE_FEED_API_KEY` is set, else simulated prices. The engine, the API, the
charts and the backtester read Redis and `history_candles` only, so they do not change with the provider.

- **Live (`KiteSource`, `ae_marketdata/kite_feed.py`):** Kite's websocket in full mode, one connection, our own
  parser for its binary packets (no SDK: it would pull in Twisted). Kite sends ticks, not candles, so the feed
  builds the 1-minute bars itself, as it does for simulated prices. Instruments are found by Kite's instrument token
  from Zerodha's public daily instrument lists: indices by tradingsymbol (`instruments.kite_symbol`, migration
  0013: NIFTY 50, NIFTY BANK, NIFTY FIN SERVICE, NIFTY MID SELECT, SENSEX), options by name, expiry, strike, right.
- **Daily login:** like Breeze's, from Monitor > Market data. The platform Kite app's redirect URL is that page;
  Kite sends back a request token, which the API exchanges (it holds `KITE_FEED_API_SECRET`) for the day's access
  token, stored encrypted in `platform_secrets` (`kite_feed_session`) until 06:00 IST. The feed picks it up within
  seconds. A token Kite refuses is not retried until a new login is saved. If the login lands elsewhere, the
  admin pastes the whole address (or just the token), or an access token another program already got from today's
  login to the same Kite app (checked with Kite's profile call before it is kept); `KITE_FEED_CLIENT_ID`, when set, refuses any other Zerodha
  account, and Monitor shows which account is logged in.
- **History:** `python -m ae_worker backfill` fetches from the feed's provider. `KiteHistory` answers the backfill's
  requests (written in Breeze's parameters) from Kite's historical API in 60-day windows. Kite only has contracts
  that are still listed, so expired options are skipped; the index has full history.
- **Switching:** set `KITE_FEED_API_KEY` and `KITE_FEED_API_SECRET`, set `MARKET_DATA_SOURCE=kite`, restart the
  feed, log in from Monitor. Back to Breeze: `MARKET_DATA_SOURCE=breeze` (or `auto`) and restart.

**Consequences.** Another provider later is one more `Source` class and its login. The Kite app needs Kite's paid
Connect plan with live data and historical access (the free personal plan has neither, as far as we know). Kite
allows 3,000 instruments per connection; the feed stops adding past that. Packet layouts follow Kite's published
docs and are unit-tested, but have not yet run against a live Kite session.
